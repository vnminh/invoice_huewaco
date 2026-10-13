"""Security and integrity boundaries for interactive knowledge, on isolated SQLite only."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.access import AccessPolicy
from app.core import Core
from app.db import Alias, BankAlias, KnowledgeChange, KnowledgeReceipt, Pattern, Posting
from app.excel import ExcelTransaction
from app.main import create_app
from app.interactive import resolve_channel


@pytest.fixture
def interactive(db, tmp_path):
    engine, factory = db
    core = Core()
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, 'TT tien nuoc KH 001234 Nguyen Van A', payer='BIDV'), '001234', 'Nguyễn Văn A')
    application = create_app(engine, core, tmp_path / 'work', access_policy=AccessPolicy('admin', 'test-secret-long-enough'))
    with TestClient(application) as client:
        client.auth = ('admin', 'test-secret-long-enough')
        yield client, factory
    application.state.executor.shutdown(wait=True)


def stamp(client):
    response = client.get('/database/tables')
    assert response.status_code == 200
    return {'If-Match': response.headers['X-Knowledge-Revision']}


def test_every_data_route_requires_authentication(interactive):
    client, _ = interactive
    for path in ('/', '/stats', '/database/tables', '/jobs', '/export/00000000-0000-0000-0000-000000000001'):
        assert client.get(path, auth=('bad', 'bad')).status_code == 401
    assert client.post('/knowledge/aliases', auth=('bad', 'bad'), json={}).status_code == 401


def test_csrf_and_revision_conflicts_do_not_change_knowledge(interactive):
    client, factory = interactive
    body = {'customer_id': '001234', 'alias': 'Tên đã kiểm tra', 'confirmed': True}
    headers = stamp(client)
    assert client.post('/knowledge/aliases', json=body).status_code == 428
    assert client.post('/knowledge/aliases', json=body, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    first = client.post('/knowledge/aliases', json=body, headers=headers)
    assert first.status_code == 201
    assert client.post('/knowledge/aliases', json={**body, 'alias': 'Tên khác'}, headers=headers).status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count(KnowledgeChange.id))) == 1
        assert session.scalar(select(KnowledgeChange.actor)) == 'admin'


def test_alias_postings_and_undo_are_atomic(interactive):
    client, factory = interactive
    response = client.post('/knowledge/aliases', headers=stamp(client),
        json={'customer_id': '001234', 'alias': 'Nguyễn Anh Dũng', 'confirmed': True})
    assert response.status_code == 201
    change_id, alias_id = response.json()['change_id'], response.json()['id']
    with factory() as session:
        assert session.scalar(select(Posting.id).where(Posting.token == 'name:dung'))
    result = client.post(f'/knowledge/changes/{change_id}/undo', headers=stamp(client))
    assert result.status_code == 200
    with factory() as session:
        assert session.get(Alias, alias_id) is None
        assert session.scalar(select(Posting.id).where(Posting.token == 'name:dung')) is None
        assert session.get(KnowledgeChange, change_id).reverted_by
    assert client.post(f'/knowledge/changes/{change_id}/undo', headers=stamp(client)).status_code == 409


def test_bank_alias_is_exact_scoped_and_cannot_remap_a_bank(interactive):
    client, factory = interactive
    payload = {'alias': 'Bank Hue Test', 'channel_key': 'bidv', 'confirmed': True}
    assert client.post('/knowledge/bank-aliases', headers=stamp(client), json=payload).status_code == 201
    assert client.post('/knowledge/bank-aliases', headers=stamp(client), json=payload).status_code == 409
    assert client.post('/knowledge/bank-aliases', headers=stamp(client), json={**payload, 'alias': 'VCB'}).status_code == 409
    with factory() as session:
        assert resolve_channel(session, 'BANK HUẾ TEST') == 'bidv'
        assert resolve_channel(session, 'Bank Hue Test other') != 'bidv'
        core = Core()
        core.learn(session, ExcelTransaction(2, 'Tien nuoc Ma KH 001234', payer='Bank Hue Test'), '001234')
        session.commit()
    with factory() as session:
        assert set(session.scalars(select(Pattern.channel_key))) == {'bidv'}
    # An alias already used to learn cannot be removed via undo.
    change = client.get('/database/tables/knowledge_changes/rows').json()['items'][0]
    assert client.post('/knowledge/changes/'+str(change['id'])+'/undo', headers=stamp(client)).status_code == 409


def test_no_arbitrary_database_write_or_mass_assignment(interactive):
    client, factory = interactive
    assert client.patch('/database/tables/customers/rows/001234', headers=stamp(client), json={'id': '999999'}).status_code == 405
    assert client.post('/knowledge/aliases', headers=stamp(client), json={
        'customer_id': '001234', 'alias': 'Tên hợp lệ', 'confirmed': True, 'confidence': 500}).status_code == 422
    assert client.get('/database/tables/customers;DROP TABLE customers/rows').status_code == 404
    assert client.get('/database/tables/customers/rows', params={'q': "%' OR 1=1;--"}).json()['items'] == []
    assert client.get('/benchmark/latest').status_code == 404
    assert client.get('/').headers['X-Frame-Options'] == 'DENY'


def test_unconfigured_remote_access_and_forwarded_access_are_denied(db, tmp_path):
    engine, _ = db
    application = create_app(engine, Core(), tmp_path/'local', access_policy=AccessPolicy(testing=False))
    try:
        with TestClient(application, client=('192.0.2.3', 4321)) as client:
            assert client.get('/stats').status_code == 403
        with TestClient(application, client=('127.0.0.1', 4321), base_url='http://localhost') as client:
            assert client.get('/stats', headers={'X-Forwarded-For':'192.0.2.3'}).status_code == 403
            assert client.get('/stats', headers={'Host':'evil.example'}).status_code == 403
    finally:
        application.state.executor.shutdown(wait=True)


def test_bulk_payment_detects_same_count_changed_values(interactive):
    client, factory = interactive
    headers = stamp(client)
    scope = client.get('/database/tables/payment_templates/rows').json()
    with factory.begin() as session:
        pattern = session.scalar(select(Pattern))
        pattern.payment_mode = 'self'  # A concurrent out-of-band change with the same row counts.
    response = client.post('/knowledge/templates/payment/bulk', headers=headers, json={
        'payment_mode': 'proxy', 'provider_kind': 'bank', 'provider_name': 'BIDV',
        'q': '', 'filter_payment_mode': None, 'expected_count': scope['total'],
        'expected_links': scope['link_total'], 'scope_digest': scope['scope_digest'], 'confirmed': True})
    assert response.status_code == 409
    with factory() as session:
        assert session.scalar(select(Pattern.payment_mode)) == 'self'
        assert session.scalar(select(func.count(KnowledgeChange.id))) == 0


def test_approved_learning_invalidates_an_open_form(interactive):
    client, factory = interactive
    old = stamp(client)
    with factory.begin() as session:
        Core().learn(session, ExcelTransaction(2, 'Ma KH 001234 TT nuoc ky 8/2026', payer='MB'), '001234')
    response = client.patch('/knowledge/customers/001234', headers=old, json={'name':'Tên cũ từ form mở trước'})
    assert response.status_code == 409


def test_compact_settlement_also_locks_bank_alias_undo(interactive):
    client, factory = interactive
    assert client.post('/knowledge/bank-aliases', headers=stamp(client),
        json={'alias':'Vi Hue Test', 'channel_key':'momo', 'confirmed':True}).status_code == 201
    with factory.begin() as session:
        change_id = session.scalar(select(KnowledgeChange.id))
        Core().learn_settlement(session, ExcelTransaction(4, 'Quyet toan giao dich FT20260913123456', payer='Vi Hue Test'),
                               '001234', 'Nguyễn Văn A', 10, receipt_key='explicit-approved-receipt')
    assert client.post(f'/knowledge/changes/{change_id}/undo', headers=stamp(client)).status_code == 409


def test_malformed_origin_is_rejected_and_auth_failures_are_bounded(interactive):
    client, _ = interactive
    assert client.post('/knowledge/aliases', headers={'Origin':'http://[malformed'}, json={}).status_code == 403
    for _ in range(10):
        assert client.get('/stats', auth=('bad','bad')).status_code == 401
    assert client.get('/stats', auth=('bad','bad')).status_code == 429


def test_confirmed_review_and_import_share_receipt_through_bank_alias(interactive):
    client, factory = interactive
    assert client.post('/knowledge/bank-aliases', headers=stamp(client), json={
        'alias':'Hue Alias BIDV', 'channel_key':'bidv', 'confirmed':True}).status_code == 201
    raw = 'Ma KH 001234 TT tien nuoc ky 8/2026'
    core = Core()
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(2, raw, date='2026-09-01', amount=100, payer='BIDV', reference='ref1'), '001234')
    with factory() as session:
        receipts = session.scalar(select(func.count(KnowledgeReceipt.id)))
        observations = session.scalar(select(func.sum(Pattern.seen_count)))
    with factory.begin() as session:
        result = core.classify(session, raw, 'Hue Alias BIDV')
        core.review(session, {**result, 'raw':raw, 'payer':'Hue Alias BIDV', 'date':'2026-09-01',
                              'amount':100, 'reference':'ref1', 'entry_key':'file:1'}, True, '001234')
    with factory() as session:
        assert session.scalar(select(func.count(KnowledgeReceipt.id))) == receipts
        assert session.scalar(select(func.sum(Pattern.seen_count))) == observations


def test_roster_only_customer_is_suggested_until_own_pattern_is_confirmed(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        core.learn_settlement(session, ExcelTransaction(1,'Quyet toan FT20260913123456',payer='MOMO'),
                              '001234','Nguyễn Văn A',20)
    raw = 'TT tien nuoc Ma KH 001234'
    with factory.begin() as session:
        result = core.classify(session, raw, 'BIDV')
        assert result['customer_id'] == '001234'
        assert result['decision'] == 'manual_check' and 0 < result['score'] < .6
        assert result['evidence']['registry_only_customer']
        core.review(session, {**result,'raw':raw,'payer':'BIDV','entry_key':'own-transfer:1'}, True,'001234')
    with factory() as session:
        assert core.classify(session, raw, 'BIDV')['decision'] == 'auto_accept'


def test_transaction_group_search_is_read_only_authenticated_and_not_bulk_acceptable(interactive):
    client, factory = interactive
    result = client.post('/classify', json={'transaction':'TT tien nuoc KH 009999 Nguyen Van A','payer':'BIDV'}).json()
    row_id = result['transaction_id']
    path = f'/transactions/{row_id}/candidates'
    assert result['decision'] == 'manual_check' and result['score'] == 0
    assert client.get(path, auth=('bad','bad')).status_code == 401
    group = client.get(path).json()
    assert group['total'] == 1 and group['members'][0]['customer_id'] == '001234'
    example = group['members'][0]['examples'][0]
    assert example['content'] == 'TT tien nuoc KH 001234 Nguyen Van A'
    assert example['numeric_comparison']['ordered_comparison'][0]['query_value'] == '009999'
    assert example['numeric_comparison']['ordered_comparison'][0]['history_value'] == '001234'
    assert not example['numeric_comparison']['ordered_comparison'][0]['exact']
    assert client.get(path, params={'q':'001234'}).json()['total'] == 1
    assert client.get(path, params={'q':"%' OR 1=1;--"}).json()['total'] == 0
    assert client.get(path, params={'limit':100000}).status_code == 422
    assert client.get('/transactions/999999999/candidates').status_code == 404
    assert client.post('/feedback/batch', json={'transaction_ids':[row_id]}).json()['confirmed'] == []
    with factory() as session:
        assert session.scalar(select(func.count(KnowledgeChange.id))) == 0
        assert session.scalar(select(func.count(Pattern.id))) == 1
