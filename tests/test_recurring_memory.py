"""Incremental learning must retain old individual and shared associations."""
from sqlalchemy import delete, func, select

from app.core import Core
from app.db import Metadata, Pattern, RecurringLink
from app.excel import ExcelTransaction
from app.recurring import VERSION, VERSION_KEY, lookup, owner_query, rebuild


def learn(core, session, cid, raw, *, date='2026-07-01', name='Nguyễn Văn An', bank='BIDV'):
    return core.learn(session, ExcelTransaction(1, raw, date=date, payer=bank, amount=100000), cid, name)


def test_one_confirmation_recognizes_personal_old_text_without_id_after_month_changes_and_restart(db):
    _, factory = db
    core = Core(candidate_limit=1)
    with factory.begin() as session:
        before = core.classify(session, 'Nguyen Van An nop tien nuoc ky 07/2026', 'BIDV')
        assert before['decision'] == 'manual_check'
        core.review(session, {**before, 'raw':'Nguyen Van An nop tien nuoc ky 07/2026',
                              'payer':'BIDV', 'entry_key':'first-confirmation'}, True, '001234', 'Nguyễn Văn An')
    with factory() as session:
        result = Core(candidate_limit=1).classify(session, 'Nguyen Van An nop tien nuoc ky 08/2026', 'BIDV')
        assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'
        assert result['evidence']['memory_lookup']


def test_old_personal_binding_survives_many_other_patterns_and_alias_owners(db):
    _, factory = db
    core = Core(candidate_limit=1)
    old = 'Nguyen Van An nop tien nuoc ky 07/2026'
    with factory.begin() as session:
        learn(core, session, '001234', old)
        old_ids = set(session.scalars(select(Pattern.id)))
    with factory.begin() as session:
        for i in range(60):
            learn(core, session, str(100000+i), f'Nguyen Van An nop tien nuoc Ma KH {100000+i}', date='2026-08-01')
    with factory() as session:
        assert old_ids <= set(session.scalars(select(Pattern.id)))
        result = core.classify(session, 'Nguyen Van An nop tien nuoc ky 09/2026', 'BIDV')
        assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'


def test_shared_old_group_and_new_specific_personal_binding_coexist(db):
    _, factory = db
    core = Core()
    shared = 'Don vi An Binh nop tien nuoc ky 07/2026'
    with factory.begin() as session:
        learn(core, session, '001234', shared, name='Đơn vị An Bình')
        learn(core, session, '005678', shared, name='Đơn vị An Bình')
        old_links = set(session.scalars(select(RecurringLink.id)))
    with factory() as session:
        result = core.classify(session, shared, 'BIDV')
        assert result['decision'] == 'manual_check' and result['customer_id'] is None
        assert result['evidence']['confirmed_recurrence']['owner_count'] == 2
    with factory.begin() as session:
        learn(core, session, '001234', 'Don vi An Binh nop tien nuoc Ma KH 001234 ky 08/2026',
              date='2026-08-01', name='Đơn vị An Bình')
    with factory() as session:
        personal = core.classify(session, 'Don vi An Binh nop tien nuoc Ma KH 001234 ky 09/2026', 'BIDV')
        assert personal['decision'] == 'auto_accept' and personal['customer_id'] == '001234'
        old = core.classify(session, 'Don vi An Binh nop tien nuoc ky 09/2026', 'BIDV')
        assert old['decision'] == 'manual_check' and old['customer_id'] is None
        assert old['evidence']['confirmed_recurrence']['owner_count'] == 2
        assert old_links <= set(session.scalars(select(RecurringLink.id)))
        assert {x['customer_id'] for x in old['candidate_group']['members']} >= {'001234','005678'}


def test_new_evidence_adds_a_pattern_instead_of_overwriting_old_individual(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Nguyen Van An nop tien nuoc ky 07/2026')
        learn(core, session, '001234', 'Tien nuoc Ma KH 001234 ky 08/2026', date='2026-08-01')
    with factory() as session:
        for raw in ('Nguyen Van An nop tien nuoc ky 09/2026', 'Tien nuoc Ma KH 001234 ky 09/2026'):
            result = core.classify(session, raw, 'BIDV')
            assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'
        assert session.scalar(select(func.count(RecurringLink.id))) == 2


def test_generic_singleton_is_not_identity_and_new_owner_makes_a_shared_group(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:learn(core, session, '001234', 'Thanh toan tien nuoc ky 07/2026')
    with factory() as session:
        assert core.classify(session, 'Thanh toan tien nuoc ky 08/2026', 'BIDV')['decision'] == 'manual_check'
    with factory.begin() as session:learn(core, session, '005678', 'Thanh toan tien nuoc ky 08/2026')
    with factory() as session:
        result = core.classify(session, 'Thanh toan tien nuoc ky 09/2026', 'BIDV')
        assert result['decision'] == 'manual_check' and result['customer_id'] is None


def test_reference_changes_do_not_erase_memory_but_stable_codes_must_match(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Nguyen Van An tien nuoc CS 2 REF:1234567890 ky 07/2026')
    with factory() as session:
        result = core.classify(session, 'Nguyen Van An tien nuoc CS 2 REF:9876543210 ky 08/2026', 'BIDV')
        assert result['decision'] == 'auto_accept' and result['evidence']['memory_lookup']
        changed = core.classify(session, 'Nguyen Van An tien nuoc CS 3 REF:9876543210 ky 08/2026', 'BIDV')
        assert not changed['evidence'].get('memory_lookup') and changed['decision'] != 'auto_accept'


def test_collisions_are_counted_before_preview_limits(db):
    _, factory = db
    core = Core(candidate_limit=1)
    raw = 'Don vi An Binh nop tien nuoc ky 07/2026'
    with factory.begin() as session:
        for i in range(40):learn(core, session, str(100000+i), raw)
    with factory() as session:
        result = core.classify(session, raw, 'BIDV')
        assert result['decision'] == 'manual_check' and result['customer_id'] is None
        assert result['evidence']['confirmed_recurrence']['owner_count'] == 40
        assert core.recommendation_group(session,raw,'BIDV',q='100039')['total'] == 1


def test_backfill_is_restartable_preserves_new_observations_and_is_receipt_idempotent(db):
    engine, factory = db
    core = Core()
    with factory.begin() as session:
        learn(core, session, '001234', 'Nguyen Van An nop tien nuoc ky 07/2026')
        session.execute(delete(RecurringLink))
        session.execute(delete(Metadata).where(Metadata.key == VERSION_KEY))
    with factory.begin() as session:
        learn(core, session, '001234', 'Tien nuoc KH 001234 ky 08/2026', date='2026-08-01')
        assert session.get(Metadata, VERSION_KEY).value == 'pending'
        existing = set(session.scalars(select(RecurringLink.id)))
    rebuild(engine, batch_size=1)
    with factory() as session:
        assert existing <= set(session.scalars(select(RecurringLink.id)))
        assert session.get(Metadata, VERSION_KEY).value == VERSION
        assert core.classify(session, 'Nguyen Van An nop tien nuoc ky 09/2026','BIDV')['decision'] == 'auto_accept'
        counts = (session.scalar(select(func.count(RecurringLink.id))),session.scalar(select(func.sum(RecurringLink.seen_count))))
    rebuild(engine, batch_size=1)
    with factory.begin() as session:
        learn(core, session, '001234', 'Tien nuoc KH 001234 ky 08/2026',date='2026-08-01')
        session.flush()
        assert counts == (session.scalar(select(func.count(RecurringLink.id))),session.scalar(select(func.sum(RecurringLink.seen_count))))


def test_approved_label_with_different_written_id_is_remembered_but_not_auto_accepted(db):
    _, factory = db
    core = Core()
    raw='Tien nuoc Ma KH 001234 ky 07/2026'
    with factory.begin() as session:learn(core, session,'005678',raw)
    with factory() as session:
        result=core.classify(session,'Tien nuoc Ma KH 001234 ky 08/2026','BIDV')
        assert result['decision']=='manual_check' and result['customer_id']=='005678'
        assert result['evidence']['explicit_id_label_conflict']


def test_rejection_survives_reference_changes_without_destroying_positive_bindings(db):
    _, factory = db
    core = Core()
    raw='Nguyen Van An nop tien nuoc REF:1234567890 ky 07/2026'
    with factory.begin() as session:learn(core, session,'001234',raw)
    with factory.begin() as session:
        result=core.classify(session,raw,'BIDV')
        core.review(session,{**result,'raw':raw,'payer':'BIDV','entry_key':'rejected'},False)
    with factory() as session:
        later=core.classify(session,'Nguyen Van An nop tien nuoc REF:9876543210 ky 08/2026','BIDV')
        assert later['decision']!='auto_accept'
        assert session.scalar(select(func.count(RecurringLink.id)))==1


def test_a_corrupted_example_cannot_be_used_as_exact_memory_evidence(db):
    _, factory = db
    core = Core()
    raw='Nguyen Van An nop tien nuoc ky 07/2026'
    with factory.begin() as session:
        learn(core,session,'001234',raw)
        session.flush()
        session.scalar(select(RecurringLink)).raw_example='Tran Van Binh nop tien nuoc ky 07/2026'
    with factory() as session:
        result=core.classify(session,raw,'BIDV')
        assert result['decision']=='manual_check' and not result['evidence'].get('memory_lookup')
        assert not result['evidence']['confirmed_recurrence']['sample_valid']
