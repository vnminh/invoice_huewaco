from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
import hashlib
import json
import math
import re

from sqlalchemy import func, select, text, tuple_, update

from .db import (Alias, Customer, HardNegative, KnowledgeReceipt, Metadata,
                 NumericFeature, NumericSlot, Pattern, Payer, Posting, now)
from .embedding import Embedder, cosine, vector_buckets
from .normalize import fold, normalize_text, identity_signature, branch_markers, RuleExtractor
from .numeric_match import compare_ordered_numbers
from .retrieval import CHANNEL_LABELS, fuse_rankings

WEIGHTS = {'customer': .30, 'payer': .10, 'text': .20, 'structure': .15, 'number': .15, 'history': .10}
STOPWORDS = set('rem tfr ac o l tt tien nuoc thoi gian gd thanh toan chuyen khoan the tai va cua cho tu den vnd cty cong ty co phan'.split())
STOPWORDS.update('hue bidv ngan hang dau phat trien viet nam bank charge vat dtls ref tkthe bftvvnvx icbvvnvx'.split())


def vietnamese_reason(reason):
    translations = {
        'Customer ID is absent from confirmed knowledge; confidence is 0%; manual check required': 'ID khách hàng chưa có trong dữ liệu đã xác nhận. Độ tin cậy 0%; cần kiểm tra thủ công.',
        'No confirmed customer evidence; confidence is 0%; manual check required': 'Chưa có bằng chứng khách hàng đã xác nhận. Độ tin cậy 0%; cần kiểm tra thủ công.',
        'Historical candidates do not identify this customer reliably; confidence is 0%; manual check required': 'Các mẫu lịch sử chưa đủ để xác định khách hàng. Độ tin cậy 0%; cần kiểm tra thủ công.',
        'Weighted historical evidence': 'Tổng hợp bằng chứng lịch sử theo trọng số.',
        'Explicit customer ID plus matching confirmed transfer template': 'ID khách hàng trùng khớp và nội dung tương đồng với mẫu chuyển tiền đã xác nhận.',
        'Confirmed customer alias and supporting template/payer history': 'Tên khách hàng đã xác nhận khớp, kèm bằng chứng mẫu nội dung hoặc đơn vị trả tiền.',
        'Similar generic template lacks customer-specific evidence': 'Mẫu chuyển tiền tương tự nhưng thiếu bằng chứng riêng để xác định khách hàng.',
        'Explicit customer ID does not match this historical customer': 'ID khách hàng trong giao dịch khác ID của khách hàng trong lịch sử.',
        'Human feedback previously rejected this customer for this transaction': 'Người dùng đã từ chối ghép khách hàng này với giao dịch này.',
        'Non-credit bank transaction': 'Giao dịch ghi nợ hoặc không có tiền ghi có.',
        'Confirmed unique payer account and matching payment details': 'Tài khoản người gửi chỉ gắn với một khách hàng trong lịch sử; nội dung thanh toán trùng khớp.',
        'Unique historical payment details and confirmed customer name': 'Nội dung thanh toán và tên khách hàng trùng một lịch sử duy nhất.',
        'Name alone or shared payer does not identify a meter reliably': 'Chỉ tên đơn vị hoặc người trả tiền chung chưa đủ để xác định đúng đồng hồ/khách hàng.',
        'Multiple customer IDs require manual allocation': 'Có nhiều mã khách hàng trong cùng giao dịch; cần phân bổ và kiểm tra thủ công.',
        'Unknown or conflicting water contract requires manual check': 'Hợp đồng cấp nước chưa có trong lịch sử hoặc mâu thuẫn với mã khách hàng; cần kiểm tra thủ công.',
        'Exact water contract and ordered template evidence': 'Mã hợp đồng trùng chính xác; mẫu nội dung và vị trí số khớp với lịch sử đã xác nhận.',
        'Confirmed customer token at the same template position': 'Số đã gắn với khách hàng trong lịch sử trùng chính xác tại cùng vị trí trong mẫu nội dung.',
        'Conflicting contract or identifier order blocks acceptance': 'Mã hợp đồng hoặc thứ tự mã định danh khác mẫu lịch sử; không chấp nhận ghép.',
        'Unique payer with semantic and exact structured evidence': 'Người trả tiền duy nhất trong lịch sử, kèm nội dung tương đồng và số/vị trí đã kiểm tra chính xác.',
        'Unique exact mixed code and ordered identity evidence': 'Mã chữ/số trùng chính xác, chỉ gắn với một khách hàng trong lịch sử; toàn bộ số định danh khớp đúng thứ tự.',
        'Customer ID maps to multiple confirmed profiles; manual check required': 'Mã khách hàng ánh xạ tới nhiều hồ sơ đã xác nhận; cần kiểm tra thủ công.',
        'Conflicting confirmed identifiers require manual check': 'Các mã đã xác nhận trong giao dịch chỉ tới những khách hàng khác nhau; cần kiểm tra thủ công.',
        'Unmatched or reordered customer-specific codes require manual check': 'Mã chữ/số hoặc số cơ sở chưa khớp đầy đủ theo đúng thứ tự với mẫu lịch sử; cần kiểm tra thủ công.',
        'Unmatched confirmed customer number requires manual check': 'Số từng được xác nhận là mã khách hàng bị thiếu, đổi giá trị hoặc đổi vị trí so với mẫu lịch sử; cần kiểm tra thủ công.',
    }
    suffix = '; competing customer requires human review'
    base = reason.removesuffix(suffix)
    return translations.get(base, base) + (' Có khách hàng cạnh tranh với điểm gần nhau; cần con người kiểm tra.' if reason.endswith(suffix) else '')


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def posting_in(values):
    values = set(values)
    return Posting.token_digest.in_({fingerprint(value) for value in values}) & Posting.token.in_(values)


def posting_equal(value):
    return posting_in([value])


def posting_owners(session, tokens):
    """Read ownership across the entire knowledge, independently of candidate limits."""
    owners = defaultdict(set)
    if tokens:
        for token, customer_id in session.execute(select(Posting.token, Pattern.customer_id)
                .join(Pattern, Pattern.id == Posting.pattern_id).where(posting_in(tokens)).distinct()):
            owners[token].add(customer_id)
    return owners


def id_key(value):
    return str(value).lstrip('0') or '0'


def text_tokens(value):
    return {w for w in re.findall(r'[a-z]{2,}', value) if w not in STOPWORDS}


def confidence_update(value):
    return value + .30 * (1 - value)


def period_of(date):
    # Unknown month cannot establish recurrence across months.
    return date[:7] if re.match(r'^\d{4}-\d{2}', date or '') else 'unknown'


def pattern_identity(segments):
    # Separate different contracts/codes even when their placeholder text is identical.
    return [(n['slot'], n['numeric_type'], n['value'], n.get('format', '#')) for n in segments
            if n['type'] == 'number' and (n['numeric_type'] in
            ('CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'LOCATION_NUMBER') or n.get('confirmed_customer_value'))]


def learning_receipt(raw, customer_id, date='', amount=0, payer='BIDV'):
    """The same confirmed content exported/imported as CSV is learned only once."""
    return 'confirmed-content:' + fingerprint(json.dumps(
        [raw, str(customer_id), date or '', float(amount or 0), payer or 'BIDV'], ensure_ascii=False))


class Core:
    def __init__(self, embedder=None, candidate_limit=40, auto_threshold=.9, review_threshold=.6, extractor=None,
                 match_margin=.08):
        self.embedder = embedder or Embedder()
        self.candidate_limit = candidate_limit
        self.auto_threshold = auto_threshold
        self.review_threshold = review_threshold
        self.match_margin = match_margin
        if not 1 <= candidate_limit <= 500:
            raise ValueError('candidate_limit must be between 1 and 500')
        if not 0 <= review_threshold < auto_threshold <= 1 or not 0 <= match_margin <= 1:
            raise ValueError('Matching thresholds and margin must be valid values between 0 and 1')
        if extractor is not None and getattr(extractor, 'provider', 'rules') != 'rules':
            raise ValueError('Local LLM extraction is disabled; use semantic embeddings with deterministic identifiers')
        self.extractor = RuleExtractor()

    def normalize(self, raw):
        return self.extractor.augment(normalize_text(raw))

    def prepare_batch(self, raws):
        self.embedder.prepare([self.normalize(raw).semantic_text for raw in raws])

    def check_model(self, session, writing=False):
        meta = session.get(Metadata, 'embedding_model')
        if meta and meta.value != self.embedder.name:
            raise ValueError(f'Knowledge uses embedding model {meta.value!r}; configure that model or rebuild into a new database')
        if writing and not meta:
            session.add(Metadata(key='embedding_model', value=self.embedder.name))
            session.flush()
        extraction = session.get(Metadata, 'feature_extractor')
        if extraction and extraction.value != self.extractor.name:
            raise ValueError('Knowledge uses a different feature extractor; configure that extractor or reimport confirmed history into a fresh database')
        if writing and not extraction:
            session.add(Metadata(key='feature_extractor', value=self.extractor.name))
            session.flush()

    def ensure_customer(self, session, customer_id, customer_name):
        customer_id = str(customer_id).strip()
        if not customer_id:
            raise ValueError('A confirmed customer ID is required')
        customer = session.get(Customer, customer_id)
        if not customer and customer_id.isdigit():
            known = posting_owners(session, {'id:' + id_key(customer_id)})['id:' + id_key(customer_id)]
            if len(known) > 1:
                raise ValueError('Mã khách hàng trùng nhiều hồ sơ; cần sửa các hồ sơ trùng trước khi học.')
            if known:
                customer = session.get(Customer, next(iter(known)))
        if not customer:
            name = str(customer_name or customer_id).strip()
            customer = Customer(id=customer_id, canonical_name=name, normalized_name=fold(name))
            session.add(customer)
            session.flush()
        name = str(customer_name or customer.canonical_name).strip()
        alias = fold(name)
        if not session.scalar(select(Alias.id).where(Alias.customer_id == customer.id, Alias.normalized_alias == alias)):
            session.add(Alias(customer_id=customer.id, alias=name, normalized_alias=alias, confidence=1))
            name_keys = {'name:' + word for word in text_tokens(alias)}
            pattern_ids = list(session.scalars(select(Pattern.id).where(Pattern.customer_id == customer.id)))
            existing = set(session.execute(select(Posting.pattern_id, Posting.token).where(
                Posting.pattern_id.in_(pattern_ids), posting_in(name_keys))).all())
            session.add_all([Posting(pattern_id=pattern_id, token=token, weight=2.0)
                             for pattern_id in pattern_ids for token in name_keys
                             if (pattern_id, token) not in existing])
        return customer

    def learn(self, session, transaction, customer_id, customer_name='', receipt_key=None):
        """Only called for an explicitly confirmed import or human feedback."""
        if transaction.label_status in ('skipped', 'unresolved'):
            return False
        self.check_model(session, writing=True)
        receipt_key = receipt_key or '|'.join([transaction.raw, str(customer_id), transaction.date,
                                              str(transaction.amount), str(transaction.row_index)])
        receipt = fingerprint(receipt_key)
        if session.scalar(select(KnowledgeReceipt.id).where(KnowledgeReceipt.fingerprint == receipt)):
            return False
        customer = self.ensure_customer(session, customer_id, customer_name)
        norm = self.normalize(transaction.raw)
        for number in norm.numbers:
            if number['numeric_type'] == 'UNKNOWN_NUMBER' and number['value'].isdigit() and id_key(number['value']) == id_key(customer.id):
                number['confirmed_customer_value'] = True
                next(s for s in norm.segments if s.get('slot') == number['slot'])['confirmed_customer_value'] = True
        period = period_of(transaction.date)
        session.add(KnowledgeReceipt(fingerprint=receipt, customer_id=customer.id, period=period))
        payer_ids = []
        for payer_name in [transaction.payer or 'BIDV'] + norm.payer_accounts:
            payer = session.scalar(select(Payer).where(Payer.customer_id == customer.id, Payer.payer_name == payer_name))
            if not payer:
                payer = Payer(customer_id=customer.id, payer_name=payer_name,
                              payer_type='account' if payer_name in norm.payer_accounts else 'bank',
                              confidence=.2, seen_count=1, last_period=period)
                session.add(payer)
                session.flush()
            else:
                payer.seen_count += 1
                if period > payer.last_period and period != 'unknown':
                    payer.confidence = confidence_update(payer.confidence)
                    payer.last_period = period
                payer.last_seen = now()
            payer_ids.append(payer.id)
        identity = pattern_identity(norm.segments)
        pattern_hash = fingerprint(json.dumps([norm.template, norm.structure, identity], ensure_ascii=False))
        pattern = session.scalar(select(Pattern).where(Pattern.customer_id == customer.id, Pattern.fingerprint == pattern_hash))
        if pattern is None:
            # Reuse compatible older records without forcing a database/model rebuild.
            legacy = session.scalars(select(Pattern).where(Pattern.customer_id == customer.id,
                Pattern.template_text == norm.template, Pattern.structure == norm.structure))
            pattern = next((candidate for candidate in legacy if pattern_identity(candidate.segments) == identity), None)
        new_pattern = pattern is None
        if new_pattern:
            pattern = Pattern(customer_id=customer.id, payer_id=payer_ids[0] if payer_ids else None,
                              fingerprint=pattern_hash, normalized_text=norm.normalized,
                              template_text=norm.template, raw_example=norm.raw, structure=norm.structure,
                              source_file=transaction.source, source_row=transaction.row_index,
                              example_date=transaction.date,
                              segments=norm.segments, embedding=self.embedder.encode(norm.semantic_text),
                              embedding_model=self.embedder.name, seen_count=1, confidence=.2, last_period=period)
            session.add(pattern)
            session.flush()
        else:
            pattern.seen_count += 1
            if period > pattern.last_period and period != 'unknown':
                pattern.confidence = confidence_update(pattern.confidence)
                pattern.last_period = period
            pattern.last_seen = now()
        current = {(n['slot'], n['value']) for n in norm.numbers if n['numeric_type'] != 'AMOUNT'}
        current_digests = {(slot, fingerprint(value)) for slot, value in current}
        features = {(n.slot_index, n.numeric_value): n for n in session.scalars(
            select(NumericFeature).where(NumericFeature.pattern_id == pattern.id,
                tuple_(NumericFeature.slot_index, NumericFeature.value_digest).in_(current_digests),
                NumericFeature.numeric_value.in_({value for _, value in current})))}
        slots = {s.slot_index: s for s in session.scalars(select(NumericSlot).where(
            NumericSlot.pattern_id == pattern.id, NumericSlot.slot_index.in_({n[0] for n in current})))}
        for number in norm.numbers:
            if number['numeric_type'] == 'AMOUNT':
                continue
            key = (number['slot'], number['value'])
            slot = slots.get(number['slot'])
            if not slot:
                slot = NumericSlot(pattern_id=pattern.id, slot_index=number['slot'], slot_confidence=.2,
                                   seen_count=1, last_period=period)
                session.add(slot)
                slots[number['slot']] = slot
            else:
                slot.seen_count += 1
                if period > slot.last_period and period != 'unknown':
                    slot.slot_confidence = confidence_update(slot.slot_confidence)
                    slot.last_period = period
            feature = features.get(key)
            if feature:
                feature.seen_count += 1
                feature.missing_count = 0
                feature.last_seen = now()
                if period > feature.last_period and period != 'unknown':
                    feature.exact_value_confidence = confidence_update(feature.exact_value_confidence)
                    feature.last_period = period
            else:
                feature = NumericFeature(pattern_id=pattern.id, slot_index=number['slot'],
                                         numeric_value=number['value'], numeric_type=number['numeric_type'],
                                         exact_value_confidence=.2, seen_count=1, missing_count=0, last_period=period)
                session.add(feature)
                features[key] = feature
        # Decay missing exact values in SQL; never materialize a pattern's full history.
        if period != 'unknown':
            session.execute(update(NumericFeature).where(NumericFeature.pattern_id == pattern.id,
                NumericFeature.last_period < period,
                ~tuple_(NumericFeature.slot_index, NumericFeature.value_digest).in_(current_digests))
                .values(exact_value_confidence=NumericFeature.exact_value_confidence * .7,
                        missing_count=NumericFeature.missing_count + 1, last_period=period),
                execution_options={'synchronize_session': False})
        # Indexed postings allow bounded retrieval in the portable benchmark harness.
        tokens = {'id:' + id_key(customer.id): 8.0, 'exact:' + fingerprint(norm.normalized): 10.0}
        signature = identity_signature(norm.raw)
        if signature:
            tokens['detail:' + fingerprint(signature)] = 12.0
        tokens.update({'w:' + w: 1.0 for w in text_tokens(norm.template)})
        alias_names = list(session.scalars(select(Alias.normalized_alias).where(Alias.customer_id == customer.id)))
        tokens.update({'name:' + w: 2.0 for w in text_tokens(' '.join(alias_names + [customer.normalized_name, fold(customer_name)]))})
        tokens.update({'payer:' + value: 3.0 for value in norm.payer_accounts})
        tokens.update({'contract:' + value: 12.0 for value in norm.contract_ids})
        tokens.update({'code:' + value: 4.0 for value in norm.mixed_codes})
        tokens.update({'bound-id:' + n['value']: 10.0 for n in norm.numbers if n.get('confirmed_customer_value')})
        tokens.update({token: .3 for token in vector_buckets(pattern.embedding)})
        # Unknown protocol numbers are excluded: they often repeat across every customer.
        tokens.update({'n:' + n['value']: .8 for n in norm.numbers
                       if n['numeric_type'] in ('ACCOUNT_ID', 'CARD_ID', 'CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'INVOICE_CODE')})
        existing = set(session.scalars(select(Posting.token).where(Posting.pattern_id == pattern.id, posting_in(tokens))))
        session.add_all([Posting(token=token, pattern_id=pattern.id, weight=weight)
                         for token, weight in tokens.items() if token not in existing])
        return True

    def retrieve(self, session, norm, vector, payer, *, customer_scope=None, with_trace=False):
        """Combine bounded search rankings; only an explicit known ID scopes the search."""
        limit = self.candidate_limit
        source_limit = min(500, max(32, limit * 3))
        channels, candidate_customers = {}, {}
        if norm.customer_ids and customer_scope is None:
            owners = posting_owners(session, {'id:' + id_key(value) for value in norm.customer_ids})
            known = set().union(*owners.values()) if owners else set()
            if len(known) != 1:
                empty, trace = fuse_rankings({}, {}, limit)
                return (empty, trace) if with_trace else empty
            customer_scope = next(iter(known))

        def add_channel(name, rows):
            channels[name] = []
            counts = defaultdict(int)
            for row in rows:
                if not customer_scope and counts[row.customer_id] >= 4:
                    continue
                channels[name].append(row.id)
                candidate_customers[row.id] = row.customer_id
                counts[row.customer_id] += 1
                if len(channels[name]) >= source_limit:
                    break

        def postings_channel(name, tokens):
            if not tokens:
                return
            # Group BEFORE limiting: multiple matching tokens must not consume
            # several candidate positions for the same historical pattern.
            query = select(Pattern.id, Pattern.customer_id, func.sum(Posting.weight).label('score'),
                           (Pattern.template_text == norm.template).label('template_match'),
                           (Pattern.structure == norm.structure).label('structure_match'), Pattern.last_seen) \
                .join(Posting, Posting.pattern_id == Pattern.id).where(posting_in(tokens))
            if customer_scope:
                query = query.where(Pattern.customer_id == customer_scope)
            hits = query.group_by(Pattern.id, Pattern.customer_id, Pattern.template_text,
                                  Pattern.structure, Pattern.last_seen).subquery()
            order = (hits.c.score.desc(), hits.c.template_match.desc(), hits.c.structure_match.desc(),
                     hits.c.last_seen.desc(), hits.c.id.desc())
            if customer_scope:
                query = select(hits.c.id, hits.c.customer_id).order_by(*order).limit(source_limit)
            else:
                diversified = select(hits, func.row_number().over(
                    partition_by=hits.c.customer_id, order_by=order).label('customer_rank')).subquery()
                query = select(diversified.c.id, diversified.c.customer_id).where(diversified.c.customer_rank <= 4) \
                    .order_by(diversified.c.score.desc(), diversified.c.template_match.desc(),
                              diversified.c.structure_match.desc(), diversified.c.last_seen.desc(),
                              diversified.c.id.desc()).limit(source_limit)
            add_channel(name, session.execute(query))

        if customer_scope:
            literal_ids = select(Posting.pattern_id).where(posting_in({'n:' + value for value in norm.customer_ids}))
            add_channel('customer_id', session.execute(select(Pattern.id, Pattern.customer_id)
                .where(Pattern.customer_id == customer_scope).order_by(
                    (Pattern.normalized_text == norm.normalized).desc(),
                    (Pattern.template_text == norm.template).desc(), (Pattern.structure == norm.structure).desc(),
                    Pattern.id.in_(literal_ids).desc(), Pattern.last_seen.desc(), Pattern.id.desc()).limit(source_limit)))
        postings_channel('exact_text', {'exact:' + fingerprint(norm.normalized)})
        identifying = {'contract:' + value for value in norm.contract_ids}
        identifying.update('bound-id:' + n['value'] for n in norm.numbers if n['numeric_type'] == 'UNKNOWN_NUMBER')
        postings_channel('identity', identifying)
        structured = {'code:' + value for value in norm.mixed_codes}
        signature = identity_signature(norm.raw)
        if signature:
            structured.add('detail:' + fingerprint(signature))
        postings_channel('structured', structured)
        postings_channel('payer', {'payer:' + value for value in norm.payer_accounts})
        words = text_tokens(norm.normalized)
        tokens = {'w:' + word for word in words} | {'name:' + word for word in words}
        tokens.update('n:' + n['value'] for n in norm.numbers if n['numeric_type'] in (
            'ACCOUNT_ID', 'CARD_ID', 'CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'INVOICE_CODE'))
        tokens.update(vector_buckets(vector))
        postings_channel('postings', tokens)

        if session.bind.dialect.name == 'postgresql':
            vector_text = '[' + ','.join(str(float(v)) for v in vector) + ']'
            parameters = {'vector': vector_text, 'k': source_limit * 4, 'customer_id': customer_scope}
            if customer_scope:
                # Materialize the indexed customer scope before cosine ranking,
                # so an ANN post-filter cannot miss this customer's older variants.
                sql = ('WITH scoped AS MATERIALIZED (SELECT id, customer_id, embedding FROM transaction_patterns '
                       'WHERE customer_id = :customer_id) SELECT id, customer_id FROM scoped '
                       'ORDER BY embedding <=> CAST(:vector AS vector), id LIMIT :k')
            else:
                # Keep the distance/LIMIT form usable by the existing HNSW index.
                sql = ('SELECT id, customer_id FROM transaction_patterns '
                       'ORDER BY embedding <=> CAST(:vector AS vector) LIMIT :k')
            add_channel('semantic', session.execute(text(sql), parameters))

            scope_sql = ' AND p.customer_id = :customer_id' if customer_scope else ''
            searches = []
            if words:
                # OR tolerates extra narrative words; exact identifiers are checked
                # independently during reranking. Tokens contain only a-z letters.
                searches.append(('full_text',
                    "SELECT p.id, p.customer_id, ts_rank_cd(to_tsvector('simple', p.template_text), to_tsquery('simple', :query)) AS score "
                    "FROM transaction_patterns p WHERE to_tsvector('simple', p.template_text) @@ to_tsquery('simple', :query)" + scope_sql,
                    {'query': ' | '.join(sorted(words))}))
            searches.extend([
                ('trigram', 'SELECT p.id, p.customer_id, similarity(p.template_text, :query) AS score '
                 'FROM transaction_patterns p WHERE p.template_text % :query' + scope_sql, {'query': norm.template}),
                ('customer_name', 'SELECT p.id, p.customer_id, similarity(c.normalized_name, :query) AS score '
                 'FROM customers c JOIN transaction_patterns p ON p.customer_id = c.id '
                 'WHERE c.normalized_name % :query' + scope_sql, {'query': norm.normalized}),
            ])
            for name, sql, query_parameters in searches:
                sql = ('WITH hits AS (' + sql + '), diversified AS (SELECT hits.*, '
                       'row_number() OVER (PARTITION BY customer_id ORDER BY score DESC, id DESC) AS customer_rank FROM hits) '
                       'SELECT id, customer_id FROM diversified WHERE customer_rank <= :variants '
                       'ORDER BY score DESC, id DESC LIMIT :k')
                add_channel(name, session.execute(text(sql), {**query_parameters, 'customer_id': customer_scope,
                    'variants': source_limit if customer_scope else 4, 'k': source_limit}))
        ids, trace = fuse_rankings(channels, candidate_customers, limit, scoped=bool(customer_scope))
        trace['customer_scope'] = customer_scope
        patterns = {pattern.id: pattern for pattern in session.scalars(select(Pattern).where(Pattern.id.in_(ids)))} if ids else {}
        candidates = [patterns[pattern_id] for pattern_id in ids]
        return (candidates, trace) if with_trace else candidates

    def classify(self, session, raw, payer='BIDV'):
        self.check_model(session)
        norm = self.normalize(raw)
        # Changing thresholds must never let contradictory identifiers pass.
        guard_cap = min(.59, max(0, self.review_threshold - .01))
        def manual(reason, **evidence):
            return {'customer_id': None, 'customer_name': None, 'score': 0.0, 'decision': 'manual_check',
                    'evidence': {'reason': reason, 'reason_vi': vietnamese_reason(reason), 'candidate_count': 0, 'unknown_customer': True,
                                 'detected_customer_ids': norm.customer_ids, **evidence},
                    'normalization': norm.dict(), 'alternatives': []}
        if len({id_key(value) for value in norm.customer_ids}) > 1:
            return manual('Multiple customer IDs require manual allocation', multiple_customer_ids=True)
        id_tokens = {'id:' + id_key(value) for value in norm.customer_ids}
        bound_values = {n['value'] for n in norm.numbers if n['numeric_type'] == 'UNKNOWN_NUMBER'}
        mixed_values = {n['value'] for n in norm.numbers if n['numeric_type'] == 'MIXED_CODE'
                        and len(n['value']) >= 6 and sum(c.isdigit() for c in n['value']) >= 4}
        identity_tokens = id_tokens | {'contract:' + value for value in norm.contract_ids} | \
            {'bound-id:' + value for value in bound_values} | {'code:' + value for value in mixed_values}
        identity_owners = posting_owners(session, identity_tokens)
        customer_scope = None
        if norm.customer_ids:
            known_customers = set().union(*(identity_owners[token] for token in id_tokens))
            if not known_customers:
                return manual('Customer ID is absent from confirmed knowledge; confidence is 0%; manual check required')
            if len(known_customers) > 1:
                return manual('Customer ID maps to multiple confirmed profiles; manual check required',
                              unknown_customer=False, conflicting_customer_ids=sorted(known_customers))
            customer_scope = next(iter(known_customers))
        contract_owners = {}
        consistent_new_contracts = set()
        for value in norm.contract_ids:
            owners = identity_owners['contract:' + value]
            if not owners and value in norm.customer_ids:
                # Two explicit fields agree exactly with an already known customer.
                # This does not register the contract or learn anything during testing.
                consistent_new_contracts.add(value)
            elif not owners or (customer_scope and customer_scope not in owners):
                return manual('Unknown or conflicting water contract requires manual check', detected_contract_ids=norm.contract_ids)
            contract_owners[value] = owners
        # Ownership comes from the WHOLE knowledge, before bounded retrieval.
        # Contradictory known codes must not disappear when a competing customer
        # falls outside the candidate budget or an explicit-ID search scope.
        ownership_constraints = [owners for owners in identity_owners.values() if owners]
        compatible_owners = set.intersection(*ownership_constraints) if ownership_constraints else None
        ownership_evidence = {token: sorted(owners) for token, owners in identity_owners.items() if owners}
        if compatible_owners is not None and not compatible_owners:
            return manual('Conflicting confirmed identifiers require manual check', unknown_customer=False,
                          confirmed_identifier_owners=ownership_evidence)
        vector = self.embedder.encode(norm.semantic_text)
        mixed_code_owners = {value: identity_owners['code:' + value] for value in mixed_values}
        candidates, retrieval_trace = self.retrieve(session, norm, vector, payer,
                                                  customer_scope=customer_scope, with_trace=True)
        if not candidates:
            return manual('No confirmed customer evidence; confidence is 0%; manual check required', retrieval=retrieval_trace)
        customer_ids = {p.customer_id for p in candidates}
        customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(customer_ids)))}
        aliases = defaultdict(list)
        for alias in session.scalars(select(Alias).where(Alias.customer_id.in_(customer_ids))):
            aliases[alias.customer_id].append(alias.normalized_alias)
        pattern_ids = [p.id for p in candidates]
        numeric = defaultdict(list)
        query_values = {n['value'] for n in norm.numbers}
        for feature in session.scalars(select(NumericFeature).where(NumericFeature.pattern_id.in_(pattern_ids),
                                      NumericFeature.value_digest.in_({fingerprint(value) for value in query_values}),
                                      NumericFeature.numeric_value.in_(query_values), NumericFeature.exact_value_confidence >= .05)):
            numeric[feature.pattern_id].append(feature)
        payer_accounts = defaultdict(set)
        bank_history = set()
        for entity in session.scalars(select(Payer).where(Payer.customer_id.in_(customer_ids),
                                      Payer.payer_name.in_(norm.payer_accounts + [payer]))):
            if entity.payer_type == 'account':
                payer_accounts[entity.customer_id].add(entity.payer_name)
            else:
                bank_history.add(entity.customer_id)
        blocked = set(session.scalars(select(HardNegative.customer_id).where(
            HardNegative.transaction_fingerprint == fingerprint(norm.normalized))))
        account_owners = defaultdict(set)
        if norm.payer_accounts:
            for account, owner in session.execute(select(Payer.payer_name, Payer.customer_id).where(
                    Payer.payer_type == 'account', Payer.payer_name.in_(norm.payer_accounts))):
                account_owners[account].add(owner)
        signature = identity_signature(raw)
        detail_owners = set(session.scalars(select(Pattern.customer_id).join(Posting).where(
            posting_equal('detail:' + fingerprint(signature))))) if signature else set()
        ranked = []
        for pattern in candidates:
            identifier_guards = []
            customer = customers[pattern.customer_id]
            number_comparison = compare_ordered_numbers(norm, pattern.segments, pattern.template_text)
            unique_contract = bool(number_comparison['aligned_contracts'] and number_comparison['template_compatible'] and
                                   all(contract_owners.get(v) == {customer.id} for v in number_comparison['aligned_contracts']))
            unique_bound_tokens = [value for value in number_comparison['confirmed_customer_tokens']
                                   if identity_owners['bound-id:' + value] == {customer.id}]
            confirmed_token = bool(unique_bound_tokens)
            unique_mixed_codes = [r['query_value'] for r in number_comparison['ordered_comparison']
                                  if r['type'] == 'MIXED_CODE' and r['same_role'] and r['exact'] and r['same_format']
                                  and mixed_code_owners.get(r['query_value']) == {customer.id}]
            exact_id = any(id_key(customer.id) == id_key(value) for value in norm.customer_ids)
            alias_matches = [a for a in aliases[customer.id] if len(a) > 3 and re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', norm.normalized)]
            customer_score = 1.0 if exact_id or unique_contract or confirmed_token or unique_mixed_codes or alias_matches else 0.0
            account_match = bool(payer_accounts[customer.id])
            unique_accounts = [a for a in payer_accounts[customer.id] if account_owners[a] == {customer.id}]
            payment_similarity = SequenceMatcher(None, signature, identity_signature(pattern.raw_example), autojunk=False).ratio()
            generic_detail_words = set('thang nam ky sach sinh hoat may invoice hop dong hd truong tieu hoc mam non so cs thanh tra nop cp chi'.split())
            distinctive_overlap = (text_tokens(signature) & text_tokens(identity_signature(pattern.raw_example))) - generic_detail_words
            branch_agreement = branch_markers(raw) == branch_markers(pattern.raw_example)
            unique_details = detail_owners == {customer.id}
            payer_score = 1.0 if account_match else .2 if customer.id in bank_history else 0
            text_score = SequenceMatcher(None, norm.template, pattern.template_text, autojunk=False).ratio()
            vector_score = cosine(vector, pattern.embedding)
            text_score = .65 * text_score + .35 * vector_score
            structure = SequenceMatcher(None, norm.structure.split(), pattern.structure.split(), autojunk=False).ratio()
            matched_numbers = []
            number_score = 0.0
            for feature in numeric[pattern.id]:
                if feature.numeric_type in ('UNKNOWN_NUMBER', 'BANK_REFERENCE', 'REFERENCE_ID', 'BANK_PROTOCOL', 'RECEIVER_ACCOUNT', 'AMOUNT', 'QUANTITY'):
                    continue
                same_slot = any(r['query_value'] == feature.numeric_value and r['history_slot'] == feature.slot_index and
                                r['type'] == feature.numeric_type and r['same_role'] and r['exact'] and r['same_format']
                                for r in number_comparison['ordered_comparison'])
                if not same_slot or not number_comparison['template_compatible']:
                    continue
                weight = 1.0 if feature.numeric_type == 'CUSTOMER_ID' else .6
                contribution = feature.exact_value_confidence * weight * (1 if same_slot else .6)
                number_score = max(number_score, contribution)
                matched_numbers.append({'value': feature.numeric_value, 'type': feature.numeric_type,
                                        'slot': feature.slot_index, 'same_slot': same_slot,
                                        'confidence': round(feature.exact_value_confidence, 4)})
            number_score = max(number_score, number_comparison['ordered_agreement'] if number_comparison['template_compatible'] else 0)
            history = min(1.0, .5 * pattern.confidence + .5 * min(1, math.log1p(pattern.seen_count) / math.log(10)))
            features = {'customer': customer_score, 'payer': payer_score, 'text': text_score,
                        'structure': structure, 'number': number_score, 'history': history}
            weighted = sum(WEIGHTS[key] * value for key, value in features.items())
            score = weighted
            reason = 'Weighted historical evidence'
            # Generic wire templates cannot identify customers without independent evidence.
            # The receiving company's name contains "nước" even on unrelated transfers.
            water_intent = bool(re.search(r'\b(?:tien\s+nuoc|nuoc\s+(?:sinh|sach|may)|water|phi\s+nuoc|cp\s+nuoc)\b', fold(raw)))
            if exact_id and (text_score >= .75 or water_intent):
                score = max(score, .92 + .06 * text_score)
                reason = 'Explicit customer ID plus matching confirmed transfer template'
            elif unique_contract and water_intent:
                score = max(score, .96)
                reason = 'Exact water contract and ordered template evidence'
            elif confirmed_token and water_intent:
                score = max(score, .95)
                reason = 'Confirmed customer token at the same template position'
            elif unique_mixed_codes and water_intent and text_score >= .85 and \
                 number_comparison['stable_sequence_matches'] and number_comparison['ordered_agreement'] == 1:
                score = max(score, .95)
                reason = 'Unique exact mixed code and ordered identity evidence'
            elif unique_accounts and branch_agreement and payment_similarity >= .92 and (water_intent or payment_similarity == 1):
                score = max(score, .94 + .03 * payment_similarity)
                reason = 'Confirmed unique payer account and matching payment details'
            elif self.embedder.name != 'hash' and unique_accounts and branch_agreement and water_intent and \
                 vector_score >= .94 and payment_similarity >= .80 and len(distinctive_overlap) >= 2 and number_comparison['template_compatible']:
                score = max(score, .94 + .02 * vector_score)
                reason = 'Unique payer with semantic and exact structured evidence'
            elif unique_details and alias_matches and max(len(a.split()) for a in alias_matches) >= 2 and water_intent and \
                 (max(len(a.split()) for a in alias_matches) >= 3 or len(distinctive_overlap) >= 2):
                score = max(score, .93)
                reason = 'Unique historical payment details and confirmed customer name'
            elif not exact_id:
                score = min(score, guard_cap)
                reason = 'Name alone or shared payer does not identify a meter reliably'
            if norm.customer_ids and not exact_id:
                score = min(score, guard_cap)
                reason = 'Explicit customer ID does not match this historical customer'
                identifier_guards.append(reason)
            unresolved_contract = norm.contract_ids and set(number_comparison['aligned_contracts']) != set(norm.contract_ids) and not \
                (exact_id and consistent_new_contracts == set(norm.contract_ids) and not any(s.get('numeric_type') == 'CONTRACT_ID' for s in pattern.segments))
            if number_comparison['contract_conflict'] or unresolved_contract:
                score = min(score, guard_cap)
                reason = 'Conflicting contract or identifier order blocks acceptance'
                identifier_guards.append(reason)
            if not number_comparison['code_alignment_complete']:
                score = min(score, guard_cap)
                reason = 'Unmatched or reordered customer-specific codes require manual check'
                identifier_guards.append(reason)
            if number_comparison['unresolved_confirmed_customer_tokens'] and not exact_id:
                score = min(score, guard_cap)
                reason = 'Unmatched confirmed customer number requires manual check'
                identifier_guards.append(reason)
            if compatible_owners is not None and customer.id not in compatible_owners:
                score = min(score, guard_cap)
                reason = 'Conflicting confirmed identifiers require manual check'
                identifier_guards.append(reason)
            if customer.id in blocked:
                score = 0
                reason = 'Human feedback previously rejected this customer for this transaction'
            retrieval_sources = retrieval_trace['candidate_sources'].get(pattern.id, {})
            sources_vi = ', '.join(CHANNEL_LABELS[channel] for channel in retrieval_sources)
            ranked.append({'customer_id': customer.id, 'customer_name': customer.canonical_name,
                           'score': round(score, 4), 'evidence': {
                               'reason': reason, 'reason_vi': vietnamese_reason(reason), 'features': {k: round(v, 4) for k, v in features.items()},
                               'weights': WEIGHTS, 'weighted_score': round(weighted, 4),
                               'vector_score': round(vector_score, 4), 'embedding_model': self.embedder.name,
                               'matched_pattern_id': pattern.id, 'matched_pattern': pattern.raw_example,
                               'pattern_source': {'file': pattern.source_file, 'row': pattern.source_row,
                                                  'date': pattern.example_date},
                               'matched_template': pattern.template_text, 'matched_aliases': alias_matches,
                               'explicit_customer_id': exact_id, 'matched_numbers': matched_numbers,
                               'numeric_comparison': number_comparison, 'explicit_contract_ids': norm.contract_ids,
                               'unique_contract_match': unique_contract, 'confirmed_customer_token': confirmed_token,
                               'unique_confirmed_customer_tokens': unique_bound_tokens,
                               'confirmed_identifier_owners': ownership_evidence,
                               'identifier_guards': identifier_guards,
                               'unique_mixed_codes': unique_mixed_codes,
                               'consistent_new_contracts': sorted(consistent_new_contracts),
                               'candidate_count': len(candidates),
                               'retrieval': {key: value for key, value in retrieval_trace.items() if key != 'candidate_sources'},
                               'retrieval_sources': retrieval_sources,
                               'history_seen_count': pattern.seen_count, 'history_period': pattern.last_period,
                               'matched_payer_accounts': sorted(payer_accounts[customer.id]),
                               'query_template': norm.template,
                               'payment_detail_similarity': round(payment_similarity, 4),
                               'distinctive_detail_words': sorted(distinctive_overlap),
                               'branch_markers_agree': branch_agreement,
                               'unique_payer_accounts': unique_accounts, 'unique_payment_details': unique_details,
                               'feature_extraction': norm.extraction,
                               'match_steps': [
                                   f'Retrieved {len(candidates)} historical candidates from confirmed knowledge.',
                                   'Explicit customer ID agrees with the stored customer.' if exact_id else
                                       'Customer alias found: ' + ', '.join(alias_matches) if alias_matches else
                                       'No exact customer ID/name agreement.',
                                   f'Template similarity {text_score:.1%}; ordered structure similarity {structure:.1%}.',
                                   'Matching payer accounts: ' + ', '.join(sorted(payer_accounts[customer.id])) if account_match else
                                       'No customer-specific payer account matched.',
                                   reason],
                               'match_steps_vi': [
                                   f'Tìm được {len(candidates)} mẫu lịch sử từ dữ liệu đã xác nhận.',
                                   'Mẫu này được tìm qua: ' + sources_vi + '.',
                                   'ID khách hàng trong giao dịch trùng với ID lưu trong dữ liệu.' if exact_id else
                                       'Tên hoặc bí danh khách hàng khớp: ' + ', '.join(alias_matches) if alias_matches else
                                       'Chưa tìm thấy ID hoặc tên khách hàng khớp chính xác.',
                                   f'Độ tương đồng nội dung {text_score:.1%}; độ tương đồng thứ tự chữ/số {structure:.1%}.',
                                   'Tài khoản trả tiền khớp: ' + ', '.join(sorted(payer_accounts[customer.id])) if account_match else
                                       'Chưa tìm thấy tài khoản trả tiền riêng của khách hàng trùng khớp.',
                                   vietnamese_reason(reason)]}})
        # One result per customer so repeated patterns do not create a false tie.
        by_customer = {}
        for result in sorted(ranked, key=lambda r: (-r['score'], r['customer_id'])):
            by_customer.setdefault(result['customer_id'], result)
        ranked = list(by_customer.values())
        best = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        gap = round(best['score'] - runner_up['score'], 4) if runner_up else None
        best['evidence']['customer_candidate_count'] = len(ranked)
        best['evidence']['score_margin'] = gap
        best['evidence']['required_score_margin'] = self.match_margin
        best['evidence']['runner_up'] = ({key: runner_up[key] for key in ('customer_id', 'customer_name', 'score')}
                                       if runner_up else None)
        if (best['score'] < self.review_threshold or best['evidence']['identifier_guards']) and best['customer_id'] not in blocked:
            guard_reasons = {'Conflicting contract or identifier order blocks acceptance',
                             'Unmatched or reordered customer-specific codes require manual check',
                             'Unmatched confirmed customer number requires manual check',
                             'Conflicting confirmed identifiers require manual check'}
            manual_reason = best['evidence']['reason'] if best['evidence']['reason'] in guard_reasons else \
                'Historical candidates do not identify this customer reliably; confidence is 0%; manual check required'
            return manual(manual_reason,
                          candidate_count=len(candidates),
                          retrieval=best['evidence']['retrieval'],
                          confirmed_identifier_owners=ownership_evidence,
                          nearest_history={'customer_id': best['customer_id'], 'customer_name': best['customer_name'],
                                           **best['evidence']})
        if runner_up and runner_up['score'] >= self.review_threshold:
            best['evidence']['match_steps_vi'].append(
                f"Khách hàng kế tiếp {runner_up['customer_id']} có điểm {runner_up['score']:.1%}; "
                f"chênh lệch {gap:.1%}, yêu cầu tối thiểu {self.match_margin:.1%}.")
        if runner_up and runner_up['score'] >= self.review_threshold and gap < self.match_margin:
            best['evidence']['score_before_ambiguity_guard'] = best['score']
            best['score'] = min(best['score'], max(self.review_threshold, self.auto_threshold - .01))
            best['evidence']['reason'] += '; competing customer requires human review'
            best['evidence']['reason_vi'] = vietnamese_reason(best['evidence']['reason'])
            best['evidence']['match_steps_vi'].append('Có nhiều khách hàng có điểm gần nhau; cần kiểm tra thủ công.')
        best['decision'] = 'auto_accept' if best['score'] >= self.auto_threshold else 'review' if best['score'] >= self.review_threshold else 'reject'
        best['normalization'] = norm.dict()
        best['alternatives'] = [{k: r[k] for k in ('customer_id', 'customer_name', 'score')} for r in ranked[1:6]]
        return best

    def review(self, session, record, accepted, correct_customer_id=None, customer_name=''):
        """Human confirmation learns immediately; operational records stay outside PostgreSQL."""
        from .excel import ExcelTransaction
        self.check_model(session)
        chosen = (correct_customer_id or record.get('customer_id')) if accepted else None
        if accepted and not chosen:
            raise ValueError('Choose a customer before accepting')
        if accepted:
            if not customer_name.strip() and not session.get(Customer, str(chosen).strip()):
                existing = session.scalar(select(Pattern.id).join(Posting).where(posting_equal('id:' + id_key(chosen))).limit(1))
                if not existing:
                    raise ValueError('Enter a name for the new customer')
            chosen = self.ensure_customer(session, str(chosen).strip(), customer_name).id
        date = record.get('date') or ''
        receipt_key = learning_receipt(record['raw'], chosen, date, record.get('amount', 0), record.get('payer', 'BIDV')) if chosen else 'reject:' + record['entry_key']
        wrong = record.get('customer_id')
        negative_receipt = fingerprint('negative-review:' + record['entry_key'] + ':' + str(wrong) + ':' + str(chosen))
        previous_negative = session.scalar(select(KnowledgeReceipt.id).where(KnowledgeReceipt.fingerprint == negative_receipt))
        if not previous_negative and wrong and wrong != chosen and session.get(Customer, wrong):
            normalized = record.get('normalization') or self.normalize(record['raw']).dict()
            negative_hash = fingerprint(normalized['normalized'])
            negative = session.scalar(select(HardNegative).where(HardNegative.transaction_fingerprint == negative_hash,
                                      HardNegative.customer_id == wrong))
            if negative:
                negative.count += 1
            else:
                pattern_id = record.get('evidence', {}).get('matched_pattern_id')
                linked_pattern = session.get(Pattern, pattern_id) if pattern_id else None
                if linked_pattern is None or linked_pattern.customer_id != wrong:
                    pattern_id = None
                session.add(HardNegative(transaction_fingerprint=negative_hash, customer_id=wrong,
                                         pattern_id=pattern_id, count=1))
            matched = session.get(Pattern, record.get('evidence', {}).get('matched_pattern_id')) if record.get('evidence', {}).get('matched_pattern_id') else None
            if matched and matched.customer_id == wrong:
                matched.confidence *= .7
            session.add(KnowledgeReceipt(fingerprint=negative_receipt, customer_id=wrong, period=period_of(date)))
        learned = False
        if chosen:
            customer = session.get(Customer, chosen)
            source = record.get('source', '')
            if record.get('sheet'):
                source += ' [' + record['sheet'] + ']'
            row = ExcelTransaction(record.get('row_index') or 0, record['raw'], date=date,
                amount=record.get('amount', 0), payer=record.get('payer', 'BIDV'),
                reference=record.get('reference', ''), sheet=record.get('sheet', ''),
                source=source + ' (người dùng xác nhận)', label_status='confirmed')
            self.learn(session, row, chosen, customer_name or customer.canonical_name, receipt_key=receipt_key)
            learned = True
        return {**record, 'confirmed_customer_id': chosen,
                'confirmed_customer_name': session.get(Customer, chosen).canonical_name if chosen else '',
                'status': 'confirmed' if chosen else 'rejected', 'learned': learned,
                'reviewed_at': now()}
