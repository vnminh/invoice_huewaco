import pytest

from app.core import Core
from app.embedding import Embedder
from app.normalize import normalize_text
from app.numeric_match import compare_ordered_numbers
from test_core import learn


@pytest.mark.parametrize('label', ['HD:', 'HD ', 'HD so ', 'hợp đồng:', 'Hợp đồng số '])
def test_hd_is_always_contract_with_full_code(label):
    n = normalize_text('TT tiền nước ' + label + 'AB-001545/02 tháng 07/2026')
    assert n.contract_ids == ['ab-001545/02']
    assert n.numbers[0]['raw_value'] == 'AB-001545/02'
    assert n.numbers[0]['slot'] == 1
    assert not n.customer_ids


def test_explicit_invoice_differs_from_contract_and_bare_id_is_not_date():
    n = normalize_text('HD:072026 hóa đơn số 882233 TKThe:001234 IDKH:001545 tháng 07/2026')
    assert [(v['value'], v['numeric_type'], v['slot']) for v in n.numbers] == [
        ('072026', 'CONTRACT_ID', 1), ('882233', 'INVOICE_ID', 2),
        ('001234', 'CARD_ID', 3), ('001545', 'CUSTOMER_ID', 4)]
    assert n.payer_cards == ['001234']


def test_codes_are_atomic_and_semantic_input_retains_vietnamese():
    n = normalize_text('Nguyễn Thị Hồng TT tiền nước mã ABC001X2 và 12AB003 tháng 07/2026')
    assert n.mixed_codes == ['12ab003', 'abc001x2']
    assert [v['raw_value'] for v in n.numbers] == ['ABC001X2', '12AB003']
    assert 'abc<NUM_1>x<NUM_1>' in n.template
    assert 'Nguyễn Thị Hồng' in n.semantic_text
    assert '001' not in n.semantic_text


def test_glued_export_reference_and_branch_are_not_customer_codes():
    n = normalize_text('2670110310424137Thanh toán Trường Phú Đa 2CHUYEN tiền nước ABC001X2')
    assert [v['numeric_type'] for v in n.numbers] == ['BANK_REFERENCE', 'LOCATION_NUMBER', 'MIXED_CODE']
    assert 'thanh' in n.normalized and 'chuyen' in n.normalized
    assert normalize_text('TKThe:12AB003').payer_cards == ['12ab003']


class AllSimilar(Embedder):
    """Even perfect semantic similarity must never override numeric conflicts."""
    def __init__(self):
        self.name = 'test-semantic'

    def encode(self, value):
        return [1.0] + [0.0] * 383

    def prepare(self, values):
        pass


@pytest.mark.parametrize('changed', [
    'TKThe:999000 TT tiền nước KH:001545 HD:AB-001545/02 mã ABC002X2',
    'TKThe:999000 TT tiền nước KH:001545 HD:AB-001545/02 mã XYZ001X2',
])
def test_semantic_similarity_cannot_override_changed_contract_or_code(db, changed):
    _, factory = db
    core = Core(embedder=AllSimilar())
    learn(core, factory, 'TKThe:999000 TT tiền nước KH:001545 HD:AB-001545/02 mã ABC001X2')
    with factory() as session:
        result = core.classify(session, changed)
        assert result['score'] == 0
        assert result['decision'] == 'manual_check'


@pytest.mark.parametrize('changed', [
    'TKThe:999000 TT tiền nước KH:001545 HD:AB-001546/02 mã ABC001X2',
    'TKThe:999000 TT tiền nước KH:001545 HD:AB-1545/02 mã ABC001X2',
])
def test_known_explicit_customer_id_takes_priority_over_changed_contract(db, changed):
    # One HD may cover several IDKH; a known explicit IDKH is not negated by a new HD.
    _, factory = db
    core = Core(embedder=AllSimilar())
    learn(core, factory, 'TKThe:999000 TT tiền nước KH:001545 HD:AB-001545/02 mã ABC001X2')
    with factory() as session:
        result = core.classify(session, changed)
        assert result['customer_id'] == '001545'
        assert result['evidence']['customer_id_priority_over_contract']


def test_swapped_roles_or_positions_are_not_exact_ordered_evidence():
    old = normalize_text('TKThe:998812 HD:001545 TT tiền nước')
    query = normalize_text('TKThe:001545 HD:998812 TT tiền nước')
    result = compare_ordered_numbers(query, old.segments, old.template)
    assert not result['aligned_contracts']
    assert result['contract_conflict']
    assert all(not row['exact'] for row in result['ordered_comparison'])
    moved = normalize_text('HD:001545 TKThe:998812 TT tiền nước')
    assert not compare_ordered_numbers(moved, old.segments, old.template)['aligned_contracts']


def test_confirmed_bare_customer_number_requires_same_position(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'Thu tiền nước 001545 ghi chú 777777')
    with factory() as session:
        assert core.classify(session, 'Thu tiền nước 001545 ghi chú 777777')['decision'] == 'auto_accept'
        moved = core.classify(session, 'Thu tiền nước 777777 ghi chú 001545')
        assert moved['decision'] == 'manual_check'
        assert moved['score'] == 0


def test_card_number_is_not_customer_id_even_if_equal_to_another_customer(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'TKThe:998812 Trần Thị Em TT tiền nước tháng 07/2026')
    learn(core, factory, 'KH:998812 TT tiền nước', cid='998812', name='Khách khác', row=2)
    with factory() as session:
        result = core.classify(session, 'TKThe:998812 Trần Thị Em TT tiền nước tháng 08/2026')
        assert result['normalization']['customer_ids'] == []
        # The card equals another customer's ID but is never read as that customer's ID.
        assert result['customer_id'] != '998812'
        assert result['evidence'].get('nearest_history', result['evidence']).get('customer_id', '001545') != '998812'


def test_optional_bidv_invoice_copy_does_not_reorder_identity_fields(db):
    _, factory = db
    core = Core()
    old = 'REM Tfr Ac:8858616369 O@L_040001_212501_0_0_2286885558_048946_E5534257_TT tien nuoc ky 6/2026 E5534257_048946_HD:048946@@6/2026'
    query = 'REM Tfr Ac:8858616369 O@L_040001_212001_0_0_2392047064_048946_TT tien nuoc ky 7/2026 E5534257-048946-HD:048946@@7/2026'
    learn(core, factory, old, cid='048946')
    with factory() as session:
        result = core.classify(session, query)
        assert result['decision'] == 'auto_accept'
        comparison = result['evidence']['numeric_comparison']
        assert comparison['stable_sequence_matches']
        assert comparison['aligned_contracts'] == ['048946']
        contract = comparison['ordered_comparison'][-1]
        assert contract['history_slot'] == contract['slot'] + 1
        assert not contract['absolute_slot_match']
        bad = core.classify(session, query.replace('HD:048946', 'HD:048947'))
        assert bad['score'] == 0


def test_contextual_bank_reference_and_quantity_do_not_become_identity():
    n = normalize_text('TT tiền nước KBNNTTSP_KBA260804198477 số tiêu thụ 14m3 mã ABC001X2')
    assert [x['numeric_type'] for x in n.numbers] == ['BANK_REFERENCE', 'QUANTITY', 'MIXED_CODE']
    assert normalize_text('mã KBA260804198477').numbers[0]['numeric_type'] == 'MIXED_CODE'


def test_bank_reference_format_changes_do_not_change_stable_identity_order():
    a = normalize_text('TKThe:001234 HD:AB001545 TT tiền nước ref ABC123')
    b = normalize_text('TKThe:001234 HD:AB001545 TT tiền nước ref 123AB456CD')
    result = compare_ordered_numbers(a, b.segments, b.template)
    assert result['stable_sequence_matches']
    assert result['aligned_contracts'] == ['ab001545']
    assert not result['code_conflicts']


def test_mixed_code_needs_unique_confirmed_owner_and_all_stable_values_in_order(db):
    _, factory = db
    core = Core(embedder=AllSimilar())
    learn(core, factory, 'TT tiền nước mã ABC001X2 cơ sở 9')
    with factory() as session:
        good = core.classify(session, 'TT tiền nước mã ABC001X2 cơ sở 9')
        assert good['customer_id'] == '001545'
        assert good['decision'] == 'auto_accept'
        assert good['evidence']['unique_mixed_codes'] == ['abc001x2']
        changed = core.classify(session, 'TT tiền nước mã ABC001X2 cơ sở 8')
        assert changed['score'] == 0
    learn(core, factory, 'TT tiền nước mã ABC001X2 cơ sở 9', cid='998812', name='Khách khác', row=2)
    with factory() as session:
        shared = core.classify(session, 'TT tiền nước mã ABC001X2 cơ sở 9')
        assert shared['score'] == 0
        assert shared['decision'] == 'manual_check'
