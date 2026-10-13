"""Name positions learned from confirmed narratives of shared bank layouts.

The confirmed file gives each row's real customer name. Finding that name in the narrative
reveals where a layout writes names, e.g. ``BIDV;5510000370;<name> chuyen tien`` or VCB
``...06/2026.<Name>..1.``. A position is the two units before the name (same shapes as
customer-ID slots), per bank channel. At matching time a learned position yields a name with a
known role even when NER finds none (lowercase, unaccented text). It is only used for whole-name
comparison; it never identifies a customer by itself.
"""
from collections import Counter, defaultdict
import re

from sqlalchemy import delete, select

from .db import Alias, Customer, Metadata, NameSlotStat, Pattern, now
from .id_slots import BATCH_MIN_CUSTOMERS, wilson
from .identifiers import channel_key
from .name_extraction import locate_names
from .normalize import fold

UNIT = re.compile(r'[a-z\d]+|[^\sa-z\d]')
NAME_MIN_HITS = 20
NAME_MIN_LOWER_BOUND = .6   # the payer sometimes writes their own name there: still a name field
MAX_NAME_WORDS = 6
# Payment words that end a free-text name even before a boundary is learned.
STOP_WORDS = set('chuyen tra thanh toan tt nop ck ct tien nuoc ma id kh mkh idkh ky thang cho gui hd so '
                 'thu ho chi den tu nam sinh hoat cty cong ty'.split())
VERSION_KEY = 'name_slots_version'


def shape(unit):
    return 'N' + str(len(unit)) if unit.isdigit() else 'M' if any(c.isdigit() for c in unit) else unit


def folded_text(raw):
    """Same length as ``raw`` so spans map back to the original characters."""
    return ''.join(fold(char)[:1] or ' ' for char in str(raw or ''))


def word_starts(folded):
    """Every letter word with its two-unit left context: (context, unit index, units)."""
    units = list(UNIT.finditer(folded))
    for index, unit in enumerate(units):
        if index >= 2 and unit.group().isalpha():
            yield ' '.join(shape(u.group()) for u in units[index - 2:index]), index, units


def rebuild(session, batch_size=2000, check_cancel=None):
    """Recount name positions from all confirmed links (order-independent, like ID slots)."""
    check_cancel = check_cancel or (lambda: None)
    names = defaultdict(set)
    for customer_id, name in session.execute(select(Customer.id, Customer.canonical_name)):
        names[customer_id].add(name)
    for customer_id, alias in session.execute(select(Alias.customer_id, Alias.normalized_alias).where(Alias.confidence >= 1.0)):
        names[customer_id].add(alias)
    hits, totals, boundaries, examples = Counter(), Counter(), defaultdict(Counter), {}

    def flush(raw, owners, channel):
        if not raw or len(owners) >= BATCH_MIN_CUSTOMERS:
            return
        folded = folded_text(raw)
        starts = {entity['start']: entity['end'] for entity in
                  locate_names(raw, {name for owner in owners for name in names.get(owner, ())})}
        for context, index, units in word_starts(folded):
            key = (channel, context)
            totals[key] += 1
            end = starts.get(units[index].start())
            if end is not None:
                hits[key] += 1
                after = next((u.group() for u in units[index:] if u.start() >= end), '')
                boundaries[key][shape(after) if after else '']+= 1
                examples.setdefault(key, raw[:300])

    current, raw, owners, channel = None, None, set(), 'unknown'
    rows = session.execute(select(Pattern.channel_key, Pattern.normalized_text, Pattern.raw_example, Pattern.customer_id)
                           .order_by(Pattern.channel_key, Pattern.normalized_text, Pattern.id)
                           .execution_options(yield_per=batch_size))
    for index, (bank, normalized, example, customer_id) in enumerate(rows):
        if index % batch_size == 0:
            check_cancel()
        if (bank, normalized) != current:
            flush(raw, owners, channel)
            current, raw, owners, channel = (bank, normalized), example, set(), channel_key(bank)
        owners.add(customer_id)
    flush(raw, owners, channel)
    session.execute(delete(NameSlotStat))
    stamp = now()
    learned = [NameSlotStat(channel_key=bank, context=context, hits=hits[(bank, context)], total=totals[(bank, context)],
                            boundaries=dict(boundaries[(bank, context)]), example=examples[(bank, context)], updated_at=stamp)
               for (bank, context) in hits if hits[(bank, context)] >= NAME_MIN_HITS]
    session.add_all(learned)
    version = session.get(Metadata, VERSION_KEY)
    if version is None:
        session.add(Metadata(key=VERSION_KEY, value=stamp))
    else:
        version.value = stamp
    session.flush()
    return {'name_positions': sum(is_name_slot(slot.hits, slot.total) for slot in learned)}


def is_name_slot(hits, total):
    return hits >= NAME_MIN_HITS and wilson(hits, total)[0] >= NAME_MIN_LOWER_BOUND


def extract(raw, slots):
    """Names written at learned positions; entities shaped like NER output."""
    text, folded = str(raw or ''), folded_text(raw)
    entities, used = [], set()
    for context, index, units in word_starts(folded):
        stops = slots.get(context)
        if stops is None:
            continue
        words = []
        for unit in units[index:index + MAX_NAME_WORDS]:
            word = unit.group()
            if not word.isalpha() or word in stops or word in STOP_WORDS or (words and folded[words[-1].end():unit.start()].strip()):
                break
            words.append(unit)
        if len(words) >= 2 and words[0].start() not in used:
            used.add(words[0].start())
            start, end = words[0].start(), words[-1].end()
            entities.append({'name': text[start:end], 'label': 'PERSON', 'score': None,
                             'source': 'learned_name_position', 'start': start, 'end': end})
    return entities


class NameSlotCache:
    """Per-process snapshot of name positions per channel, refreshed after a rebuild."""
    def __init__(self):
        self.snapshot = (None, {})

    def slots_for(self, session, channel='unknown'):
        version = session.scalar(select(Metadata.value).where(Metadata.key == VERSION_KEY))
        cached_version, cached = self.snapshot
        if version != cached_version:
            cached = defaultdict(dict)
            for bank, context, hits, total, ends in session.execute(select(
                    NameSlotStat.channel_key, NameSlotStat.context, NameSlotStat.hits, NameSlotStat.total, NameSlotStat.boundaries)):
                if is_name_slot(hits, total):
                    total_ends = sum((ends or {}).values()) or 1
                    # Words that commonly end the name here (e.g. "chuyen", "tra") stop extraction.
                    cached[bank][context] = {unit for unit, count in (ends or {}).items()
                                             if unit.isalpha() and count / total_ends >= .05}
            cached = dict(cached)
            self.snapshot = (version, cached)
        return cached.get(channel_key(channel), {})
