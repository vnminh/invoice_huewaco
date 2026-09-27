import pytest

from app.excel import StreamingWorkbook
from app.normalize import normalize_text
from conftest import workbook


@pytest.mark.parametrize('date', ['07/2026','07-2026','THANG 072026','T07','THANG 07','THANG 7 NAM 2026','T7/26'])
def test_contextual_dates_and_order(date):
    normalized = normalize_text('CTY ÁBC KH 001545 ' + date + ' HD 998812')
    assert normalized.raw.endswith('HD 998812')
    assert normalized.template == 'cty abc kh <NUM_1> hd <NUM_2>'
    assert [n['value'] for n in normalized.numbers] == ['001545','998812']
    assert [s['type'] for s in normalized.segments] == ['text','number','date','text','number']


def test_compact_number_without_context_is_preserved():
    n = normalize_text('KH 072026 so tien 500000 VND Ac:00123456789')
    assert n.numbers[0]['value'] == '072026'
    assert n.numbers[0]['numeric_type'] == 'CUSTOMER_ID'
    assert n.numbers[1]['numeric_type'] == 'AMOUNT'
    assert n.numbers[2]['numeric_type'] == 'ACCOUNT_ID'


def test_streaming_preserves_original_rows_and_leading_zero_ids(tmp_path):
    path = workbook(tmp_path/'confirmed.xlsx', [(4,{'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
        (7,{'A':46204,'C':'001545','D':'Trần Thị Em','E':100000,'G':'TT KH 001545','H':'BIDV'}),
        (20,{'A':46205,'C':'079458','D':'ABC','E':200000,'G':'TT KH 079458','H':'BIDV'})])
    with StreamingWorkbook(path) as wb:
        batches = list(wb.batches(batch_size=1))
    assert len(batches)==2
    assert batches[0][0].row_index == 7
    assert batches[0][0].customer_id == '001545'
    assert batches[0][0].date.startswith('2026-07-01')
    with StreamingWorkbook(path) as wb:
        with pytest.raises(ValueError, match='not found'):
            list(wb.rows('Missing'))
