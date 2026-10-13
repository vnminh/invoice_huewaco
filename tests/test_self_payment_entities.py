"""Confirmed self-payments do not need an ID in their narrative to be learned."""
import pytest
from sqlalchemy import func, select

from app.core import Core
from app.db import Alias, Pattern, RecurringLink
from app.excel import ExcelTransaction
from app.knowledge import import_confirmed
from app.name_extraction import NameExtractor


def ner(*names):
    # Exercise the real extraction/masking/persistence path without downloading a
    # model; inference spans are controlled fixtures, not a model-quality score.
    extractor = NameExtractor()
    extractor.enabled = True

    def pipeline(inputs, **kwargs):
        def predict(content):
            return [{'entity_group': 'PER', 'start': content.index(name),
                     'end': content.index(name) + len(name), 'score': .98}
                    for name in names if name in content]
        return [predict(content) for content in inputs] if isinstance(inputs, list) else predict(inputs)

    extractor.pipe = pipeline
    return extractor


def test_self_payment_without_id_imports_ner_entities_and_recognizes_next_month(db, tmp_path):
    engine, factory = db
    core = Core(name_extractor=ner('Le Van Tra'))
    raw = 'Le Van Tra thanh toan tien nuoc ky 08/2026'
    path = tmp_path / 'confirmed.csv'
    path.write_text('IDKH,TENKH,NOIDUNG,SOTIEN,NGANHANG,PAYMENT_MODE\n'
                    f'001234,Khach hang da duyet,{raw},100000,BIDV,self\n', encoding='utf-8')
    assert core.normalize(raw).customer_ids == []
    summary = import_confirmed(engine, core, path)
    assert summary['counts']['learned'] == 1 and summary['row_error_count'] == 0
    with factory() as session:
        assert session.scalar(select(Pattern)).payment_mode == 'self'
        assert session.scalar(select(Alias).where(Alias.normalized_alias == 'le van tra'))
        link = session.scalar(select(RecurringLink))
        entity = link.signature['name_extraction']['entities'][0]
        assert entity['source'] == 'ner' and entity['label'] == 'PERSON'
        assert link.raw_example[entity['start']:entity['end']] == 'Le Van Tra'
        result = core.classify(session, raw.replace('08/2026', '09/2026'), 'BIDV')
        assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'


def test_manual_confirmation_without_narrative_id_learns_self_payment(db):
    _, factory = db
    core = Core(name_extractor=ner('Le Van Tra'))
    raw = 'Le Van Tra nop tien nuoc'
    with factory.begin() as session:
        result = core.classify(session, raw, 'VCB')
        record = {**result, 'raw': raw, 'payer': 'VCB', 'amount': 100000, 'entry_key': 'self-no-id'}
        accepted = core.review(session, record, True, '001234', payment_mode='self')
        assert accepted['learned'] and accepted['status'] == 'confirmed'
        assert accepted['payment_mode'] == 'self'
    with factory() as session:
        assert session.scalar(select(RecurringLink)).signature['name_extraction']['entities']


def test_multiple_ner_entities_are_kept_without_assigning_all_as_customer_aliases(db):
    _, factory = db
    core = Core(name_extractor=ner('Le Van Tra', 'Nguyen Thi An'))
    raw = 'Le Van Tra va Nguyen Thi An nop tien nuoc'
    with factory.begin() as session:
        assert core.learn(session, ExcelTransaction(1, raw, amount=100000, payment_mode='self'),
                          '001234', 'Khach hang da duyet')
    with factory() as session:
        entities = session.scalar(select(RecurringLink)).signature['name_extraction']['entities']
        assert {entity['name'] for entity in entities} == {'Le Van Tra', 'Nguyen Thi An'}
        assert not session.scalar(select(Alias.id).where(Alias.normalized_alias.in_(['le van tra', 'nguyen thi an'])))


def test_missing_ner_never_blocks_learning_and_reimport_enriches_without_duplicate(db):
    _, factory = db
    disabled = NameExtractor()
    disabled.enabled = False
    core = Core(name_extractor=disabled)
    row = ExcelTransaction(1, 'Le Van Tra nop tien nuoc', amount=100000, payment_mode='self')
    with factory.begin() as session:
        assert core.learn(session, row, '001234')
    core.name_extractor = ner('Le Van Tra')
    with factory.begin() as session:
        assert not core.learn(session, row, '001234')
    core.name_extractor = disabled
    with factory.begin() as session:
        assert not core.learn(session, row, '001234')
    with factory() as session:
        assert session.scalar(select(func.count(RecurringLink.id))) == 1
        link = session.scalar(select(RecurringLink))
        assert link.seen_count == 1
        assert link.signature['name_extraction']['entities'][0]['name'] == 'Le Van Tra'


def test_confirmed_self_label_overrides_bank_hint_for_no_id_recurring_memo(db):
    _, factory = db
    core = Core(name_extractor=ner('Le Van Tra'))
    raw = 'TTHD NUOC HUE, Le Van Tra ky 08/2026'
    assert core.normalize(raw).customer_ids == []
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, raw, amount=100000, payer='BIDV', payment_mode='self'), '001234')
    with factory() as session:
        result = core.classify(session, raw.replace('08/2026', '09/2026'), 'BIDV')
        assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'
        assert result['payment_mode'] == 'self'


def test_self_payment_without_any_extracted_entity_is_still_saved(db):
    _, factory = db
    core = Core(name_extractor=ner())
    with factory.begin() as session:
        assert core.learn(session, ExcelTransaction(1, 'Nop tien nuoc', amount=100000, payment_mode='self'), '001234')
    with factory() as session:
        assert session.scalar(select(Pattern)).payment_mode == 'self'
        assert session.scalar(select(RecurringLink)).signature['name_extraction']['entities'] == []


class PerfectSemanticVectors:
    """Worst case: embedding similarity cannot detect the changed name."""
    name = 'test-perfect-semantic'

    def encode(self, _):
        return [1.0] + [0.0] * 383

    def prepare(self, _):
        pass


def named_payment(name, *, account=True, period='07/2026'):
    return ('TKThe:107873404892 tai ICBVVNVX. ' if account else '') + name + \
        f' thanh toan tien nuoc sinh hoat hang thang cho gia dinh ky {period}'


@pytest.mark.parametrize('historical,current', [
    ('NGUYEN THI THUY', 'NGUYEN THI KIM THUY'),
    ('NGUYEN THI KIM THUY', 'NGUYEN THI THUY'),
    ('NGUYEN THI THUY', 'NGUYEN THUY THI'),
    ('NGUYEN THI THUY', 'NGUYEN THI THUY AN'),
])
@pytest.mark.parametrize('account', [True, False])
def test_different_full_names_block_no_id_matching_even_with_perfect_vectors(db, historical, current, account):
    _, factory = db
    # Longest span only, so a shorter entity cannot also be returned inside a longer name.
    extractor = ner(historical)
    core = Core(embedder=PerfectSemanticVectors(), name_extractor=extractor,
                auto_threshold=.3, review_threshold=.2)
    old = named_payment(historical, account=account)
    new = named_payment(current, account=account, period='08/2026')
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, old, payment_mode='self'), '001234', current)
    # Use current entities after a restart; historical entities come from the saved observation.
    core.name_extractor = ner(current)
    with factory() as session:
        result = core.classify(session, new)
        assert result['normalization']['customer_ids'] == []
        assert result['decision'] == 'manual_check'
        assert result['score'] < core.review_threshold
        comparison = result['evidence']['nearest_history']['name_entity_comparison']
        assert comparison == {'status': 'mismatch', 'query_names': [current], 'history_names': [historical]}
        assert result['evidence']['reason'] == 'Full name entities differ from the historical transaction'


def test_full_name_match_allows_supporting_account_evidence_with_changed_wording(db):
    _, factory = db
    old = named_payment('NGUYEN THI THUY')
    new = named_payment('Nguyễn Thị Thuỷ', period='08/2026').replace('hang thang', 'dinh ky')
    core = Core(embedder=PerfectSemanticVectors(), name_extractor=ner('NGUYEN THI THUY', 'Nguyễn Thị Thuỷ'))
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, old, payment_mode='self'), '001234')
    with factory() as session:
        result = core.classify(session, new)
        assert result['decision'] == 'auto_accept'
        assert result['evidence']['name_entity_comparison']['status'] == 'match'
        assert not result['evidence'].get('memory_lookup')


def test_missing_ner_cannot_use_fuzzy_account_match_but_exact_memory_survives(db):
    _, factory = db
    core = Core(embedder=PerfectSemanticVectors(), name_extractor=ner('NGUYEN THI THUY'))
    old = named_payment('NGUYEN THI THUY')
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, old, payment_mode='self'), '001234')
    core.name_extractor = ner()
    with factory() as session:
        wrong = core.classify(session, named_payment('NGUYEN THI KIM THUY', period='08/2026'))
        assert wrong['decision'] == 'manual_check'
        assert wrong['evidence']['nearest_history']['name_entity_comparison']['status'] == 'missing'
        same = core.classify(session, old.replace('07/2026', '08/2026'))
        assert same['decision'] == 'auto_accept' and same['evidence']['memory_lookup']


def test_explicit_customer_id_keeps_priority_over_changed_payer_name(db):
    _, factory = db
    core = Core(embedder=PerfectSemanticVectors(), name_extractor=ner('NGUYEN THI THUY', 'NGUYEN THI KIM THUY'))
    old = named_payment('NGUYEN THI THUY') + ' Ma KH 001234'
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(1, old, payment_mode='self'), '001234')
    with factory() as session:
        result = core.classify(session, old.replace('NGUYEN THI THUY', 'NGUYEN THI KIM THUY'))
        assert result['decision'] == 'auto_accept' and result['customer_id'] == '001234'


def test_human_can_confirm_both_name_variants_without_erasing_old_memory(db):
    _, factory = db
    core = Core(name_extractor=ner('NGUYEN THI THUY', 'NGUYEN THI KIM THUY'))
    variants = [named_payment(name, account=False) for name in ('NGUYEN THI THUY', 'NGUYEN THI KIM THUY')]
    with factory.begin() as session:
        for raw in variants:
            core.learn(session, ExcelTransaction(1, raw, payment_mode='self'), '001234')
    with factory() as session:
        assert session.scalar(select(func.count(RecurringLink.id))) == 2
        for raw in variants:
            assert core.classify(session, raw.replace('07/2026', '08/2026'))['decision'] == 'auto_accept'
