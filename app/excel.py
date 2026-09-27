"""Disk-backed XLSX streaming, including shared strings (openpyxl caches these)."""
from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass, field
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

from .normalize import fold

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
    sheet: str = ''
    validation_errors: list[str] = field(default_factory=list)

    def dict(self):
        return asdict(self)


def parse_money(value):
    """Parse bank amounts, never identifiers. Unknown formats remain unknown."""
    if value is None or value == '':
        return 0.0
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            token = str(value).strip().removeprefix("'").strip()
            negative = token.startswith('(') and token.endswith(')')
            if negative:
                token = token[1:-1]
            token = re.sub(r'\s+', '', token)
            token = re.sub(r'(?:VND|VNĐ|đ|₫)$', '', token, flags=re.I)
            if ',' in token and '.' in token:
                decimal, grouping = (',', '.') if token.rfind(',') > token.rfind('.') else ('.', ',')
                integer, fraction = token.rsplit(decimal, 1)
                if not fraction.isdigit() or not re.fullmatch(r'[+-]?\d{1,3}(?:' + re.escape(grouping) + r'\d{3})+', integer):
                    return None
                token = token.replace(grouping, '').replace(decimal, '.')
            elif re.fullmatch(r'[+-]?\d{1,3}(?:[.,]\d{3})+', token):
                token = token.replace(',', '').replace('.', '')
            elif ',' in token:
                token = token.replace(',', '.')
            result = float(token)
            if negative:
                result = -result
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def money(value):
    return parse_money(value) or 0.0


def date_text(value):
    if isinstance(value, (int, float)):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=value)).isoformat()
        except (OverflowError, ValueError):
            return str(value)
    text = str(value or '')
    text = text.strip().removeprefix("'").strip()
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
                '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y',
                '%d.%m.%Y', '%d-%m-%y', '%d/%m/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            pass
    return text


def valid_date(value):
    if not re.match(r'^\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def label_status(customer_id, name):
    code = str(customer_id).strip().lower()
    if code == 'ko' or 'ko phải tiền nước' in str(name).lower():
        return 'skipped'
    if code in ('ht', 'pd', 'th'):
        return 'unresolved'
    return 'confirmed'


# Aliases use folded text; columns are determined per sheet, never by bank name.
HEADER_ALIASES = {
    'raw': ('mo ta giao dich', 'mo ta', 'dien giai', 'noi dung giao dich', 'noi dung',
            'lenh goc ngan hang', 'ebl', 'noidung', 'transaction description', 'description', 'trans detail'),
    'date': ('ngay gio giao dich', 'thoi gian giao dich', 'ngay giao dich', 'ngay chuyen nh',
             'ngay chuyen', 'ngay hieu luc', 'ngay gia tri', 'ngay hach toan', 'ngay',
             'transaction date time', 'transaction date', 'value date', 'accounting date', 'date'),
    'credit': ('so tien ghi co', 'so tien co', 'phat sinh co', 'ghi co', 'so tien gui vao',
               'credit amount', 'credit', 'co'),
    'debit': ('so tien ghi no', 'so tien no', 'phat sinh no', 'ghi no', 'so tien rut ra',
              'debit amount', 'debit', 'no'),
    'amount': ('so tien giao dich', 'so tien thanh toan', 'so tien', 'sotien', 'transaction amount', 'amount'),
    'reference': ('so tham chieu', 'so giao dich', 'so gd', 'so but toan', 'but toan', 'so id',
                  'so ref', 'ma giao dich', 'ma tham chieu', 'reference no', 'reference',
                  'transaction number', 'transaction id', 'trans id'),
    'customer_id': ('idkh', 'id kh', 'ma khach hang', 'customer id'),
    'customer_name': ('ten khach hang', 'ten kh', 'tenkh', 'don vi chuyen tien', 'customer name'),
    'payer': ('hinh thuc', 'ngan hang', 'kenh thanh toan', 'bank'),
}


def detect_layout(values):
    columns, priorities = {}, {}
    for column, value in values.items():
        label = ' '.join(re.findall(r'[a-z0-9]+', fold(value)))
        for role, aliases in HEADER_ALIASES.items():
            for priority, alias in enumerate(aliases):
                if role == 'debit' and alias == 'no' and label == 'no' and 'ợ' not in str(value).lower():
                    continue  # English "No." is a row counter, not a debit column.
                # One-word labels must not match narrative cells (e.g. "co phan").
                if label == alias or (' ' in alias and label.startswith(alias + ' ')) or label in (alias + ' debit', alias + ' credit'):
                    if role not in priorities or priority < priorities[role]:
                        columns[role], priorities[role] = column, priority
                    break
    if 'customer_id' in columns and 'raw' in columns:
        # Historical FN fixtures have some blank header cells; retain that established layout.
        if columns['customer_id'] == 'C' and columns['raw'] == 'G':
            for role, column in {'date': 'A', 'amount': 'E', 'customer_name': 'D', 'payer': 'H'}.items():
                columns.setdefault(role, column)
        return {'kind': 'confirmed', 'columns': columns}
    if 'raw' in columns and 'date' in columns and ('credit' in columns or 'amount' in columns):
        return {'kind': 'raw', 'columns': columns}
    # Compatibility with the existing abbreviated BIDV headers in saved fixtures.
    if fold(values.get('B', '')).strip() == 'so tham chieu' and fold(values.get('I', '')).strip() == 'mo ta':
        return {'kind': 'raw', 'columns': {'reference': 'B', 'date': 'C', 'debit': 'D', 'credit': 'E', 'raw': 'I'}}
    return None


def headerless_layout(sheet, values):
    # The supplied Sacombank export stores no column headers. Read its established
    # cell positions, but never accept its inferred debit/credit direction automatically.
    if fold(sheet).strip() != 'sacombank':
        return None
    if not values.get('C') or not values.get('M') or not (values.get('Q') or values.get('T')):
        return None
    if not valid_date(date_text(values.get('E'))):
        return None
    return {'kind': 'raw', 'columns': {'reference': 'C', 'date': 'E', 'raw': 'M', 'debit': 'Q', 'credit': 'T'},
            'direction_unverified': True}


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
                payer=record.get('NGANHANG') or record.get('SHEET') or 'BIDV', source=self.path.name,
                reference=record.get('REFERENCE') or '', sheet=record.get('SHEET') or 'CSV', label_status=status))
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
        self.resources = ExitStack()
        try:
            self.zip = self.resources.enter_context(zipfile.ZipFile(self.path))
            if sum(i.file_size for i in self.zip.infolist()) > 2 * 1024**3:
                raise ValueError('Workbook expands beyond the 2 GB limit')
            self.temp = self.resources.enter_context(tempfile.TemporaryDirectory(prefix='invoice-xlsx-'))
            self.db = self.resources.enter_context(closing(sqlite3.connect(str(Path(self.temp.name) / 'strings.db'))))
            self.db.execute('PRAGMA cache_size=-2048')
            self.db.execute('CREATE TABLE strings (id INTEGER PRIMARY KEY, value TEXT)')
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
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        # Close streams/connections before deleting files; Windows forbids deleting open files.
        self.resources.close()

    def rows(self, sheet=None):
        if sheet is None:
            for name in self.sheets:
                yield from self.rows(name)
            return
        if sheet not in self.sheets:
            raise ValueError(f'Sheet {sheet!r} not found. Available: {", ".join(self.sheets)}')
        # Also register the stream for cleanup if iteration is stopped while suspended at yield.
        with self.resources.enter_context(self.zip.open(self.sheets[sheet])) as stream:
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
                            # Do not round long numeric identifiers through a float,
                            # or discard zeros present in the stored cell value.
                            if not (value.isdigit() and (len(value) > 15 or (len(value) > 1 and value.startswith('0')))):
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

    def sheet_layouts(self):
        if hasattr(self, '_layouts'):
            return self._layouts
        layouts = {}
        for sheet in self.sheets:
            self.check_cancel()
            source = self.rows(sheet)
            try:
                for row_index, values in source:
                    layout = detect_layout(values)
                    if layout:
                        layouts[sheet] = {**layout, 'header_row': row_index}
                        break
                    layout = headerless_layout(sheet, values)
                    if layout:
                        layouts[sheet] = {**layout, 'header_row': row_index - 1}
                        break
                    if row_index >= 100:
                        break
            finally:
                source.close()
        self._layouts = layouts
        return layouts

    def raw_sheets(self):
        return [sheet for sheet, layout in self.sheet_layouts().items() if layout['kind'] == 'raw']

    def confirmed_sheets(self):
        return [sheet for sheet, layout in self.sheet_layouts().items() if layout['kind'] == 'confirmed']

    def skipped_sheets(self, kind):
        layouts = self.sheet_layouts()
        return [{'sheet': sheet, 'reason': 'confirmed_layout' if layouts.get(sheet, {}).get('kind') == 'confirmed'
                 else 'raw_layout' if layouts.get(sheet, {}).get('kind') == 'raw' else 'unsupported_layout'}
                for sheet in self.sheets if layouts.get(sheet, {}).get('kind') != kind]

    def transactions(self, sheet=None, kind='auto'):
        if kind not in ('auto', 'raw', 'confirmed'):
            raise ValueError('kind must be auto, raw, or confirmed')
        layouts = self.sheet_layouts()
        if sheet is None:
            selected = [name for name, layout in layouts.items() if kind == 'auto' or layout['kind'] == kind]
            if not selected:
                raise ValueError('No supported transaction sheets found')
            for name in selected:
                yield from self.transactions(name, kind)
            return
        if sheet not in self.sheets:
            raise ValueError(f'Sheet {sheet!r} not found. Available: {", ".join(self.sheets)}')
        layout = layouts.get(sheet)
        if not layout:
            raise ValueError(f'Could not identify transaction headers in sheet {sheet!r}')
        if kind != 'auto' and layout['kind'] != kind:
            raise ValueError('Batch classification requires raw bank layout; upload the unfiltered file')
        columns = layout['columns']
        source = f'{self.path.name} [{sheet}]'
        for row_index, values in self.rows(sheet):
            if row_index <= layout['header_row']:
                continue
            if detect_layout(values):
                continue  # Repeated bilingual headers/page headings are not transactions.
            get = lambda role: values.get(columns.get(role, ''), '')
            raw = str(get('raw'))
            if not raw.strip():
                continue
            customer_id = str(get('customer_id')).strip()
            if layout['kind'] == 'confirmed':
                name = str(get('customer_name')).strip()
                if not customer_id or customer_id.startswith('#'):
                    continue
                status = label_status(customer_id, name)
                if status in ('skipped', 'unresolved'):
                    yield ExcelTransaction(row_index, '', customer_id=customer_id, source=source,
                                           sheet=sheet, label_status=status)
                    continue
                amount = parse_money(get('amount'))
                if amount is None or amount < 0:
                    raise ValueError(f'Invalid amount in sheet {sheet!r}, row {row_index}')
                yield ExcelTransaction(row_index, raw, date=date_text(get('date')),
                    amount=amount, customer_id=customer_id, customer_name=name or customer_id,
                    payer=str(get('payer') or sheet).strip(), source=source, sheet=sheet, label_status=status)
                continue
            reference = str(get('reference')).strip()
            # Totals/closing balances without a date are not transaction rows.
            if not get('date') and not reference:
                continue
            errors = []
            if layout.get('direction_unverified'):
                errors.append('Sheet thiếu tiêu đề ghi nợ/ghi có; kiểm tra cột Q/T trong tệp gốc trước khi xác nhận.')
            date = date_text(get('date'))
            if not valid_date(date):
                errors.append('Ngày giao dịch chưa hợp lệ; cần kiểm tra thủ công.')
            if 'credit' in columns:
                credit = parse_money(get('credit'))
                debit = parse_money(get('debit'))
                if credit is None or debit is None or credit < 0 or debit < 0:
                    errors.append('Số tiền ghi có/ghi nợ chưa hợp lệ; cần kiểm tra thủ công.')
                amount, debit = credit if credit is not None else 0, debit if debit is not None else 0
            else:
                original = get('amount')
                signed = parse_money(original)
                if signed is None:
                    amount, debit = 0, 0
                    errors.append('Số tiền giao dịch chưa hợp lệ; cần kiểm tra thủ công.')
                else:
                    amount, debit = max(signed, 0), max(-signed, 0)
                    token = str(original).strip().removeprefix("'").strip()
                    if signed > 0 and not token.startswith('+'):
                        errors.append('Chưa xác định được chiều ghi có/ghi nợ; cần kiểm tra thủ công.')
            yield ExcelTransaction(row_index, raw, reference, date, amount, debit,
                payer=sheet, source=source, sheet=sheet, validation_errors=errors)

    def batches(self, sheet=None, kind='auto', batch_size=1000):
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
