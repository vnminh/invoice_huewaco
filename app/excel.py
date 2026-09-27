"""Disk-backed XLSX streaming, including shared strings (openpyxl caches these)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
import base64
import binascii
import csv
import math
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


@dataclass
class ExcelTransaction:
    row_index: int
    raw: str
    reference: str = ''
    date: str = ''
    amount: float = 0
    debit: float = 0
    customer_id: str = ''
    customer_name: str = ''
    payer: str = 'BIDV'
    source: str = ''
    label_status: str = 'unlabeled'

    def dict(self):
        return asdict(self)


def money(value):
    try:
        return float(str(value or 0).replace(',', '').replace(' ', ''))
    except ValueError:
        return 0.0


def date_text(value):
    if isinstance(value, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=value)).isoformat()
    text = str(value or '')
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            pass
    return text


def label_status(customer_id, name):
    code = str(customer_id).strip().lower()
    if code == 'ko' or 'ko phải tiền nước' in str(name).lower():
        return 'skipped'
    if code in ('ht', 'pd', 'th'):
        return 'unresolved'
    return 'confirmed'


class ConfirmedCsv:
    """Portable confirmed examples, including CSV exported by the review screen."""
    def __init__(self, path, check_cancel=None):
        self.path = Path(path)
        self.check_cancel = check_cancel or (lambda: None)

    def __enter__(self):
        self.source = self.path.open(encoding='utf-8-sig', newline='')
        self.reader = csv.DictReader(self.source)
        if not {'IDKH', 'NOIDUNG'}.issubset(self.reader.fieldnames or []):
            self.source.close()
            raise ValueError('Learning CSV requires IDKH and NOIDUNG columns')
        return self

    def __exit__(self, *_):
        self.source.close()

    def confirmed_sheets(self):
        return ['CSV']

    def batches(self, sheet=None, batch_size=1000, **_):
        batch = []
        for record in self.reader:
            self.check_cancel()
            customer_id = (record.get('IDKH') or '').strip()
            status = label_status(customer_id, record.get('TENKH'))
            if status in ('skipped', 'unresolved'):
                # Ignore these labels before parsing customer data or decoding content.
                batch.append(ExcelTransaction(self.reader.line_num, '', customer_id=customer_id,
                    source=self.path.name, label_status=status))
                if len(batch) == batch_size:
                    yield batch
                    batch = []
                continue
            raw = record.get('NOIDUNG') or ''
            if record.get('NOIDUNG_GOC_B64'):
                try:
                    original = base64.b64decode(record['NOIDUNG_GOC_B64'], validate=True).decode('utf-8')
                except (binascii.Error, UnicodeDecodeError) as error:
                    raise ValueError('Invalid original text in learning CSV') from error
                protected = "'" + original if original.lstrip().startswith(('=', '+', '-', '@')) else original
                # Preserve exported text exactly, but honor explicit edits to the visible column.
                if raw in (original, protected):
                    raw = original
            if not raw.strip() or len(raw) > 32767:
                raise ValueError('Learning CSV contains empty or excessively long content')
            if not customer_id or len(customer_id) > 100:
                raise ValueError('Learning CSV contains an invalid customer ID')
            try:
                amount = float((record.get('SOTIEN') or '0').strip())
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError()
            except ValueError as error:
                raise ValueError('Learning CSV contains invalid amounts') from error
            batch.append(ExcelTransaction(self.reader.line_num, raw, date=date_text(record.get('NGAY')),
                amount=amount, customer_id=customer_id, customer_name=record.get('TENKH') or '',
                payer=record.get('NGANHANG') or 'BIDV', source=self.path.name,
                label_status=status))
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


class StreamingWorkbook:
    def __init__(self, path: str | Path, check_cancel=None):
        self.path = Path(path)
        self.check_cancel = check_cancel or (lambda: None)

    def __enter__(self):
        self.zip = zipfile.ZipFile(self.path)
        if sum(i.file_size for i in self.zip.infolist()) > 2 * 1024**3:
            self.zip.close()
            raise ValueError('Workbook expands beyond the 2 GB limit')
        self.temp = tempfile.TemporaryDirectory(prefix='invoice-xlsx-')
        self.db = sqlite3.connect(str(Path(self.temp.name) / 'strings.db'))
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('CREATE TABLE strings (id INTEGER PRIMARY KEY, value TEXT)')
        try:
            if 'xl/sharedStrings.xml' in self.zip.namelist():
                with self.zip.open('xl/sharedStrings.xml') as stream:
                    events = ET.iterparse(stream, events=('start', 'end'))
                    _, root = next(events)
                    batch = []
                    index = 0
                    for event, element in events:
                        if event == 'end' and element.tag == NS + 'si':
                            self.check_cancel()
                            text = ''.join(t.text or '' for t in element.iter(NS + 't'))
                            batch.append((index, text))
                            index += 1
                            root.clear()
                            if len(batch) == 1000:
                                self.db.executemany('INSERT INTO strings VALUES (?,?)', batch)
                                batch.clear()
                    self.db.executemany('INSERT INTO strings VALUES (?,?)', batch)
                    self.db.commit()
            relationships = ET.fromstring(self.zip.read('xl/_rels/workbook.xml.rels'))
            links = {r.attrib['Id']: r.attrib['Target'] for r in relationships}
            workbook = ET.fromstring(self.zip.read('xl/workbook.xml'))
            self.sheets = {}
            for sheet in workbook.find(NS + 'sheets'):
                target = links[sheet.attrib[REL + 'id']]
                self.sheets[sheet.attrib['name']] = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.db.close()
        self.zip.close()
        self.temp.cleanup()

    def rows(self, sheet='BIDV'):
        if sheet not in self.sheets:
            raise ValueError(f'Sheet {sheet!r} not found. Available: {", ".join(self.sheets)}')
        with self.zip.open(self.sheets[sheet]) as stream:
            events = ET.iterparse(stream, events=('start', 'end'))
            _, root = next(events)
            for event, element in events:
                if event != 'end' or element.tag != NS + 'row':
                    continue
                self.check_cancel()
                values = {}
                for cell in element.findall(NS + 'c'):
                    col = re.sub(r'\d', '', cell.attrib['r'])
                    kind = cell.attrib.get('t')
                    value = cell.find(NS + 'v')
                    value = value.text if value is not None else None
                    if kind == 's' and value is not None:
                        record = self.db.execute('SELECT value FROM strings WHERE id=?', (int(value),)).fetchone()
                        value = record[0] if record else ''
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.iter(NS + 't'))
                    elif value is not None and kind not in ('str', 'e'):
                        try:
                            value = float(value)
                            if value.is_integer():
                                value = int(value)
                        except ValueError:
                            pass
                    if value is not None:
                        values[col] = value
                yield int(element.attrib['r']), values
                element.clear()
                # Clear the sheetData parent, not only rows: otherwise empty row shells grow.
                sheet_data = root.find(NS + 'sheetData')
                if sheet_data is not None:
                    sheet_data.clear()

    def transactions(self, sheet='BIDV', kind='auto'):
        layout = kind if kind != 'auto' else None
        for row_index, values in self.rows(sheet):
            if layout is None:
                if str(values.get('C', '')).strip().upper() == 'IDKH':
                    layout = 'confirmed'
                    continue
                if str(values.get('B', '')).strip().lower() == 'số tham chiếu':
                    layout = 'raw'
                    continue
                continue
            if layout == 'raw':
                raw = str(values.get('I', '')).strip()
                reference = str(values.get('B', '')).strip()
                if not raw or not reference or not values.get('C'):
                    continue
                yield ExcelTransaction(row_index, raw, reference, date_text(values.get('C')),
                                       money(values.get('E')), money(values.get('D')), source=self.path.name)
            elif layout == 'confirmed':
                raw = str(values.get('G', '')).strip()
                customer_id = str(values.get('C', '')).strip()
                name = str(values.get('D', '')).strip()
                if not raw or raw == 'LỆNH GỐC NGÂN HÀNG' or not customer_id or customer_id.startswith('#'):
                    continue
                yield ExcelTransaction(row_index, raw, date=date_text(values.get('A')),
                                       amount=money(values.get('E')), customer_id=customer_id,
                                       customer_name=name or customer_id, payer=str(values.get('H') or 'BIDV'),
                                       source=self.path.name, label_status=label_status(customer_id, name))
            else:
                raise ValueError('kind must be auto, raw, or confirmed')
        if layout is None:
            raise ValueError('Could not identify BIDV raw or confirmed headers')

    def confirmed_sheets(self):
        result = []
        for sheet in self.sheets:
            rows = self.rows(sheet)
            try:
                for row_index, values in rows:
                    if str(values.get('C', '')).strip().upper() == 'IDKH':
                        result.append(sheet)
                        break
                    if row_index >= 30:
                        break
            finally:
                rows.close()
        return result

    def batches(self, sheet='BIDV', kind='auto', batch_size=1000):
        if not 1 <= batch_size <= 5000:
            raise ValueError('batch_size must be between 1 and 5000')
        batch = []
        for transaction in self.transactions(sheet, kind):
            batch.append(transaction)
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
