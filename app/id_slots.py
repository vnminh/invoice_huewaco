"""Customer-ID positions learned from confirmed (clean) narratives.

Every 4-6 digit number carries a name-agnostic ``context`` (see ``normalize.slot_context``).
After an import, all confirmed narratives are scanned: a context is a learned ID slot when the
number found there equals one of the narrative's confirmed customers with a high Wilson lower
bound. A context that almost never holds the customer is a learned non-ID slot (amounts,
references), which must not act as identity evidence for anyone.
"""
from collections import defaultdict
import math

from sqlalchemy import delete, select

from .db import IdSlotStat, Metadata, Pattern, now
from .normalize import normalize_text

SLOT_MIN_LOWER_BOUND = .95   # ID slot: precision lower bound (~75 clean observations at 100%)
NON_ID_MAX_UPPER_BOUND = .05  # non-ID slot: precision upper bound
BATCH_MIN_CUSTOMERS = 10      # narratives split across this many customers are batch settlements
VERSION_KEY = 'id_slots_version'


def id_key(value):
    return str(value).lstrip('0') or '0'


def wilson(hits, total, z=1.96):
    if not total:
        return 0.0, 1.0
    p = hits / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    denominator = 1 + z * z / total
    return (centre - margin) / denominator, (centre + margin) / denominator


def slot_kind(hits, total):
    lower, upper = wilson(hits, total)
    return 'id' if lower >= SLOT_MIN_LOWER_BOUND else 'not_id' if upper <= NON_ID_MAX_UPPER_BOUND else 'unknown'


def rebuild(session, batch_size=2000, check_cancel=None):
    """Recount every learned context from the stored confirmed links, order-independently.

    Links are grouped by normalized narrative, so a payment for several customers counts each
    listed ID as a hit. Batch settlements are excluded: their numbers never identify a customer.
    """
    check_cancel = check_cancel or (lambda: None)
    counts = defaultdict(lambda: [0, 0])
    examples = {}

    def flush(raw, owners):
        if not raw or len(owners) >= BATCH_MIN_CUSTOMERS:
            return
        for number in normalize_text(raw).numbers:
            context = number.get('context')
            if context:
                counts[context][0] += id_key(number['value']) in owners
                counts[context][1] += 1
                examples.setdefault(context, raw[:300])

    current, raw, owners = None, None, set()
    rows = session.execute(select(Pattern.normalized_text, Pattern.raw_example, Pattern.customer_id)
                           .order_by(Pattern.normalized_text, Pattern.id)
                           .execution_options(yield_per=batch_size))
    for index, (normalized, example, customer_id) in enumerate(rows):
        if index % batch_size == 0:
            check_cancel()
        if normalized != current:
            flush(raw, owners)
            current, raw, owners = normalized, example, set()
        owners.add(id_key(customer_id))
    flush(raw, owners)
    session.execute(delete(IdSlotStat))
    stamp = now()
    session.add_all([IdSlotStat(context=context, hits=hits, total=total, example=examples[context], updated_at=stamp)
                     for context, (hits, total) in counts.items()])
    version = session.get(Metadata, VERSION_KEY)
    if version is None:
        session.add(Metadata(key=VERSION_KEY, value=stamp))
    else:
        version.value = stamp
    session.flush()
    kinds = defaultdict(int)
    for hits, total in counts.values():
        kinds[slot_kind(hits, total)] += 1
    return {'contexts': len(counts), 'id_slots': kinds['id'], 'non_id_slots': kinds['not_id']}


class SlotCache:
    """Per-process cache, refreshed when a rebuild changes the stored version."""
    def __init__(self):
        self.version, self.kinds = None, {}

    def kinds_for(self, session):
        meta = session.get(Metadata, VERSION_KEY)
        version = meta.value if meta else None
        if version != self.version:
            self.kinds = {context: slot_kind(hits, total) for context, hits, total in
                          session.execute(select(IdSlotStat.context, IdSlotStat.hits, IdSlotStat.total))}
            self.version = version
        return self.kinds
