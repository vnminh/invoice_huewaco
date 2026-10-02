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
from .row_errors import ROW_DATA_ERRORS, RowErrors

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
    payment_mode: str = ''
    provider_kind: str = ''
    provider_name: str = ''

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
LAYOUT_VERSION = 'bank-layout-v2-customer-id-aliases'
HEADER_ALIASES = {
    'raw': ('mo ta giao dich', 'mo ta', 'dien giai', 'noi dung giao dich', 'noi dung',
            'lenh goc ngan hang', 'ebl', 'noidung', 'transaction description', 'description', 'trans detail',
            'transaction details', 'narration', 'remarks', 'details'),
    'date': ('ngay gio giao dich', 'thoi gian giao dich', 'ngay giao dich', 'ngay chuyen nh',
             'ngay chuyen', 'ngay hieu luc', 'ngay gia tri', 'ngay hach toan', 'ngay',
             'transaction date time', 'transaction date', 'value date', 'accounting date', 'date',
             'ngay gd', 'ngaygio', 'posting date', 'date time'),
    'credit': ('so tien ghi co', 'so tien co', 'phat sinh co', 'ghi co', 'so tien gui vao',
               'credit amount', 'credit', 'co', 'ghico', 'credit turnover', 'deposit', 'amount credit', 'so tien credit'),
    'debit': ('so tien ghi no', 'so tien no', 'phat sinh no', 'ghi no', 'so tien rut ra',
              'debit amount', 'debit', 'no', 'ghino', 'debit turnover', 'withdrawal', 'amount debit', 'so tien debit'),
    'amount': ('so tien giao dich', 'so tien thanh toan', 'so tien', 'sotien', 'transaction amount', 'amount'),
    'reference': ('so tham chieu', 'so giao dich', 'so gd', 'so but toan', 'but toan', 'so id',
                  'so ref', 'ma giao dich', 'ma tham chieu', 'reference no', 'reference',
                  'transaction number', 'transaction id', 'trans id'),
    'customer_id': ('idkh', 'id kh', 'ma khach hang', 'ma kh', 'makh', 'makhachhang',
                    'ma so khach hang', 'ma so kh', 'mkh', 'customer id', 'customer code',
                    'customer number', 'id khach hang', 'khach hang id'),
    'customer_name': ('ten khach hang', 'ten kh', 'tenkh', 'don vi chuyen tien', 'customer name', 'ho va ten'),
    'payer': ('hinh thuc', 'ngan hang', 'kenh thanh toan', 'bank', 'nganhang'),
    'payment_mode': ('kieuthanhtoan', 'kieu thanh toan', 'payment mode'),
    'provider_kind': ('loaidonvithuho', 'loai don vi thu ho', 'provider kind'),
    'provider_name': ('donvithuho', 'don vi thu ho', 'provider name'),
}

STANDARD_LAYOUTS = {
    'raw': {'title': 'Bố cục chuẩn cho đối soát', 'columns': [
        {'key': 'NGAY', 'description': 'Ngày giờ chuyển khoản: dd/mm/yyyy HH:MM:SS hoặc yyyy-mm-dd. Không phải kỳ hóa đơn.', 'required': True},
        {'key': 'NOIDUNG', 'description': 'Giữ nguyên toàn bộ nội dung chuyển tiền, cả chữ và số.', 'required': True},
        {'key': 'GHICO', 'description': 'Số tiền nhận vào, không âm. Dùng ô số hoặc chuỗi số; để trống/0 nếu chỉ ghi nợ.', 'required': True},
        {'key': 'GHINO', 'description': 'Số tiền chuyển ra, không âm. Để trống/0 nếu chỉ ghi có.', 'required': False},
        {'key': 'REFERENCE', 'description': 'Tham chiếu ngân hàng. Định dạng Text để giữ số 0 đầu và mã dài.', 'required': False},
        {'key': 'NGANHANG', 'description': 'Ngân hàng/kênh giao dịch; để trống thì dùng tên sheet.', 'required': False},
        {'key': 'KIEUTHANHTOAN', 'description': 'proxy = thu hộ; self = tự trả; unknown = chưa xác định. Có thể để trống.', 'required': False},
        {'key': 'LOAIDONVITHUHO', 'description': 'bank, wallet, other hoặc unknown; chỉ mô tả đơn vị thu hộ.', 'required': False},
        {'key': 'DONVITHUHO', 'description': 'Tên ngân hàng/ví/dịch vụ thu hộ đã kiểm tra; không phải mã khách hàng.', 'required': False}]},
    'confirmed': {'title': 'Bố cục chuẩn cho học dữ liệu đã xác nhận', 'columns': [
        {'key': 'IDKH', 'description': 'Mã khách hàng đã kiểm tra; định dạng Text để giữ đủ số và số 0 đầu. Nhãn ko được bỏ qua.', 'required': True},
        {'key': 'TENKH', 'description': 'Tên khách hàng đã kiểm tra. Có thể để trống khi bổ sung mẫu cho hồ sơ đã có.', 'required': False},
        {'key': 'NOIDUNG', 'description': 'Nội dung chuyển tiền nguyên văn, thuộc đúng khách hàng đã xác nhận.', 'required': True},
        {'key': 'NGAY', 'description': 'Ngày giờ chuyển khoản: dd/mm/yyyy HH:MM:SS hoặc yyyy-mm-dd.', 'required': False},
        {'key': 'SOTIEN', 'description': 'Số tiền thanh toán không âm, để trống thì 0.', 'required': False},
        {'key': 'NGANHANG', 'description': 'Ngân hàng/kênh trả tiền; để trống thì dùng tên sheet.', 'required': False},
        {'key': 'REFERENCE', 'description': 'Tham chiếu ngân hàng, định dạng Text.', 'required': False},
        {'key': 'KIEUTHANHTOAN', 'description': 'proxy = thu hộ; self = tự trả; unknown = chưa xác định. Trống thì dùng loại của mẫu đã xác nhận nếu có.', 'required': False},
        {'key': 'LOAIDONVITHUHO', 'description': 'bank, wallet, other hoặc unknown.', 'required': False},
        {'key': 'DONVITHUHO', 'description': 'Tên ngân hàng/ví/dịch vụ thu hộ đã kiểm tra.', 'required': False}]},
}


def layout_guidance(kind):
    return 'Không nhận diện được bố cục. Xem mẫu bố cục trên giao diện để chỉnh lại tệp.'


def header_columns(values):
    columns, priorities, ambiguous = {}, {}, set()
    for column, value in values.items():
        label = ' '.join(re.findall(r'[a-z0-9]+', fold(value)))
        def matches(alias):
            return (label == alias or (' ' in alias and label.startswith(alias + ' '))
                    or label in (alias + ' debit', alias + ' credit'))
        for role, aliases in HEADER_ALIASES.items():
            if role == 'amount' and any(matches(alias) for financial in ('credit', 'debit')
                                        for alias in HEADER_ALIASES[financial]):
                continue  # "Số tiền ghi có/nợ" is not a competing generic amount column.
            for priority, alias in enumerate(aliases):
                if role == 'debit' and alias == 'no' and label == 'no' and 'ợ' not in str(value).lower():
                    continue  # English "No." is a row counter, not a debit column.
                # One-word labels must not match narrative cells (e.g. "co phan").
                if matches(alias) or (role == 'customer_id' and label.replace(' ', '') == alias.replace(' ', '')):
                    if role == 'customer_id':
                        priority = 0  # Different IDKH aliases are equally authoritative; never pick between two ID columns.
                    if role not in priorities or priority < priorities[role]:
                        columns[role], priorities[role] = column, priority
                        ambiguous.discard(role)
                    elif priority == priorities[role] and column != columns[role]:
                        ambiguous.add(role)
                    break
    # "ID" alone can be a bank reference. Only treat it as a customer label
    # next to an explicitly named customer column and transaction narrative (e.g. SHB).
    customer_label = ' '.join(re.findall(r'[a-z0-9]+', fold(values.get(columns.get('customer_name'), ''))))
    explicit_customer_name = any(customer_label == alias or customer_label.startswith(alias + ' ')
                                for alias in ('ten khach hang', 'ten kh', 'tenkh', 'customer name', 'ho va ten'))
    if 'customer_id' not in priorities and 'raw' in columns and explicit_customer_name:
        generic_ids = [column for column, value in values.items()
                       if ' '.join(re.findall(r'[a-z0-9]+', fold(value))) in ('id', 'ma id', 'maid')]
        if len(generic_ids) == 1:
            columns['customer_id'] = generic_ids[0]
        elif generic_ids:
            ambiguous.add('customer_id')
    return {role: column for role, column in columns.items() if role not in ambiguous}, ambiguous


def detect_layout(values):
    columns, ambiguous = header_columns(values)
    if ambiguous.intersection({'raw', 'date', 'credit', 'debit', 'amount', 'customer_id'}):
        return None  # Several equally named financial/identity columns are not safe to guess.
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
    """Confirmed examples with the same header aliases as Excel; also accepts older CSVs."""
    def __init__(self, path, check_cancel=None, row_errors=None):
        self.path = Path(path)
        self.check_cancel = check_cancel or (lambda: None)
        self.row_errors = row_errors if row_errors is not None else RowErrors(file=self.path.name)

    def __enter__(self):
        self.source = self.path.open(encoding='utf-8-sig', newline='')
        try:
            self.reader = csv.DictReader(self.source)
            fields = self.reader.fieldnames or []
            columns, ambiguous = header_columns(dict(enumerate(fields)))
            if ambiguous:
                raise ValueError('CSV có nhiều cột cùng vai trò. Xem mẫu bố cục trên giao diện để chỉnh lại tiêu đề.')
            if not {'customer_id', 'raw'}.issubset(columns):
                raise ValueError('Learning CSV requires IDKH and NOIDUNG columns')
            canonical = {'customer_id': 'IDKH', 'customer_name': 'TENKH', 'raw': 'NOIDUNG',
                         'date': 'NGAY', 'amount': 'SOTIEN', 'payer': 'NGANHANG', 'reference': 'REFERENCE',
                         'payment_mode': 'KIEUTHANHTOAN', 'provider_kind': 'LOAIDONVITHUHO', 'provider_name': 'DONVITHUHO'}
            renamed = {column: canonical[role] for role, column in columns.items() if role in canonical}
            if 'amount' not in columns and 'credit' in columns:
                renamed[columns['credit']] = 'SOTIEN'
            normalized = [renamed.get(index, str(name).strip().upper()) for index, name in enumerate(fields)]
            if len(normalized) != len(set(normalized)):
                raise ValueError('CSV có tiêu đề cột trùng nhau. Xem mẫu bố cục trên giao diện để chỉnh lại tiêu đề.')
            self.reader.fieldnames = normalized
        except BaseException:
            self.source.close()
            raise
        return self

    def __exit__(self, *_):
        self.source.close()

    def confirmed_sheets(self):
        return ['CSV']

    def batches(self, sheet=None, batch_size=1000, **_):
        batch = []
        for record in self.reader:
            self.check_cancel()
            try:
                transaction = self._transaction(record, self.reader.line_num)
            except ROW_DATA_ERRORS as error:
                self.row_errors.record(record.get('SHEET') or 'CSV', self.reader.line_num, error)
                continue
            batch.append(transaction)
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def _transaction(self, record, row_index):
        customer_id = (record.get('IDKH') or '').strip()
        status = label_status(customer_id, record.get('TENKH'))
        if status in ('skipped', 'unresolved'):
            return ExcelTransaction(row_index, '', customer_id=customer_id,
                source=self.path.name, sheet=record.get('SHEET') or 'CSV', label_status=status)
        raw = record.get('NOIDUNG') or ''
        if record.get('NOIDUNG_GOC_B64'):
            try:
                original = base64.b64decode(record['NOIDUNG_GOC_B64'], validate=True).decode('utf-8')
            except (binascii.Error, UnicodeDecodeError) as error:
                raise ValueError('Nội dung gốc mã hóa trong CSV chưa hợp lệ.') from error
            protected = "'" + original if original.lstrip().startswith(('=', '+', '-', '@')) else original
            if raw in (original, protected):
                raw = original
        if not raw.strip() or len(raw) > 32767:
            raise ValueError('Nội dung giao dịch trống hoặc vượt quá 32.767 ký tự.')
        if not customer_id or len(customer_id) > 100:
            raise ValueError('Mã khách hàng trống hoặc vượt quá 100 ký tự.')
        try:
            amount = float((record.get('SOTIEN') or '0').strip())
            if not math.isfinite(amount) or amount < 0:
                raise ValueError()
        except ValueError as error:
            raise ValueError('Số tiền trong CSV chưa hợp lệ.') from error
        return ExcelTransaction(row_index, raw, date=date_text(record.get('NGAY')),
            amount=amount, customer_id=customer_id, customer_name=record.get('TENKH') or '',
            payer=record.get('NGANHANG') or record.get('SHEET') or 'BIDV', source=self.path.name,
            reference=record.get('REFERENCE') or '', sheet=record.get('SHEET') or 'CSV', label_status=status,
            payment_mode=record.get('KIEUTHANHTOAN') or '', provider_kind=record.get('LOAIDONVITHUHO') or '',
            provider_name=record.get('DONVITHUHO') or '')


class StreamingWorkbook:
    def __init__(self, path: str | Path, check_cancel=None, row_errors=None):
        self.path = Path(path)
        self.check_cancel = check_cancel or (lambda: None)
        self.row_errors = row_errors if row_errors is not None else RowErrors(file=self.path.name)

    def __enter__(self):
        self.resources = ExitStack()
        try:
            self.zip = self.resources.enter_context(zipfile.ZipFile(self.path))
            if sum(i.file_size for i in self.zip.infolist()) > 2 * 1024**3:
                raise ValueError('Workbook expands beyond the 2 GB limit')
            self.temp = tempfile.TemporaryDirectory(prefix='invoice-xlsx-')
            # TemporaryDirectory.__enter__ returns a path string, not the manager.
            self.temp_path = Path(self.resources.enter_context(self.temp))
            self.db = self.resources.enter_context(closing(sqlite3.connect(str(self.temp_path / 'strings.db'))))
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

    def rows(self, sheet=None, report_errors=True):
        if sheet is None:
            for name in self.sheets:
                yield from self.rows(name, report_errors=report_errors)
            return
        if sheet not in self.sheets:
            raise ValueError(f'Sheet {sheet!r} not found. Available: {", ".join(self.sheets)}')
        # Also register the stream for cleanup if iteration is stopped while suspended at yield.
        with self.resources.enter_context(self.zip.open(self.sheets[sheet])) as stream:
            events = ET.iterparse(stream, events=('start', 'end'))
            _, root = next(events)
            row_position = 0
            for event, element in events:
                if event != 'end' or element.tag != NS + 'row':
                    continue
                self.check_cancel()
                row_position += 1
                row_index = None
                try:
                    row_index = int(element.attrib['r'])
                    if row_index < 1:
                        row_index = None
                        raise ValueError('Chỉ số dòng Excel chưa hợp lệ.')
                    values = self._row_values(element)
                except ROW_DATA_ERRORS as error:
                    if report_errors:
                        self.row_errors.record(sheet, row_index, error, row_position=row_position)
                else:
                    yield row_index, values
                finally:
                    element.clear()
                    # Clear empty row shells to keep streaming memory bounded.
                    sheet_data = root.find(NS + 'sheetData')
                    if sheet_data is not None:
                        sheet_data.clear()

    def _row_values(self, element):
        values = {}
        for cell in element.findall(NS + 'c'):
            col = re.sub(r'\d', '', cell.attrib['r'])
            kind = cell.attrib.get('t')
            value = cell.find(NS + 'v')
            value = value.text if value is not None else None
            if kind == 's' and value is not None:
                record = self.db.execute('SELECT value FROM strings WHERE id=?', (int(value),)).fetchone()
                if record is None:
                    raise ValueError(f'Ô {cell.attrib["r"]} tham chiếu nội dung không tồn tại trong Excel.')
                value = record[0]
            elif kind == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.iter(NS + 't'))
            elif value is not None and kind not in ('str', 'e'):
                try:
                    # Keep long numeric identifiers and leading zeros as exact strings.
                    if not (value.isdigit() and (len(value) > 15 or (len(value) > 1 and value.startswith('0')))):
                        value = float(value)
                        if value.is_integer():
                            value = int(value)
                except ValueError:
                    pass
            if value is not None:
                values[col] = value
        return values

    def sheet_layouts(self):
        if hasattr(self, '_layouts'):
            return self._layouts
        layouts, diagnostics = {}, {}
        for sheet in self.sheets:
            self.check_cancel()
            # Header probing must not report the same bad row again during the real scan.
            source = self.rows(sheet, report_errors=False)
            previous, previous_index, best_columns, best_ambiguous = {}, 0, {}, set()
            try:
                for row_index, values in source:
                    columns, ambiguous = header_columns(values)
                    if len(columns) + len(ambiguous) > len(best_columns) + len(best_ambiguous):
                        best_columns, best_ambiguous = columns, ambiguous
                    layout = detect_layout(values)
                    header_text_only = not any(isinstance(value, (int, float)) or
                        valid_date(date_text(value)) or re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', str(value).strip())
                        for value in values.values())
                    if (not layout and header_text_only and columns and row_index == previous_index + 1
                            and len(header_columns(previous)[0]) >= 2):
                        # Combine adjacent header lines conservatively, only when
                        # they add recognized roles. Numeric data are never headers.
                        combined = {column: ' '.join(str(value) for value in (previous.get(column, ''), values.get(column, '')) if value != '')
                                    for column in previous.keys() | values.keys()}
                        merged_columns, _ = header_columns(combined)
                        if len(merged_columns) > len(header_columns(previous)[0]):
                            layout = detect_layout(combined)
                    if layout:
                        layouts[sheet] = {**layout, 'header_row': row_index}
                        break
                    layout = headerless_layout(sheet, values)
                    if layout:
                        layouts[sheet] = {**layout, 'header_row': row_index - 1}
                        break
                    if row_index >= 100:
                        break
                    previous, previous_index = values, row_index
            finally:
                source.close()
            diagnostics[sheet] = {'detected_columns': best_columns, 'ambiguous_columns': sorted(best_ambiguous)}
        self._layouts = layouts
        self._layout_diagnostics = diagnostics
        return layouts

    def raw_sheets(self):
        return [sheet for sheet, layout in self.sheet_layouts().items() if layout['kind'] == 'raw']

    def confirmed_sheets(self):
        return [sheet for sheet, layout in self.sheet_layouts().items() if layout['kind'] == 'confirmed']

    def skipped_sheets(self, kind):
        layouts = self.sheet_layouts()
        skipped = []
        for sheet in self.sheets:
            if layouts.get(sheet, {}).get('kind') == kind:
                continue
            details = self._layout_diagnostics.get(sheet, {})
            columns = details.get('detected_columns', {})
            reason = 'confirmed_layout' if layouts.get(sheet, {}).get('kind') == 'confirmed' else \
                     'raw_layout' if layouts.get(sheet, {}).get('kind') == 'raw' else \
                     'ambiguous_layout' if details.get('ambiguous_columns') else 'unsupported_layout'
            missing = []
            for role, label in ((('raw', 'NOIDUNG'), ('date', 'NGAY')) if kind == 'raw' else
                                (('raw', 'NOIDUNG'), ('customer_id', 'IDKH'))):
                if role not in columns:
                    missing.append(label)
            if kind == 'raw' and not {'credit', 'amount'}.intersection(columns):
                missing.append('GHICO hoặc SOTIEN có dấu')
            skipped.append({'sheet': sheet, 'reason': reason, **details, 'missing_columns': missing,
                            'suggested_layout': kind, 'guidance': layout_guidance(kind)})
        return skipped

    def transactions(self, sheet=None, kind='auto'):
        if kind not in ('auto', 'raw', 'confirmed'):
            raise ValueError('kind must be auto, raw, or confirmed')
        layouts = self.sheet_layouts()
        if sheet is None:
            selected = [name for name, layout in layouts.items() if kind == 'auto' or layout['kind'] == kind]
            if not selected:
                raise ValueError(layout_guidance('raw' if kind == 'auto' else kind))
            for name in selected:
                yield from self.transactions(name, kind)
            return
        if sheet not in self.sheets:
            raise ValueError(f'Sheet {sheet!r} not found. Available: {", ".join(self.sheets)}')
        layout = layouts.get(sheet)
        if not layout:
            raise ValueError(f'Sheet {sheet!r}: ' + layout_guidance('raw' if kind == 'auto' else kind))
        if kind != 'auto' and layout['kind'] != kind:
            raise ValueError('Batch classification requires raw bank layout; upload the unfiltered file')
        for row_index, values in self.rows(sheet):
            if row_index <= layout['header_row']:
                continue
            try:
                transaction = self._transaction(sheet, row_index, values, layout)
            except ROW_DATA_ERRORS as error:
                self.row_errors.record(sheet, row_index, error)
                continue
            if transaction is not None:
                yield transaction

    def _transaction(self, sheet, row_index, values, layout):
        if detect_layout(values):
            return None  # Repeated page headers are not transactions.
        columns = layout['columns']
        source = f'{self.path.name} [{sheet}]'
        get = lambda role: values.get(columns.get(role, ''), '')
        raw = str(get('raw'))
        if not raw.strip():
            return None
        customer_id = str(get('customer_id')).strip()
        if layout['kind'] == 'confirmed':
            name = str(get('customer_name')).strip()
            if not customer_id:
                return None
            status = label_status(customer_id, name)
            if status in ('skipped', 'unresolved'):
                return ExcelTransaction(row_index, '', customer_id=customer_id, source=source,
                                       sheet=sheet, label_status=status)
            if customer_id.startswith('#') or len(customer_id) > 100:
                raise ValueError('Mã khách hàng chưa hợp lệ hoặc vượt quá 100 ký tự.')
            if len(raw) > 32767:
                raise ValueError('Nội dung giao dịch vượt quá 32.767 ký tự.')
            amount = parse_money(get('amount') if 'amount' in columns else get('credit'))
            if amount is None or amount < 0:
                raise ValueError('Số tiền của dòng học chưa hợp lệ.')
            return ExcelTransaction(row_index, raw, date=date_text(get('date')),
                amount=amount, customer_id=customer_id, customer_name=name or customer_id,
                payer=str(get('payer') or sheet).strip(), source=source, sheet=sheet,
                reference=str(get('reference')).strip(), label_status=status,
                payment_mode=str(get('payment_mode')).strip(), provider_kind=str(get('provider_kind')).strip(),
                provider_name=str(get('provider_name')).strip())
        reference = str(get('reference')).strip()
        if not get('date') and not reference:
            return None  # Totals/closing balances.
        if len(raw) > 32767:
            raise ValueError('Nội dung giao dịch vượt quá 32.767 ký tự.')
        errors = []
        if layout.get('direction_unverified'):
            errors.append('Sheet thiếu tiêu đề ghi nợ/ghi có; kiểm tra cột Q/T trong tệp gốc trước khi xác nhận.')
        date = date_text(get('date'))
        # Printed page/contact footers can occupy the date/description columns.
        # Ignore only recognizable footer text without any transaction evidence;
        # malformed dates on actual payments must still go to manual review.
        footer = re.match(r'^(?:telex|swift|website|contact center)\s*:', fold(raw).strip()) or \
                 re.fullmatch(r'(?:trang|page)\s+\d+\s*/\s*\d+', fold(raw).strip())
        has_money_cells = any(str(get(role)).strip() for role in ('credit', 'debit', 'amount'))
        if footer and not reference and not has_money_cells and not valid_date(date):
            return None
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
        return ExcelTransaction(row_index, raw, reference, date, amount, debit,
            payer=str(get('payer') or sheet).strip(), source=source, sheet=sheet, validation_errors=errors,
            payment_mode=str(get('payment_mode')).strip(), provider_kind=str(get('provider_kind')).strip(),
            provider_name=str(get('provider_name')).strip())

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
