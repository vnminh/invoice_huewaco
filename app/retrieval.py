"""Rank fusion and bounded customer diversity, independent of score scales."""
from collections import Counter, defaultdict


CHANNEL_WEIGHTS = {
    'customer_id': 6.0, 'exact_text': 5.0, 'identity': 5.0,
    'structured': 3.0, 'payer': 2.0, 'name_entity': 2.0, 'shared_template': 2.0, 'postings': 1.0,
    'semantic': 1.0, 'full_text': 1.0, 'trigram': 1.0, 'customer_name': 1.0,
}
CHANNEL_LABELS = {
    'customer_id': 'mã khách hàng', 'exact_text': 'nội dung trùng chính xác',
    'identity': 'hợp đồng hoặc số khách hàng đã xác nhận',
    'structured': 'mã chữ/số hoặc chi tiết thanh toán', 'payer': 'tài khoản trả tiền',
    'name_entity': 'tên trích từ nội dung',
    'shared_template': 'bố cục mẫu dùng chung',
    'postings': 'từ khóa và tên đã học', 'semantic': 'nội dung tương đồng',
    'full_text': 'tìm kiếm nội dung', 'trigram': 'nội dung gần giống',
    'customer_name': 'tên khách hàng',
}


def fuse_rankings(channels, customers, limit, scoped=False):
    """Weighted reciprocal rank fusion, with at most three variants per first pass.

    Rank positions (not SQL score magnitudes) combine channels. A repeated
    pattern within one channel gets one vote. Exact customer scopes retain the
    full variant budget; otherwise reserve space for competing customers.
    """
    scores, sources = defaultdict(float), defaultdict(dict)
    for channel, ids in channels.items():
        for rank, pattern_id in enumerate(dict.fromkeys(ids), start=1):
            if pattern_id not in customers:
                continue
            scores[pattern_id] += CHANNEL_WEIGHTS[channel] / (60 + rank)
            sources[pattern_id][channel] = rank
    ordered = sorted(scores, key=lambda pattern_id: (-scores[pattern_id], pattern_id))
    # Preserve one direct identity/exact-text hit per customer per channel even
    # when several weak, generic channels otherwise crowd it out of the budget.
    reserved = []
    for channel in ('identity', 'exact_text'):
        seen_customers = set()
        for pattern_id in channels.get(channel, []):
            customer = customers.get(pattern_id)
            if customer is not None and customer not in seen_customers:
                if pattern_id not in reserved:
                    reserved.append(pattern_id)
                seen_customers.add(customer)
    reserved = reserved[:limit]
    if scoped:
        selected = reserved + [pattern_id for pattern_id in ordered if pattern_id not in reserved][:limit - len(reserved)]
    else:
        selected, deferred = list(reserved), []
        counts = Counter(customers[pattern_id] for pattern_id in reserved)
        for pattern_id in ordered:
            if pattern_id in reserved:
                continue
            customer = customers[pattern_id]
            if counts[customer] < 3 and len(selected) < limit:
                selected.append(pattern_id)
                counts[customer] += 1
            else:
                deferred.append(pattern_id)
        selected.extend(deferred[:max(0, limit - len(selected))])
    trace = {'method': 'weighted_rrf', 'rank_constant': 60,
             'pool_count': len(ordered), 'selected_count': len(selected),
             'reserved_identity_count': len(reserved),
             'selected_customer_count': len({customers[pattern_id] for pattern_id in selected}),
             'channel_counts': {channel: len(set(ids)) for channel, ids in channels.items()},
             'candidate_sources': {pattern_id: sources[pattern_id] for pattern_id in selected}}
    return selected, trace
