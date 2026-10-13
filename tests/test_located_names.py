"""Names/IDs that NER or rules miss are located by the confirmed customer's own values."""
from app.core import Core
from app.excel import ExcelTransaction
from app.name_extraction import locate_names
from test_self_payment_entities import ner

HISTORY = 'TKThe :6460205294302, tai VBAAVNVX. BIDV;5510000370;nguyen bi tra tien nuoc 324464-0200970405071509563220261I6020473'
QUERY = 'TKThe :6460205294302, tai VBAAVNVX. BIDV;5510000370;LE THI TAM chuyen tien-0200970405091320574820262DX9N065188'


def learn(core, factory, raw, cid, name, row=1):
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(row, raw, date='2026-07-15', amount=150000, payer='BIDV'), cid, name)


def test_locate_names_finds_lowercase_unaccented_confirmed_name_with_position():
    [entity] = locate_names(HISTORY, ['Nguyễn Bỉ'])
    assert entity['name'] == 'nguyen bi' and entity['source'] == 'confirmed_name'
    assert HISTORY[entity['start']:entity['end']] == 'nguyen bi'
    # Whole names only: one word or a word inside a longer token never matches.
    assert locate_names(HISTORY, ['Bỉ']) == []
    assert locate_names('nguyen bich tra tien', ['Nguyễn Bỉ']) == []


def test_history_name_missed_by_ner_is_compared_as_a_real_mismatch(db):
    _, factory = db
    core = Core(name_extractor=ner('LE THI TAM'))  # NER misses the lowercase "nguyen bi"
    learn(core, factory, HISTORY, '324464', 'Nguyễn Bỉ')
    with factory() as session:
        result = core.classify(session, QUERY, 'BIDV')
        assert result['decision'] == 'manual_check'
        history = result['evidence'].get('nearest_history', result['evidence'])
        comparison = history['name_entity_comparison']
        assert comparison['status'] == 'mismatch'
        assert comparison['history_names'] == ['nguyen bi'] and comparison['history_source'] == 'confirmed_name'


def test_same_confirmed_name_in_both_narratives_is_a_name_match(db):
    _, factory = db
    core = Core(name_extractor=ner('LE THI TAM'))
    learn(core, factory, HISTORY, '324464', 'Nguyễn Bỉ')
    with factory() as session:
        result = core.classify(session, QUERY.replace('LE THI TAM chuyen tien', 'nguyen bi tra tien nuoc'), 'BIDV')
        history = result['evidence'].get('nearest_history', result['evidence'])
        assert history['name_entity_comparison']['status'] == 'match'
        assert history['name_entity_comparison']['query_source'] == 'confirmed_name'


def test_candidate_idkh_written_as_bare_number_is_found(db):
    _, factory = db
    core = Core(name_extractor=ner('LE THI TAM'))
    # History without any IDKH in the text (payment content need not contain one).
    learn(core, factory, HISTORY.replace(' 324464', ''), '324464', 'Nguyễn Bỉ')
    with factory() as session:
        # This month the payer also wrote the customer's own 6-digit IDKH, unlabelled.
        result = core.classify(session, QUERY.replace('LE THI TAM chuyen tien', 'nguyen bi tra tien nuoc 324464'), 'BIDV')
        assert result['customer_id'] == '324464' and result['decision'] == 'auto_accept'
        assert result['evidence']['reason'] == 'Customer ID written in the narrative plus water-bill purpose'


def test_written_idkh_suppresses_recommendations_for_other_customers_of_the_same_payer(db):
    _, factory = db
    core = Core()
    old = 'REM Tfr Ac:8865034234 O@L_040001_212501_0_0_2481741669_117949_A1234567_TT tien nuoc ky 7/2026 A1234567_117949_HD:117949@@7/2026'
    new = 'REM Tfr Ac:8865034234 O@L_040001_212501_0_0_2581741669_004152_A5650185_TT tien nuoc ky 8/2026 A5650185_004152_HD:004152@@8/2026'
    learn(core, factory, old, '117949', 'Nguyễn Long')
    with factory() as session:
        result = core.classify(session, new, 'BIDV')
        assert result['decision'] == 'manual_check'
        assert result['customer_id'] is None and result['suggested_customer_id'] == '004152'
        # The payer's other meter stays visible only as a demoted, labelled hint.
        [other] = [item for item in result['recommendations'] if item['customer_id'] == '117949']
        assert other['conflicts_written_id'] and other['score'] <= .3
        assert 'Khác mã 004152 ghi trong nội dung' in other['sources']


FIRST = ['nguyen', 'tran', 'le', 'pham', 'hoang']
LAST = ['an', 'binh', 'cuong', 'dung', 'giang']


def test_shared_layout_learns_name_position_and_reads_names_ner_misses(db, tmp_path):
    from sqlalchemy import select
    from app.db import NameSlotStat
    from app.knowledge import import_confirmed
    engine, factory = db
    disabled = __import__('app.name_extraction', fromlist=['NameExtractor']).NameExtractor()
    disabled.enabled = False
    core = Core(name_extractor=disabled)
    rows = ['IDKH,TENKH,NOIDUNG,SOTIEN,NGANHANG']
    for index in range(25):
        name = f'{FIRST[index % 5]} van {LAST[index // 5]}'
        rows.append(f'{300000 + index:06d},{name.title()},TKThe :64602052{index:05d} tai VBAAVNVX. BIDV;5510000370;{name} '
                    f'tra tien nuoc-02009704050715095632202{index:05d}X,150000,BIDV')
    path = tmp_path / 'fn.csv'
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    summary = import_confirmed(engine, core, path)
    assert summary['name_slots']['name_positions'] >= 1
    with factory() as session:
        slot = session.scalar(select(NameSlotStat).order_by(NameSlotStat.hits.desc()))
        assert slot.context == 'N10 ;' and slot.hits == 25
        # Same card as customer 300000 ("nguyen van an"); another person's name at the name position.
        query = 'TKThe :6460205200000 tai VBAAVNVX. BIDV;5510000370;le thi tam chuyen tien-0200970405091320574820262DX9N065188'
        result = core.classify(session, query, 'BIDV')
        assert result['extracted_names'] == ['le thi tam']
        assert result['name_extraction']['entities'][0]['source'] == 'learned_name_position'
        assert result['decision'] == 'manual_check'
        comparison = result['evidence'].get('nearest_history', result['evidence']).get('name_entity_comparison')
        assert comparison is None or comparison['status'] == 'mismatch'
