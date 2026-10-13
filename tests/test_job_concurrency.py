"""Exercise scheduling, cancellation and shared knowledge through real job endpoints."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import Core
from app.db import Customer, KnowledgeReceipt, Metadata, Pattern, RecurringLink
from app.main import create_app
from app.workspace import WorkingFiles
from conftest import workbook


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Timed out waiting for job state')


def upload(client, path, learning=False):
    with path.open('rb') as source:
        response = client.post('/knowledge/import' if learning else '/batch/classify',
            files={'file': (path.name, source)}, data={'confirmed': 'true'} if learning else {})
    assert response.status_code == 202, response.text
    return response.json()['job_id']


def raw_file(tmp_path, key):
    return workbook(tmp_path / f'{key}.xlsx', [(12, {'B': 'Số tham chiếu', 'I': 'Mô tả'}),
        (14, {'B': key, 'C': '01/08/2026', 'E': 100000, 'D': 0, 'I': key})])


class GatedCore(Core):
    def __init__(self, *keys):
        super().__init__()
        self.entered = {key: Event() for key in keys}
        self.release = {key: Event() for key in keys}

    def prepare_batch(self, raws):
        for raw in raws:
            if raw == 'BROKEN':
                raise RuntimeError('Controlled worker failure')
            if raw in self.entered:
                self.entered[raw].set()
                assert self.release[raw].wait(10), 'Test failed to release processing'
        return super().prepare_batch(raws)

    def release_all(self):
        for event in self.release.values():
            event.set()


def test_two_jobs_overlap_third_waits_and_outputs_are_isolated(db, tmp_path):
    engine, _ = db
    core = GatedCore('FIRST', 'SECOND', 'THIRD')
    app = create_app(engine, core, tmp_path / 'runtime')
    with TestClient(app) as client:
        try:
            ids = [upload(client, raw_file(tmp_path, key)) for key in core.entered]
            assert core.entered['FIRST'].wait(5) and core.entered['SECOND'].wait(5)
            jobs = {job['id']: job for job in client.get('/jobs').json()}
            assert [jobs[jid]['status'] for jid in ids] == ['running', 'running', 'queued']
            assert not core.entered['THIRD'].is_set()
            assert client.delete('/jobs/' + ids[0]).status_code == 409
            core.release['FIRST'].set()
            assert core.entered['THIRD'].wait(5)  # second is still busy
            assert client.get('/jobs/' + ids[1]).json()['status'] == 'running'
            core.release_all()
            wait_until(lambda: all(app.state.working.job(jid)['status'] == 'completed' for jid in ids))
            rows = [client.get('/transactions', params={'job_id': jid}).json()['items'] for jid in ids]
            assert [batch[0]['raw'] for batch in rows] == list(core.entered)
            assert len({batch[0]['id'] for batch in rows}) == 3
            assert all(batch[0]['job_id'] == jid for jid, batch in zip(ids, rows))
            assert all(app.state.working.job(jid)['progress'] == 1 for jid in ids)
            wait_until(lambda: not list((tmp_path / 'runtime/uploads').glob('*/*.xlsx')))
        finally:
            core.release_all()


def test_cancelling_one_job_and_failure_do_not_stop_other_jobs(db, tmp_path):
    engine, _ = db
    core = GatedCore('FIRST', 'SECOND', 'QUEUED')
    app = create_app(engine, core, tmp_path / 'runtime')
    with TestClient(app) as client:
        try:
            first, second, queued = [upload(client, raw_file(tmp_path, key)) for key in core.entered]
            assert core.entered['FIRST'].wait(5) and core.entered['SECOND'].wait(5)
            assert client.post('/jobs/' + queued + '/cancel').json()['status'] == 'cancelled'
            assert not core.entered['QUEUED'].is_set()
            assert client.post('/jobs/' + first + '/cancel').json()['status'] == 'cancelling'
            core.release['FIRST'].set()
            wait_until(lambda: app.state.working.job(first)['status'] == 'cancelled')
            broken = upload(client, raw_file(tmp_path, 'BROKEN'))
            wait_until(lambda: app.state.working.job(broken)['status'] == 'failed')
            assert app.state.working.job(second)['status'] == 'running'
            after = upload(client, raw_file(tmp_path, 'AFTER'))
            wait_until(lambda: app.state.working.job(after)['status'] == 'completed')
            core.release['SECOND'].set()
            wait_until(lambda: app.state.working.job(second)['status'] == 'completed')
            assert not list(app.state.working.rows(first))
            assert not list(app.state.working.rows(queued))
            assert list(app.state.working.rows(second))[0]['raw'] == 'SECOND'
            assert app.state.working.job(second)['error'] is None
            wait_until(lambda: not list((tmp_path / 'runtime/uploads').glob('*/*.xlsx')))
        finally:
            core.release_all()


def test_shutdown_cancels_queue_and_running_jobs_and_cleans_uploads(db, tmp_path):
    engine, _ = db
    core = GatedCore('FIRST', 'SECOND', 'QUEUED')
    app = create_app(engine, core, tmp_path / 'runtime')
    with TestClient(app) as client:
        try:
            ids = [upload(client, raw_file(tmp_path, key)) for key in core.entered]
            assert core.entered['FIRST'].wait(5) and core.entered['SECOND'].wait(5)
            with ThreadPoolExecutor(max_workers=1) as controller:
                stopped = controller.submit(app.router.on_shutdown[0])
                try:
                    wait_until(lambda: app.state.working.job(ids[2])['status'] == 'cancelled')
                    assert all(app.state.working.job(jid)['status'] == 'cancelling' for jid in ids[:2])
                    assert not core.entered['QUEUED'].is_set()
                finally:
                    core.release_all()
                stopped.result(timeout=10)
            assert all(app.state.working.job(jid)['status'] == 'cancelled' for jid in ids)
            assert not list((tmp_path / 'runtime/uploads').glob('*/*.xlsx'))
            assert not app.state.working.active()
        finally:
            core.release_all()


def test_parallel_imports_of_identical_file_do_not_duplicate_knowledge(db, tmp_path):
    engine, factory = db
    rendezvous = Barrier(2)

    class ImportCore(Core):
        def prepare_batch(self, raws):
            rendezvous.wait(timeout=5)
            return super().prepare_batch(raws)

    app = create_app(engine, ImportCore(), tmp_path / 'runtime')
    source = workbook(tmp_path / 'confirmed.xlsx', [(4, {'C': 'IDKH', 'G': 'LỆNH GỐC NGÂN HÀNG'}),
        (5, {'A': 46204, 'C': '001234', 'D': 'Nguyễn Văn An', 'E': 100000,
             'G': 'TT tien nuoc KH 001234', 'H': 'BIDV'})])
    with TestClient(app) as client:
        ids = [upload(client, source, learning=True) for _ in range(2)]
        wait_until(lambda: all(app.state.working.job(jid)['status'] in ('completed', 'failed') for jid in ids))
        jobs = [app.state.working.job(jid) for jid in ids]
        assert all(job['status'] == 'completed' for job in jobs), jobs
        assert all(job['summary']['row_error_count'] == 0 for job in jobs)
        assert sum(job['summary']['counts']['learned'] for job in jobs) == 1
        with factory() as session:
            for model in (Customer, Pattern, RecurringLink, KnowledgeReceipt):
                assert session.scalar(select(func.count()).select_from(model)) == 1
            assert session.scalar(select(Pattern)).seen_count == 1
            assert session.scalar(select(RecurringLink)).seen_count == 1


def test_cancelled_duplicate_import_preserves_other_jobs_completed_metadata(db, tmp_path):
    engine, factory = db
    raw = 'TT tien nuoc KH 001234'
    core = GatedCore(raw)
    original = core.prepare_batch
    app = create_app(engine, core, tmp_path / 'runtime')
    source = workbook(tmp_path / 'confirmed.xlsx', [(4, {'C': 'IDKH', 'G': 'LỆNH GỐC NGÂN HÀNG'}),
        (5, {'A': 46204, 'C': '001234', 'D': 'Nguyễn Văn An', 'E': 100000, 'G': raw, 'H': 'BIDV'})])
    with TestClient(app) as client:
        try:
            first = upload(client, source, learning=True)
            assert core.entered[raw].wait(5)
            # First invocation is already held in the gate; the second may finish.
            core.prepare_batch = lambda raws: Core.prepare_batch(core, raws)
            second = upload(client, source, learning=True)
            wait_until(lambda: app.state.working.job(second)['status'] == 'completed')
            assert client.post('/jobs/' + first + '/cancel').json()['status'] == 'cancelling'
            core.release_all()
            wait_until(lambda: app.state.working.job(first)['status'] == 'cancelled')
            with factory() as session:
                imports = list(session.scalars(select(Metadata).where(Metadata.key.like('import:%'))))
                assert len(imports) == 1 and json.loads(imports[0].value)['status'] == 'completed'
                assert session.scalar(select(func.count(Pattern.id))) == 1
        finally:
            core.release_all()
            core.prepare_batch = original


def test_restart_marks_both_running_jobs_and_queue_interrupted_keeps_results(tmp_path):
    working = WorkingFiles(tmp_path)
    jobs = [working.create_job('batch_classify', f'{i}.xlsx') for i in range(3)]
    for job in jobs[:2]:
        working.update_job(job['id'], status='running')
    ids = working.append(jobs[0]['id'], [{'raw': 'Da xu ly', 'decision': 'manual_check'}])
    restarted = WorkingFiles(tmp_path)
    assert all(job['status'] == 'interrupted' for job in restarted.list_jobs())
    assert restarted.row(ids[0])['raw'] == 'Da xu ly'
    assert not restarted.active()
