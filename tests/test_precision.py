import csv
import json

from sqlalchemy import select, func

from app.benchmark import run_benchmark
from app.core import Core
from app.db import Base, KnowledgeReceipt, Pattern, make_engine, sessions
from app.excel import ExcelTransaction
from app.knowledge import import_confirmed
from app.normalize import normalize_text
from conftest import workbook


def add(core, factory, raw, cid='001545', name='Trường tiểu học A', row=1):
    with factory.begin() as session:
        core.learn(session, ExcelTransaction(row, raw, date='2026-07-02'), cid, name)


def test_bidv_layout_and_embedded_mkh_preserve_actual_customer_id():
    bank = 'REM Tfr Ac:5510331733 O@L_040001_212001_0_0_2329539047_035980_TT tien nuoc ky 7/2026 A2482031-035980-HD:035980@@7/2026'
    assert normalize_text(bank).customer_ids == ['035980']
    assert normalize_text('IBFT le van lanhMKH247220').customer_ids == []  # glued name is not an explicit label
    assert normalize_text('TT tien nuoc MKH247220').customer_ids == ['247220']
    assert normalize_text('TK: 5510000370 TT tien nuoc MKH: 001545').payer_accounts == []
    assert normalize_text('TT nuoc ma khach hang 170342 va 008441').customer_ids == ['008441', '170342']


def test_unique_payer_history_recovers_customer_without_name_but_shared_account_abstains(db):
    _, factory = db
    core = Core()
    july = 'TKThe :107873404892 tai ICBVVNVX. DO VAN CUONG chuyen tien-020097040001'
    august = 'TKThe :107873404892, tai ICBVVNVX. DO VAN CUONG chuyen tien-020097040002'
    add(core, factory, july)
    with factory() as session:
        result = core.classify(session, august)
        assert result['customer_id'] == '001545'
        assert result['decision'] == 'auto_accept'
        assert result['evidence']['unique_payer_accounts'] == ['107873404892']
    add(core, factory, july, cid='002222', name='Other meter', row=2)
    with factory() as session:
        result = core.classify(session, august)
        assert result['score'] == 0
        assert result['customer_id'] is None


def test_same_agency_name_and_different_meter_never_auto_accepts(db):
    _, factory = db
    core = Core()
    add(core, factory, 'TKThe :123456789 tai BANK. Truong tieu hoc A thanh toan tien nuoc CS1 thang 6/2026')
    with factory() as session:
        result = core.classify(session, 'TKThe :123456789, tai BANK. Truong tieu hoc A thanh toan tien nuoc CS2 thang 7/2026')
        assert result['decision'] == 'manual_check'
        assert result['score'] == 0


def test_receiver_name_is_not_a_water_payment_purpose(db):
    _, factory = db
    core = Core()
    add(core, factory, '@@123@@_CHIHO_Truong tieu hoc A_ CTCP CAP NUOC HUE 5510000370')
    with factory() as session:
        result = core.classify(session, '@@456@@_CHIHO_Truong tieu hoc A_ CTCP CAP NUOC HUE 5510000370')
        assert result['decision'] == 'manual_check'
        assert result['score'] == 0


def test_benchmark_reopens_saved_july_and_does_not_learn_august(tmp_path):
    engine = make_engine('sqlite:///' + str(tmp_path/'saved.sqlite'))
    Base.metadata.create_all(engine)
    core = Core()
    train = workbook(tmp_path/'july.xlsx', [(4, {'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
        (5, {'A':46204,'C':'001545','D':'Em','E':10,'G':'TT KH 001545 tien nuoc','H':'BIDV'})])
    imported = import_confirmed(engine, core, train)
    assert imported['counts']['learned'] == 1
    assert import_confirmed(engine, core, train)['already_imported']
    engine.dispose()
    engine = make_engine('sqlite:///' + str(tmp_path/'saved.sqlite'))
    factory = sessions(engine)
    with factory() as session:
        before = session.scalar(select(func.count(KnowledgeReceipt.id)))
    raw = workbook(tmp_path/'august.xlsx', [(12, {'B':'Số tham chiếu','I':'Mô tả'}),
        (14, {'B':'r1','C':'01/08/2026','E':10,'D':0,'I':'TT KH 001545 tien nuoc'}),
        (15, {'B':'r2','C':'01/08/2026','E':10,'D':0,'I':'TT KH 999999 tien nuoc'})])
    truth = workbook(tmp_path/'august_fn.xlsx', [(4, {'C':'IDKH','G':'EBL'}),
        (5, {'A':46235,'C':'001545','D':'Em','E':10,'G':'TT KH 001545 tien nuoc'}),
        (6, {'A':46235,'C':'999999','D':'New','E':10,'G':'TT KH 999999 tien nuoc'})])
    report = run_benchmark(raw, truth, tmp_path/'out', knowledge_engine=engine, core=core)
    assert report['knowledge_snapshot']['last_period'] == '2026-07'
    assert report['metrics']['auto_accept']['precision'] == 1
    assert report['metrics']['seen_customers_auto_accept']['recall'] == 1
    with factory() as session:
        assert session.scalar(select(func.count(KnowledgeReceipt.id))) == before
        assert session.scalar(select(func.count(Pattern.id))) == 1
    rows = list(csv.DictReader((tmp_path/'out/predictions.csv').open(encoding='utf-8-sig')))
    assert all(row['date'].startswith('2026-08') for row in rows)
    assert rows[1]['score'] == '0.0'
    engine.dispose()
