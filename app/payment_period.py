"""Display-only water billing periods; never used by learning or matching."""
from __future__ import annotations

import re

from .normalize import fold
from .bank_content import mb_water_fields

PAYMENT_PERIOD_VERSION = 3
MONTH = r'(?:0?[1-9]|1[0-2])'
YEAR = r'(?:20\d{2}|\d{2})'
LABEL = r'(?<![a-z\d])(?:(?:thang|ky|month)(?:\s+(?:thanh\s+toan|tien\s+nuoc|hoa\s+don))?|t)\s*[:.]?\s*'
YEAR_SEPARATOR = r'(?:\s*[/.-]\s*|\s+(?:nam\s+)?)'
END = r'(?![a-z\d]|[/.-]\d)'

# Identify complete dates FIRST, so 07/2026 inside 11/07/2026 cannot become
# a billing month. These spans also cover transfer times and ISO dates.
FULL_DATE = re.compile(
    r'(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:20\d{2}|\d{2})(?!\d)'
    r'|(?<!\d)20\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])(?!\d)')
IDENTIFIER = re.compile(
    r'\b(?:hd|hop\s*dong|idkh|mkh|kh|ma\s*(?:kh|khach\s*hang)|tkthe|tk\s*the|'
    r'stk|tk|ac|tai\s*khoan|ref|reference|ma\s*gd)\s*[:#=]?\s*([a-z\d][a-z\d_./-]*)')
TRANSFER_CONTEXT = re.compile(
    r'\b(?:ngay(?:\s+gio)?(?:\s+(?:gd|giao\s*dich|chuyen\s*(?:khoan|tien)))?|'
    r'thoi\s*(?:gian|diem)(?:\s+(?:gd|giao\s*dich|chuyen\s*(?:khoan|tien)))?|'
    r'giao\s*dich|chuyen\s*(?:khoan|tien)|ngay\s*(?:lap|in|tao)|timestamp|luc|'
    r'(?:den\s+)?han(?:\s+(?:thanh\s+toan|nop))?)'
    r'\s*[:=]?\s*(?:thang\s*)?$')
WATER_PAYMENT = re.compile(r'\b(?:tien\s*nuoc|nuoc\s*(?:sinh|sach|may)|phi\s*nuoc|cp\s*nuoc|water)\b')

LABELED = re.compile(LABEL + rf'(?P<month>{MONTH}){YEAR_SEPARATOR}(?P<year>{YEAR})' + END)
# A compact period needs an explicit label: "kỳ 082026" is August 2026,
# while an unlabelled six-digit value may be a customer/contract identifier.
COMPACT = re.compile(LABEL + rf'(?P<month>{MONTH})(?P<year>20\d{{2}})' + END)
MONTH_LIST = re.compile(LABEL + rf'(?P<months>{MONTH}(?:\s*(?:,|&|va|\+)\s*{MONTH})+)'
                        + YEAR_SEPARATOR + rf'(?P<year>{YEAR})' + END)
MONTH_RANGE = re.compile(LABEL + rf'(?P<first>{MONTH})\s*(?:-|den)\s*(?P<last>{MONTH})'
                         + YEAR_SEPARATOR + rf'(?P<year>{YEAR})' + END)
FULL_RANGE = re.compile(LABEL + rf'(?P<first>{MONTH}){YEAR_SEPARATOR}(?P<year>{YEAR})'
                       + r'\s*(?:den|-)\s*' + rf'(?:{LABEL})?(?P<last>{MONTH})'
                       + YEAR_SEPARATOR + rf'(?P<last_year>{YEAR})' + END)
UNLABELED = re.compile(rf'(?<![a-z\d_/-])(?P<month>{MONTH})\s*[/.-]\s*(?P<year>20\d{{2}})' + END)


def payment_period_fields(raw: str) -> dict:
    """Extract explicit periods without borrowing the transfer date/year.

    Separated/compact periods share the same labels (kỳ 8/2026 or kỳ 082026).
    Short years are supported only with a billing label (T7/26 -> 07/2026).
    Dates, labeled identifiers and transfer-date contexts cannot provide a period.
    A bare MM/YYYY needs nearby water-payment wording or the BIDV @@ suffix.
    """
    original = str(raw or '')
    value = fold(original)
    # Normalized offsets are used only for matching. Map evidence back to the
    # original Vietnamese text, including combining-accent characters.
    offsets = []
    for offset, char in enumerate(original):
        offsets.extend([offset] * len(fold(char)))
    offsets.append(len(original))
    protected = [match.span(1) for match in IDENTIFIER.finditer(value)]
    full_dates = [match.span() for match in FULL_DATE.finditer(value)]
    claimed, periods, matches = [], [], []
    bank_fields = mb_water_fields(value)
    if bank_fields:
        month, year = bank_fields['period'].split('/')
        period = f'{int(month):02d}/{year}'
        start, end = bank_fields['period_span']
        periods.append(period)
        claimed.append((start, end))
        matches.append({'text': original[offsets[start]:offsets[end]], 'periods': [period], 'source': 'bank_field'})

    def overlaps(span, spans):
        return any(span[0] < end and span[1] > start for start, end in spans)

    def add(match, months, *, grouped=False, labeled=True, explicit_periods=None):
        span = match.span()
        prefix = value[max(0, span[0] - 100):span[0]]
        transfer_context = TRANSFER_CONTEXT.search(prefix)
        # "chuyển khoản kỳ 082026" names the bill's cycle. A plain transfer
        # action does not turn this explicit "kỳ" into a transfer date; actual
        # date/time/deadline labels and complete dates remain excluded.
        billing_cycle_after_transfer = bool(labeled and value[span[0]:].startswith('ky') and transfer_context
            and re.fullmatch(r'chuyen\s*(?:khoan|tien)\s*[:=]?\s*', transfer_context.group()))
        if overlaps(span, protected) or overlaps(span, claimed) or (transfer_context and not billing_cycle_after_transfer):
            return
        if re.match(r'\s*(?:t\s*)?\d{1,2}:\d{2}', value[span[1]:]):
            return
        # "tháng 6-7/2026" is an explicit month range, whereas an unlabelled
        # 6-7/2026 is a complete calendar date and is never a billing period.
        if not grouped and overlaps(span, full_dates):
            return
        if not labeled:
            bidv_suffix = prefix.endswith('@@') and 'o@l_' in value and WATER_PAYMENT.search(value)
            if not WATER_PAYMENT.search(prefix) and not bidv_suffix:
                return
        year_text = match['year']
        year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        found = explicit_periods if explicit_periods is not None else [f'{month:02d}/{year:04d}' for month in months]
        claimed.append(span)
        for period in found:
            if period not in periods:
                periods.append(period)
        matches.append({'text': original[offsets[span[0]]:offsets[span[1]]], 'periods': found})

    for match in FULL_RANGE.finditer(value):
        first_year = int(match['year']) + (2000 if len(match['year']) == 2 else 0)
        last_year = int(match['last_year']) + (2000 if len(match['last_year']) == 2 else 0)
        first = first_year * 12 + int(match['first']) - 1
        last = last_year * 12 + int(match['last']) - 1
        if 0 <= last - first < 24:
            found = [f'{period % 12 + 1:02d}/{period // 12:04d}' for period in range(first, last + 1)]
            add(match, [], grouped=True, explicit_periods=found)
        else:
            claimed.append(match.span())
    for match in MONTH_LIST.finditer(value):
        months = [int(month) for month in re.findall(r'\d+', match['months'])]
        add(match, months, grouped=True)
    for match in MONTH_RANGE.finditer(value):
        first, last = int(match['first']), int(match['last'])
        if first <= last:
            add(match, range(first, last + 1), grouped=True)
        else:
            # Invalid/backwards range must not leak its final month through
            # the generic MM/YYYY recognizer.
            claimed.append(match.span())
    for pattern in (LABELED, COMPACT):
        for match in pattern.finditer(value):
            add(match, [int(match['month'])])
    for match in UNLABELED.finditer(value):
        add(match, [int(match['month'])], labeled=False)

    periods.sort(key=lambda period: (period[3:], period[:2]))
    return {'payment_period': ', '.join(periods), 'payment_periods': periods,
            'payment_period_evidence': {'version': PAYMENT_PERIOD_VERSION,
                                       'status': 'identified' if periods else 'not_found', 'matches': matches}}


def with_payment_period(record: dict) -> dict:
    """Decorate old working files on read; never backfill PostgreSQL."""
    if 'payment_periods' in record and record.get('payment_period_evidence', {}).get('version') == PAYMENT_PERIOD_VERSION:
        return record
    return {**record, **payment_period_fields(record.get('raw', ''))}
