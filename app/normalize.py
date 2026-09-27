from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata

NORMALIZER_VERSION = 'rules-v5-ordered-contracts'


def fold(text: str) -> str:
    text = unicodedata.normalize('NFKD', str(text)).lower().replace('đ', 'd')
    return ''.join(c for c in text if not unicodedata.combining(c))


# Dates need date/month syntax. A six-digit identifier by itself is never a date.
DATE = re.compile(
    r'(?<![a-z\d])(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:20\d{2}|\d{2})'
    r'(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?(?![a-z\d])'
    r'|(?<![a-z\d])(?:0?[1-9]|1[0-2])[/.-]20\d{2}(?![a-z\d])'
    r'|\b(?:thang|ky|month)\s*[:.]?\s*(?:0?[1-9]|1[0-2])(?:\s+(?:nam\s+)?20\d{2}|[/.-](?:20\d{2}|\d{2})|20\d{2})?\b'
    r'|\bt\s*(?:0?[1-9]|1[0-2])(?:\s+20\d{2}|[/.-](?:20\d{2}|\d{2}))?\b'
    r'|\b(?:thang|ky|month)\s*(?:0[1-9]|1[0-2])20\d{2}\b', re.I)
# Keep digit-leading bank codes and all letter/number runs intact.
TOKEN = re.compile(r'\d+(?:[.,]\d{3})+(?![a-z\d])|[a-z\d]+')
CUSTOMER_CONTEXT = re.compile(r'(?:idkh|mkh|mk|ma\s*(?:kh|khach\s*hang|danh\s*bo)|danh\s*bo|kh|customer|id)\s*[:#=]?\s*$')
CONTRACT_CONTEXT = re.compile(r'(?:hd|hop\s*dong)\s*(?:so)?\s*[:#=]?\s*$')
INVOICE_CONTEXT = re.compile(r'(?:hoa\s*don|inv(?:oice)?)\s*(?:so)?\s*[:#=]?\s*$')
REFERENCE_CONTEXT = re.compile(r'(?:ref|reference|ma\s*gd)\s*[:#=/]?\s*$')
CARD_CONTEXT = re.compile(r'(?:tkthe|tk\s*the|the)\s*[:#=]?\s*$')
ACCOUNT_CONTEXT = re.compile(r'(?:ac|tk|stk|tai\s*khoan|account)\s*[:#=]?\s*$')
AMOUNT_CONTEXT = re.compile(r'(?:so\s*tien|amount|vnd|tien)\s*[:=]?\s*$')
RECEIVER_ACCOUNTS = {'5510000370', '55110000000370'}
TRUSTED_IDENTIFIERS = {'CUSTOMER_ID', 'CONTRACT_ID'}
PAYER_IDENTIFIERS = {'ACCOUNT_ID', 'CARD_ID'}


@dataclass
class NormalizedTransaction:
    raw: str
    normalized: str
    template: str
    segments: list[dict]
    structure: str
    numbers: list[dict]
    customer_ids: list[str]
    payer_accounts: list[str]
    extraction: dict | None = None
    contract_ids: list[str] = field(default_factory=list)
    mixed_codes: list[str] = field(default_factory=list)
    payer_cards: list[str] = field(default_factory=list)
    semantic_text: str = ''

    def dict(self):
        return asdict(self)


@dataclass
class _Token:
    value: str
    start_offset: int
    end_offset: int

    def group(self): return self.value
    def start(self): return self.start_offset
    def end(self): return self.end_offset
    def span(self): return (self.start_offset, self.end_offset)


def _source_offsets(raw):
    """Map folded character offsets back to original accented text for semantic input."""
    offsets = []
    for offset, char in enumerate(raw):
        offsets.extend([offset] * len(fold(char)))
    return offsets + [len(raw)]


def normalize_text(raw: str) -> NormalizedTransaction:
    lowered = fold(raw)
    offsets = _source_offsets(raw)
    protected = []
    contract_tokens = []
    for m in re.finditer(r'\b(?:hd|hop\s*dong)\s*(?:so)?\s*[:#=]?\s*([a-z\d]+(?:[-/][a-z\d]+)*)', lowered):
        value = re.split(r'-(?:020097|ctlnhidi)', m[1])[0]
        if any(char.isdigit() for char in value):
            end = m.start(1) + len(value)
            contract_tokens.append(_Token(value, m.start(1), end))
            protected.append((m.start(1), end))
    for m in re.finditer(r'(?:idkh|mkh|ma\s*(?:kh|khach\s*hang)|(?:hd|hop\s*dong)\s*(?:so)?)\s*[:#=]?\s*([a-z\d]+)', lowered):
        protected.append(m.span(1))
    dates = [m for m in DATE.finditer(lowered) if not any(a <= m.start() < b for a, b in protected)]
    # Partial date ranges only when a clear day/range context is present.
    occupied = [m.span() for m in dates]
    for m in re.finditer(r'\b(?:tu|den|ngay)\s+((?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2]))(?![/\d])', lowered):
        if not any(a < m.end(1) and b > m.start(1) for a, b in occupied):
            dates.append(m)
    date_spans = sorted((m.start(1), m.end(1)) if m.lastindex else m.span() for m in dates)
    masked = list(lowered)
    for start, end in date_spans:
        masked[start:end] = ' ' * (end - start)
    masked = ''.join(masked)
    roles = {}
    roles.update({m.span(): 'CONTRACT_ID' for m in contract_tokens})
    invoice_codes = set()
    # BIDV O@L: protocol, reference, then IDKH. The invoice code may be absent.
    for m in re.finditer(r'o@l_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d{6})(?=_)', lowered):
        for i in range(1, 5):
            roles[m.span(i)] = 'BANK_PROTOCOL'
        roles[m.span(5)] = 'BANK_REFERENCE'
        roles[m.span(6)] = 'CUSTOMER_ID'
        following = re.match(r'_([a-z]{1,4}\d{6,})(?=_)', lowered[m.end(6):])
        if following:
            invoice_codes.add(following[1])
        # The same BIDV code/ID/HD suffix is present even when the optional
        # earlier copy of the invoice code is omitted. Validate both explicit
        # ID repetitions against this transaction's protocol ID, never FN truth.
        for suffix in re.finditer(r'([a-z]{1,4}\d{6,})[_-](\d{6})[_-]hd\s*:\s*(\d{6})\b', lowered[m.end(6):]):
            if suffix[2] == m[6] and suffix[3] == m[6]:
                invoice_codes.add(suffix[1])
                start = m.end(6) + suffix.start(2)
                roles[(start, start + len(suffix[2]))] = 'CUSTOMER_ID'
    for m in re.finditer(r'kbnnttsp_(kba\d{12,})\b', lowered):
        roles[m.span(1)] = 'BANK_REFERENCE'
    for m in re.finditer(r'(?:idkh|mkh|ma\s*(?:kh|khach\s*hang))\s*[:#=]?\s*\d{4,6}((?:\s*(?:va|&|,)\s*\d{6})+)', lowered):
        for continuation in re.finditer(r'\d{6}', m[1]):
            start = m.start(1) + continuation.start()
            roles[(start, start + 6)] = 'CUSTOMER_ID'
    for expression in (r'(?<=rem\s)[a-z\d]+', r'dtls-ref/([a-z\d]+)',
                       r'-((?:020097|ctlnhidi)[a-z\d]+)', r'@@(\d+)@@'):
        for m in re.finditer(expression, lowered):
            roles[m.span(1) if m.lastindex else m.span()] = 'BANK_REFERENCE'
    for m in re.finditer(r'mbvcb\.(\d+)\.(\d+)\.', lowered):
        roles[m.span(1)] = roles[m.span(2)] = 'BANK_REFERENCE'
    segments, numbers, text_group = [], [], []
    text_start = 0
    slot = 0

    def flush():
        if text_group:
            segments.append({'type': 'text', 'value': ' '.join(text_group),
                             'position': len(segments), 'offset': text_start})
            text_group.clear()

    events = [(a, 'date', (a, b)) for a, b in date_spans]
    for m in TOKEN.finditer(masked):
        if any(a < m.end() and b > m.start() for a, b in (t.span() for t in contract_tokens)):
            continue
        prefix = masked[max(0, m.start() - 48):m.start()]
        glued = re.fullmatch(r'(\d{1,2}|\d{12,})(thanh|chuyen|chi|tra|tien|truong|tram|tt)', m.group())
        # Bank exports sometimes glue a branch number/reference to the following
        # Vietnamese verb. Split only these known words in narrative context;
        # explicit card/customer/contract/reference identifiers remain atomic.
        labeled = any(regex.search(prefix) for regex in (CARD_CONTEXT, ACCOUNT_CONTEXT, CUSTOMER_CONTEXT, CONTRACT_CONTEXT, INVOICE_CONTEXT, REFERENCE_CONTEXT))
        if glued and not labeled:
            end = m.start() + len(glued[1])
            token = _Token(glued[1], m.start(), end)
            roles[token.span()] = 'BANK_REFERENCE' if len(glued[1]) >= 12 else 'LOCATION_NUMBER'
            events.append((m.start(), 'token', token))
            events.append((end, 'token', _Token(glued[2], end, m.end())))
        else:
            events.append((m.start(), 'token', m))
    events += [(m.start(), 'token', m) for m in contract_tokens]
    for _, kind, match in sorted(events, key=lambda item: item[0]):
        if kind == 'date':
            flush()
            start, end = match
            segments.append({'type': 'date', 'numeric_type': 'DATE', 'value': lowered[start:end],
                             'offset': start, 'end': end, 'position': len(segments)})
            continue
        value = match.group()
        if value.isalpha():
            if not text_group:
                text_start = match.start()
            text_group.append(value)
            continue
        flush()
        slot += 1
        prefix = masked[max(0, match.start() - 48):match.start()]
        numeric_type = roles.get(match.span(), 'UNKNOWN_NUMBER')
        original_value = value
        embedded_id = re.fullmatch(r'(?:mkh|mk|idkh|kh)(\d{4,6})', value)
        if numeric_type == 'UNKNOWN_NUMBER':
            if CONTRACT_CONTEXT.search(prefix):
                numeric_type = 'CONTRACT_ID'
            elif CARD_CONTEXT.search(prefix):
                numeric_type = 'CARD_ID'
            elif ACCOUNT_CONTEXT.search(prefix):
                numeric_type = 'ACCOUNT_ID'
            elif embedded_id:
                value = embedded_id[1]
                numeric_type = 'CUSTOMER_ID'
            elif CUSTOMER_CONTEXT.search(prefix):
                numeric_type = 'CUSTOMER_ID'
            elif INVOICE_CONTEXT.search(prefix):
                numeric_type = 'INVOICE_ID'
            elif REFERENCE_CONTEXT.search(prefix):
                numeric_type = 'REFERENCE_ID'
            elif AMOUNT_CONTEXT.search(prefix) or re.search(r'^\s*(?:vnd|dong)\b', masked[match.end():]):
                numeric_type = 'AMOUNT'
            elif value in invoice_codes:
                numeric_type = 'INVOICE_CODE'
            elif any(c.isalpha() for c in value):
                numeric_type = 'QUANTITY' if re.fullmatch(r'\d+m3', value) and re.search(r'(?:tieu\s*thu|luong|su\s*dung)\s*$', prefix) else 'MIXED_CODE'
        if value in RECEIVER_ACCOUNTS:
            numeric_type = 'RECEIVER_ACCOUNT'
        anchor = ' '.join(re.findall(r'[a-z]+', prefix)[-3:])
        code_format = re.sub(r'\d+', '#', original_value) if any(c.isalpha() for c in original_value) else '#'
        feature = {'type': 'number', 'numeric_type': numeric_type, 'slot': slot,
                   'value': value, 'raw_value': raw[offsets[match.start()]:offsets[match.end()]], 'format': code_format, 'anchor': anchor,
                   'position': len(segments), 'offset': match.start(), 'end': match.end()}
        numbers.append(feature)
        segments.append(feature.copy())
    flush()
    active = [s for s in segments if s['type'] != 'date']
    normalized = ' '.join(s['value'] for s in active)
    template = ' '.join(s['value'] if s['type'] == 'text' else
                        (s['format'].replace('#', f'<NUM_{s["slot"]}>') if s['format'] != '#' else f'<NUM_{s["slot"]}>')
                        for s in active)
    # Semantic encoder gets original Vietnamese, with dates and numeric values replaced.
    replacements = [(a, b, ' ') for a, b in date_spans]
    replacements += [(n['offset'], n['end'], ' ' + n['numeric_type'].lower().replace('_', ' ') + ' ') for n in numbers]
    semantic = raw
    for start, end, replacement in sorted(replacements, reverse=True):
        semantic = semantic[:offsets[start]] + replacement + semantic[offsets[end]:]
    return NormalizedTransaction(raw, normalized, template, segments,
        ' '.join('T' if s['type'] == 'text' else s['numeric_type'] + ':' + s['format'] for s in active), numbers,
        sorted({n['value'] for n in numbers if n['numeric_type'] == 'CUSTOMER_ID'}),
        sorted({n['value'] for n in numbers if n['numeric_type'] in PAYER_IDENTIFIERS}),
        {'provider': 'rules', 'status': 'explicit_id' if any(n['numeric_type'] == 'CUSTOMER_ID' for n in numbers) else 'rules', 'entities': []},
        sorted({n['value'] for n in numbers if n['numeric_type'] == 'CONTRACT_ID'}),
        sorted({n['value'] for n in numbers if n['numeric_type'] in {'MIXED_CODE', 'INVOICE_CODE'}}),
        sorted({n['value'] for n in numbers if n['numeric_type'] == 'CARD_ID'}), ' '.join(semantic.split()))


class RuleExtractor:
    name = NORMALIZER_VERSION

    def augment(self, norm):
        return norm


def identity_signature(raw: str) -> str:
    """Names/locations retain branch numbers. Changing bank/bill references are excluded."""
    value = fold(raw)
    value = re.sub(r'-\s*(?:020097\w+|ctlnhidi\S+).*$', '', value)
    value = re.sub(r'^tkthe\s*:[^.,]+(?:,?\s*tai\s+[^.]+)\.', '', value)
    value = re.sub(r'^rem\s+\S+\s+b/o\s+', '', value)
    value = re.sub(r'\s+f/o[- ]+\d+\s+.*?dtls-ref/\S+\s*', ' ', value)
    value = re.sub(r'bank charge.*$', '', value)
    value = re.sub(r'^@@\d+@@_chiho_', '', value)
    value = DATE.sub(' ', value)
    # HD means hợp đồng in this domain; only explicit hóa đơn/invoice is a bill.
    value = re.sub(r'(?:hoa\s*don|inv)\s*(?:so)?\s*[:.]?\s*\d{6,}', ' invoice ', value)
    value = re.sub(r'\b(?:cong ty co phan cap nuoc hue|ctcp cap nuoc hue|ctcp cap nuoc|ct cp cap nuoc hue)\b', ' ', value)
    for account in RECEIVER_ACCOUNTS:
        value = value.replace(account, ' ')
    return ' '.join(re.findall(r'[a-z]+\d*|\d+', value))


def branch_markers(raw: str) -> set[str]:
    markers = set()
    for m in re.finditer(r'\b(cs|co\s*so|kv|khu\s*vuc|truong\s*(?:th|tieu\s*hoc)\s*so)\s*(\d+)\b', fold(raw)):
        prefix = 'cs' if m[1] in ('cs', 'co so') else 'kv' if m[1] in ('kv', 'khu vuc') else 'school'
        markers.add(prefix + ':' + m[2])
    return markers


def build_template(raw: str):
    return normalize_text(raw).template


def segment_transaction(raw: str):
    return normalize_text(raw).segments
