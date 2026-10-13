"""Read-only historical cohorts. Membership never authorizes an automatic match."""
import hashlib
import re

from sqlalchemy import case, func, literal, or_, select, union_all

from .db import Alias, CollectionMember, Customer, HardNegative, Pattern, PaymentTemplate, RecurringLink
from .identifiers import channel_key
from .payment_templates import shared_key, shared_shape
from .text import fold
from .recurring import owner_query as recurring_owners, negative_key as recurring_negative, signature as recurring_signature


def attach_examples(session, norm, payer, members):
    """Fetch evidence only for a visible page; never use it to change match decisions.

    Window queries bound each customer's examples without an N+1 query or a
    global limit that could hide other customers. Every example is owned by the
    displayed customer; collection memberships expose a layout, not a personal memo.
    """
    by_customer = {member['customer_id']: member for member in members}
    if not by_customer:
        return
    for member in members:
        member['examples'] = []
    channel = channel_key(payer)
    digest, _ = recurring_signature(norm, channel)

    def add(cid, example):
        examples = by_customer[cid]['examples']
        if len(examples) < 3 and not any(
                (old['kind'] == 'collection') == (example['kind'] == 'collection')
                and old['content'] == example['content'] and old['channel'] == example['channel']
                for old in examples):
            examples.append(example)

    # The exact remembered observation may differ from the pattern's first example.
    ranked_memory = select(RecurringLink.id, func.row_number().over(
        partition_by=RecurringLink.customer_id,
        order_by=(RecurringLink.last_seen.desc(), RecurringLink.id.desc())).label('rank')) \
        .join(Pattern, Pattern.id == RecurringLink.pattern_id).where(
            RecurringLink.customer_id.in_(by_customer), Pattern.customer_id == RecurringLink.customer_id,
            Pattern.channel_key == RecurringLink.channel_key, RecurringLink.channel_key == channel,
            RecurringLink.signature_hash == digest).subquery()
    for memory, pattern in session.execute(select(RecurringLink, Pattern)
            .join(ranked_memory, ranked_memory.c.id == RecurringLink.id)
            .join(Pattern, Pattern.id == RecurringLink.pattern_id).where(ranked_memory.c.rank <= 1)):
        add(memory.customer_id, {'kind': 'recurring', 'pattern_id': pattern.id,
            'content': memory.raw_example, 'channel': memory.channel_key,
            'payment_mode': pattern.payment_mode, 'source_file': memory.source_file,
            'source_row': memory.source_row, 'example_date': memory.example_date,
            'reasons': ['Nội dung ổn định từng được xác nhận cho khách hàng']})

    ranked_patterns = select(Pattern.id, func.row_number().over(partition_by=Pattern.customer_id,
        order_by=((Pattern.normalized_text == norm.normalized).desc(),
                  (Pattern.template_text == norm.template).desc(), (Pattern.channel_key == channel).desc(),
                  Pattern.last_seen.desc(), Pattern.id.desc())).label('rank')) \
        .where(Pattern.customer_id.in_(by_customer)).subquery()
    for pattern in session.scalars(select(Pattern).join(ranked_patterns, ranked_patterns.c.id == Pattern.id)
            .where(ranked_patterns.c.rank <= 3).order_by(Pattern.customer_id, ranked_patterns.c.rank)):
        reasons = []
        if pattern.normalized_text == norm.normalized:
            reasons.append('Nội dung chuẩn hóa trùng lịch sử')
        elif pattern.template_text == norm.template:
            reasons.append('Cùng bố cục nội dung')
        if pattern.channel_key == channel:
            reasons.append('Cùng ngân hàng / kênh thanh toán')
        add(pattern.customer_id, {'kind': 'pattern', 'pattern_id': pattern.id,
            'content': pattern.raw_example, 'channel': pattern.channel_key,
            'payment_mode': pattern.payment_mode, 'source_file': pattern.source_file,
            'source_row': pattern.source_row, 'example_date': pattern.example_date,
            'reasons': reasons or ['Mẫu đã học của khách hàng']})

    ranked_collections = select(CollectionMember.id, func.row_number().over(
        partition_by=CollectionMember.customer_id,
        order_by=((PaymentTemplate.template_text == norm.template).desc(),
                  (PaymentTemplate.channel_key == channel).desc(), CollectionMember.last_seen.desc(),
                  CollectionMember.id.desc())).label('rank')) \
        .join(PaymentTemplate, PaymentTemplate.id == CollectionMember.template_id) \
        .where(CollectionMember.customer_id.in_(by_customer)).subquery()
    for member, template in session.execute(select(CollectionMember, PaymentTemplate)
            .join(PaymentTemplate, PaymentTemplate.id == CollectionMember.template_id)
            .join(ranked_collections, ranked_collections.c.id == CollectionMember.id)
            .where(ranked_collections.c.rank <= 3).order_by(CollectionMember.customer_id, ranked_collections.c.rank)):
        add(member.customer_id, {'kind': 'collection', 'template_id': template.id,
            'content': template.template_text, 'channel': template.channel_key,
            'payment_mode': template.payment_mode, 'source_file': '', 'source_row': None, 'example_date': '',
            'reasons': ['Khách hàng từng có trong danh sách thu hộ của mẫu này']})


def candidate_group(session, norm, payer, extracted_names=(), *, suggestions=(), q='', offset=0, limit=6):
    """Prefer entity + bank/layout; fall back to a clearly labelled bank cohort.

    A missing ID is useful only as a conditional historical feature, never proof
    of identity. All members remain manual suggestions, including singleton groups.
    Aggregation/pagination stays in SQL, including large provider rosters.
    """
    channel = channel_key(payer)
    individual = {item['customer_id']: item.get('sources') or ['Nội dung / định danh liên quan trong lịch sử']
                  for item in suggestions[:6] if item.get('customer_id')}
    words = re.findall(r'[a-z]+', norm.normalized)[:200]
    phrases = {' '.join(fold(name).split()) for name in extracted_names if name}
    phrases.update(' '.join(words[start:start + width])
                   for width in range(2, 7) for start in range(len(words) - width + 1))
    phrases.update(word for word in words if len(word) >= 3 and word not in {
        'thanh', 'toan', 'tien', 'nuoc', 'ngan', 'hang', 'chuyen', 'khoan', 'thang', 'nam',
        'water', 'supply', 'hue', 'jsc', 'bill', 'invoice', 'cong', 'hop', 'dong'})
    aliases = select(Alias.customer_id).where(Alias.normalized_alias.in_(phrases)).distinct()
    entity_exists = bool(phrases) and session.scalar(select(aliases.exists()))
    # Names already in the narrative also allow a <NAME> shared layout lookup
    # without requiring NER availability. Keep the full owner query, not this preview.
    names = list(extracted_names)
    if entity_exists:
        names += list(session.scalars(select(Alias.alias).where(Alias.normalized_alias.in_(phrases)).distinct().limit(20)))
    shapes = {norm.template, shared_shape(norm, names)}
    template_ids = select(PaymentTemplate.id).where(PaymentTemplate.channel_key == channel,
        or_(PaymentTemplate.fingerprint.in_({shared_key(shape, norm.structure, channel) for shape in shapes}),
            PaymentTemplate.template_text.in_(shapes)))
    has_templates = session.scalar(select(template_ids.exists()))
    scope = 'bank_template' if has_templates else 'bank'
    sources = ['Cùng ngân hàng / kênh thanh toán']
    if has_templates:
        sources.append('Cùng bố cục nội dung đã được xác nhận')
    pattern_filters, member_filters = [], []
    if has_templates:
        pattern_filters.append(Pattern.template_id.in_(template_ids))
        member_filters.append(CollectionMember.template_id.in_(template_ids))
    else:
        pattern_filters.append(Pattern.channel_key == channel)
        member_filters.append(PaymentTemplate.channel_key == channel)
    if entity_exists:
        # Intersect only when that entity actually has history in this scope.
        in_scope = session.scalar(select(select(Pattern.id).where(*pattern_filters,
            Pattern.customer_id.in_(aliases)).exists())) or session.scalar(select(select(CollectionMember.id)
                .join(PaymentTemplate, PaymentTemplate.id == CollectionMember.template_id)
                .where(*member_filters, CollectionMember.customer_id.in_(aliases)).exists()))
        scope = 'bank_template_entity' if in_scope and has_templates else 'bank_entity' if in_scope else 'entity'
        sources.append('Tên / đơn vị trong nội dung trùng bí danh đã học')
        if not in_scope:
            pattern_filters, member_filters = [], []
            sources = ['Tên / đơn vị trong nội dung trùng bí danh đã học']
        pattern_filters.append(Pattern.customer_id.in_(aliases))
        member_filters.append(CollectionMember.customer_id.in_(aliases))
    elif channel == 'unknown' and not has_templates:
        if not individual:
            return {'kind': 'none', 'total': 0, 'members': [], 'sources': [], 'offset': offset, 'limit': limit,
                    'missing_customer_id': not bool(norm.customer_ids), 'broad': False}
        scope, sources = 'individual', ['Gợi ý cá nhân liên quan']
        pattern_filters, member_filters = [literal(False)], [literal(False)]
    missing_profile = False
    if scope == 'bank' and not norm.customer_ids:
        # Legacy records lacking metadata do not count as observed absence.
        no_id = Pattern.label_metadata['mentioned_customer_ids'].as_string() == '[]'
        if session.scalar(select(select(Pattern.id).where(*pattern_filters, no_id).exists())):
            pattern_filters.append(no_id)
            missing_profile = True
            sources.append('Từng chuyển tiền không ghi IDKH')
    patterns = select(Pattern.customer_id.label('customer_id'),
        Pattern.seen_count.label('observations'), Pattern.last_seen.label('last_seen'),
        Pattern.last_period.label('last_period'), literal(1).label('pattern_count'), literal(0).label('collection_count'),
        case((Pattern.channel_key == channel, 1), else_=0).label('bank_match')).where(*pattern_filters)
    members = select(CollectionMember.customer_id.label('customer_id'),
        CollectionMember.seen_count.label('observations'), CollectionMember.last_seen.label('last_seen'),
        CollectionMember.last_period.label('last_period'), literal(0).label('pattern_count'), literal(1).label('collection_count'),
        case((PaymentTemplate.channel_key == channel, 1), else_=0).label('bank_match')) \
        .join(PaymentTemplate, PaymentTemplate.id == CollectionMember.template_id).where(*member_filters)
    links = union_all(patterns, members).subquery()
    history = select(links.c.customer_id, func.sum(links.c.observations).label('seen_count'),
        func.max(links.c.last_seen).label('last_seen'), func.max(links.c.last_period).label('last_period'),
        func.sum(links.c.pattern_count).label('pattern_count'), func.sum(links.c.collection_count).label('collection_count'),
        func.max(links.c.bank_match).label('bank_match')).group_by(links.c.customer_id).cte('candidate_history')
    normalized_hash = hashlib.sha256(norm.normalized.encode()).hexdigest()
    blocked = select(HardNegative.customer_id).where(HardNegative.transaction_fingerprint.in_(
        (normalized_hash, recurring_negative(recurring_signature(norm, channel)[0]))))
    query = select(Customer.id, Customer.canonical_name, func.coalesce(history.c.seen_count, 0).label('seen_count'),
        history.c.last_seen, history.c.last_period, func.coalesce(history.c.pattern_count, 0).label('pattern_count'),
        func.coalesce(history.c.collection_count, 0).label('collection_count'))
    query = query.outerjoin(history, history.c.customer_id == Customer.id)
    contextual_members = aliases if scope == 'entity' else select(history.c.customer_id)
    # A new phrasing/bank or a payer name can make an exact cohort too narrow.
    # Preserve relevant individual proposals as a union, with their own provenance.
    member_ids = contextual_members.union(select(Customer.id).where(Customer.id.in_(individual)),
                                          recurring_owners(norm, channel))
    query = query.where(Customer.id.in_(member_ids))
    query = query.where(Customer.id.not_in(blocked))
    if q.strip():
        value = ' '.join(fold(q).split())
        query = query.where(or_(Customer.id.contains(q.strip(), autoescape=True),
            Customer.normalized_name.contains(value, autoescape=True),
            Customer.id.in_(select(Alias.customer_id).where(Alias.normalized_alias.contains(value, autoescape=True)))))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    priority = case({cid: len(individual)-index for index, cid in enumerate(individual)}, value=Customer.id, else_=0) \
        if individual else literal(0)
    rows = session.execute(query.order_by(priority.desc(), func.coalesce(history.c.bank_match, 0).desc(),
        func.coalesce(history.c.seen_count, 0).desc(), history.c.last_seen.desc(), Customer.id)
        .offset(offset).limit(limit)).all()
    return {'kind': scope, 'channel': channel, 'total': total, 'offset': offset, 'limit': limit,
        'missing_customer_id': not bool(norm.customer_ids), 'missing_id_profile_used': missing_profile,
        'broad': scope == 'bank', 'sources': sources, 'includes_individual_suggestions': bool(individual),
        'members': [{'customer_id': row.id, 'customer_name': row.canonical_name,
            'seen_count': row.seen_count, 'last_period': row.last_period or '',
            'pattern_count': row.pattern_count, 'collection_count': row.collection_count,
            'sources': individual.get(row.id, sources), 'group_only': row.id not in individual} for row in rows]}
