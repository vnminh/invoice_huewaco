"""Shared layout keys and payment routes; never guess customers from a provider."""
import hashlib
import re

from .normalize import fold

PAYMENT_MODES = ('unknown', 'proxy', 'self')
PROVIDER_KINDS = ('unknown', 'bank', 'wallet', 'other')


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


def shared_key(shape, structure):
    return hashlib.sha256((shape + '\x1f' + structure).encode('utf-8')).hexdigest()


def payment_hint(raw):
    protocol = bool(re.search(r'\bo@l_(?:\d+_){5}\d{6}_', fold(raw)))
    return {'suggested_mode': 'proxy' if protocol else 'unknown',
            'reason_vi': 'Nội dung có dấu hiệu dùng dịch vụ thanh toán của ngân hàng; cần kiểm tra nguồn hoặc xác nhận kiểu thanh toán.'
                if protocol else 'Chưa có nguồn hoặc dấu hiệu đủ rõ để phân biệt tự trả và thu hộ.'}


def payment_route(raw, payment_mode='', provider_kind='', provider_name=''):
    mode = str(payment_mode or '').strip().lower()
    kind = str(provider_kind or '').strip().lower()
    name = str(provider_name or '').strip()
    if mode and mode not in PAYMENT_MODES or kind and kind not in PROVIDER_KINDS:
        raise ValueError('Kiểu thanh toán/đơn vị thu hộ chưa hợp lệ.')
    # A recognizable protocol is a hint, not an authoritative transaction category.
    if not mode:
        mode = 'unknown'
    if mode == 'self':
        kind, name = 'unknown', ''
    return {'payment_mode': mode, 'provider_kind': kind or 'unknown', 'provider_name': name}
