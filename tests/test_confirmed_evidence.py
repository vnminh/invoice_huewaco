"""Critical v7 regressions; hash vectors only. Not run as part of this update."""
import pytest
from sqlalchemy import func, select

from app.core import Core
from app.db import Alias, Customer, IdSlotStat, KnowledgeReceipt, Pattern
from app.excel import ExcelTransaction
from app.identifiers import confirmed_customer_ids
from app.normalize import normalize_text
from app.payment_period import payment_period_fields


@pytest.mark.parametrize('raw', [
    'TTHD Tien nuoc Ma KH-279021 Ma HD-Ky 8/2026',
    'TT tiền nước IDKH:279021 kỳ 082026',
    'TT tien nuoc KH279021 T8N2026',
    'MA_GD:629957609|279021,Nuoc8/2026#Nguyen Van A#',
])
def test_customer_and_period_fields_stay_separate(raw):
    norm = normalize_text(raw)
    assert norm.customer_ids == ['279021']
    assert payment_period_fields(raw)['payment_periods'] == ['08/2026']
    assert not any(number['value'] == '082026' for number in norm.numbers)


def test_no_slicing_long_numbers_or_borrowing_transfer_year():
    assert normalize_text('Ma KH:001234005678 tien nuoc').customer_ids == []
    with pytest.raises(ValueError):
        confirmed_customer_ids('001234005678')
    assert confirmed_customer_ids('1234,005678') == ['001234', '005678']
    fields = payment_period_fields('TT tiền nước tháng 8; chuyển ngày 13/09/2026 10:30')
    assert fields['payment_periods'] == []
    assert fields['payment_period_evidence']['partial_months'] == [8]


class PayerName:
    def extract(self, raw):
        return {'status': 'ok', 'entities': [
            {'name': 'Le Van Tra', 'label': 'PERSON', 'source': 'ner', 'score': .99}]}


def test_confirmed_multi_customer_without_codes_does_not_copy_payer_alias(db):
    _, factory = db
    core = Core(name_extractor=PayerName())
    row = ExcelTransaction(1, 'Le Van Tra thanh toan tien nuoc', amount=100, payer='VCB')
    with factory.begin() as session:
        assert core.learn(session, row, '001234,005678')
        assert not core.learn(session, row, '001234,005678')
        assert set(session.scalars(select(Customer.id))) == {'001234', '005678'}
        assert session.scalar(select(func.count(Pattern.id))) == 2
        assert session.scalar(select(func.count(KnowledgeReceipt.id))) == 2
        assert session.scalar(select(func.count(Alias.id)).where(Alias.normalized_alias == 'le van tra')) == 0


def test_aggregate_customer_positions_are_scoped_to_bank(db):
    _, factory = db
    core = Core()
    raw = 'TT tien nuoc 001234'
    context = normalize_text(raw).numbers[0]['context']
    with factory.begin() as session:
        session.add(IdSlotStat(channel_key='vcb', context=context, hits=100, total=100))
        # Cache versions are maintained by a rebuild in production; use an explicit version here.
        from app.db import Metadata
        session.add(Metadata(key='id_slots_version', value='fixture'))
    with factory() as session:
        vcb, bidv = core.normalize(raw), core.normalize(raw)
        assert core.apply_learned_slots(session, vcb, 'Vietcombank') == ['001234']
        assert core.apply_learned_slots(session, bidv, 'BIDV') == []
        assert bidv.customer_ids == []


def test_mentioned_codes_do_not_override_approved_recipients(db):
    _, factory = db
    core = Core()
    raw = 'TT tien nuoc Ma KH 001234,005678 ky 8/2026'
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, raw), '009999', 'Khach da duyet')
    with factory() as session:
        result = core.classify(session, raw)
        assert result['decision'] == 'manual_check'
        assert result['suggested_customer_id'] == '009999'
        assert result['evidence']['allocation_source'] == 'confirmed_text'
        assert result['evidence']['mentioned_unknown_customer_ids'] == ['001234', '005678']
        assert result['evidence']['nearest_history']['matched_pattern'] == raw
