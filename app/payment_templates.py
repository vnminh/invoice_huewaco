"""Shared layout keys and payment routes; never guess customers from a provider."""
import hashlib
import re

from .text import fold
from .identifiers import channel_key

PAYMENT_MODES = ('unknown', 'proxy', 'self')
PROVIDER_KINDS = ('unknown', 'bank', 'wallet', 'other')
# Collection channels named in the confirmed learning files ("Ngân hàng" column). These
# services collect from many customers and settle to the water company on their behalf.
COLLECTION_CHANNELS = {'momo': 'wallet', 'zalopay': 'wallet', 'viettelpay': 'wallet', 'shopee': 'wallet',
                       'shopeepay': 'wallet', 'vnpay': 'wallet', 'payoo': 'wallet', 'vnpt': 'other',
                       'buu dien': 'other', 'napas': 'other'}


def collection_channel(payer):
    """Provider kind for a confirmed collection channel label, or None for banks/unknown."""
    return COLLECTION_CHANNELS.get(' '.join(re.findall(r'[a-z]+', fold(payer or ''))))


def unique_transfer_reference(norm):
    """True when the narrative carries a per-transfer reference, so identical text means one transfer.

    Generic texts such as "thanh toan tien nuoc thang 7/2026" repeat across separate payers and
    must not be mistaken for one transfer split across many customers.
    """
    return any(n['numeric_type'] == 'BANK_REFERENCE' or
               (n['numeric_type'] not in ('RECEIVER_ACCOUNT', 'ACCOUNT_ID', 'CARD_ID', 'AMOUNT')
                and sum(c.isdigit() for c in n['value']) >= 8)
               for n in norm.numbers)


def shared_shape(norm, names=()):
    shape = norm.template
    # Only mask validated names; ordered number placeholders remain untouched.
    for name in sorted({str(name) for name in names if name}, key=len, reverse=True):
        words = re.findall(r'[a-z]+', fold(name))
        if len(words) < 2 or any(char.isdigit() for char in name):
            continue
        expression = r'(?<![a-z])' + r'\s+'.join(re.escape(word) for word in words) + r'(?![a-z])'
        shape = re.sub(expression, '<NAME>', shape)
    return shape


def shared_key(shape, structure, channel='unknown'):
    return hashlib.sha256((channel_key(channel) + '\x1f' + shape + '\x1f' + structure).encode('utf-8')).hexdigest()


def payment_hint(raw):
    value = fold(raw)
    protocol = bool(re.search(r'\bo@l_(?:\d+_){5}\d{6}_', value))
    fixed = protocol or bool(re.search(
        r'ma_gd:\d+\|\d{6},\s*nuoc|atbp:[a-z\d]+\s+tthd\s+nuoc\s+hue'
        r'|tthd\s+tien\s+nuoc\s+ma\s+kh-|tthd\s+nuoc\s+hue\s*,'
        r'|\but\s+\d{6}\s+t\d{1,2}n20\d{2}\s+cap\s+nuoc\s+hue'
        r'|^mb\.\d+-\d+-\d{8}-\d{6}-\d+\.\d{8}\.hue water supply jsc\.', value))
    return {'suggested_mode': 'proxy' if fixed else 'unknown', 'fixed_collection_layout': fixed,
            'reason_vi': 'Nội dung theo bố cục thu hộ của ngân hàng; có thể chỉnh lại kiểu thanh toán khi duyệt.'
                if fixed else 'Chưa có nguồn hoặc dấu hiệu đủ rõ để phân biệt tự trả và thu hộ.'}


def payment_route(raw, payment_mode='', provider_kind='', provider_name=''):
    mode = ' '.join(re.findall(r'[a-z]+', fold(payment_mode or '')))
    kind = ' '.join(re.findall(r'[a-z]+', fold(provider_kind or '')))
    name = str(provider_name or '').strip()
    # Human-reviewed spreadsheets may use the same Vietnamese labels as the UI.
    if any(term in mode for term in ('thu ho', 'thuho', 'vi dien tu', 'tra qua vi')):
        if 'ngan hang' in mode:
            kind = kind or 'bank'
        elif 'vi' in mode:
            kind = kind or 'wallet'
        mode = 'proxy'
    elif any(term in mode for term in ('tu tra', 'tu chuyen', 'tu thanh toan')):
        mode = 'self'
    elif mode in ('chua xac dinh', 'khong ro'):
        mode = 'unknown'
    kind = {'ngan hang': 'bank', 'vi dien tu': 'wallet', 'dich vu khac': 'other',
            'khac': 'other', 'chua xac dinh': 'unknown'}.get(kind, kind)
    # An unfamiliar optional spreadsheet label cannot discard an approved customer.
    # API forms validate their enum values separately.
    if mode not in PAYMENT_MODES:
        mode = 'unknown'
    if kind not in PROVIDER_KINDS:
        kind = 'unknown'
    # A recognizable protocol is a hint, not an authoritative transaction category.
    if not mode:
        mode = 'unknown'
    if mode == 'self':
        kind, name = 'unknown', ''
    return {'payment_mode': mode, 'provider_kind': kind or 'unknown', 'provider_name': name}
