"""Working files for classification/review; never writes operational history to PostgreSQL.

One server process owns this directory. Large payloads are append-only JSONL;
the in-memory index contains byte offsets only, not all transaction contents.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from threading import RLock
import uuid

from .db import now
from .normalize import fold
from .payment_period import with_payment_period
from .name_extraction import structured_name_fields
from .platform_utils import sync_directory


class WorkingFiles:
    def __init__(self, root):
        self.root = Path(root) / 'working'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.jobs, self.locations = {}, {}
        self.meta_path = self.root / 'state.json'
        self.state = self._read(self.meta_path) if self.meta_path.exists() else {'namespace': uuid.uuid4().hex, 'next_id': 1}
        self._write(self.meta_path, self.state)
        for folder in self.root.iterdir():
            if not folder.is_dir() or folder.name.startswith('.') or not (folder / 'job.json').exists():
                continue
            job = self._read(folder / 'job.json')
            if job['status'] in ('queued', 'running', 'cancelling'):
                job.update(status='interrupted', error='Server restarted; upload the file again')
                self._write(folder / 'job.json', job)
            self.jobs[job['id']] = job
            for row_id, entry in self._read_index(folder).items():
                if isinstance(entry, int):
                    entry = {'offset': entry, 'decision': None, 'status': 'pending'}
                review_path = folder / 'reviews' / (str(row_id) + '.json')
                status = self._read(review_path)['status'] if review_path.exists() else entry['status']
                self.locations[int(row_id)] = (job['id'], entry['offset'], entry['decision'], status)

    @staticmethod
    def _read(path):
        with Path(path).open(encoding='utf-8') as source:
            return json.load(source)

    @staticmethod
    def _write(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as output:
                json.dump(value, output, ensure_ascii=False, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_index(folder):
        path = folder / 'index.json'
        return WorkingFiles._read(path) if path.exists() else {}

    @staticmethod
    def _remove_intent(path):
        path = Path(path)
        if not path.exists():
            return
        path.unlink()
        sync_directory(path.parent)

    def create_job(self, kind, filename=''):
        with self.lock:
            job_id = str(uuid.uuid4())
            job = {'id': job_id, 'kind': kind, 'filename': filename, 'status': 'queued',
                   'progress': 0, 'summary': {}, 'error': None, 'created_at': now()}
            self._write(self.root / job_id / 'job.json', job)
            self.jobs[job_id] = job
            return deepcopy(job)

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('Job not found')
            return deepcopy(self.jobs[job_id])

    def list_jobs(self):
        with self.lock:
            return deepcopy(sorted(self.jobs.values(), key=lambda job: job['created_at'], reverse=True))

    def update_job(self, job_id, **values):
        with self.lock:
            job = {**self.jobs[job_id], **values}
            self._write(self.root / job_id / 'job.json', job)
            self.jobs[job_id] = job
            return deepcopy(job)

    def active(self):
        return any(job['status'] in ('queued', 'running', 'cancelling') for job in self.list_jobs())

    def append(self, job_id, records):
        with self.lock:
            folder = self.root / job_id
            index = self._read_index(folder)
            first = self.state['next_id']
            self.state['next_id'] += len(records)
            self._write(self.meta_path, self.state)
            additions = {}
            with (folder / 'results.jsonl').open('ab') as output:
                for row_id, record in enumerate(records, start=first):
                    record = with_payment_period(record)
                    entry = {**record, 'id': row_id, 'job_id': job_id,
                             'entry_key': self.state['namespace'] + ':' + str(row_id),
                             'created_at': now(), 'status': 'pending', 'learned': False,
                             'confirmed_customer_id': None}
                    additions[str(row_id)] = {'offset': output.tell(), 'decision': entry['decision'], 'status': 'pending'}
                    output.write((json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
            index.update(additions)
            self._write(folder / 'index.json', index)
            self.locations.update({int(key): (job_id, entry['offset'], entry['decision'], entry['status']) for key, entry in additions.items()})
            return list(map(int, additions))

    def append_row_error(self, job_id, issue):
        with self.lock:
            path = self.root / job_id / 'row-errors.jsonl'
            with path.open('ab') as output:
                output.write((json.dumps(issue, ensure_ascii=False) + '\n').encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
            summary = deepcopy(self.jobs[job_id]['summary'])
            summary['row_error_count'] = summary.get('row_error_count', 0) + 1
            samples = summary.setdefault('row_errors', [])
            if len(samples) < 50:
                samples.append(issue)
            counts = summary.setdefault('error_sheet_counts', {})
            counts[issue['sheet']] = counts.get(issue['sheet'], 0) + 1
            self.update_job(job_id, summary=summary)

    def row_errors(self, job_id):
        # Snapshot length; close the handle before yielding, including on Windows.
        with self.lock:
            path = self.root / job_id / 'row-errors.jsonl'
            size = path.stat().st_size if path.exists() else 0
        offset = 0
        while offset < size:
            with self.lock:
                if not path.exists():
                    return
                with path.open('rb') as source:
                    source.seek(offset)
                    line = source.readline()
                    offset = source.tell()
            if not line:
                return
            yield json.loads(line)

    def row(self, row_id):
        with self.lock:
            location = self.locations.get(int(row_id))
            if not location:
                raise ValueError('Transaction not found')
            job_id, offset, _, _ = location
            folder = self.root / job_id
            overlay = folder / 'reviews' / (str(row_id) + '.json')
            with (folder / 'results.jsonl').open('rb') as source:
                source.seek(offset)
                original = json.loads(source.readline())
            record = {**original, **self._read(overlay)} if overlay.exists() else original
            if 'name_extraction' not in record:
                record = {**record, **structured_name_fields(record.get('raw', ''))}
            return with_payment_period(record)

    def rows(self, job_id=None, reverse=False):
        # Take only index keys; read one payload at a time and release the lock between reads.
        with self.lock:
            row_ids = sorted((row_id for row_id, location in self.locations.items()
                              if job_id is None or location[0] == job_id), reverse=reverse)
        for row_id in row_ids:
            try:
                yield self.row(row_id)
            except ValueError:
                continue  # A user may have removed a completed working file.

    def query(self, offset=0, limit=20, decision=None, status=None, job_id=None, q=''):
        if not q:
            with self.lock:
                ids = sorted((row_id for row_id, location in self.locations.items()
                              if (job_id is None or location[0] == job_id)
                              and (not decision or location[2] == decision)
                              and (not status or location[3] == status)), reverse=True)
                return {'total': len(ids), 'items': [self.row(row_id) for row_id in ids[offset:offset + limit]]}
        total, items = 0, []
        for row in self.rows(job_id, reverse=True):
            if decision and row['decision'] != decision or status and row['status'] != status:
                continue
            if q and fold(q) not in fold(' '.join(str(row.get(key) or '') for key in
                       ('raw', 'customer_id', 'customer_name', 'confirmed_customer_id', 'confirmed_customer_name',
                        'sheet', 'payer', 'reference', 'source', 'payment_period', 'extracted_name'))):
                continue
            if offset <= total < offset + limit:
                items.append(row)
            total += 1
        return {'total': total, 'items': items}

    def begin_review(self, row, payload):
        with self.lock:
            path = self.root / row['job_id'] / 'intents' / (str(row['id']) + '.json')
            if path.exists():
                previous = self._read(path)
                if previous['payload'] != payload:
                    raise ValueError('A confirmation is pending; retry the same customer first')
                return previous
            intent = {'transaction_id': row['id'], 'payload': payload, 'created_at': now()}
            self._write(path, intent)
            return intent

    def finish_review(self, record):
        with self.lock:
            folder = self.root / record['job_id']
            overlay = {key: record[key] for key in ('confirmed_customer_id', 'confirmed_customer_name', 'status', 'learned', 'reviewed_at',
                       'payment_mode', 'provider_kind', 'provider_name', 'payment_mode_evidence') if key in record}
            self._write(folder / 'reviews' / (str(record['id']) + '.json'), overlay)
            location = self.locations[record['id']]
            self.locations[record['id']] = (*location[:3], record['status'])
            self._remove_intent(folder / 'intents' / (str(record['id']) + '.json'))

    def pending_reviews(self):
        with self.lock:
            return [self._read(path) for path in self.root.glob('*/intents/*.json')]

    def abort_review(self, row):
        with self.lock:
            self._remove_intent(self.root / row['job_id'] / 'intents' / (str(row['id']) + '.json'))

    def counts(self, job_id=None):
        result = {'transactions': 0, 'pending_review': 0, 'confirmed': 0, 'rejected': 0, 'decisions': {}}
        with self.lock:
            locations = list(self.locations.values())
        for location in locations:
            if job_id and location[0] != job_id:
                continue
            result['transactions'] += 1
            result['pending_review'] += location[3] == 'pending'
            result['confirmed'] += location[3] == 'confirmed'
            result['rejected'] += location[3] == 'rejected'
            result['decisions'][location[2]] = result['decisions'].get(location[2], 0) + 1
        return result

    def remove_job(self, job_id):
        with self.lock:
            job = self.job(job_id)
            if job['status'] in ('queued', 'running', 'cancelling'):
                raise ValueError('Stop the job before removing its working files')
            if any(path.is_file() for path in (self.root / job_id / 'intents').glob('*.json')):
                raise ValueError('Finish pending confirmations before removing working files')
            # Rename first: after a crash, deleted directories are never re-opened as jobs.
            folder = self.root / job_id
            removed = self.root / ('.removed-' + uuid.uuid4().hex)
            folder.rename(removed)
            self.jobs.pop(job_id)
            self.locations = {row_id: location for row_id, location in self.locations.items() if location[0] != job_id}
        shutil.rmtree(removed)
