from sqlalchemy import func, select

from app.core import Core
from app.db import Customer, HardNegative, NumericFeature, NumericSlot, Pattern
from app.excel import ExcelTransaction


def learn(core, factory, raw, cid='001545', name='Trần Thị Em', date='2026-06-01', row=1):
    with factory.begin() as session:
        return core.learn(session, ExcelTransaction(row, raw, date=date), cid, name)


def test_identity_matches_but_unseen_id_does_not(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'REM Tfr Ac:5510593029 TT tien nuoc KH:001545 ky 06/2026')
    with factory() as session:
        hit = core.classify(session, 'REM Tfr Ac:5510593029 TT tien nuoc KH:001545 ky 07/2026')
        assert hit['customer_id'] == '001545'
        assert hit['decision'] == 'auto_accept'
        unknown = core.classify(session, 'REM Tfr Ac:5510593029 TT tien nuoc KH:998812 ky 07/2026')
        assert unknown['decision'] == 'manual_check'
        assert unknown['score'] == 0
        assert unknown['customer_id'] is None


def test_prediction_does_not_learn_and_confirmation_learns_immediately(db):
    _, factory = db
    core = Core()
    with factory.begin() as session:
        raw = 'TT KH 001545 tien nuoc'
        result = {**core.classify(session, raw), 'raw': raw, 'entry_key': 'test:1', 'date': '2026-06-01'}
        assert session.scalar(select(func.count(Pattern.id))) == 0
        reviewed = core.review(session, result, True, '001545', 'Trần Thị Em')
        assert reviewed['status'] == 'confirmed' and reviewed['learned']
        core.review(session, result, True, '001545', 'Trần Thị Em')
        assert session.scalar(select(func.count(Pattern.id))) == 1
        assert session.scalar(select(Pattern.seen_count)) == 1
    with factory() as session:
        assert session.scalar(select(func.count(Pattern.id))) == 1


def test_reject_stores_idempotent_hard_negative(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'TT KH 001545 tien nuoc')
    with factory.begin() as session:
        raw = 'TT KH 001545 tien nuoc'
        result = {**core.classify(session, raw), 'raw': raw, 'entry_key': 'test:2'}
        core.review(session, result, False)
        assert session.scalar(select(func.count(HardNegative.id))) == 1
        assert core.classify(session, 'TT KH 001545 tien nuoc')['decision'] == 'reject'
        core.review(session, result, False)
        assert session.scalar(select(HardNegative.count)) == 1


def test_number_recurrence_depends_on_month_not_rows_and_variable_slots_decay(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'TT KH 001545 ref 123456', date='2026-05-01')
    learn(core, factory, 'TT KH 001545 ref 123456', date='2026-05-02', row=2)
    with factory() as session:
        features = list(session.scalars(select(NumericFeature)))
        assert all(f.exact_value_confidence == .2 for f in features)
    learn(core, factory, 'TT KH 001545 ref 654321', date='2026-06-01')
    with factory() as session:
        stable = session.scalar(select(NumericFeature).where(NumericFeature.numeric_value == '001545'))
        old = session.scalar(select(NumericFeature).where(NumericFeature.numeric_value == '123456'))
        slot = session.scalar(select(NumericSlot).where(NumericSlot.slot_index == 2))
        assert abs(stable.exact_value_confidence - .44) < 1e-8
        assert abs(old.exact_value_confidence - .14) < 1e-8
        assert abs(slot.slot_confidence - .44) < 1e-8


def test_import_is_idempotent_and_multiple_customer_ids_need_manual_allocation(db):
    _, factory = db
    core = Core()
    assert learn(core, factory, 'TT KH 001545 tien nuoc')
    assert not learn(core, factory, 'TT KH 001545 tien nuoc')
    learn(core, factory, 'TT KH 998812 tien nuoc', cid='998812', name='ABC')
    with factory() as session:
        result = core.classify(session, 'TT KH 001545 KH 998812 tien nuoc')
        assert result['decision'] == 'manual_check'
        assert result['score'] == 0
        assert result['customer_id'] is None
        assert result['evidence']['multiple_customer_ids']


def test_padding_variants_share_customer_profile_and_explain_source(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'TT KH 001545 tien nuoc', cid='001545')
    learn(core, factory, 'TT KH 1545 tien nuoc', cid='1545', date='2026-07-01')
    with factory() as session:
        assert session.scalar(select(func.count(Customer.id))) == 1
        result = core.classify(session, 'TT KH 001545 tien nuoc')
        assert result['evidence']['matched_pattern_id']
        assert result['evidence']['matched_template']
        assert result['evidence']['pattern_source']['row'] == 1
        assert result['evidence']['match_steps']
        assert 'khách hàng' in result['evidence']['reason_vi']
        assert result['evidence']['match_steps_vi']
