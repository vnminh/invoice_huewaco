"""Persistent recognition of confirmed observations, independent of ANN budgets."""
import hashlib
import json
import re
from types import SimpleNamespace

from sqlalchemy import func, select, text

from .db import Customer, HardNegative, Metadata, Pattern, RecurringLink, now, sessions
from .identifiers import channel_key
from .normalize import NORMALIZER_VERSION, normalize_text

VERSION = 'confirmed-recurring-v1'
VERSION_KEY = 'recurring_memory_version'
# Keep every unknown number intact. Only explicitly identified changing fields
# are omitted; account/card/customer/contract/code values and their order survive.
VARIABLE = {'BANK_REFERENCE', 'BANK_PROTOCOL', 'REFERENCE_ID', 'INVOICE_ID', 'INVOICE_CODE',
            'AMOUNT', 'QUANTITY', 'RECEIVER_ACCOUNT'}
GENERIC = set('thanh toan tien nuoc nop tra chuyen khoan thu ho dich vu dinh ky thang nam ngay '
              'hoa don hop dong ma kh id tthd tt gui nhan den tu cho cua va tai ngan hang bank '
              'bidv vcb mb vietcombank vietinbank agribank sacombank momo payoo vnpay viettel '
              'hue thua thien water supply jsc co phan cong ty cap sach sinh hoat may '
              'truong tieu hoc mam non so cs khu vuc chi nhanh phu phi gia dinh ho khach hang '
              'rem bo fo ac stk tk the tkthe ref invoice chiho ctcp ct cp bftvvnvx icbvvnvx'.split())


def signature(norm, channel):
    parts = []
    for segment in norm.segments:
        if segment['type'] == 'text':
            parts.append(['text', segment['value']])
        elif segment['type'] == 'number' and segment['numeric_type'] not in VARIABLE:
            parts.append(['number', segment['numeric_type'], segment['value'], segment.get('format', '#')])
    # Removing a changing numeric field may leave adjacent text fragments.
    joined = []
    for part in parts:
        if joined and part[0] == joined[-1][0] == 'text':
            joined[-1][1] += ' ' + part[1]
        else:
            joined.append(part)
    content = ' '.join(part[1] for part in joined if part[0] == 'text')
    specific = set(re.findall(r'[a-z]{2,}', content)) - GENERIC
    anchor_roles = {part[1] for part in joined if part[0] == 'number'}
    identity_anchor = bool(anchor_roles & {'CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'LOCATION_NUMBER'})
    personal = identity_anchor or bool(anchor_roles & {'ACCOUNT_ID', 'CARD_ID'}) or len(specific) >= 2
    payload = {'version': VERSION, 'parts': joined, 'personal': personal,
               'identity_anchor': identity_anchor, 'specific_words': sorted(specific)}
    digest = hashlib.sha256(json.dumps([VERSION, channel_key(channel), joined], ensure_ascii=False,
                                       separators=(',', ':')).encode()).hexdigest()
    return digest, payload


def negative_key(digest):
    return hashlib.sha256(('recurring:' + VERSION + ':' + digest).encode()).hexdigest()


def initialize(session):
    if session.get(Metadata, VERSION_KEY) is None:
        empty = session.scalar(select(Pattern.id).limit(1)) is None
        session.add(Metadata(key=VERSION_KEY, value=VERSION if empty else 'pending'))
        session.flush()


def remember(session, pattern, norm, transaction, *, already_learned=False, name_extraction=None):
    digest, payload = signature(norm, pattern.channel_key)
    link = session.scalar(select(RecurringLink).where(RecurringLink.pattern_id == pattern.id,
                                                     RecurringLink.signature_hash == digest))
    if link is None:
        if name_extraction is not None:
            payload['name_extraction'] = name_extraction
        link = RecurringLink(pattern_id=pattern.id, customer_id=pattern.customer_id,
            channel_key=pattern.channel_key, signature_hash=digest, signature=payload,
            raw_example=norm.raw, source_file=transaction.source, source_row=transaction.row_index,
            example_date=transaction.date, last_period=pattern.last_period)
        session.add(link)
    elif not already_learned:
        link.seen_count += 1
        link.last_seen = now()
        if pattern.last_period != 'unknown':
            link.last_period = pattern.last_period
    # Entity offsets refer to this exact saved example, not another month's text.
    # Reimport may enrich old knowledge without counting it twice. Disabling NER
    # or a temporary extraction failure must not erase previously saved entities.
    if name_extraction is not None and link.raw_example == norm.raw:
        previous = (link.signature or {}).get('name_extraction', {})
        if name_extraction.get('entities') or not previous.get('entities'):
            link.signature = {**link.signature, 'name_extraction': name_extraction}
    return link


def owner_query(norm, channel):
    digest, _ = signature(norm, channel)
    return select(RecurringLink.customer_id).join(Pattern, Pattern.id == RecurringLink.pattern_id).where(
        Pattern.customer_id == RecurringLink.customer_id, Pattern.channel_key == RecurringLink.channel_key,
        RecurringLink.channel_key == channel_key(channel), RecurringLink.signature_hash == digest).distinct()


def lookup(session, norm, channel):
    digest, payload = signature(norm, channel)
    links = select(RecurringLink).join(Pattern, Pattern.id == RecurringLink.pattern_id).where(
        Pattern.customer_id == RecurringLink.customer_id, Pattern.channel_key == RecurringLink.channel_key,
        RecurringLink.channel_key == channel_key(channel), RecurringLink.signature_hash == digest)
    # Count ALL owners before fetching a bounded preview. Rejections never erase
    # positive history or make a previously shared key look uniquely owned.
    owners = owner_query(norm, channel)
    total = session.scalar(select(func.count()).select_from(owners.subquery()))
    if not total:
        return None
    preview = session.execute(select(Customer.id, Customer.canonical_name).where(Customer.id.in_(owners))
                              .order_by(Customer.id).limit(6)).all()
    record = session.scalar(links.order_by(RecurringLink.last_seen.desc(), RecurringLink.id.desc()).limit(1))
    blocked = set(session.scalars(select(HardNegative.customer_id).where(
        HardNegative.transaction_fingerprint.in_((negative_key(digest), hashlib.sha256(norm.normalized.encode()).hexdigest())),
        HardNegative.customer_id.in_(owners))))
    version = session.get(Metadata, VERSION_KEY)
    return {'signature_hash': digest, 'owner_count': total, 'complete': bool(version and version.value == VERSION),
        'personal': payload['personal'], 'identity_anchor': payload['identity_anchor'],
        'sample_valid': signature(normalize_text(record.raw_example), channel)[0] == digest,
        'owners': [{'customer_id': cid, 'customer_name': name, 'score': .59,
                    'sources': ['Mẫu này đã được xác nhận cho khách hàng' + ('; có phản hồi từ chối cần kiểm tra' if cid in blocked else '')]}
                   for cid, name in preview], 'blocked_customer_ids': sorted(blocked),
        'link': record}


def rebuild(engine, batch_size=500, progress=None):
    """Append missing bindings from approved examples; preserve all newer observations."""
    factory = sessions(engine)
    with factory.begin() as session:
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
        extractor = session.get(Metadata, 'feature_extractor')
        if extractor and extractor.value != NORMALIZER_VERSION:
            raise ValueError('Cập nhật đặc trưng v7 trước khi bổ sung bộ nhớ mẫu đã xác nhận.')
        record = session.get(Metadata, VERSION_KEY)
        if record: record.value = 'building'
        else: session.add(Metadata(key=VERSION_KEY, value='building'))
        last_observation = session.scalar(select(func.max(RecurringLink.id))) or 0
    observation_id, observations = 0, 0
    # Retain/reparse all approved observations, including variants no longer
    # represented by a pattern's first raw_example. Never clear the memory table.
    while observation_id < last_observation:
        with factory.begin() as session:
            if engine.dialect.name == 'postgresql':
                session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
            links = list(session.scalars(select(RecurringLink).where(RecurringLink.id > observation_id,
                RecurringLink.id <= last_observation).order_by(RecurringLink.id).limit(batch_size)))
            if not links: break
            for link in links:
                pattern = session.get(Pattern, link.pattern_id)
                sample = SimpleNamespace(source=link.source_file,row_index=link.source_row,date=link.example_date)
                remember(session, pattern, normalize_text(link.raw_example), sample, already_learned=True)
                observation_id, observations = link.id, observations + 1
    last_id, count = 0, 0
    while True:
        with factory.begin() as session:
            if engine.dialect.name == 'postgresql':
                session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
            patterns = list(session.scalars(select(Pattern).where(Pattern.id > last_id).order_by(Pattern.id).limit(batch_size)))
            if not patterns: break
            for pattern in patterns:
                norm = normalize_text(pattern.raw_example)
                sample = SimpleNamespace(source=pattern.source_file, row_index=pattern.source_row, date=pattern.example_date)
                remember(session, pattern, norm, sample, already_learned=True)
                last_id, count = pattern.id, count + 1
        if progress: progress(count)
    with factory.begin() as session:
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
        session.get(Metadata, VERSION_KEY).value = VERSION
        from .interactive import touch_revision
        touch_revision(session)
    return {'patterns': count, 'observations_rechecked': observations, 'version': VERSION}
