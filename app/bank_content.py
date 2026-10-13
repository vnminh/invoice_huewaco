"""Read explicit bank fields without guessing the roles of numeric identifiers."""
from datetime import datetime
import re
import unicodedata

from .text import fold


MB_WATER_CONTENT = re.compile(
    r'^\s*MB\.(?:\d+-){2}(?P<transfer_date>\d{8})-(?P<transfer_time>\d{6})-\d+\.'
    r'(?P<date_copy>\d{8})\.(?P<receiver>[^.]+)\.(?P<customer_id>\d{6})\.(?P<reference>\d+)\.'
    r'(?P<period>(?:0?[1-9]|1[0-2])/20\d{2})\.(?P<name>[^.\d]*)\.\.\d+\.?\s*$', re.I)


def is_water_receiver(name):
    value = ' '.join(re.findall(r'[a-z]+', fold(name)))
    return bool(re.search(r'\b(?:hue\s+water|water\s+supply|huewaco|cap\s+nuoc\s+hue)\b', value))


def valid_name(name):
    words = name.split()
    return (2 <= len(words) <= 12 and len(name) <= 200
            and all(char.isalpha() or char.isspace() or unicodedata.category(char).startswith('M')
                    or char in "-'’" for char in name)
            and not is_water_receiver(name))


def mb_water_fields(raw):
    """Validated collection layout: explicit customer field, reference, period and name."""
    match = MB_WATER_CONTENT.fullmatch(str(raw or ''))
    if not match or not is_water_receiver(match['receiver']) or match['date_copy'] != match['transfer_date']:
        return None
    try:
        datetime.strptime(match['transfer_date'] + match['transfer_time'], '%Y%m%d%H%M%S')
    except ValueError:
        return None
    name = match['name'].strip()
    # Name quality must not invalidate the bank's separate ID/period fields.
    if name and not valid_name(name):
        name = ''
    start, end = match.span('name')
    start += len(match['name']) - len(match['name'].lstrip())
    end -= len(match['name']) - len(match['name'].rstrip())
    return {'period': match['period'], 'period_span': match.span('period'),
            'name': name, 'name_span': (start, end), 'receiver_span': match.span('receiver'),
            'customer_id': match['customer_id'], 'customer_span': match.span('customer_id'),
            'reference_span': match.span('reference'),
            'protocol_span': (0, match.start('receiver'))}


def agr_water_fields(raw):
    match = re.search(r'MA_GD:\d+\|(?P<customer_id>\d{6}),\s*Nuoc\d{1,2}/20\d{2}#(?P<name>[^#]+)#', str(raw), re.I)
    if not match or not valid_name(match['name'].strip()):
        return None
    name = match['name'].strip()
    start, end = match.span('name')
    start += len(match['name']) - len(match['name'].lstrip())
    end -= len(match['name']) - len(match['name'].rstrip())
    return {'name': name, 'name_span': (start, end), 'customer_id': match['customer_id']}
