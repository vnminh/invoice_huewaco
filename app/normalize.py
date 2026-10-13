from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import re
from .text import fold
from .identifiers import CUSTOMER_ID
from .bank_content import mb_water_fields
from .payment_period import scan_periods

NORMALIZER_VERSION = 'rules-v7-confirmed-evidence'


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
CUSTOMER_LABEL = (r'(?:ma[\s_]*(?:so[\s_]*)?(?:khach[\s_]*hang|kh|danh[\s_]*bo)'
                  r'|id[\s_]*kh|m[\s_]*(?:s[\s_]*)?kh|mk|(?:ma[\s_]*|so[\s_]*)?danh[\s_]*bo|msdb|khach[\s_]*hang|kh'
                  r'|customer(?:[\s_]*(?:id|code|number))?|id)')
IDENTIFIER_SEPARATOR = r'[\s:#=._\-\u2010-\u2015]*'
CUSTOMER_CONTEXT = re.compile(r'(?<![a-z\d])' + CUSTOMER_LABEL + IDENTIFIER_SEPARATOR + r'$')
CUSTOMER_FIELD = re.compile(r'(?<![a-z\d])' + CUSTOMER_LABEL + IDENTIFIER_SEPARATOR + r'(\d{6})(?![a-z\d]|[.,]\d{3}(?!\d))')
CUSTOMER_CONTINUATION = re.compile(r'\s*(?:[,;/+&\-\u2010-\u2015]|va\b|and\b)\s*(\d{6})(?![a-z\d]|\.\d)')
# "ID: 035285 035447 046165", "MA KH273426 282974", "mkh. 309876. 279225.": a list of
# customer-ID-sized values after one label. Only separate six-digit runs continue it,
# so a following date, amount, long reference or invoice number never joins the list.
CUSTOMER_SPACE_CONTINUATION = re.compile(r'(?:\s+|\s*\.\s*)(\d{6})(?![a-z\d]|[./-]\d)')
CONTEXT_UNIT = re.compile(r'[a-z\d]+|[^\sa-z\d]')
# Learned customer-ID slots only consider customer-ID-sized digit runs.
SLOT_VALUE = CUSTOMER_ID
SLOT_TYPES = {'UNKNOWN_NUMBER', 'CUSTOMER_ID', 'CONTRACT_ID', 'INVOICE_ID', 'REFERENCE_ID', 'LOCATION_NUMBER'}


def slot_context(lowered, start, end):
    """Name-agnostic local layout around a number: two units before, one after, digit length.

    Units are words, single punctuation marks, ``N<length>`` for another number or ``M`` for a
    letter/digit code, so ``<invoice code>-<ID>-`` and ``<year>-<date>-`` stay different layouts.
    Bank layouts such as ``JSC.<ID>.`` or ``MA_GD:<ref>|<ID>,`` are recognised without payer names.
    """
    unit = lambda u: 'N' + str(len(u)) if u.isdigit() else 'M' if any(c.isdigit() for c in u) else u
    left = [unit(u) for u in CONTEXT_UNIT.findall(lowered[max(0, start - 40):start])][-2:]
    right = [unit(u) for u in CONTEXT_UNIT.findall(lowered[end:end + 20])][:1]
    return ' '.join(left) + ' _ ' + ' '.join(right) + ' #' + str(end - start)
CONTRACT_LABEL = r'(?:(?:ma[\s_]*)?(?:hd|hop[\s_]*dong))[\s_]*(?:so)?'
CONTRACT_CONTEXT = re.compile(r'(?<![a-z\d])' + CONTRACT_LABEL + IDENTIFIER_SEPARATOR + r'$')
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
    customer_spans = []
    for match in CUSTOMER_FIELD.finditer(lowered):
        customer_spans.append(match.span(1))
        end = match.end(1)
        # Only explicit list separators extend an ID label. Preserve each digit
        # run in full; unlabeled following numbers do not extend the list.
        width = len(match[1])
        while True:
            continuation = CUSTOMER_CONTINUATION.match(lowered, end)
            if not continuation and width == 6:
                continuation = CUSTOMER_SPACE_CONTINUATION.match(lowered, end)
                if continuation and len(continuation[1]) != width:
                    continuation = None
            if not continuation:
                break
            customer_spans.append(continuation.span(1))
            end = continuation.end(1)
    protected.extend(customer_spans)
    # One known export glues a bank suffix directly to a labeled customer ID.
    # Split at the explicit bank marker, never by guessing lengths in other codes.
    for m in re.finditer(r'(?<![a-z\d])' + CUSTOMER_LABEL + IDENTIFIER_SEPARATOR + r'(\d{6})(?=kbnnttsp_)', lowered):
        customer_spans.append(m.span(1))
        protected.append(m.span(1))
    contract_tokens = []
    for m in re.finditer(r'(?<![a-z\d])' + CONTRACT_LABEL + IDENTIFIER_SEPARATOR + r'([a-z\d]+(?:[-/._][a-z\d]+)*)', lowered):
        if re.match(r'(?:ky|thang|month)\d', m[1]) and DATE.match(lowered, m.start(1)):
            continue  # e.g. "Ma HD-Ky082026" gives a billing period, not a contract ID.
        value = re.split(r'-(?:020097|ctlnhidi)', m[1])[0]
        if any(char.isdigit() for char in value):
            end = m.start(1) + len(value)
            contract_tokens.append(_Token(value, m.start(1), end))
            protected.append((m.start(1), end))
    periods = scan_periods(raw)
    spans = periods['billing_spans'] + periods['date_spans']
    spans += [m.span() for m in DATE.finditer(lowered)]
    date_spans = sorted(set((a, b) for a, b in spans if not any(a < y and b > x for x, y in protected)))
    # Partial date ranges only when a clear day/range context is present.
    occupied = list(date_spans)
    for m in re.finditer(r'\b(?:tu|den|ngay)\s+((?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2]))(?![/\d])', lowered):
        if not any(a < m.end(1) and b > m.start(1) for a, b in occupied):
            date_spans.append(m.span(1))
    # Union overlapping masks: a month list/range must not produce duplicate events.
    merged = []
    for a, b in sorted(date_spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    date_spans = merged
    masked = list(lowered)
    for start, end in date_spans:
        masked[start:end] = ' ' * (end - start)
    masked = ''.join(masked)
    roles = {span: 'CUSTOMER_ID' for span in customer_spans}
    roles.update({m.span(): 'CONTRACT_ID' for m in contract_tokens})
    bank_fields = mb_water_fields(raw)
    if bank_fields:
        to_folded = lambda span: (len(fold(raw[:span[0]])), len(fold(raw[:span[1]])))
        customer_span = to_folded(bank_fields['customer_span'])
        roles[customer_span] = 'CUSTOMER_ID'
        roles[to_folded(bank_fields['reference_span'])] = 'BANK_REFERENCE'
        protocol_end = to_folded(bank_fields['protocol_span'])[1]
        for m in TOKEN.finditer(masked[:protocol_end]):
            if any(c.isdigit() for c in m.group()):
                roles[m.span()] = 'BANK_PROTOCOL'
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
    for expression in (r'(?<=rem\s)[a-z\d]+', r'dtls-ref/([a-z\d]+)',
                       r'-((?:020097|ctlnhidi)[a-z\d]+)', r'@@(\d+)@@'):
        for m in re.finditer(expression, lowered):
            roles[m.span(1) if m.lastindex else m.span()] = 'BANK_REFERENCE'
    for m in re.finditer(r'mbvcb\.(\d+)\.(\d+)\.', lowered):
        roles[m.span(1)] = roles[m.span(2)] = 'BANK_REFERENCE'
    # Agribank's collection fields repeat the six-digit customer code before the
    # period; trailing bracket fields are bill amounts/references, not other IDs.
    for m in re.finditer(r'ma_gd:(\d+)\|(\d{6}),\s*nuoc', lowered):
        roles[m.span(1)] = 'BANK_REFERENCE'
        roles[m.span(2)] = 'CUSTOMER_ID'
        for repeated in re.finditer(r'\[(\d{6})\s+', lowered[m.end():]):
            if repeated[1] == m[2]:
                a, b = repeated.span(1)
                roles[(m.end() + a, m.end() + b)] = 'CUSTOMER_ID'
    for expression in (r'atbp:([a-z\d]+)', r'vcbcsh\.\s*(\d+)', r'\[bpment-ft-(\d+)\]'):
        for m in re.finditer(expression, lowered):
            roles[m.span(1)] = 'BANK_REFERENCE'
    for expression in (r'\batbp:[a-z\d]+\s+tthd\s+nuoc\s+hue\s+(\d{6})(?!\d)',
                       r'\but\s+(\d{6})\s+t\d{1,2}n20\d{2}\s+cap\s+nuoc\s+hue',
                       r'\btien\s+nuoc\s+ma\s*(\d{6})(?![a-z\d])'):
        for m in re.finditer(expression, lowered):
            roles[m.span(1)] = 'CUSTOMER_ID'
    segments, numbers, text_group = [], [], []
    text_start = 0
    slot = 0

    def flush():
        if text_group:
            segments.append({'type': 'text', 'value': ' '.join(text_group),
                             'position': len(segments), 'offset': text_start})
            text_group.clear()

    events = [(a, 'date', (a, b)) for a, b in date_spans]
    tokens = []
    for match in TOKEN.finditer(masked):
        labeled_numbers = [span for span in customer_spans if match.start() <= span[0] and span[1] <= match.end()]
        if len(labeled_numbers) > 1:
            # TOKEN also recognizes thousands separators in amounts. An explicit
            # customer list such as KH:123,456 must keep its two values separate.
            tokens.extend(_Token(part.group(), match.start() + part.start(), match.start() + part.end())
                          for part in re.finditer(r'[a-z\d]+', match.group()))
        elif (len(labeled_numbers) == 1 and labeled_numbers[0][1] == match.end()
              and match.start() < labeled_numbers[0][0]
              and lowered[match.start():labeled_numbers[0][0]].isalpha()):
            a, b = labeled_numbers[0]
            tokens.extend([_Token(lowered[match.start():a], match.start(), a), _Token(lowered[a:b], a, b)])
        elif len(labeled_numbers) == 1 and match.group().endswith('kbnnttsp'):
            a, b = labeled_numbers[0]
            tokens.extend([_Token(lowered[a:b], a, b), _Token(lowered[b:match.end()], b, match.end())])
        else:
            tokens.append(match)
    for m in tokens:
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
        embedded_id = re.fullmatch(r'(?:makhachhang|makh|mskh|mkh|mk|idkh|id|kh)(\d{6})', value)
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
            elif CUSTOMER_CONTEXT.search(prefix) and CUSTOMER_ID.fullmatch(value):
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
        if numeric_type == 'CUSTOMER_ID':
            feature['customer_id_source'] = 'bank_field' if bank_fields and match.span() == customer_span else 'explicit_label'
        elif numeric_type == 'CONTRACT_ID' and CUSTOMER_ID.fullmatch(value):
            # HD can mean the customer code in a collection layout. Keep it a
            # contract until confirmed examples establish this particular position.
            feature['possible_customer_id'] = True
        if SLOT_VALUE.fullmatch(value) and numeric_type in SLOT_TYPES:
            start = match.start() + (original_value.find(value) if value != original_value else 0)
            feature['context'] = slot_context(lowered, start, start + len(value))
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
        list(dict.fromkeys(n['value'] for n in numbers if n['numeric_type'] == 'CUSTOMER_ID')),
        sorted({n['value'] for n in numbers if n['numeric_type'] in PAYER_IDENTIFIERS}),
        {'provider': 'rules', 'status': 'explicit_id' if any(n['numeric_type'] == 'CUSTOMER_ID' for n in numbers) else 'rules', 'entities': []},
        sorted({n['value'] for n in numbers if n['numeric_type'] == 'CONTRACT_ID'}),
        sorted({n['value'] for n in numbers if n['numeric_type'] in {'MIXED_CODE', 'INVOICE_CODE'}}),
        sorted({n['value'] for n in numbers if n['numeric_type'] == 'CARD_ID'}), ' '.join(semantic.split()))


class RuleExtractor:
    name = NORMALIZER_VERSION

    def augment(self, norm):
        return norm


@lru_cache(maxsize=4096)
def identity_signature(raw: str) -> str:
    """Names/locations retain branch numbers. Changing bank/bill references are excluded."""
    period_fields = scan_periods(raw)
    # Strip periods before editing the narrative, preserving distinct branch numbers.
    original = fold(raw)
    chars = list(original)
    for start, end in period_fields['billing_spans'] + period_fields['date_spans']:
        chars[start:end] = ' ' * (end - start)
    value = ''.join(chars)
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
