"""Billing-period evidence shared with learning's date masking; no inferred years."""
from __future__ import annotations

import re

from .text import fold
from .bank_content import mb_water_fields

PAYMENT_PERIOD_VERSION = 4
MONTH = r'(?:0?[1-9]|1[0-2])'
YEAR = r'(?:20\d{2}|\d{2})'
LABEL = (r'(?<![a-z\d])(?:ky(?:\s*(?:thanh\s*toan|tien\s*nuoc|hoa\s*don))?(?:\s*thang)?'
         r'|thang|month|t)\s*[:=._-]?\s*')
SEP = r'(?:\s*[/._-]\s*|\s*(?:nam|n)\s*|\s+)'
END = r'(?![a-z\d]|[/_-]\d)'
WATER_PAYMENT = re.compile(r'\b(?:tien\s*nuoc|nuoc|water|huewaco|tthd)\b|nuoc(?=\d)')
FULL_DATE = re.compile(
    r'(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:20\d{2}|\d{2})(?!\d)'
    r'|(?<!\d)20\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])(?!\d)'
    r'|(?<!\d)20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)')
IDENTIFIER = re.compile(
    r'(?<![a-z\d])(?:ma\s*(?:kh|khach\s*hang|hd|hop\s*dong)|hd|hop\s*dong|'
    r'id\s*kh|mkh|kh|id|tkthe|tk\s*the|stk|tk|ac|tai\s*khoan|ref|reference|ma\s*gd)'
    r'\s*(?:so\s*)?[:#=._-]*\s*(\d+[a-z\d]*(?:[/_-][a-z\d]+)*)')
TRANSFER_CONTEXT = re.compile(
    r'\b(?:ngay(?:\s+gio)?(?:\s+(?:gd|giao\s*dich|chuyen\s*(?:khoan|tien)|lap|in|tao))?'
    r'|thoi\s*(?:gian|diem)(?:\s+(?:gd|giao\s*dich|chuyen\s*(?:khoan|tien)))?'
    r'|timestamp|luc|(?:den\s+)?han(?:\s+(?:thanh\s+toan|nop))?)\s*[:=]?\s*(?:thang\s*)?$')
LABELED = re.compile(LABEL + rf'(?P<month>{MONTH}){SEP}(?P<year>{YEAR})' + END)
COMPACT = re.compile(LABEL + rf'(?P<month>{MONTH})(?P<year>20\d{{2}})' + END)
YEAR_FIRST = re.compile(LABEL + rf'(?P<year>20\d{{2}})[/._-]?(?P<month>0[1-9]|1[0-2])' + END)
MONTH_LIST = re.compile(LABEL + rf'(?P<months>{MONTH}(?:\s*(?:,|&|va|\+|/)\s*{MONTH})+)'
                        + SEP + rf'(?P<year>{YEAR})' + END)
MONTH_RANGE = re.compile(LABEL + rf'(?P<first>{MONTH})\s*(?:-|den|toi)\s*(?P<last>{MONTH})'
                         + SEP + rf'(?P<year>{YEAR})' + END)
FULL_RANGE = re.compile(LABEL + rf'(?P<first>{MONTH}){SEP}(?P<year>{YEAR})'
                       + r'\s*(?:den|toi|-)\s*' + rf'(?:{LABEL})?(?P<last>{MONTH})'
                       + SEP + rf'(?P<last_year>{YEAR})' + END)
UNLABELED = re.compile(rf'(?<![a-z\d_/-])(?P<month>{MONTH})\s*[/.-]\s*(?P<year>20\d{{2}})' + END)
GLUED_WATER = re.compile(rf'\bnuoc\s*(?P<month>{MONTH})\s*[/.-]\s*(?P<year>20\d{{2}})' + END)
MONTH_ONLY = re.compile(LABEL + rf'(?P<month>{MONTH})(?![a-z\d]|\s*[/._-]\s*\d|\s+nam\s*\d)')


def _overlaps(span, spans):
    return any(span[0] < end and span[1] > start for start, end in spans)


def _year(value):
    return int(value) + (2000 if len(value) == 2 else 0)


def scan_periods(raw):
    original, value = str(raw or ''), fold(raw or '')
    offsets = []
    for offset, char in enumerate(original):
        offsets.extend([offset] * len(fold(char)))
    offsets.append(len(original))
    # Ignore an appended clearing reference while preserving every source offset.
    # "tiền nước T6-020097..." contains a partial billing month, not a month range.
    value = re.sub(r'-(?:020097|ctlnhidi)[a-z\d/_.-]*', lambda m: ' ' * len(m.group()), value)
    protected = [match.span(1) for match in IDENTIFIER.finditer(value)]
    bank = mb_water_fields(original)
    if bank:
        a, b = bank['name_span']
        protected.append((len(fold(original[:a])), len(fold(original[:b]))))
    full_dates = [match.span() for match in FULL_DATE.finditer(value) if not _overlaps(match.span(), protected)]
    claimed, periods, matches, partial = [], [], [], []

    def save(span, found, source, confidence):
        claimed.append(span)
        for period in found:
            if period not in periods:
                periods.append(period)
        matches.append({'text': original[offsets[span[0]]:offsets[span[1]]], 'periods': found,
                        'source': source, 'confidence': confidence})

    if bank:
        month, year = bank['period'].split('/')
        a, b = bank['period_span']
        span = (len(fold(original[:a])), len(fold(original[:b])))
        save(span, [f'{int(month):02d}/{year}'], 'bank_field', 1.0)

    def add(match, months=(), *, grouped=False, labeled=True, found=None, source='billing_label'):
        span = match.span()
        prefix = value[max(0, span[0] - 90):span[0]]
        if (_overlaps(span, protected) or _overlaps(span, claimed) or TRANSFER_CONTEXT.search(prefix)
                or (not grouped and _overlaps(span, full_dates))):
            return
        if re.match(r'\s*(?:t\s*)?\d{1,2}:\d{2}', value[span[1]:]):
            return
        if not labeled and not WATER_PAYMENT.search(value):
            return
        found = found if found is not None else [f'{month:02d}/{_year(match["year"]):04d}' for month in months]
        save(span, found, source, .98 if labeled else .8)

    for match in FULL_RANGE.finditer(value):
        first, last = _year(match['year']) * 12 + int(match['first']) - 1, _year(match['last_year']) * 12 + int(match['last']) - 1
        if 0 <= last - first < 24:
            add(match, grouped=True, found=[f'{p % 12 + 1:02d}/{p // 12:04d}' for p in range(first, last + 1)])
        else:
            claimed.append(match.span())
    for match in MONTH_LIST.finditer(value):
        add(match, [int(m) for m in re.findall(r'\d+', match['months'])], grouped=True)
    for match in MONTH_RANGE.finditer(value):
        first, last = int(match['first']), int(match['last'])
        if first <= last:
            add(match, range(first, last + 1), grouped=True)
        else:
            claimed.append(match.span())
    for pattern in (LABELED, COMPACT, YEAR_FIRST):
        for match in pattern.finditer(value):
            add(match, [int(match['month'])])
    for pattern in (UNLABELED, GLUED_WATER):
        for match in pattern.finditer(value):
            add(match, [int(match['month'])], labeled=False, source='water_period')
    for match in MONTH_ONLY.finditer(value):
        if (_overlaps(match.span(), protected + claimed + full_dates)
                or TRANSFER_CONTEXT.search(value[max(0, match.start() - 90):match.start()])):
            continue
        month = int(match['month'])
        claimed.append(match.span())
        if month not in partial:
            partial.append(month)
        matches.append({'text': original[offsets[match.start()]:offsets[match.end()]], 'periods': [],
                        'month': month, 'source': 'month_only', 'confidence': .55})
    periods.sort(key=lambda p: (p[3:], p[:2]))
    partial = [month for month in partial if not any(int(period[:2]) == month for period in periods)]
    display = ', '.join(periods)
    if partial:
        display += ('; ' if display else '') + ', '.join(f'Tháng {month:02d} (chưa rõ năm)' for month in sorted(partial))
    return {'payment_period': display, 'payment_periods': periods,
            'payment_period_evidence': {'version': PAYMENT_PERIOD_VERSION,
                'status': 'identified' if periods else 'partial' if partial else 'not_found',
                'matches': matches, 'partial_months': sorted(partial)},
            'billing_spans': sorted(claimed), 'date_spans': sorted(full_dates)}


def payment_period_fields(raw):
    return {key: value for key, value in scan_periods(raw).items() if key not in ('billing_spans', 'date_spans')}


def with_payment_period(record):
    if 'payment_periods' in record and record.get('payment_period_evidence', {}).get('version') == PAYMENT_PERIOD_VERSION:
        return record
    return {**record, **payment_period_fields(record.get('raw', ''))}
