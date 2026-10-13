"""Company customer IDs are six digits. Never slice an arbitrary numeric run."""
import re

from .text import fold

CUSTOMER_ID = re.compile(r'[0-9]{6}')


def confirmed_customer_id(value):
    """Restore Excel's lost leading zeroes only in an explicitly confirmed ID column."""
    text = str(value or '').strip().removeprefix("'").strip()
    if re.fullmatch(r'[0-9]{1,6}\.0+', text):
        text = text.split('.')[0]
    if not re.fullmatch(r'[0-9]{1,6}', text):
        raise ValueError('IDKH phải là mã 6 chữ số. Nhiều mã cách nhau bằng dấu phẩy; không nối hoặc chia một dãy số.')
    return text.zfill(6)


def confirmed_customer_ids(value):
    tokens = [token for token in re.split(r'[\s,;/+&]+', str(value or '').strip()) if token]
    if not tokens:
        raise ValueError('Dòng học thiếu mã khách hàng đã xác nhận.')
    return list(dict.fromkeys(confirmed_customer_id(token) for token in tokens))


def allocation_names(customer_ids, names):
    """A single payer name must never become the alias of several recipients."""
    names = str(names or '').strip()
    if len(customer_ids) == 1:
        return [names]
    parts = [part.strip() for part in re.split(r'[;|\n]', names)]
    return parts if len(parts) == len(customer_ids) else [''] * len(customer_ids)


def id_key(value):
    # Compatibility for old Excel labels stored without leading zeroes. Content parsing
    # itself uses CUSTOMER_ID.fullmatch and never shortens a long account/reference.
    value = str(value).strip()
    return value.zfill(6) if re.fullmatch(r'[0-9]{1,6}', value) else value


def channel_key(value):
    words = re.findall(r'[a-z]+', fold(value or ''))
    key = ' '.join(words)
    for label, canonical in (('an binh', 'abbank'), ('cong thuong', 'vietinbank'),
                             ('quan doi', 'mb'), ('vp bank', 'vpbank'), ('nn ', 'agribank')):
        if key == label.strip() or key.startswith(label if label.endswith(' ') else label + ' '):
            return canonical
    banks = {'bidv': 'bidv', 'vcb': 'vcb', 'vietcombank': 'vcb', 'mb': 'mb', 'mbbank': 'mb',
             'vtb': 'vietinbank', 'vietinbank': 'vietinbank', 'icb': 'vietinbank',
             'agr': 'agribank', 'agribank': 'agribank', 'exb': 'eximbank', 'eximbank': 'eximbank',
             'vpb': 'vpbank', 'vpbank': 'vpbank', 'abb': 'abbank', 'ab': 'abbank', 'abbank': 'abbank',
             'scb': 'sacombank', 'shb': 'shb', 'sacombank': 'sacombank', 'momo': 'momo',
             'viettelpay': 'viettelpay', 'payoo': 'payoo', 'vnpay': 'vnpay', 'zalopay': 'zalopay'}
    return banks.get(words[0], key) if words else 'unknown'
