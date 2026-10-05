"""Disk-backed exports, grouped by original sheet, with identifiers kept as text."""
import csv
from pathlib import Path
import re
import zipfile

from .platform_utils import portable_filename


RESULT_COLUMNS = ('row_index', 'date', 'raw', 'amount', 'predicted_customer_id', 'customer_name', 'score',
    'decision', 'status', 'confirmed_customer_id', 'confirmed_customer_name', 'learned', 'reason',
    'matched_pattern_id', 'matched_pattern', 'matched_template', 'source_file', 'source_row',
    'sheet', 'payer', 'reference', 'debit', 'validation_errors', 'input_file', 'payment_period',
    'extracted_name', 'extracted_names', 'name_extraction_status', 'shared_template_id',
    'shared_template_customer_count', 'payment_mode', 'provider_kind', 'provider_name',
    'case_type', 'suggested_customer_ids')
RESULT_LABELS = ('Dòng gốc', 'Thời gian chuyển khoản', 'Nội dung chuyển tiền', 'Số tiền ghi có',
    'Mã khách hàng đề xuất', 'Tên khách hàng đề xuất', 'Điểm so khớp', 'Đề xuất', 'Trạng thái duyệt',
    'Mã khách hàng xác nhận', 'Tên khách hàng xác nhận', 'Đã học', 'Lý do', 'Mã mẫu lịch sử',
    'Nội dung mẫu lịch sử', 'Cấu trúc mẫu lịch sử', 'Tệp lịch sử', 'Dòng lịch sử', 'Sheet gốc',
    'Ngân hàng / kênh', 'Tham chiếu', 'Số tiền ghi nợ', 'Thông tin cần kiểm tra', 'Tệp đầu vào',
    'Kỳ thanh toán', 'Tên trích từ nội dung', 'Các tên trích từ nội dung', 'Trạng thái trích tên',
    'Mã mẫu giao dịch', 'Số khách hàng dùng mẫu', 'Kiểu thanh toán', 'Loại đơn vị thu hộ', 'Đơn vị thu hộ',
    'Trường hợp', 'Mã đề xuất cần kiểm tra')
CASES = {'batch': 'Thu hộ tổng hợp', 'multi': 'Nhiều khách hàng', 'history_multi': 'Lịch sử chia nhiều khách hàng',
         'suggested': 'Mã đề xuất'}
DECISIONS = {'auto_accept': 'Có thể xác nhận', 'review': 'Cần duyệt',
             'manual_check': 'Kiểm tra thủ công', 'reject': 'Không ghép'}
STATUSES = {'pending': 'Chưa duyệt', 'confirmed': 'Đã xác nhận & học', 'rejected': 'Đã từ chối'}


def csv_value(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        return value
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def case_of(row):
    evidence = row.get('evidence', {})
    if evidence.get('batch_settlement'):
        return 'batch', []
    if evidence.get('allocation_customer_ids') or evidence.get('unknown_allocation_ids'):
        return 'multi', evidence.get('allocation_customer_ids', []) + evidence.get('unknown_allocation_ids', [])
    if evidence.get('history_allocation_customer_ids'):
        return 'history_multi', evidence['history_allocation_customer_ids']
    if row.get('suggested_customer_id'):
        return 'suggested', [row['suggested_customer_id']]
    return '', []


def export_values(row):
    evidence = row.get('evidence', {})
    source = evidence.get('pattern_source', {})
    return [row.get('row_index'), row.get('date'), row['raw'], row.get('amount', 0),
        row.get('customer_id'), row.get('customer_name'), row['score'], row['decision'], row['status'],
        row.get('confirmed_customer_id'), row.get('confirmed_customer_name'), row['learned'],
        evidence.get('reason_vi') or evidence.get('reason'), evidence.get('matched_pattern_id'),
        evidence.get('matched_pattern'), evidence.get('matched_template'), source.get('file'), source.get('row'),
        row.get('sheet'), row.get('payer'), row.get('reference'), row.get('debit', 0),
        ' '.join(row.get('validation_errors', [])), row.get('source'), row.get('payment_period'),
        row.get('extracted_name'), '; '.join(row.get('extracted_names', [])),
        row.get('name_extraction', {}).get('status'), evidence.get('shared_template_id'),
        evidence.get('shared_template_customer_count'), row.get('payment_mode', 'unknown'),
        row.get('provider_kind', 'unknown'), row.get('provider_name', ''),
        CASES.get(case_of(row)[0], ''), ', '.join(case_of(row)[1])]


def sheet_names(job):
    summary = job.get('summary', {})
    # New jobs retain even unsupported/empty sheets. Old jobs have fewer details.
    names = summary.get('input_sheets') or summary.get('sheets') or []
    if not names:
        names = list(summary.get('sheet_counts', {}))
        names += [item['sheet'] for item in summary.get('skipped_sheets', [])]
    return list(dict.fromkeys(names))


def csv_archive(records, sheets, path):
    """Keep only one CSV handle open, including for very many input sheets."""
    path = Path(path)
    paths, used = {}, set()

    def add_sheet(name):
        filename = portable_filename(name + '.csv', max_bytes=120)
        stem, index = filename[:-4], 2
        while filename.casefold() in used:
            filename = f'{stem}_{index}.csv'
            index += 1
        used.add(filename.casefold())
        target = path.parent / filename
        with target.open('w', encoding='utf-8-sig', newline='') as output:
            csv.writer(output).writerow(RESULT_COLUMNS)
        paths[name] = target

    for name in sheets:
        add_sheet(name)
    current, output = None, None
    try:
        for row in records:
            values = export_values(row)
            name = row.get('sheet') or 'Giao dịch'
            if name not in paths:
                add_sheet(name)
            if current != name:
                if output is not None:
                    output.close()
                output = paths[name].open('a', encoding='utf-8', newline='')
                writer = csv.writer(output)
                current = name
            writer.writerow([csv_value(value) for value in values])
    finally:
        if output is not None:
            output.close()
    if not paths:
        add_sheet('Giao dịch')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for target in paths.values():
            archive.write(target, arcname=target.name)


def workbook_writer(path):
    import xlsxwriter
    return xlsxwriter.Workbook(str(path), {'constant_memory': True, 'tmpdir': str(Path(path).parent),
        'strings_to_numbers': False, 'strings_to_formulas': False, 'strings_to_urls': False})


def write_cell(sheet, row, column, value, cell_format=None):
    # Identifiers and raw text never go through float conversion or formula detection.
    if value is None:
        value = ''
    if isinstance(value, str):
        if len(value) > 32767:
            raise ValueError('Một ô kết quả vượt giới hạn Excel. Hãy tải CSV theo sheet để giữ đầy đủ nội dung.')
        result = sheet.write_string(row, column, value, cell_format)
    else:
        result = sheet.write(row, column, value, cell_format)
    if result != 0:
        raise ValueError('Kết quả vượt giới hạn dòng/cột Excel. Hãy tải CSV theo sheet.')


def result_workbook(records, sheets, path):
    names, used = {}, set()
    with workbook_writer(path) as workbook:
        header = workbook.add_format({'bold': True, 'font_color': 'white', 'bg_color': '#007F91',
                                      'text_wrap': True, 'valign': 'top'})
        numeric = workbook.add_format({'num_format': '#,##0.00'})
        percent = workbook.add_format({'num_format': '0.0%'})

        def add_sheet(name):
            title = re.sub(r'[\[\]:*?/\\\x00-\x1f]', '_', name).strip("'")[:31].strip("'") or 'Giao dịch'
            base, index = title, 2
            if title.casefold() == 'history':
                title = base = 'History_'
            while title.casefold() in used:
                suffix = f'_{index}'
                title = base[:31 - len(suffix)] + suffix
                index += 1
            used.add(title.casefold())
            sheet = workbook.add_worksheet(title)
            sheet.freeze_panes(1, 3)
            sheet.set_row(0, 35)
            sheet.set_column(0, len(RESULT_COLUMNS) - 1, 22)
            sheet.set_column(2, 2, 65)
            sheet.set_column(14, 16, 45)
            for column, label in enumerate(RESULT_LABELS):
                write_cell(sheet, 0, column, label, header)
            names[name] = [sheet, 1]

        for name in sheets:
            add_sheet(name)
        for row in records:
            name = row.get('sheet') or 'Giao dịch'
            if name not in names:
                add_sheet(name)
            sheet, index = names[name]
            values = export_values(row)
            values[7] = DECISIONS.get(row['decision'], row['decision'])
            values[8] = STATUSES.get(row['status'], row['status'])
            values[11] = 'Có' if row['learned'] else 'Chưa'
            values[27] = {'completed': 'Đã nhận diện', 'disabled': 'Chưa bật',
                'unavailable': 'Chưa sẵn sàng', 'not_run': 'Đọc trường ngân hàng'}.get(values[27], values[27])
            values[30] = {'proxy': 'Thu hộ', 'self': 'Khách hàng tự trả', 'unknown': 'Chưa xác định'}.get(values[30], values[30])
            values[31] = {'bank': 'Ngân hàng', 'wallet': 'Ví điện tử', 'other': 'Dịch vụ khác', 'unknown': 'Chưa xác định'}.get(values[31], values[31])
            for column, value in enumerate(values):
                cell_format = percent if column == 6 else numeric if column in (3, 21) else None
                write_cell(sheet, index, column, value, cell_format)
            names[name][1] += 1
        if not names:
            add_sheet('Giao dịch')
        for sheet, index in names.values():
            sheet.autofilter(0, 0, index - 1, len(RESULT_COLUMNS) - 1)

