import json
import csv
import time

from fastapi.testclient import TestClient

from app.benchmark import apply_registry_reporting_policy, metrics, run_benchmark
from app.core import Core
from app.db import Metadata
from app.excel import ExcelTransaction
from app.main import create_app
from conftest import workbook


def test_api_roundtrip_import_batch_review_and_export(db, tmp_path):
    engine, factory = db
    core = Core()
    application = create_app(engine, core, tmp_path/'runtime')
    try:
        with TestClient(application) as client:
            assert client.get('/').status_code == 200
            assert client.get('/health').json()['status'] == 'ok'
            assert client.post('/classify',json={'transaction':'   '}).status_code == 422
            file = workbook(tmp_path/'confirmed.xlsx',[(4,{'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
                (5,{'A':46204,'C':'001545','D':'Trần Thị Em','E':100000,'G':'TT KH 001545 tien nuoc','H':'BIDV'})])
            with file.open('rb') as f:
                assert client.post('/knowledge/import',files={'file':('fn.xlsx',f)}).status_code == 422
            with file.open('rb') as f:
                response = client.post('/knowledge/import',files={'file':('fn.xlsx',f)},data={'confirmed':'true'})
            assert response.status_code == 202
            wait_job(client,response.json()['job_id'])
            result=client.post('/classify',json={'transaction':'TT KH 001545 tien nuoc'}).json()
            assert result['decision']=='auto_accept'
            assert client.get('/stats').json()['patterns']==1
            feedback=client.post('/feedback',json={'transaction_id':result['transaction_id'],'accepted':True})
            assert feedback.json()['status']=='confirmed'
            assert feedback.json()['learned'] is True
            assert client.post('/knowledge/update',json={}).json()['automatic_on_confirmation'] is True
            assert client.post('/knowledge/update',json={}).json()['updated']==0
            raw=workbook(tmp_path/'raw.xlsx',[(12,{'B':'Số tham chiếu','I':'Mô tả'}),
                (14,{'B':'ref1','C':'02/07/2026','E':100000,'D':0,'I':'TT KH 001545 tien nuoc'}),
                (15,{'B':'ref2','C':'02/07/2026','E':0,'D':100000,'I':'TT KH 001545 tien nuoc'})])
            with raw.open('rb') as f:
                response=client.post('/batch/classify',files={'file':('raw.xlsx',f)},data={'batch_size':1})
            job_id=response.json()['job_id']
            job=wait_job(client,job_id)
            assert job['progress']==2
            rows=client.get('/transactions',params={'job_id':job_id}).json()['items']
            assert {r['decision'] for r in rows}=={'auto_accept','reject'}
            assert client.get('/export/'+job_id).status_code==200
            assert 'row_index' in client.get('/export/'+job_id).text
    finally:
        application.state.executor.shutdown(wait=True)


def wait_job(client,job_id):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        job=client.get('/jobs/'+job_id).json()
        if job['status'] in ('completed','failed'):
            assert job['status']=='completed',job
            return job
        time.sleep(.02)
    raise AssertionError('Job did not complete')


def test_benchmark_does_not_learn_from_holdout_and_keeps_negative_examples(tmp_path):
    raw=workbook(tmp_path/'raw.xlsx',[(12,{'B':'Số tham chiếu','I':'Mô tả'}),
        (14,{'B':'r1','C':'01/07/2026','E':10,'D':0,'I':'TT KH 001545 tien nuoc ky 6/2026'}),
        (15,{'B':'r2','C':'25/07/2026','E':10,'D':0,'I':'TT KH 001545 tien nuoc ky 7/2026'}),
        (16,{'B':'r3','C':'25/07/2026','E':10,'D':0,'I':'TT KH 998812 tien nuoc ky 7/2026'}),
        (17,{'B':'r4','C':'25/07/2026','E':0,'D':10,'I':'Debit fee'})])
    fn=workbook(tmp_path/'fn.xlsx',[(4,{'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
        (5,{'A':46204,'C':'001545','D':'Em','E':10,'G':'TT KH 001545 tien nuoc ky 6/2026'}),
        (6,{'A':46228,'C':'001545','D':'Em','E':10,'G':'TT KH 001545 tien nuoc ky 7/2026'}),
        (7,{'A':46228,'C':'998812','D':'Other','E':10,'G':'TT KH 998812 tien nuoc ky 7/2026'})])
    report=run_benchmark(raw,fn,tmp_path/'report',batch_size=1)
    assert report['counts']['training_customers']==1
    assert report['counts']['unseen_customer_positives']==1
    assert report['metrics']['classification']['precision']==1
    assert report['metrics']['classification']['recall']==.5
    assert report['metrics']['auto_accept']['false_positive']==0
    assert (tmp_path/'report/predictions.csv').exists()


def test_empty_metric_is_undefined_not_inflated():
    assert metrics(0,0,1)['precision'] is None
    assert metrics(0,0,1)['recall']==0


def test_health_reports_existing_knowledge_version_conflict(db, tmp_path):
    engine, factory = db
    with factory.begin() as session:
        session.add(Metadata(key='feature_extractor', value='rules-v2'))
    application = create_app(engine, Core(), tmp_path/'runtime')
    try:
        with TestClient(application) as client:
            response = client.get('/health')
            assert response.status_code == 409
            assert response.json()['status'] == 'configuration_required'
            assert 'maintenance' in response.json()['detail']
    finally:
        application.state.executor.shutdown(wait=True)


def test_cross_month_learns_all_channels_skips_ko_and_reports_unknown_zero(tmp_path):
    train=workbook(tmp_path/'july.xlsx',[(4,{'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
        (5,{'A':46204,'C':'001545','D':'Em','E':10,'G':'TT KH 001545 tien nuoc','H':'BIDV'}),
        (6,{'A':46204,'C':'887766','D':'Wallet customer','E':10,'G':'Thanh toan ma KH 887766','H':'VNPAY'}),
        (7,{'A':46204,'C':'ko','D':'Skip','E':10,'G':'Unused transfer','H':'BIDV'})])
    raw=workbook(tmp_path/'august.xlsx',[(12,{'B':'Số tham chiếu','I':'Mô tả'}),
        (14,{'B':'r1','C':'01/08/2026','E':10,'D':0,'I':'TT KH 1545 tien nuoc'}),
        (15,{'B':'r2','C':'01/08/2026','E':10,'D':0,'I':'TT KH 999999 tien nuoc'}),
        (16,{'B':'r3','C':'01/08/2026','E':10,'D':0,'I':'Skipped transfer'})])
    truth=workbook(tmp_path/'august_fn.xlsx',[(4,{'C':'IDKH','G':'EBL'}),
        (5,{'A':46235,'C':'1545','D':'Em','E':10,'G':'TT KH 1545 tien nuoc','H':'BIDV'}),
        (6,{'A':46235,'C':'999999','D':'New','E':10,'G':'TT KH 999999 tien nuoc','H':'BIDV'}),
        (7,{'A':46235,'C':'ko','D':'Skip','E':10,'G':'Skipped transfer','H':'BIDV'})])
    report=run_benchmark(raw,truth,tmp_path/'out',training_path=train,batch_size=1)
    assert report['counts']['training_rows']==2
    assert report['training_payers']=={'BIDV':1,'VNPAY':1}
    assert report['counts']['ko_rows_skipped']==1
    assert report['metrics']['classification']['true_positive']==1
    assert report['metrics']['classification']['false_negative']==1
    evidence=[json.loads(line) for line in (tmp_path/'out/evidence.jsonl').read_text().splitlines()]
    unknown=next(row for row in evidence if row['truth_customer_id']=='999999')
    assert unknown['result']['score']==0
    assert unknown['result']['decision']=='manual_check'


def test_reporting_unknown_zero_keeps_raw_false_positive_metrics(tmp_path):
    report={'protocol':'Test','cutoff':None,'training_file':'July.xlsx','raw_file':'August.xlsx','sheet':'BIDV',
            'metrics':{'classification':metrics(0,1,1)},'counts':{},'examples':{},
            'duration_seconds':0,'process_peak_rss_mb':0,'limitations':[]}
    (tmp_path/'benchmark.json').write_text(json.dumps(report))
    original={'customer_id':'001545','customer_name':'Known candidate','score':.8,'decision':'review',
              'evidence':{'reason':'Weighted historical evidence'}}
    record={'row_index':14,'raw':'Shared payer name','truth_customer_id':'999999',
            'customer_in_training':False,'result':original}
    (tmp_path/'evidence.jsonl').write_text(json.dumps(record)+'\n')
    with (tmp_path/'predictions.csv').open('w',newline='') as out:
        writer=csv.writer(out)
        writer.writerow(['true_customer_id','customer_in_training','score','decision','predicted_customer_id','reason'])
        writer.writerow(['999999','False',.8,'review','001545','Weighted historical evidence'])
    apply_registry_reporting_policy(tmp_path)
    result=json.loads((tmp_path/'evidence.jsonl').read_text())
    assert result['result']['score']==0
    assert result['result']['decision']=='manual_check'
    assert result['model_result']['score']==.8
    updated=json.loads((tmp_path/'benchmark.json').read_text())
    assert updated['metrics']==report['metrics']
    assert updated['reporting_policy']['nonzero_model_suggestions_overridden']==1
    apply_registry_reporting_policy(tmp_path)
    assert json.loads((tmp_path/'benchmark.json').read_text())['metrics']==report['metrics']
