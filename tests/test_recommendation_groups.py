"""Contextual groups assist review without becoming identity/learning evidence."""
from sqlalchemy import func, select

from app.core import Core, fingerprint
from app.db import Customer, HardNegative, Pattern
from app.excel import ExcelTransaction


def learn(core, session, cid, text, bank='BIDV', name=''):
    core.learn(session, ExcelTransaction(1, text, payer=bank), cid, name)


def test_recurring_layout_without_id_keeps_a_manual_group(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Thanh toan tien nuoc ky 07/2026', name='Nguyễn Văn A')
        learn(core, session, '005678', 'Thanh toan tien nuoc ky 07/2026', name='Trần Văn B')
        learn(core, session, '009999', 'Thanh toan tien nuoc ky 07/2026', 'VCB', 'Lê Văn C')
    with factory() as session:
        result = core.classify(session, 'Thanh toan tien nuoc ky 08/2026', 'BIDV')
        group = result['candidate_group']
        assert result['decision'] == 'manual_check'
        assert group['kind'] == 'bank_template'
        assert group['missing_customer_id'] and not group['broad']
        assert {item['customer_id'] for item in group['members']} == {'001234', '005678'}
        assert result['evidence']['confirmed_recurrence']['owner_count'] == 2
        assert session.scalar(select(func.count(Pattern.id))) == 3


def test_entity_name_narrows_shared_layout_and_confirmed_personal_text_is_remembered(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Nguyen Van An thanh toan tien nuoc ky 07/2026', name='Nguyễn Văn An')
        learn(core, session, '005678', 'Tran Van Binh thanh toan tien nuoc ky 07/2026', name='Trần Văn Bình')
    with factory() as session:
        result = core.classify(session, 'Nguyen Van An thanh toan tien nuoc ky 08/2026', 'BIDV')
        assert result['decision'] == 'auto_accept' and result['evidence']['memory_lookup']
        group = core.recommendation_group(session, 'Nguyen Van An thanh toan tien nuoc ky 08/2026', 'BIDV')
        assert group['kind'] == 'bank_template_entity'
        assert group['total'] == 1 and group['members'][0]['customer_id'] == '001234'


def test_absent_id_is_a_conditional_bank_hint_not_global_identity(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Thanh toan tien nuoc', name='Nguyễn Văn A')
        learn(core, session, '005678', 'Tien nuoc KH 005678', name='Trần Văn B')
        learn(core, session, '009999', 'Thanh toan tien nuoc', 'VCB', 'Lê Văn C')
    with factory() as session:
        group = core.recommendation_group(session, 'Noi dung moi chua tung gap', 'BIDV')
        assert group['kind'] == 'bank' and group['broad']
        assert group['missing_id_profile_used']
        assert [item['customer_id'] for item in group['members']] == ['001234']
        assert core.recommendation_group(session, 'Noi dung moi', 'unknown')['total'] == 0


def test_group_is_paginated_searchable_deduplicated_and_respects_rejection(db):
    _, factory = db
    core = Core()
    raw = 'Thanh toan tien nuoc ky 08/2026'
    with factory.begin() as session:
        for index in range(25):
            learn(core, session, f'{index + 100000}', 'Thanh toan tien nuoc ky 07/2026', name=f'Người dùng {index}')
        session.add(HardNegative(transaction_fingerprint=fingerprint(core.normalize(raw).normalized),
                                 customer_id='100000', count=1))
    with factory() as session:
        first = core.recommendation_group(session, raw, 'BIDV', limit=10)
        second = core.recommendation_group(session, raw, 'BIDV', limit=10, offset=10)
        assert first['total'] == second['total'] == 24
        assert not ({item['customer_id'] for item in first['members']} & {item['customer_id'] for item in second['members']})
        assert all(item['customer_id'] != '100000' for item in first['members'] + second['members'])
        assert core.recommendation_group(session, raw, 'BIDV', q='100024')['total'] == 1
        assert core.recommendation_group(session, raw, 'BIDV', q="%' OR 1=1;--")['total'] == 0


def test_unknown_explicit_id_stays_zero_with_a_contextual_group(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Thanh toan tien nuoc KH 001234', name='Nguyễn Văn A')
    with factory() as session:
        result = core.classify(session, 'Thanh toan tien nuoc KH 009999', 'BIDV')
        assert result['decision'] == 'manual_check' and result['score'] == 0
        assert result['suggested_customer_id'] == '009999'
        assert result['candidate_group']['total'] == 1
        assert session.get(Customer, '009999') is None


def test_compact_roster_is_available_as_group_not_current_allocation(db):
    _, factory = db
    core = Core()
    raw = 'Quyet toan giao dich FT20260913123456'
    with factory.begin() as session:
        for cid in ('001234', '005678'):
            core.learn_settlement(session, ExcelTransaction(1, raw, payer='MOMO'), cid, '', 20)
    with factory() as session:
        group = core.recommendation_group(session, raw, 'MOMO')
        assert group['total'] == 2
        assert all(item['collection_count'] == 1 and item['pattern_count'] == 0 for item in group['members'])
        result = core.classify(session, raw, 'MOMO')
        assert result['decision'] == 'manual_check'
        assert not result['evidence'].get('allocation_customer_ids')


def test_context_does_not_discard_individual_evidence_from_another_layout_or_bank(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Thanh toan tien nuoc', name='Nguyễn Văn A')
        learn(core, session, '005678', 'Tien nuoc Ma KH 005678', 'VCB', 'Trần Văn B')
    suggestion = [{'customer_id':'005678', 'sources':['Tài khoản từng thanh toán cho khách hàng']}]
    with factory() as session:
        group = core.recommendation_group(session, 'Thanh toan tien nuoc', 'BIDV', suggestions=suggestion)
        assert {item['customer_id'] for item in group['members']} == {'001234', '005678'}
        assert group['members'][0]['customer_id'] == '005678'
        assert not group['members'][0]['group_only']
        assert group['members'][0]['sources'] == suggestion[0]['sources']
        unknown = core.recommendation_group(session, 'Thanh toan tien nuoc', 'unknown', suggestions=suggestion)
        assert unknown['kind'] == 'individual' and unknown['total'] == 1
