"""Compact collection customers can reuse verified bank layouts across recipients."""
import pytest
from sqlalchemy import func, select

from app.core import Core, fingerprint
from app.db import HardNegative, Pattern
from app.excel import ExcelTransaction
from app.recurring import negative_key, signature


def payment(cid, month=8):
    return f'TTHD Tien nuoc Ma KH-{cid} Ma HD-Ky {month}/2026'


def seed(factory, *, example_id='123456', example_raw=None):
    core = Core()
    with factory.begin() as session:
        core.learn_settlement(session, ExcelTransaction(1, 'Quyet toan FT20260713123456', payer='MOMO'),
                              '001234', 'Nguyễn Văn A', 20)
        core.learn(session, ExcelTransaction(2, example_raw or payment('123456', 7), payer='BIDV'),
                   example_id, 'Khách khác')
    return core


def test_known_collection_customer_reuses_other_customers_bank_template(db):
    _, factory = db
    core = seed(factory)
    with factory() as session:
        result = core.classify(session, payment('001234'), 'BIDV')
        assert result['decision'] == 'auto_accept'
        assert result['customer_id'] == '001234'
        assert result['payment_mode'] == 'proxy'
        assert result['evidence']['shared_collection_match']
        assert result['evidence']['shared_example_customer_id'] == '123456'
        assert result['evidence']['shared_template_id']
        assert result['evidence']['shared_template']
        assert 'matched_pattern_id' not in result['evidence']  # Do not present another person's example as theirs.
        assert session.scalar(select(func.count(Pattern.id)).where(Pattern.customer_id == '001234')) == 0


@pytest.mark.parametrize('raw,bank', [
    (payment('001234'), 'VCB'),
    ('TT tien nuoc Ma KH 001234 noi dung moi', 'BIDV'),
    (payment('999999'), 'BIDV'),
    (payment('001234') + ' Ma KH 123456', 'BIDV'),
    ('Quyet toan FT20260813123456', 'MOMO'),
])
def test_shared_layout_does_not_bypass_missing_or_ambiguous_identity(db, raw, bank):
    _, factory = db
    core = seed(factory)
    with factory() as session:
        result = core.classify(session, raw, bank)
        assert result['decision'] == 'manual_check'
        assert not result['evidence'].get('shared_collection_match')


@pytest.mark.parametrize('recurring', [False, True])
def test_shared_layout_respects_human_rejection(db, recurring):
    _, factory = db
    core = seed(factory)
    raw = payment('001234')
    norm = core.normalize(raw)
    key = negative_key(signature(norm, 'bidv')[0]) if recurring else fingerprint(norm.normalized)
    with factory.begin() as session:
        session.add(HardNegative(customer_id='001234', transaction_fingerprint=key))
    with factory() as session:
        result = core.classify(session, raw, 'BIDV')
        assert result['decision'] == 'manual_check'
        assert result['evidence']['reason'] == 'Human feedback previously rejected this customer for this transaction'


def test_wrongly_labelled_example_does_not_verify_shared_id_field(db):
    _, factory = db
    core = seed(factory, example_id='654321')
    with factory() as session:
        result = core.classify(session, payment('001234'), 'BIDV')
        assert result['decision'] == 'manual_check'
        assert not result['evidence'].get('shared_collection_match')


def test_settlement_layout_cannot_be_used_as_single_recipient_evidence(db):
    _, factory = db
    core = seed(factory)
    with factory.begin() as session:
        core.learn_settlement(session, ExcelTransaction(3, payment('123456', 7), payer='BIDV'),
                              '123456', 'Khách khác', 20)
    with factory() as session:
        result = core.classify(session, payment('001234'), 'BIDV')
        assert result['decision'] == 'manual_check'
        assert not result['evidence'].get('shared_collection_match')
