from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import json
import math
import re

from sqlalchemy import delete, func, or_, select, text, tuple_, update

from .db import (Alias, CollectionMember, Customer, HardNegative, KnowledgeReceipt, Metadata,
                 NumericFeature, NumericSlot, Pattern, PaymentTemplate, Payer, Posting, RecurringLink, now)
from .embedding import Embedder, cosine, vector_buckets
from .normalize import fold, normalize_text, identity_signature, branch_markers, RuleExtractor
from .numeric_match import compare_ordered_numbers
from .name_extraction import compare_entity_names, name_fields, name_key, structured_name_fields, locate_names
from .retrieval import CHANNEL_LABELS, fuse_rankings
from .payment_templates import (collection_channel, payment_hint, payment_route, shared_key, shared_shape,
                                unique_transfer_reference)
from .id_slots import BATCH_MIN_CUSTOMERS, SlotCache, invalidate as invalidate_id_slots
from . import name_slots
from .identifiers import CUSTOMER_ID, allocation_names, confirmed_customer_id, confirmed_customer_ids, id_key, channel_key
from .interactive import resolve_channel, touch_revision
from .recommendation_groups import attach_examples, candidate_group
from .recurring import (initialize as initialize_recurring, lookup as recurring_lookup,
                        remember as remember_recurring, signature as recurring_signature,
                        negative_key as recurring_negative_key)

WEIGHTS = {'customer': .30, 'payer': .10, 'text': .20, 'structure': .15, 'number': .15, 'history': .10}
# Number roles a confirmed label may re-identify as this customer's ID token.
CONFIRMABLE_TYPES = {'UNKNOWN_NUMBER', 'INVOICE_ID', 'CONTRACT_ID', 'REFERENCE_ID', 'LOCATION_NUMBER'}
STOPWORDS = set('rem tfr ac o l tt tien nuoc thoi gian gd thanh toan chuyen khoan the tai va cua cho tu den vnd cty cong ty co phan'.split())
STOPWORDS.update('hue bidv ngan hang dau phat trien viet nam bank charge vat dtls ref tkthe bftvvnvx icbvvnvx'.split())
STOPWORDS.update('ma id kh hd ky thang nam nuoc water supply jsc hinh thuc gui nhan tk ac stk hoa don invoice'.split())
STOPWORDS.update('tthd atbp ewx bpment ft vcbcsh kbnnttsp kba chiho ibft cap sacombank vietcombank vietinbank agribank mbvcb'.split())


@lru_cache(maxsize=2048)
def _normalized_example(raw):
    return normalize_text(raw)


def vietnamese_reason(reason):
    translations = {
        'Customer ID is absent from confirmed knowledge; confidence is 0%; manual check required': 'ID khách hàng chưa có trong dữ liệu đã xác nhận. Độ tin cậy 0%; cần kiểm tra thủ công.',
        'Registered customer needs confirmation of payment purpose': 'Mã có trong danh sách khách hàng nhưng nội dung chưa đủ rõ là thanh toán tiền nước; cần kiểm tra thủ công.',
        'No confirmed customer evidence; confidence is 0%; manual check required': 'Chưa đủ bằng chứng để xác nhận khách hàng; cần kiểm tra thủ công.',
        'Historical candidates do not identify this customer reliably; confidence is 0%; manual check required': 'Các mẫu lịch sử chưa đủ để xác nhận khách hàng; cần kiểm tra thủ công.',
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
        'Full name entities differ from the historical transaction': 'Tên trong giao dịch khác tên đầy đủ trong mẫu đã học; cần kiểm tra thủ công.',
        'Full name entities could not be verified': 'Chưa đối chiếu được tên đầy đủ trong cả giao dịch và mẫu đã học; cần kiểm tra thủ công.',
        'Customer ID written in the narrative plus water-bill purpose': 'Mã khách hàng được ghi trong nội dung (tìm thấy mã của khách hàng này trong giao dịch); nội dung là thanh toán tiền nước.',
        'Multiple customer IDs require manual allocation': 'Có nhiều mã khách hàng trong cùng giao dịch; cần phân bổ và kiểm tra thủ công.',
        'Unknown or conflicting water contract requires manual check': 'Hợp đồng cấp nước chưa có bằng chứng khách hàng phù hợp trong lịch sử; cần kiểm tra thủ công.',
        'Exact water contract and ordered template evidence': 'Mã hợp đồng trùng chính xác; mẫu nội dung và vị trí số khớp với lịch sử đã xác nhận.',
        'Confirmed customer token at the same template position': 'Số đã gắn với khách hàng trong lịch sử trùng chính xác tại cùng vị trí trong mẫu nội dung.',
        'Conflicting contract or identifier order blocks acceptance': 'Mã hợp đồng hoặc thứ tự mã định danh khác mẫu lịch sử; không chấp nhận ghép.',
        'Unique payer with semantic and exact structured evidence': 'Người trả tiền duy nhất trong lịch sử, kèm nội dung tương đồng và số/vị trí đã kiểm tra chính xác.',
        'Unique exact mixed code and ordered identity evidence': 'Mã chữ/số trùng chính xác, chỉ gắn với một khách hàng trong lịch sử; toàn bộ số định danh khớp đúng thứ tự.',
        'Customer ID maps to multiple confirmed profiles; manual check required': 'Mã khách hàng ánh xạ tới nhiều hồ sơ đã xác nhận; cần kiểm tra thủ công.',
        'Conflicting confirmed identifiers require manual check': 'Các mã đã xác nhận trong giao dịch chỉ tới những khách hàng khác nhau; cần kiểm tra thủ công.',
        'Unmatched or reordered customer-specific codes require manual check': 'Mã chữ/số hoặc số cơ sở chưa khớp đầy đủ theo đúng thứ tự với mẫu lịch sử; cần kiểm tra thủ công.',
        'Unmatched confirmed customer number requires manual check': 'Số từng được xác nhận là mã khách hàng bị thiếu, đổi giá trị hoặc đổi vị trí so với mẫu lịch sử; cần kiểm tra thủ công.',
        'Shared or proxy template requires exact customer-specific numeric evidence': 'Mẫu thu hộ hoặc mẫu giao dịch chưa có mã/số riêng đủ tin cậy để xác định khách hàng; cần kiểm tra thủ công.',
        'Unknown payment route requires exact customer-specific numeric evidence': 'Chưa xác định được kiểu thanh toán và chưa có mã/số riêng đủ tin cậy của khách hàng; cần kiểm tra thủ công.',
        'Provider batch settlement covers many customers; reconcile with the provider statement': 'Khoản quyết toán tổng hợp của đơn vị thu hộ (ví/ngân hàng), gồm nhiều khách hàng trong một lần chuyển.',
        'Explicit customer ID of a customer confirmed through collection settlements': 'Mã khách hàng ghi rõ, thuộc khách hàng đã xác nhận qua bảng kê thu hộ; nội dung là thanh toán tiền nước, không có mã mâu thuẫn.',
        'Explicit customer ID in a learned position plus matching payment purpose': 'Mã khách hàng nằm ở vị trí đã học từ dữ liệu xác nhận (đúng bố cục ngân hàng/kênh), trùng khách hàng trong kho; nội dung là thanh toán tiền nước.',
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


def text_tokens(value):
    return {w for w in re.findall(r'[a-z]{2,}', value) if w not in STOPWORDS}


def confidence_update(value):
    return value + .30 * (1 - value)


def period_of(date):
    # Unknown month cannot establish recurrence across months.
    return date[:7] if re.match(r'^\d{4}-\d{2}', date or '') else 'unknown'


def newer_period(period, previous):
    return period != 'unknown' and (previous in ('', 'unknown') or period > previous)


def water_payment(raw):
    value = fold(raw)
    return payment_hint(raw).get('fixed_collection_layout', False) or bool(re.search(
        r'\b(?:tien\s*nuoc|nuoc\s+(?:sinh|sach|may)|phi\s*nuoc|(?:tt|tthd)\s+nuoc|water\s+bill)\b', value))


def pattern_identity(segments):
    # Separate different contracts/codes even when their placeholder text is identical.
    return [(n['slot'], n['numeric_type'], n['value'], n.get('format', '#')) for n in segments
            if n['type'] == 'number' and (n['numeric_type'] in
            ('CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'LOCATION_NUMBER') or n.get('confirmed_customer_value'))]


def learning_receipt(raw, customer_id, date='', amount=0, payer='BIDV', reference=''):
    """The same confirmed content exported/imported as CSV is learned only once."""
    return 'confirmed-content:' + fingerprint(json.dumps(
        [raw, id_key(customer_id), date or '', float(amount or 0), channel_key(payer), reference or ''], ensure_ascii=False))


def receipt_exists(session, receipt, transaction, customer_id):
    """Recognize pre-v7 import receipts without counting approved examples twice."""
    ids = {str(customer_id)}
    if CUSTOMER_ID.fullmatch(str(customer_id)):
        ids.add(str(int(customer_id)))  # Receipt hashes from before restoring leading zeroes.
        ids.update(cid + '.0' for cid in list(ids))
    fingerprints = {receipt}
    for cid in ids:
        fingerprints.add(fingerprint('|'.join([transaction.raw, cid, transaction.date,
                            str(transaction.amount), str(transaction.row_index)])))
        fingerprints.add(fingerprint('confirmed-content:' + fingerprint(json.dumps(
            [transaction.raw, cid, transaction.date or '', float(transaction.amount or 0),
             transaction.payer or 'BIDV'], ensure_ascii=False))))
    return session.scalar(select(KnowledgeReceipt.id).where(
        KnowledgeReceipt.fingerprint.in_(fingerprints),
        KnowledgeReceipt.customer_id == customer_id)) is not None


class Core:
    def __init__(self, embedder=None, candidate_limit=40, auto_threshold=.9, review_threshold=.6, extractor=None,
                 match_margin=.08, name_extractor=None):
        self.embedder = embedder or Embedder()
        self.candidate_limit = candidate_limit
        self.auto_threshold = auto_threshold
        self.review_threshold = review_threshold
        self.match_margin = match_margin
        self.name_extractor = name_extractor
        if not 1 <= candidate_limit <= 500:
            raise ValueError('candidate_limit must be between 1 and 500')
        if not 0 <= review_threshold < auto_threshold <= 1 or not 0 <= match_margin <= 1:
            raise ValueError('Matching thresholds and margin must be valid values between 0 and 1')
        if extractor is not None and getattr(extractor, 'provider', 'rules') != 'rules':
            raise ValueError('Local LLM extraction is disabled; use semantic embeddings with deterministic identifiers')
        self.extractor = RuleExtractor()
        self.slots = SlotCache()
        self.name_positions = name_slots.NameSlotCache()

    def normalize(self, raw):
        # Learning marks per-customer numbers; never mutate a cached/shared parse.
        return self.extractor.augment(deepcopy(_normalized_example(raw)))

    def apply_learned_slots(self, session, norm, payer='unknown'):
        """Add customer IDs found in learned ID positions; mark learned non-ID positions.

        Rule-labelled IDs stay first. A learned non-ID number (amount, reference) is excluded
        from identity ownership checks, so a coincidence with another customer's ID cannot
        block or mislead a match.
        """
        kinds = self.slots.kinds_for(session, payer)
        known = {id_key(value) for value in norm.customer_ids}
        learned = []
        for number in norm.numbers:
            kind = kinds.get(number.get('context'))
            if kind == 'not_id':
                number['learned_non_id'] = True
            elif kind == 'id' and number['numeric_type'] != 'CUSTOMER_ID':
                number['learned_customer_id'] = True
                if id_key(number['value']) not in known:
                    known.add(id_key(number['value']))
                    learned.append(number['value'])
        if learned:
            norm.customer_ids = list(dict.fromkeys(norm.customer_ids + learned))
            norm.extraction = {**(norm.extraction or {}), 'status': 'learned_id_slot' if norm.extraction.get('status') != 'explicit_id' else 'explicit_id',
                               'learned_customer_ids': sorted(learned)}
        return learned

    def batch_settlement(self, session, norm, names, payer='unknown'):
        """A layout whose single transfers were split across many confirmed customers.

        Such a credit (MoMo/Payoo/Viettel... daily settlement) pays for hundreds of bills; the
        text cannot name one customer, so it is reported as a provider batch, never guessed.
        """
        if not unique_transfer_reference(norm):
            return None
        template = session.scalar(select(PaymentTemplate).where(
            PaymentTemplate.fingerprint == shared_key(shared_shape(norm), norm.structure, payer)))
        if template is None or (template.max_transfer_customers or 1) < BATCH_MIN_CUSTOMERS:
            return None
        if template.payment_mode != 'proxy' and not template.settlement_rows:
            return None  # an organisation's multi-meter transfer, not a collection-service settlement
        customers = session.scalar(select(func.count(CollectionMember.id)).where(CollectionMember.template_id == template.id))
        examples = session.execute(select(Customer.id, Customer.canonical_name).join(
            CollectionMember, CollectionMember.customer_id == Customer.id).where(CollectionMember.template_id == template.id)
            .order_by(CollectionMember.last_seen.desc(), Customer.id).limit(20)).all()
        name, kind = template.provider_name, template.provider_kind
        if not name:
            labelled = session.execute(select(Pattern.provider_name, Pattern.provider_kind, func.count().label('n'))
                .where(Pattern.template_id == template.id, Pattern.provider_name != '')
                .group_by(Pattern.provider_name, Pattern.provider_kind).order_by(text('n DESC')).limit(1)).first()
            if labelled:
                name, kind = labelled[0], labelled[1]
        if not name:
            channel = session.execute(select(Payer.payer_name, func.count().label('n')).join(Pattern, Pattern.payer_id == Payer.id)
                .where(Pattern.template_id == template.id).group_by(Payer.payer_name).order_by(text('n DESC')).limit(1)).first()
            name = channel[0] if channel else ''
            kind = collection_channel(name) or kind
        return {'template_id': template.id, 'shared_template': template.template_text,
                'max_transfer_customers': template.max_transfer_customers, 'customer_count': customers,
                'related_customers': [{'customer_id': cid, 'customer_name': name} for cid, name in examples],
                'provider_name': name or '', 'provider_kind': kind if kind in ('bank', 'wallet', 'other') else 'unknown'}

    @staticmethod
    def bound_candidates(norm):
        return {n['value'] for n in norm.numbers if n['numeric_type'] in CONFIRMABLE_TYPES and CUSTOMER_ID.fullmatch(n['value'])
                and not n.get('learned_non_id') and not n.get('learned_customer_id')}

    def prepare_batch(self, raws):
        self.embedder.prepare([self.normalize(raw).semantic_text for raw in raws])

    def prepare_names(self, raws, check_cancel=None):
        # Name extraction stays separate from date/numeric normalization.
        if self.name_extractor:
            self.name_extractor.prepare(raws, check_cancel=check_cancel)

    def extract_names(self, raw):
        return name_fields(self.name_extractor.extract(raw)) if self.name_extractor else structured_name_fields(raw)

    def names_with_positions(self, session, raw, payer, extracted):
        """When NER finds no name, read names written at learned positions of this bank layout."""
        if extracted['extracted_names']:
            return extracted
        located = name_slots.extract(raw, self.name_positions.slots_for(session, payer))
        if not located:
            return extracted
        return name_fields({**extracted['name_extraction'], 'entities': located})

    def compare_names(self, extracted, historical_raw, saved=None, raw='', confirmed_names=(), history_slots=None):
        """Compare whole name entities; locate the candidate's confirmed names where NER found none.

        NER misses lowercase/unaccented names ("nguyen bi"). The confirmed customer name,
        folded, is then searched in that narrative to find its exact position. Names are
        still compared as complete entities, never by shared words.
        """
        historical = name_fields(saved) if saved and saved.get('entities') else self.extract_names(historical_raw)
        history_names, history_source = historical['extracted_names'], 'ner'
        if not history_names and (located := locate_names(historical_raw, confirmed_names)):
            history_names, history_source = [entity['name'] for entity in located], 'confirmed_name'
        if not history_names and history_slots and (located := name_slots.extract(historical_raw, history_slots)):
            history_names, history_source = [entity['name'] for entity in located], 'learned_name_position'
        query_names, query_source = extracted['extracted_names'], 'ner'
        if not query_names and (located := locate_names(raw, confirmed_names)):
            query_names, query_source = [entity['name'] for entity in located], 'confirmed_name'
        comparison = compare_entity_names(query_names, history_names)
        query_source = next((e['source'] for e in extracted['name_extraction'].get('entities', [])
                             if e['source'] == 'learned_name_position'), query_source)
        if {'confirmed_name', 'learned_name_position'} & {query_source, history_source}:
            comparison.update(query_source=query_source, history_source=history_source)
        return comparison

    def shared_collection_template(self, session, norm, payer, names):
        """Verify a bank/provider layout independently of the customer's own patterns.

        The customer ID identifies the recipient; another confirmed recipient can
        supply the shared layout. A settlement roster never identifies one recipient.
        """
        shapes = {shared_shape(norm), shared_shape(norm, names)}
        keys = {shared_key(shape, norm.structure, payer) for shape in shapes}
        templates = session.scalars(select(PaymentTemplate).where(
            PaymentTemplate.fingerprint.in_(keys), PaymentTemplate.channel_key == channel_key(payer),
            PaymentTemplate.max_transfer_customers < BATCH_MIN_CUSTOMERS).order_by(PaymentTemplate.id))
        for template in templates:
            if template.template_text not in shapes or template.structure != norm.structure:
                continue
            # Require a confirmed example in which this layout's ID really is the
            # approved recipient, rather than a merely mentioned, differently labelled ID.
            examples = session.scalars(select(Pattern).where(
                Pattern.template_id == template.id, Pattern.channel_key == channel_key(payer),
                (Pattern.payment_mode == 'proxy') |
                ((Pattern.payment_mode == 'unknown') & (template.payment_mode == 'proxy'))
                ).order_by(Pattern.id).execution_options(yield_per=100))
            for example in examples:
                historical = self.normalize(example.raw_example)
                self.apply_learned_slots(session, historical, payer)
                if (historical.customer_ids == [example.customer_id] and
                        not any(n.get('learned_non_id') and n['numeric_type'] == 'CUSTOMER_ID' for n in historical.numbers)):
                    return template, example
        return None

    def learning_alias_signature(self):
        names = self.name_extractor.learning_signature() if self.name_extractor else 'bank-name-alias-v1'
        return 'confirmed-evidence-v7:' + names

    def ensure_template(self, session, norm, names, payer='unknown'):
        shape = shared_shape(norm, names)
        channel = resolve_channel(session, payer)
        key = shared_key(shape, norm.structure, channel)
        template = session.scalar(select(PaymentTemplate).where(PaymentTemplate.fingerprint == key))
        if template is None:
            template = PaymentTemplate(fingerprint=key, template_text=shape, structure=norm.structure, channel_key=channel)
            session.add(template)
            session.flush()
        elif template.template_text != shape or template.structure != norm.structure:
            raise ValueError('Khóa mẫu giao dịch không nhất quán; cần kiểm tra kho kiến thức.')
        return template

    def ensure_alias(self, session, customer, name, confidence=1.0):
        name = str(name or '').strip()
        if not name:
            return False
        normalized = ' '.join(fold(name).split())
        alias = session.scalar(select(Alias).where(Alias.customer_id == customer.id,
                                                  Alias.normalized_alias == normalized))
        if alias is not None:
            # A human-confirmed name may promote an automatically extracted alias.
            alias.confidence = max(alias.confidence, confidence)
            return False
        session.add(Alias(customer_id=customer.id, alias=name, normalized_alias=normalized,
                          confidence=confidence))
        name_keys = {'name:' + word for word in text_tokens(normalized)}
        if name_keys:
            pattern_ids = list(session.scalars(select(Pattern.id).where(Pattern.customer_id == customer.id)))
            existing = set(session.execute(select(Posting.pattern_id, Posting.token).where(
                Posting.pattern_id.in_(pattern_ids), posting_in(name_keys))).all())
            session.add_all([Posting(pattern_id=pattern_id, token=token, weight=2.0)
                             for pattern_id in pattern_ids for token in name_keys
                             if (pattern_id, token) not in existing])
        return True

    def learn_names(self, session, customer, raw, extraction=None):
        extraction = extraction if extraction is not None else self.extract_names(raw)
        # Several people may be mentioned; do not attach all of them to one ID.
        names = extraction['extracted_names']
        if len(names) != 1:
            return False
        entities = [entity for entity in extraction['name_extraction']['entities'] if entity['name'] == names[0]]
        if not entities:
            return False
        confidence = min(.95, max(entity.get('score') or .8 for entity in entities))
        return self.ensure_alias(session, customer, names[0], confidence=confidence)

    def check_model(self, session, writing=False):
        maintenance = session.get(Metadata, 'knowledge_maintenance')
        if maintenance and maintenance.value == 'running':
            raise ValueError('Kho kiến thức đang được cập nhật chỉ mục. Chờ công cụ bảo trì hoàn tất rồi khởi động lại dịch vụ.')
        meta = session.get(Metadata, 'embedding_model')
        if meta and meta.value != self.embedder.name:
            raise ValueError(f'Knowledge uses embedding model {meta.value!r}; configure that model or rebuild into a new database')
        if writing and not meta:
            session.add(Metadata(key='embedding_model', value=self.embedder.name))
            session.flush()
        extraction = session.get(Metadata, 'feature_extractor')
        if ((extraction and extraction.value != self.extractor.name) or (not extraction and
                session.scalar(select(Pattern.id).where(Pattern.normalizer_version != self.extractor.name).limit(1)))):
            raise ValueError('Kho kiến thức dùng bộ trích xuất cũ. Chạy SQL nâng cấp và scripts/reindex_knowledge.py theo docs/migration-and-storage.md trước khi sử dụng.')
        if writing and not extraction:
            session.add(Metadata(key='feature_extractor', value=self.extractor.name))
            session.flush()

    def ensure_customer(self, session, customer_id, customer_name):
        customer_id = confirmed_customer_id(customer_id)
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
        elif customer_name and customer.canonical_name in (customer.id, id_key(customer.id)):
            customer.canonical_name = str(customer_name).strip()
            customer.normalized_name = fold(customer.canonical_name)
        name = str(customer_name or customer.canonical_name).strip()
        self.ensure_alias(session, customer, name)
        return customer

    def learn(self, session, transaction, customer_id, customer_name='', receipt_key=None, *, learn_name_alias=True):
        """Only called for an explicitly confirmed import or human feedback."""
        if transaction.label_status in ('skipped', 'unresolved'):
            return False
        if not math.isfinite(transaction.amount) or transaction.amount < 0:
            raise ValueError('Số tiền của dòng học chưa hợp lệ.')
        allocation = confirmed_customer_ids(customer_id)
        if len(allocation) > 1:
            # Do not divide amounts or concatenate identifiers. The approved file
            # explicitly associates this narrative with every listed customer.
            learned = False
            for cid, name in zip(allocation, allocation_names(allocation, customer_name)):
                learned = self.learn(session, transaction, cid, name,
                    receipt_key=(receipt_key + ':' + cid) if receipt_key else None, learn_name_alias=False) or learned
            return learned
        customer_id = allocation[0]
        norm = self.normalize(transaction.raw)
        extracted = self.extract_names(transaction.raw)
        detected_keys = {id_key(value) for value in norm.customer_ids}
        # Confirmed labels are trusted even when the payer wrote another/mistyped ID or the
        # rules misread one: the label is stored as this customer's link, and the other
        # mentioned IDs are never assigned to it. A single transfer may pay for several
        # customers; each confirmed label gets its own link, ordered numbers and receipt.
        multiple_customers = len(detected_keys) > 1
        # A water contract may be shared by several confirmed customer IDs.
        # Keep each customer's values/link separately.
        self.check_model(session, writing=True)
        initialize_recurring(session)
        touch_revision(session)
        learning_channel = resolve_channel(session, transaction.payer, learning=True)
        receipt_key = receipt_key or learning_receipt(transaction.raw, customer_id, transaction.date,
            transaction.amount, learning_channel, transaction.reference)
        receipt = fingerprint(receipt_key)
        customer = self.ensure_customer(session, customer_id, customer_name)
        already_learned = receipt_exists(session, receipt, transaction, customer.id)
        if learn_name_alias and not multiple_customers and (not detected_keys or id_key(customer.id) in detected_keys):
            # A payer's name in a multi-customer transfer is not necessarily the
            # name of any individual recipient. Explicitly confirmed names still apply.
            self.learn_names(session, customer, transaction.raw, extracted)
        for number in norm.numbers:
            # The label tells which number is the customer ID, whatever role the rules guessed
            # (e.g. AB bank writes the IDKH after "ma hoa don").
            if number['numeric_type'] in CONFIRMABLE_TYPES | {'CUSTOMER_ID'} and CUSTOMER_ID.fullmatch(number['value']) and id_key(number['value']) == id_key(customer.id):
                number['confirmed_customer_value'] = True
                next(s for s in norm.segments if s.get('slot') == number['slot'])['confirmed_customer_value'] = True
        period = period_of(transaction.date)
        session.execute(delete(HardNegative).where(HardNegative.customer_id == customer.id,
            HardNegative.transaction_fingerprint.in_((fingerprint(norm.normalized),
                recurring_negative_key(recurring_signature(norm, learning_channel)[0])))))
        if not already_learned:
            session.add(KnowledgeReceipt(fingerprint=receipt, customer_id=customer.id, period=period))
        payer_ids = []
        for payer_name in dict.fromkeys([transaction.payer or 'unknown'] + norm.payer_accounts):
            payer = session.scalar(select(Payer).where(Payer.customer_id == customer.id, Payer.payer_name == payer_name))
            if not payer:
                payer = Payer(customer_id=customer.id, payer_name=payer_name,
                              payer_type='account' if payer_name in norm.payer_accounts else 'bank',
                              confidence=.2, seen_count=1, last_period=period)
                session.add(payer)
                session.flush()
            elif not already_learned:
                payer.seen_count += 1
                if newer_period(period, payer.last_period):
                    payer.confidence = confidence_update(payer.confidence)
                    payer.last_period = period
                payer.last_seen = now()
            payer_ids.append(payer.id)
        identity = pattern_identity(norm.segments)
        channel = resolve_channel(session, transaction.payer)
        pattern_hash = fingerprint(json.dumps([channel, norm.template, norm.structure, identity], ensure_ascii=False))
        pattern = session.scalar(select(Pattern).where(Pattern.customer_id == customer.id, Pattern.fingerprint == pattern_hash))
        if pattern is None:
            # Reuse compatible older records without forcing a database/model rebuild.
            legacy = session.scalars(select(Pattern).where(Pattern.customer_id == customer.id,
                Pattern.template_text == norm.template, Pattern.structure == norm.structure,
                Pattern.channel_key == channel))
            pattern = next((candidate for candidate in legacy if pattern_identity(candidate.segments) == identity), None)
        names = extracted['extracted_names'] + [customer.canonical_name, customer_name]
        template = self.ensure_template(session, norm, names, channel)
        route = payment_route(transaction.raw, transaction.payment_mode, transaction.provider_kind, transaction.provider_name)
        if not transaction.payment_mode and template.payment_mode != 'unknown':
            route = {key: getattr(template, key) for key in ('payment_mode', 'provider_kind', 'provider_name')}
        elif not transaction.payment_mode and (channel_kind := collection_channel(channel)):
            # The confirmed file names the collection channel (MoMo, Payoo, ...): a real label.
            route = {'payment_mode': 'proxy', 'provider_kind': channel_kind, 'provider_name': transaction.payer}
        elif not transaction.payment_mode and payment_hint(transaction.raw).get('fixed_collection_layout'):
            route = {'payment_mode': 'proxy', 'provider_kind': 'bank', 'provider_name': transaction.payer}
        if pattern is not None:
            session.execute(delete(Posting).where(Posting.pattern_id == pattern.id,
                Posting.token.startswith('template:'), Posting.token != 'template:' + template.fingerprint))
        if already_learned and pattern is not None:
            pattern.template_id = template.id
            if transaction.payment_mode or pattern.payment_mode == 'unknown':
                for key, value in route.items():
                    setattr(pattern, key, value)
            template_token = 'template:' + template.fingerprint
            if not session.scalar(select(Posting.id).where(Posting.pattern_id == pattern.id, posting_equal(template_token))):
                session.add(Posting(pattern_id=pattern.id, token=template_token, weight=2.0))
            for key in route:
                setattr(transaction, key, getattr(pattern, key))
            remember_recurring(session, pattern, norm, transaction, already_learned=True,
                               name_extraction=extracted['name_extraction'])
            return False
        new_pattern = pattern is None
        if new_pattern:
            # A newly approved example can contradict an aggregate slot rule. Retire
            # affected rules until the next rebuild; exact approved postings work now.
            invalidate_id_slots(session, {(channel, n['context']) for n in norm.numbers if n.get('context')})
            pattern = Pattern(customer_id=customer.id, template_id=template.id, **route, payer_id=payer_ids[0] if payer_ids else None,
                              channel_key=channel, normalizer_version=self.extractor.name,
                              label_metadata={'mentioned_customer_ids': norm.customer_ids,
                                  'explicit_ids_agree': not detected_keys or id_key(customer.id) in detected_keys,
                                  'multiple_mentions': multiple_customers},
                              fingerprint=pattern_hash, normalized_text=norm.normalized,
                              template_text=norm.template, raw_example=norm.raw, structure=norm.structure,
                              source_file=transaction.source, source_row=transaction.row_index,
                              example_date=transaction.date,
                              segments=norm.segments, embedding=self.embedder.encode(norm.semantic_text),
                              embedding_model=self.embedder.name, seen_count=1, confidence=.2, last_period=period)
            session.add(pattern)
            session.flush()
        else:
            pattern.template_id = template.id
            if transaction.payment_mode or pattern.payment_mode == 'unknown':
                for key, value in route.items():
                    setattr(pattern, key, value)
            pattern.seen_count += 1
            if newer_period(period, pattern.last_period):
                pattern.confidence = confidence_update(pattern.confidence)
                pattern.last_period = period
            pattern.last_seen = now()
        remember_recurring(session, pattern, norm, transaction, already_learned=already_learned,
                           name_extraction=extracted['name_extraction'])
        # Return the effective stored label to feedback/export callers as well.
        for key in route:
            setattr(transaction, key, getattr(pattern, key))
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
                if newer_period(period, slot.last_period):
                    slot.slot_confidence = confidence_update(slot.slot_confidence)
                    slot.last_period = period
            feature = features.get(key)
            if feature:
                feature.seen_count += 1
                feature.missing_count = 0
                feature.last_seen = now()
                if newer_period(period, feature.last_period):
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
        self.index_pattern(session, pattern, norm, customer)
        if unique_transfer_reference(norm):
            # Same narrative with a per-transfer reference = one bank credit split across customers.
            exact = 'exact:' + fingerprint(norm.normalized)
            split = len(posting_owners(session, {exact})[exact])
            if split > (template.max_transfer_customers or 1):
                template.max_transfer_customers = split
        return True

    def index_pattern(self, session, pattern, norm, customer, replace=False):
        if replace:
            session.execute(delete(Posting).where(Posting.pattern_id == pattern.id))
        template = session.get(PaymentTemplate, pattern.template_id)
        tokens = {'id:' + id_key(customer.id): 8.0, 'exact:' + fingerprint(norm.normalized): 10.0}
        tokens['template:' + template.fingerprint] = 2.0
        signature = identity_signature(norm.raw)
        if signature:
            tokens['detail:' + fingerprint(signature)] = 12.0
        tokens.update({'w:' + w: 1.0 for w in text_tokens(norm.template)})
        alias_names = list(session.scalars(select(Alias.normalized_alias).where(Alias.customer_id == customer.id)))
        tokens.update({'name:' + w: 2.0 for w in text_tokens(' '.join(alias_names + [customer.normalized_name]))})
        tokens.update({'payer:' + value: 3.0 for value in norm.payer_accounts})
        tokens.update({'contract:' + value: 12.0 for value in norm.contract_ids})
        tokens.update({'code:' + value: 4.0 for value in norm.mixed_codes})
        tokens.update({'bound-id:' + n['value']: 10.0 for n in norm.numbers if n.get('confirmed_customer_value')})
        tokens.update({'slot-id:' + fingerprint(pattern.channel_key + '\x1f' + n['context']) + ':' + n['value']: 14.0
                       for n in norm.numbers if n.get('confirmed_customer_value') and n.get('context')})
        tokens.update({token: .3 for token in vector_buckets(pattern.embedding)})
        # Unknown protocol numbers are excluded: they often repeat across every customer.
        tokens.update({'n:' + n['value']: .8 for n in norm.numbers
                       if n['numeric_type'] in ('ACCOUNT_ID', 'CARD_ID', 'CUSTOMER_ID', 'CONTRACT_ID', 'MIXED_CODE', 'INVOICE_CODE')})
        existing = set(session.scalars(select(Posting.token).where(Posting.pattern_id == pattern.id, posting_in(tokens))))
        session.add_all([Posting(token=token, pattern_id=pattern.id, weight=weight)
                         for token, weight in tokens.items() if token not in existing])

    def learn_settlement(self, session, transaction, customer_id, customer_name, split, receipt_key=None):
        """Compact learning for one customer row of a provider batch settlement.

        The shared settlement text never identifies a customer, so no per-customer pattern,
        vector, numbers or postings are stored (they could only ever be guarded to manual).
        Kept: the confirmed customer, membership, name, receipt and layout statistics that let
        matching report the next settlement of this provider as a batch.
        """
        if transaction.label_status in ('skipped', 'unresolved'):
            return False
        allocation = confirmed_customer_ids(customer_id)
        if len(allocation) > 1:
            learned = False
            for cid, name in zip(allocation, allocation_names(allocation, customer_name)):
                learned = self.learn_settlement(session, transaction, cid, name, split,
                    receipt_key=(receipt_key + ':' + cid) if receipt_key else None) or learned
            return learned
        self.check_model(session, writing=True)
        if not math.isfinite(transaction.amount) or transaction.amount < 0:
            raise ValueError('Số tiền của dòng học chưa hợp lệ.')
        touch_revision(session)
        learning_channel = resolve_channel(session, transaction.payer, learning=True)
        receipt = fingerprint(receipt_key or learning_receipt(transaction.raw, allocation[0], transaction.date,
            transaction.amount, learning_channel, transaction.reference))
        customer = self.ensure_customer(session, allocation[0], customer_name)
        already_learned = receipt_exists(session, receipt, transaction, customer.id)
        if not already_learned:
            session.add(KnowledgeReceipt(fingerprint=receipt, customer_id=customer.id, period=period_of(transaction.date)))
        norm = self.normalize(transaction.raw)
        template = self.ensure_template(session, norm, [], transaction.payer)
        member = session.scalar(select(CollectionMember).where(CollectionMember.template_id == template.id,
                                                               CollectionMember.customer_id == customer.id))
        if member is None:
            session.add(CollectionMember(template_id=template.id, customer_id=customer.id,
                                         last_period=period_of(transaction.date)))
        elif not already_learned:
            member.seen_count += 1
            if newer_period(period_of(transaction.date), member.last_period):
                member.last_period = period_of(transaction.date)
            member.last_seen = now()
        template.max_transfer_customers = max(template.max_transfer_customers or 1, split)
        if template.payment_mode == 'unknown' and not transaction.payment_mode:
            if channel_kind := collection_channel(resolve_channel(session, transaction.payer)):
                template.payment_mode, template.provider_kind, template.provider_name = 'proxy', channel_kind, transaction.payer
        elif transaction.payment_mode and template.payment_mode == 'unknown':
            route = payment_route(transaction.raw, transaction.payment_mode, transaction.provider_kind, transaction.provider_name)
            template.payment_mode, template.provider_kind, template.provider_name = (
                route['payment_mode'], route['provider_kind'], route['provider_name'])
        if not already_learned:
            template.settlement_rows = (template.settlement_rows or 0) + 1
        return not already_learned

    def retrieve(self, session, norm, vector, payer, *, customer_scope=None, with_trace=False, extracted_names=None):
        """Combine bounded search rankings; only an explicit known ID scopes the search."""
        limit = self.candidate_limit
        source_limit = min(500, max(32, limit * 3))
        channels, candidate_customers = {}, {}
        channel = channel_key(payer)
        if len(norm.customer_ids) == 1 and customer_scope is None:
            owners = posting_owners(session, {'id:' + id_key(value) for value in norm.customer_ids})
            known = set().union(*owners.values()) if owners else set()
            if len(known) == 1:
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
                           (Pattern.structure == norm.structure).label('structure_match'),
                           (Pattern.channel_key == channel).label('channel_match'), Pattern.last_seen) \
                .join(Posting, Posting.pattern_id == Pattern.id).where(posting_in(tokens))
            if customer_scope:
                query = query.where(Pattern.customer_id == customer_scope)
            hits = query.group_by(Pattern.id, Pattern.customer_id, Pattern.template_text,
                                  Pattern.structure, Pattern.channel_key, Pattern.last_seen).subquery()
            order = (hits.c.score.desc(), hits.c.template_match.desc(), hits.c.structure_match.desc(),
                     hits.c.channel_match.desc(), hits.c.last_seen.desc(), hits.c.id.desc())
            if customer_scope:
                query = select(hits.c.id, hits.c.customer_id).order_by(*order).limit(source_limit)
            else:
                diversified = select(hits, func.row_number().over(
                    partition_by=hits.c.customer_id, order_by=order).label('customer_rank')).subquery()
                query = select(diversified.c.id, diversified.c.customer_id).where(diversified.c.customer_rank <= 4) \
                    .order_by(diversified.c.score.desc(), diversified.c.template_match.desc(),
                              diversified.c.structure_match.desc(), diversified.c.channel_match.desc(), diversified.c.last_seen.desc(),
                              diversified.c.id.desc()).limit(source_limit)
            add_channel(name, session.execute(query))

        if customer_scope:
            literal_ids = select(Posting.pattern_id).where(posting_in({'n:' + value for value in norm.customer_ids}))
            add_channel('customer_id', session.execute(select(Pattern.id, Pattern.customer_id)
                .where(Pattern.customer_id == customer_scope).order_by(
                    (Pattern.normalized_text == norm.normalized).desc(),
                    (Pattern.template_text == norm.template).desc(), (Pattern.structure == norm.structure).desc(),
                    Pattern.id.in_(literal_ids).desc(), (Pattern.channel_key == channel).desc(), Pattern.last_seen.desc(), Pattern.id.desc()).limit(source_limit)))
        else:
            postings_channel('customer_id', {'id:' + id_key(value) for value in norm.customer_ids})
        postings_channel('exact_text', {'exact:' + fingerprint(norm.normalized)})
        identifying = {'contract:' + value for value in norm.contract_ids}
        identifying.update('bound-id:' + value for value in self.bound_candidates(norm))
        identifying.update('slot-id:' + fingerprint(channel + '\x1f' + number['context']) + ':' + number['value']
                           for number in norm.numbers if number.get('context') and not number.get('learned_non_id'))
        postings_channel('identity', identifying)
        structured = {'code:' + value for value in norm.mixed_codes}
        signature = identity_signature(norm.raw)
        if signature:
            structured.add('detail:' + fingerprint(signature))
        postings_channel('structured', structured)
        postings_channel('payer', {'payer:' + value for value in norm.payer_accounts})
        name_words = text_tokens(' '.join(fold(name) for name in (extracted_names or [])))
        postings_channel('name_entity', {'name:' + word for word in name_words})
        shape = shared_shape(norm, extracted_names or ())
        postings_channel('shared_template', {'template:' + shared_key(shape, norm.structure, channel)})
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

    def recommend_customers(self, session, norm, payer, extracted):
        """Deterministic fallbacks, including customers without a stored pattern.

        A vector neighbour or a common bank layout is not enough to recommend a person.
        This search needs a code, account, confirmed name, or distinctive learned text.
        Recommendations never authorize learning or automatic acceptance.
        """
        proposals = {}

        def offer(cid, score, source):
            item = proposals.setdefault(cid, {'customer_id': cid, 'score': 0, 'sources': []})
            item['score'] = max(item['score'], score)
            if source not in item['sources']:
                item['sources'].append(source)

        values = {number['value'] for number in norm.numbers if CUSTOMER_ID.fullmatch(number['value'])
                  and number['numeric_type'] in CONFIRMABLE_TYPES | {'CUSTOMER_ID'}
                  and not number.get('learned_non_id')}
        written = set(norm.customer_ids)
        direct = list(session.scalars(select(Customer).where(Customer.id.in_(values | written)))) if values or written else []
        for customer in direct:
            offer(customer.id, .59 if customer.id in written else .35,
                  'Mã khách hàng trong nội dung' if customer.id in written else 'Số 6 chữ số trùng hồ sơ; cần kiểm tra vai trò')
        accounts = set(norm.payer_accounts)
        if accounts:
            for cid in session.scalars(select(Payer.customer_id).where(Payer.payer_type == 'account', Payer.payer_name.in_(accounts)).distinct()):
                offer(cid, .45, 'Tài khoản hoặc thẻ từng thanh toán cho khách hàng')
        tokens = {'contract:' + value for value in norm.contract_ids}
        channel = channel_key(payer)
        tokens.update('slot-id:' + fingerprint(channel + '\x1f' + n['context']) + ':' + n['value']
                      for n in norm.numbers if n.get('context') and not n.get('learned_non_id'))
        detail_words = text_tokens(identity_signature(norm.raw)) - set('ky thang nam sach sinh hoat may hop dong hd hoa don invoice hue water supply jsc kh id ma'.split())
        if values or accounts or len(detail_words) >= 2:
            tokens.add('exact:' + fingerprint(norm.normalized))
            signature = identity_signature(norm.raw)
            if signature:
                tokens.add('detail:' + fingerprint(signature))
        for token, owners in posting_owners(session, tokens).items():
            for cid in sorted(owners)[:20]:
                offer(cid, .65 if token.startswith('exact:') else .55,
                      'Nội dung đã được xác nhận' if token.startswith(('exact:', 'detail:')) else 'Mã hoặc vị trí số đã học')
        # Indexed exact phrases avoid an arbitrary broad-search cap hiding a common
        # Vietnamese name in a large customer registry.
        name_words = re.findall(r'[a-z]+', norm.normalized)[:200]
        phrases = {fold(name) for name in extracted['extracted_names']}
        phrases.update(' '.join(name_words[start:start + width])
                       for width in range(2, 7) for start in range(len(name_words) - width + 1))
        if phrases:
            for cid, confidence in session.execute(select(Alias.customer_id, Alias.confidence).where(
                    Alias.normalized_alias.in_(phrases)).limit(100)):
                offer(cid, .55 if confidence >= 1 else .4, 'Tên hoặc bí danh trùng nội dung')
        words = sorted(text_tokens(norm.normalized), key=lambda word: (-len(word), word))[:8]
        if words:
            query = select(Alias.customer_id, Alias.normalized_alias, Alias.confidence).where(
                or_(*(Alias.normalized_alias.contains(word, autoescape=True) for word in words))).limit(400)
            extracted_keys = {name_key(name) for name in extracted['extracted_names']}
            for cid, alias, confidence in session.execute(query):
                if not any(char.isalpha() for char in alias) or len(alias.split()) < 2:
                    continue
                if re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', norm.normalized) or name_key(alias) in extracted_keys:
                    offer(cid, .55 if confidence >= 1 else .4, 'Tên hoặc bí danh trùng nội dung')
        blocked = set(session.scalars(select(HardNegative.customer_id).where(
            HardNegative.transaction_fingerprint.in_((fingerprint(norm.normalized),
                recurring_negative_key(recurring_signature(norm, payer)[0]))))))
        written_keys = {id_key(value) for value in written}
        if len(written_keys) == 1:
            # One IDKH written in the narrative (e.g. BIDV O@L) names the recipient. A payer
            # account/name/number that points to ANOTHER customer is kept only as a demoted,
            # labelled hint (the same payer often pays several meters); it never becomes the
            # row's customer. Content confirmed for that customer keeps its rank.
            weak = {'Tài khoản hoặc thẻ từng thanh toán cho khách hàng', 'Tên hoặc bí danh trùng nội dung',
                    'Số 6 chữ số trùng hồ sơ; cần kiểm tra vai trò'}
            label = 'Khác mã ' + next(iter(sorted(written))) + ' ghi trong nội dung'
            for cid, item in proposals.items():
                if id_key(cid) not in written_keys and set(item['sources']) <= weak:
                    item.update(score=min(item['score'], .3), conflicts_written_id=True)
                    item['sources'].append(label)
        ordered = sorted((item for cid, item in proposals.items() if cid not in blocked),
                         key=lambda item: (-item['score'], item['customer_id']))[:6]
        names = dict(session.execute(select(Customer.id, Customer.canonical_name).where(
            Customer.id.in_([item['customer_id'] for item in ordered]))).all()) if ordered else {}
        result = [{**item, 'customer_name': names[item['customer_id']]} for item in ordered if item['customer_id'] in names]
        if result:
            example = session.scalar(select(Pattern).where(Pattern.customer_id == result[0]['customer_id']).order_by(
                (Pattern.normalized_text == norm.normalized).desc(), (Pattern.template_text == norm.template).desc(),
                (Pattern.channel_key == channel).desc(), Pattern.last_seen.desc(), Pattern.id.desc()).limit(1))
            if example:
                result[0]['evidence'] = {'matched_pattern_id': example.id, 'matched_pattern': example.raw_example,
                    'matched_template': example.template_text, 'query_template': norm.template,
                    'pattern_source': {'file': example.source_file, 'row': example.source_row, 'date': example.example_date},
                    'numeric_comparison': compare_ordered_numbers(norm, example.segments, example.template_text),
                    'reason_vi': '; '.join(result[0]['sources'])}
        return result

    def recommendation_group(self, session, raw, payer, *, names=(), suggestions=None, q='', offset=0, limit=6,
                             include_examples=False):
        payer = resolve_channel(session, payer)
        norm = self.normalize(raw)
        self.apply_learned_slots(session, norm, payer)
        if suggestions is None:
            suggestions = self.recommend_customers(session, norm, payer, {'extracted_names': list(names)})
        group = candidate_group(session, norm, payer, names, suggestions=suggestions, q=q, offset=offset, limit=limit)
        if include_examples:
            attach_examples(session, norm, payer, group['members'])
            for member in group['members']:
                for example in member['examples']:
                    if example['kind'] != 'collection':
                        historical = self.normalize(example['content'])
                        example['numeric_comparison'] = compare_ordered_numbers(norm, historical.segments, historical.template)
        return group

    def classify(self, session, raw, payer='BIDV', *, payment_mode='', provider_kind='', provider_name=''):
        self.check_model(session)
        input_payer = payer
        payer = resolve_channel(session, payer)
        norm = self.normalize(raw)
        written_ids = set(norm.customer_ids)
        learned_ids = self.apply_learned_slots(session, norm, payer)
        extracted = self.names_with_positions(session, raw, payer, self.extract_names(raw))
        extracted['channel_resolution'] = {'input': input_payer, 'canonical': payer,
            'source': 'confirmed_alias' if ' '.join(fold(input_payer).split()) in session.info.get('bank_alias_map', {}) else 'standard_name'}
        route_evidence = {**payment_hint(raw), 'source': 'input_label' if payment_mode else 'unresolved'}
        route = payment_route(raw, payment_mode, provider_kind, provider_name)
        if not payment_mode and collection_channel(payer):
            route = {'payment_mode': 'proxy', 'provider_kind': collection_channel(payer), 'provider_name': payer}
            route_evidence.update(source='collection_channel', reason_vi='Kênh thanh toán là dịch vụ thu hộ.')
        elif not payment_mode and route_evidence.get('fixed_collection_layout'):
            route = {'payment_mode': 'proxy', 'provider_kind': 'bank', 'provider_name': payer}
            route_evidence['source'] = 'bank_layout'
        if payment_mode:
            route_evidence['reason_vi'] = ('Kiểu thanh toán theo nhãn được cung cấp trong dữ liệu đối soát.'
                if route['payment_mode'] != 'unknown' else 'Dữ liệu nguồn đánh dấu kiểu thanh toán là Chưa xác định.')
        # Changing thresholds must never let contradictory identifiers pass.
        guard_cap = min(.59, max(0, self.review_threshold - .01))
        recurrence = recurring_lookup(session, norm, payer)
        recurrence_summary = ({key: value for key, value in recurrence.items() if key != 'link'} if recurrence else None)
        def manual(reason, detail_vi='', suggested_customer_id=None, recommendations=None, **evidence):
            recommendations = self.recommend_customers(session, norm, payer, extracted) if recommendations is None else recommendations
            if recurrence:
                remembered = [item for item in recurrence['owners'] if item['customer_id'] not in recurrence['blocked_customer_ids']]
                merged = {}
                for item in remembered + recommendations:
                    merged.setdefault(item['customer_id'], item)
                recommendations = [item for item in merged.values()
                                   if item['customer_id'] not in recurrence['blocked_customer_ids']][:6]
                evidence['confirmed_recurrence'] = recurrence_summary
            best = next((item for item in recommendations if not item.get('conflicts_written_id')), None)
            unknown = bool(evidence.get('unknown_customer', False))
            nearest = evidence.get('nearest_history')
            if recurrence and best and nearest is None:
                remembered = session.scalar(select(RecurringLink).where(RecurringLink.customer_id == best['customer_id'],
                    RecurringLink.signature_hash == recurrence['signature_hash'], RecurringLink.channel_key == payer).limit(1))
                if remembered:
                    previous = self.normalize(remembered.raw_example)
                    evidence['nearest_history'] = {'customer_id': best['customer_id'], 'customer_name': best['customer_name'],
                        'matched_pattern_id': remembered.pattern_id, 'matched_pattern': remembered.raw_example,
                        'matched_template': previous.template, 'query_template': norm.template,
                        'pattern_source': {'file': remembered.source_file, 'row': remembered.source_row, 'date': remembered.example_date},
                        'numeric_comparison': compare_ordered_numbers(norm, previous.segments, previous.template)}
                    nearest = evidence['nearest_history']
            if best and nearest is None and best.get('evidence'):
                evidence['nearest_history'] = {**best['evidence'], 'customer_id': best['customer_id'], 'customer_name': best['customer_name']}
            suggested_name = (best['customer_name'] if best and (not suggested_customer_id or suggested_customer_id == best['customer_id'])
                              else evidence.get('suggested_customer_name', ''))
            # Contextual groups are separate from individual identity evidence. Never
            # select their first member, raise confidence, or learn group membership.
            group = candidate_group(session, norm, payer, extracted['extracted_names'], suggestions=recommendations)
            return {'customer_id': best['customer_id'] if best else None,
                    'customer_name': best['customer_name'] if best else None,
                    'score': 0.0 if unknown else round(min(guard_cap, best['score']), 4) if best else 0.0,
                    'decision': 'manual_check', 'suggested_customer_id': suggested_customer_id or (best['customer_id'] if best else None),
                    'suggested_customer_name': suggested_name,
                    'evidence': {'reason': reason, 'reason_vi': (vietnamese_reason(reason) + (' ' + detail_vi if detail_vi else '')).strip(),
                                 'candidate_count': len(recommendations), 'unknown_customer': unknown,
                                 'detected_customer_ids': norm.customer_ids, 'learned_customer_ids': learned_ids, **evidence},
                    'normalization': norm.dict(),
                    'alternatives': [{key: item[key] for key in ('customer_id', 'customer_name', 'score')} for item in recommendations[1:6]],
                    'recommendations': [{key: value for key, value in item.items() if key != 'evidence'} for item in recommendations],
                    'candidate_group': group,
                    **extracted, **route,
                    'payment_mode_evidence': route_evidence}
        if recurrence and recurrence['complete']:
            if recurrence['owner_count'] > 1 and len(written_ids) <= 1:
                result = manual('Mẫu này đã được xác nhận cho nhiều khách hàng; chọn người nhận thanh toán của lần này.',
                    recommendations=recurrence['owners'], recurring_group=True)
                # Shared history is not an allocation for this transfer.
                result.update(customer_id=None, customer_name=None, suggested_customer_id=None, score=0.)
                return result
            link = recurrence['link']
            customer = session.get(Customer, link.customer_id)
            pattern = session.get(Pattern, link.pattern_id)
            code_conflict = bool(written_ids and written_ids != {id_key(customer.id)})
            effective_route = route if payment_mode or pattern.payment_mode == 'unknown' else {
                key: getattr(pattern, key) for key in ('payment_mode', 'provider_kind', 'provider_name')}
            # A confirmed self-payment label overrides generic bank/wallet hints.
            proxy = (effective_route['payment_mode'] == 'proxy' or
                     effective_route['payment_mode'] == 'unknown' and route_evidence.get('suggested_mode') == 'proxy')
            template = session.get(PaymentTemplate, pattern.template_id)
            settlement = (template.max_transfer_customers or 1) >= BATCH_MIN_CUSTOMERS and unique_transfer_reference(norm)
            name_comparison = None
            if not norm.customer_ids and not recurrence['identity_anchor']:
                owner = session.get(Customer, link.customer_id)
                name_comparison = self.compare_names(extracted, link.raw_example,
                    (link.signature or {}).get('name_extraction'), raw=raw,
                    history_slots=self.name_positions.slots_for(session, link.channel_key),
                    confirmed_names=[owner.canonical_name, *session.scalars(select(Alias.normalized_alias).where(
                        Alias.customer_id == owner.id, Alias.confidence >= 1.0))])
                if name_comparison['status'] == 'mismatch':
                    return manual('Full name entities differ from the historical transaction',
                                  name_entity_comparison=name_comparison)
                # With unavailable NER, exact stable text still preserves every name
                # word. Keep that proven memory path; fuzzy matching below needs names.
            if (recurrence['owner_count'] == 1 and recurrence['sample_valid'] and recurrence['personal'] and not recurrence['blocked_customer_ids'] and not code_conflict and not settlement
                    and (not proxy or recurrence['identity_anchor'])):
                history = self.normalize(link.raw_example)
                score = .97
                reason = 'Mẫu riêng đã được xác nhận cho khách hàng; nội dung và toàn bộ số ổn định trùng khớp.'
                code_owners = posting_owners(session, {'code:' + value for value in norm.mixed_codes})
                account_owners = defaultdict(set)
                for cid, account in session.execute(select(Payer.customer_id, Payer.payer_name).where(
                        Payer.payer_type == 'account', Payer.payer_name.in_(norm.payer_accounts))):
                    account_owners[account].add(cid)
                return {'customer_id': customer.id, 'customer_name': customer.canonical_name,
                    'score': score, 'decision': 'auto_accept' if score >= self.auto_threshold else 'review',
                    'recommendations': [{'customer_id': customer.id, 'customer_name': customer.canonical_name,
                        'score': score, 'sources': ['Mẫu riêng đã xác nhận']}], 'alternatives': [],
                    'normalization': norm.dict(), **extracted, **effective_route,
                    'payment_mode_evidence': {**route_evidence,
                        'source': 'input_label' if payment_mode else 'confirmed_link',
                        'reason_vi': route_evidence.get('reason_vi') if payment_mode else
                            'Nhận lại mẫu riêng đã được xác nhận trong lịch sử.'},
                    'evidence': {'reason': reason, 'reason_vi': reason, 'confirmed_recurrence': recurrence_summary,
                        'name_entity_comparison': name_comparison,
                        'matched_pattern_id': pattern.id, 'matched_pattern': link.raw_example,
                        'matched_template': history.template, 'query_template': norm.template,
                        'pattern_source': {'file': link.source_file, 'row': link.source_row, 'date': link.example_date},
                        'numeric_comparison': compare_ordered_numbers(norm, history.segments, history.template),
                        'candidate_count': 1, 'memory_lookup': True, 'matched_channel': payer,
                        'explicit_customer_id': bool(written_ids), 'numeric_customer_evidence': recurrence['identity_anchor'],
                        'matched_payment_mode': pattern.payment_mode,
                        'unique_mixed_codes': [value for value in norm.mixed_codes if code_owners['code:' + value] == {customer.id}],
                        'unique_payer_accounts': [value for value in norm.payer_accounts if account_owners[value] == {customer.id}],
                        'shared_template_id': template.id, 'shared_template': template.template_text,
                        'match_steps': ['Exact confirmed signature lookup, independent of candidate budget.',
                            'Checked every confirmed owner; exactly one unblocked customer.',
                            'Dates and recognized changing references excluded; all remaining numbers preserved in order.'],
                        'match_steps_vi': ['Tra trực tiếp mẫu đã xác nhận, không phụ thuộc giới hạn ứng viên.',
                            'Kiểm tra toàn bộ liên kết: chỉ có một khách hàng và không bị từ chối.',
                            'Bỏ kỳ/ngày/tham chiếu đã nhận diện; giữ đầy đủ các số còn lại theo đúng thứ tự.']}}
            if recurrence['owner_count'] == 1 and code_conflict and len(written_ids) == 1:
                return manual('Mẫu đã được xác nhận nhưng mã ghi trong nội dung khác khách hàng nhận thanh toán; kiểm tra trước khi xác nhận.',
                              explicit_id_label_conflict=True)
        distinct_ids = {id_key(value): value for value in norm.customer_ids}
        exact_token = 'exact:' + fingerprint(norm.normalized)
        exact_owners = posting_owners(session, {exact_token})[exact_token] if norm.customer_ids else set()
        if exact_owners:
            exact_owners -= set(session.scalars(select(HardNegative.customer_id).where(
                HardNegative.transaction_fingerprint == fingerprint(norm.normalized))))
        if any(n.get('learned_non_id') and n['numeric_type'] == 'CUSTOMER_ID' for n in norm.numbers):
            return manual('Vị trí ghi mã này thường không trùng IDKH trong dữ liệu đã duyệt; cần kiểm tra khách hàng được thanh toán.',
                          explicit_id_role_conflict=True)
        if len(distinct_ids) > 1:
            # One transfer for several customers: propose the allocation, never pick one or split money.
            owners = posting_owners(session, {'id:' + key for key in distinct_ids})
            registered = set(session.scalars(select(Customer.id).where(Customer.id.in_(distinct_ids))))
            allocation, unknown_ids = [], []
            for key, value in distinct_ids.items():
                found = {key} if key in registered else owners['id:' + key]
                (allocation.append(next(iter(found))) if len(found) == 1 else unknown_ids.append(value))
            mentioned_known, mentioned_unknown = list(allocation), list(unknown_ids)
            if exact_owners:
                # Approved recipients need not equal every ID mentioned by the payer.
                allocation, unknown_ids = sorted(exact_owners), []
            names = dict(session.execute(select(Customer.id, Customer.canonical_name).where(Customer.id.in_(allocation))).all()) if allocation else {}
            listed = ', '.join(f'{cid} ({names.get(cid, "")})' for cid in allocation)
            detail = ((f'Cùng nội dung từng được xác nhận cho: {listed}.' if exact_owners else
                       f'Các mã được nhắc đến có {len(allocation)} khách hàng đã biết: {listed}.') if allocation else '') + \
                     (f' Mã chưa có trong kho: {", ".join(unknown_ids)}.' if unknown_ids else '')
            result = manual('Multiple customer IDs require manual allocation', detail.strip(), multiple_customer_ids=True,
                          allocation_customer_ids=allocation, unknown_allocation_ids=unknown_ids,
                          allocation_source='confirmed_text' if exact_owners else 'mentioned_codes',
                          mentioned_known_customer_ids=mentioned_known, mentioned_unknown_customer_ids=mentioned_unknown,
                          history_allocation_customer_ids=allocation if exact_owners else [],
                          allocation=[{'customer_id': cid, 'customer_name': names.get(cid, '')} for cid in allocation],
                          allocation_complete=bool(allocation) and not unknown_ids)
            result.update(customer_id=None, customer_name='; '.join(names[cid] for cid in allocation) or None,
                          suggested_customer_id=', '.join(allocation if exact_owners else distinct_ids.values()), score=0.0)
            return result
        if norm.customer_ids:
            if exact_owners and any(id_key(owner) not in distinct_ids for owner in exact_owners):
                return manual('Nhãn đã duyệt của nội dung này khác mã được nhắc đến; cần kiểm tra khách hàng nhận thanh toán.',
                              confirmed_text_customer_ids=sorted(exact_owners), explicit_id_label_conflict=True)
        if not norm.customer_ids:
            batch = self.batch_settlement(session, norm, extracted['extracted_names'], payer)
            if batch:
                provider = batch['provider_name'] or 'đơn vị thu hộ'
                batch_route = {'payment_mode': 'proxy', 'provider_kind': batch['provider_kind'], 'provider_name': batch['provider_name']}
                route_evidence.update(source='batch_settlement', suggested_mode='proxy',
                                      reason_vi=f'Bố cục quyết toán tổng hợp của {provider}; lịch sử có một lần chuyển chia cho {batch["max_transfer_customers"]} khách hàng.')
                result = manual('Provider batch settlement covers many customers; reconcile with the provider statement',
                                f'Một lần chuyển của {provider} từng gồm {batch["max_transfer_customers"]} khách hàng '
                                f'({batch["customer_count"]} lượt khách hàng đã học với bố cục này). Đối chiếu bảng kê của {provider}; không gán cho một khách hàng.',
                                recommendations=[], batch_settlement=batch,
                                related_customer_recommendations=batch['related_customers'])
                if not payment_mode:
                    result.update(batch_route)
                return result
        id_tokens = {'id:' + id_key(value) for value in norm.customer_ids}
        mixed_values = {n['value'] for n in norm.numbers if n['numeric_type'] == 'MIXED_CODE'
                        and len(n['value']) >= 6 and sum(c.isdigit() for c in n['value']) >= 4}
        contextual_tokens = {n['value']: 'slot-id:' + fingerprint(channel_key(payer) + '\x1f' + n['context']) + ':' + n['value']
                             for n in norm.numbers if n.get('context') and not n.get('learned_non_id')}
        identity_tokens = id_tokens | {'contract:' + value for value in norm.contract_ids} | \
            set(contextual_tokens.values()) | {'code:' + value for value in mixed_values}
        identity_owners = posting_owners(session, identity_tokens)
        customer_scope = None
        registry_customer = None
        # The receiving company's name contains "nước" even on unrelated transfers.
        water_intent = water_payment(raw)
        if norm.customer_ids:
            known_customers = set().union(*(identity_owners[token] for token in id_tokens))
            if not known_customers:
                value = norm.customer_ids[0]
                source = 'vị trí mã khách hàng đã học từ dữ liệu xác nhận' if learned_ids else 'nhãn mã khách hàng trong nội dung'
                # Customers learned only from batch settlements have a confirmed name but no own pattern.
                registered = next((c for c in (session.get(Customer, v) for v in dict.fromkeys(
                    (value, value.lstrip('0') or '0', value.zfill(6)))) if c), None)
                if registered is not None and water_intent:
                    known_customers = {registered.id}
                    registry_customer = registered
            if registry_customer is None and not known_customers:
                detail = (f'Mã đề xuất {value} – {registered.canonical_name} (có trong danh sách khách hàng đã học; đọc từ {source}).'
                          if registered else f'Mã đề xuất {value} (đọc từ {source}); kiểm tra rồi xác nhận để học khách hàng mới.')
                return manual('Registered customer needs confirmation of payment purpose' if registered else
                              'Customer ID is absent from confirmed knowledge; confidence is 0%; manual check required', detail,
                              suggested_customer_id=registered.id if registered else value,
                              suggested_customer_name=registered.canonical_name if registered else '',
                              unknown_customer=registered is None,
                              customer_id_source='learned_slot' if learned_ids else 'rule')
            if len(known_customers) > 1:
                return manual('Customer ID maps to multiple confirmed profiles; manual check required',
                              unknown_customer=False, conflicting_customer_ids=sorted(known_customers))
            customer_scope = next(iter(known_customers))
        contract_owners = {}
        consistent_new_contracts = set()
        for value in norm.contract_ids:
            owners = identity_owners['contract:' + value]
            if customer_scope and customer_scope not in owners:
                # An explicit known ID takes priority over a shared/new contract.
                # Only human confirmation can learn this additional association.
                consistent_new_contracts.add(value)
            elif not owners:
                return manual('Unknown or conflicting water contract requires manual check', detected_contract_ids=norm.contract_ids)
            contract_owners[value] = owners
        # Ownership comes from the WHOLE knowledge, before bounded retrieval.
        # Contradictory non-contract codes must not disappear when a competing customer
        # falls outside the candidate budget or an explicit-ID search scope.
        ownership_constraints = [owners for token, owners in identity_owners.items()
                                 if owners and not (customer_scope and token.startswith('contract:'))]
        compatible_owners = set.intersection(*ownership_constraints) if ownership_constraints else None
        ownership_evidence = {token: sorted(owners) for token, owners in identity_owners.items() if owners}
        if compatible_owners is not None and (not compatible_owners or
                registry_customer is not None and registry_customer.id not in compatible_owners):
            return manual('Conflicting confirmed identifiers require manual check', unknown_customer=False,
                          confirmed_identifier_owners=ownership_evidence)
        vector = self.embedder.encode(norm.semantic_text)
        mixed_code_owners = {value: identity_owners['code:' + value] for value in mixed_values}
        candidates, retrieval_trace = self.retrieve(session, norm, vector, payer,
                                                  customer_scope=customer_scope, with_trace=True,
                                                  extracted_names=extracted['extracted_names'])
        if not candidates and registry_customer is not None:
            # Compact collection learning retains shared membership instead of an
            # individual vector. A confirmed shared layout is equally valid evidence.
            if compatible_owners is None or registry_customer.id in compatible_owners:
                blocked_registry = session.scalar(select(HardNegative.id).where(
                    HardNegative.customer_id == registry_customer.id,
                    HardNegative.transaction_fingerprint.in_((fingerprint(norm.normalized),
                        recurring_negative_key(recurring_signature(norm, payer)[0])))).limit(1))
                if blocked_registry is not None:
                    return manual('Human feedback previously rejected this customer for this transaction')
                shared = self.shared_collection_template(session, norm, payer, extracted['extracted_names'])
                membership = session.scalar(select(CollectionMember.id).where(
                    CollectionMember.customer_id == registry_customer.id).limit(1))
                if shared and membership is not None:
                    template, example = shared
                    reason = 'IDKH trùng khách hàng đã học; bố cục khớp mẫu thu hộ dùng chung của ngân hàng/kênh thanh toán.'
                    score = .97
                    if not payment_mode and route['payment_mode'] == 'unknown':
                        source = example if example.payment_mode == 'proxy' else template
                        route = {key: getattr(source, key) for key in ('payment_mode', 'provider_kind', 'provider_name')}
                        route_evidence = {'source': 'shared_template', 'reason_vi': 'Kiểu thanh toán từ mẫu thu hộ dùng chung đã học.'}
                    return {'customer_id': registry_customer.id, 'customer_name': registry_customer.canonical_name,
                        'score': score, 'decision': 'auto_accept' if score >= self.auto_threshold else 'review',
                        'normalization': norm.dict(), **extracted, **route, 'payment_mode_evidence': route_evidence,
                        'recommendations': [{'customer_id': registry_customer.id,
                            'customer_name': registry_customer.canonical_name, 'score': score,
                            'sources': ['IDKH đã học và mẫu thu hộ dùng chung']}], 'alternatives': [],
                        'evidence': {'reason': reason, 'reason_vi': reason, 'shared_collection_match': True,
                            'explicit_customer_id': True, 'numeric_customer_evidence': True,
                            'customer_id_source': 'learned_slot' if learned_ids else 'rule',
                            'detected_customer_ids': norm.customer_ids, 'learned_customer_ids': learned_ids,
                            'shared_template_id': template.id, 'shared_template': template.template_text,
                            'matched_template': template.template_text, 'query_template': shared_shape(norm, extracted['extracted_names']),
                            'matched_channel': payer, 'confirmed_identifier_owners': ownership_evidence,
                            'shared_example_customer_id': example.customer_id,
                            'match_steps_vi': ['IDKH trùng khách hàng đã được xác nhận trong dữ liệu thu hộ.',
                                'Đối chiếu bố cục với mẫu thu hộ cùng ngân hàng/kênh đã học từ khách hàng khác.',
                                'Không yêu cầu mẫu riêng; không gán khách hàng của ví dụ dùng chung cho giao dịch này.'],
                            'retrieval': retrieval_trace}}
                return manual('IDKH có trong dữ liệu đã học; chưa đối chiếu được bố cục giao dịch với mẫu đã xác nhận của ngân hàng/kênh này.',
                    recommendations=[{'customer_id': registry_customer.id, 'customer_name': registry_customer.canonical_name,
                                      'score': .55, 'sources': ['Mã khách hàng có trong danh sách đã duyệt']}],
                    registry_only_customer=True, explicit_customer_id=True,
                    customer_id_source='learned_slot' if learned_ids else 'rule',
                    confirmed_identifier_owners=ownership_evidence, retrieval=retrieval_trace)
        if not candidates:
            return manual('No confirmed customer evidence; confidence is 0%; manual check required', retrieval=retrieval_trace)
        customer_ids = {p.customer_id for p in candidates}
        customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(customer_ids)))}
        aliases, trusted_aliases = defaultdict(list), defaultdict(list)
        for alias in session.scalars(select(Alias).where(Alias.customer_id.in_(customer_ids))):
            aliases[alias.customer_id].append(alias.normalized_alias)
            if alias.confidence >= 1.0:
                trusted_aliases[alias.customer_id].append(alias.normalized_alias)
        extracted_name_keys = {name_key(name) for name in extracted['extracted_names']}
        names_in_content = {alias for names in trusted_aliases.values() for alias in names
                            if len(alias) > 3 and any(char.isalpha() for char in alias)
                            and (name_key(alias) in extracted_name_keys if extracted_name_keys else
                                 re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', norm.normalized))}
        alias_owners = defaultdict(set)
        if names_in_content:
            # Check the whole knowledge, even when another owner was not retrieved.
            for alias, owner in session.execute(select(Alias.normalized_alias, Alias.customer_id).where(
                    Alias.normalized_alias.in_(names_in_content))):
                alias_owners[alias].add(owner)
        pattern_ids = [p.id for p in candidates]
        # Without a labelled/learned ID, the candidate's own IDKH may still be written as a bare
        # number ("nguyen bi tra tien nuoc 324464"). Amounts, cards, references and learned
        # non-ID positions never count.
        bare_ids = {id_key(n['value']) for n in norm.numbers if n['numeric_type'] == 'UNKNOWN_NUMBER'
                    and CUSTOMER_ID.fullmatch(n['value']) and not n.get('learned_non_id')}
        bare_ids = bare_ids if len(bare_ids) == 1 else set()  # several candidates: position decides
        saved_names = {}
        if not norm.customer_ids:
            # Reuse NER saved for this exact example, not an alias of the owner or
            # a different observation aggregated into the same pattern.
            for pid, signature_data in session.execute(select(RecurringLink.pattern_id, RecurringLink.signature)
                    .join(Pattern, Pattern.id == RecurringLink.pattern_id).where(
                        RecurringLink.pattern_id.in_(pattern_ids), RecurringLink.customer_id == Pattern.customer_id,
                        RecurringLink.raw_example == Pattern.raw_example).order_by(RecurringLink.id.desc())):
                if (signature_data or {}).get('name_extraction', {}).get('entities'):
                    saved_names.setdefault(pid, signature_data['name_extraction'])
        template_ids = {pattern.template_id for pattern in candidates}
        template_counts = dict(session.execute(select(Pattern.template_id, func.count(func.distinct(Pattern.customer_id)))
            .where(Pattern.template_id.in_(template_ids)).group_by(Pattern.template_id)).all())
        templates = {template.id: template for template in session.scalars(select(PaymentTemplate).where(PaymentTemplate.id.in_(template_ids)))}
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
            HardNegative.transaction_fingerprint.in_((fingerprint(norm.normalized),
                recurring_negative_key(recurring_signature(norm, payer)[0]))))))
        account_owners = defaultdict(set)
        if norm.payer_accounts:
            for account, owner in session.execute(select(Payer.payer_name, Payer.customer_id).where(
                    Payer.payer_type == 'account', Payer.payer_name.in_(norm.payer_accounts))):
                account_owners[account].add(owner)
        signature = identity_signature(raw)
        detail_owners = set(session.scalars(select(Pattern.customer_id).join(Posting).where(
            posting_equal('detail:' + fingerprint(signature))))) if signature else set()
        ranked = []
        slot_kinds = self.slots.kinds_for(session, payer)
        for pattern in candidates:
            identifier_guards = []
            customer = customers[pattern.customer_id]
            historical_segments = deepcopy(pattern.segments)
            if pattern.channel_key == channel_key(payer):
                for segment in historical_segments:
                    if segment.get('context') and slot_kinds.get(segment['context']) == 'not_id' and segment.get('numeric_type') != 'CUSTOMER_ID':
                        segment['learned_non_id'] = True
            number_comparison = compare_ordered_numbers(norm, historical_segments, pattern.template_text)
            unique_contract = bool(number_comparison['aligned_contracts'] and number_comparison['template_compatible'] and
                                   all(contract_owners.get(v) == {customer.id} for v in number_comparison['aligned_contracts']))
            unique_bound_tokens = [value for value in number_comparison['confirmed_customer_tokens']
                                   if value in contextual_tokens and identity_owners[contextual_tokens[value]] == {customer.id}]
            confirmed_token = bool(unique_bound_tokens)
            unique_mixed_codes = [r['query_value'] for r in number_comparison['ordered_comparison']
                                  if r['type'] == 'MIXED_CODE' and r['same_role'] and r['exact'] and r['same_format']
                                  and mixed_code_owners.get(r['query_value']) == {customer.id}]
            exact_id = any(id_key(customer.id) == id_key(value) for value in norm.customer_ids)
            # A confirmed ID position that now holds another value is a conflict, not a match.
            id_in_text = (not norm.customer_ids and id_key(customer.id) in bare_ids
                          and not number_comparison['unresolved_confirmed_customer_tokens'])
            exact_id = exact_id or id_in_text
            confirmed_names = [customer.canonical_name, *trusted_aliases[customer.id]]
            alias_matches = [alias for alias in trusted_aliases[customer.id] if alias in names_in_content]
            unique_alias_matches = [alias for alias in alias_matches if alias_owners[alias] == {customer.id}]
            matched_extracted_names = [name for name in extracted['extracted_names'] if
                                       name_key(name) in {name_key(alias) for alias in aliases[customer.id]}]
            customer_score = 1.0 if exact_id or unique_contract or confirmed_token or unique_mixed_codes or unique_alias_matches else 0.0
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
            water_intent = water_payment(raw)
            if exact_id and (water_intent if id_in_text else (text_score >= .75 or water_intent)):
                score = max(score, .92 + .06 * text_score)
                reason = ('Customer ID written in the narrative plus water-bill purpose' if id_in_text else
                          'Explicit customer ID in a learned position plus matching payment purpose'
                          if any(id_key(v) == id_key(customer.id) for v in learned_ids) else
                          'Explicit customer ID plus matching confirmed transfer template')
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
            elif unique_details and unique_alias_matches and max(len(a.split()) for a in unique_alias_matches) >= 2 and water_intent and \
                 (max(len(a.split()) for a in unique_alias_matches) >= 3 or len(distinctive_overlap) >= 2):
                score = max(score, .93)
                reason = 'Unique historical payment details and confirmed customer name'
            elif not exact_id:
                score = min(score, guard_cap)
                reason = 'Name alone or shared payer does not identify a meter reliably'
            if norm.customer_ids and not exact_id:
                score = min(score, guard_cap)
                reason = 'Explicit customer ID does not match this historical customer'
                identifier_guards.append(reason)
            unresolved_contract = bool(norm.contract_ids and set(number_comparison['aligned_contracts']) != set(norm.contract_ids))
            if not exact_id and (number_comparison['contract_conflict'] or unresolved_contract):
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
            shared_customer_count = template_counts.get(pattern.template_id, 0)
            proxy_or_shared = route_evidence['suggested_mode'] == 'proxy' or route['payment_mode'] == 'proxy' or pattern.payment_mode == 'proxy' or shared_customer_count > 1
            numeric_customer_evidence = exact_id or unique_contract or confirmed_token or bool(unique_mixed_codes)
            if proxy_or_shared and not numeric_customer_evidence:
                score = min(score, guard_cap)
                reason = 'Shared or proxy template requires exact customer-specific numeric evidence'
                identifier_guards.append(reason)
            elif route['payment_mode'] == 'unknown' and pattern.payment_mode == 'unknown' and not numeric_customer_evidence:
                score = min(score, guard_cap)
                reason = 'Unknown payment route requires exact customer-specific numeric evidence'
                identifier_guards.append(reason)
            name_comparison = None
            if not norm.customer_ids and not numeric_customer_evidence:
                name_comparison = self.compare_names(extracted, pattern.raw_example, saved_names.get(pattern.id),
                                                     raw=raw, confirmed_names=confirmed_names,
                                                     history_slots=self.name_positions.slots_for(session, pattern.channel_key))
                if name_comparison['status'] != 'match':
                    score = min(score, guard_cap)
                    reason = ('Full name entities differ from the historical transaction'
                              if name_comparison['status'] == 'mismatch' else 'Full name entities could not be verified')
                    identifier_guards.append(reason)
            if customer.id in blocked:
                score = 0
                reason = 'Human feedback previously rejected this customer for this transaction'
            # Similar bank boilerplate and embeddings are discovery signals only.
            # Offer a customer only when the narrative contains some personal evidence.
            personal_overlap = distinctive_overlap - set('ma kh id hd ky nam thang code bank nuoc hue water supply jsc'.split())
            relevant = bool(exact_id or unique_contract or confirmed_token or unique_mixed_codes or alias_matches
                            or matched_extracted_names or account_match or len(personal_overlap) >= 2
                            or any(customer.id in owners for owners in identity_owners.values()))
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
                               'matched_extracted_names': matched_extracted_names,
                               'name_entity_comparison': name_comparison,
                               'unique_confirmed_aliases': unique_alias_matches,
                               'alias_owners': {alias: sorted(alias_owners[alias]) for alias in alias_matches},
                               'shared_template_id': pattern.template_id,
                               'shared_template': templates[pattern.template_id].template_text,
                               'shared_template_customer_count': shared_customer_count,
                               'numeric_customer_evidence': bool(numeric_customer_evidence),
                               'matched_payment_mode': pattern.payment_mode,
                               'matched_provider_kind': pattern.provider_kind,
                               'matched_provider_name': pattern.provider_name,
                               'explicit_customer_id': exact_id, 'matched_numbers': matched_numbers,
                               'relevant_customer_information': relevant, 'matched_channel': pattern.channel_key,
                               'numeric_comparison': number_comparison, 'explicit_contract_ids': norm.contract_ids,
                               'unique_contract_match': unique_contract, 'confirmed_customer_token': confirmed_token,
                               'unique_confirmed_customer_tokens': unique_bound_tokens,
                               'confirmed_identifier_owners': ownership_evidence,
                               'identifier_guards': identifier_guards,
                               'unique_mixed_codes': unique_mixed_codes,
                               'consistent_new_contracts': sorted(consistent_new_contracts),
                               'customer_id_priority_over_contract': bool(exact_id and norm.contract_ids),
                               'shared_contract_customer_ids': {value: sorted(owners) for value, owners in contract_owners.items() if len(owners) > 1},
                               'candidate_count': len(candidates),
                               'retrieval': {key: value for key, value in retrieval_trace.items() if key not in ('candidate_sources', 'candidate_source_families')},
                               'retrieval_sources': retrieval_sources,
                               'retrieval_source_families': retrieval_trace['candidate_source_families'].get(pattern.id, []),
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
                                   *(['Ưu tiên IDKH đã nhận diện; một HD có thể liên kết nhiều IDKH. Quan hệ HD mới chỉ được học sau xác nhận.']
                                     if exact_id and norm.contract_ids else []),
                                   f'Độ tương đồng nội dung {text_score:.1%}; độ tương đồng thứ tự chữ/số {structure:.1%}.',
                                   'Tài khoản trả tiền khớp: ' + ', '.join(sorted(payer_accounts[customer.id])) if account_match else
                                       'Chưa tìm thấy tài khoản trả tiền riêng của khách hàng trùng khớp.',
                                   vietnamese_reason(reason)]}})
        # One result per customer so repeated patterns do not create a false tie.
        by_customer = {}
        for result in sorted(ranked, key=lambda r: (-r['score'], r['customer_id'])):
            by_customer.setdefault(result['customer_id'], result)
        ranked = [item for item in by_customer.values() if item['customer_id'] not in blocked
                  and item['evidence']['relevant_customer_information']]
        if not ranked:
            fallback = self.recommend_customers(session, norm, payer, extracted)
            if by_customer and not fallback and all(cid in blocked for cid in by_customer):
                result = manual('Human feedback previously rejected this customer for this transaction', recommendations=[])
                result['decision'] = 'reject'
                return result
            return manual('No confirmed customer evidence; confidence is 0%; manual check required',
                          recommendations=fallback, retrieval=retrieval_trace)
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
                             'Shared or proxy template requires exact customer-specific numeric evidence',
                             'Unknown payment route requires exact customer-specific numeric evidence',
                             'Full name entities differ from the historical transaction',
                             'Full name entities could not be verified',
                             'Conflicting confirmed identifiers require manual check'}
            manual_reason = best['evidence']['reason'] if best['evidence']['reason'] in guard_reasons else \
                'Historical candidates do not identify this customer reliably; confidence is 0%; manual check required'
            # Recurring split payments (e.g. one agency paying several meters every month):
            # the same payment details were confirmed for a small group of customers.
            group = sorted(detail_owners) if not norm.customer_ids and 2 <= len(detail_owners) < BATCH_MIN_CUSTOMERS else []
            group_names = dict(session.execute(select(Customer.id, Customer.canonical_name).where(Customer.id.in_(group))).all()) if group else {}
            names = best['evidence'].get('name_entity_comparison') or {}
            name_detail = (f"Tên — giao dịch: {'; '.join(names.get('query_names') or ['chưa đọc được'])}; "
                           f"mẫu đã học: {'; '.join(names.get('history_names') or ['chưa đọc được'])}."
                           if manual_reason.startswith('Full name entities') else '')
            return manual(manual_reason,
                          ' '.join(filter(None, [name_detail, ('Lịch sử: cùng nội dung thanh toán đã được xác nhận cho ' +
                           ', '.join(f'{cid} ({group_names.get(cid, "")})' for cid in group) + '.') if group else ''])),
                          history_allocation_customer_ids=group,
                          candidate_count=len(candidates),
                          recommendations=ranked[:6],
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
        best['recommendations'] = [{k: r[k] for k in ('customer_id', 'customer_name', 'score')} for r in ranked[:6]]
        best.update(extracted)
        if (not payment_mode and best['score'] >= self.auto_threshold and best['evidence'].get('numeric_customer_evidence')
                and best['evidence'].get('matched_payment_mode', 'unknown') != 'unknown'
                and best['evidence'].get('shared_template') == shared_shape(norm, extracted['extracted_names'] + [best['customer_name']])):
            route = {key: best['evidence']['matched_' + key] for key in ('payment_mode', 'provider_kind', 'provider_name')}
            route_evidence.update(source='confirmed_link', pattern_id=best['evidence']['matched_pattern_id'],
                reason_vi='Kiểu thanh toán lấy từ liên kết lịch sử đã xác nhận, sau khi khớp bố cục và số riêng của khách hàng.')
        best.update(route)
        best['payment_mode_evidence'] = route_evidence
        return best

    def review(self, session, record, accepted, correct_customer_id=None, customer_name='',
               payment_mode='', provider_kind='', provider_name=''):
        """Human confirmation learns immediately; operational records stay outside PostgreSQL."""
        from .excel import ExcelTransaction
        self.check_model(session)
        chosen = (correct_customer_id or record.get('customer_id')) if accepted else None
        if accepted and not chosen:
            raise ValueError('Choose a customer before accepting')
        allocation = confirmed_customer_ids(chosen) if accepted else []
        if accepted and len(allocation) > 1:
            # One transfer paying several customers: learn one confirmed link per customer.
            results = [self.review(session, {**record, 'customer_id': None, '_multiple_allocation': True}, True, cid, name,
                                   payment_mode, provider_kind, provider_name)
                       for cid, name in zip(allocation, allocation_names(allocation, customer_name))]
            return {**results[-1], 'confirmed_customer_id': ', '.join(r['confirmed_customer_id'] for r in results),
                    'confirmed_customer_name': '; '.join(r['confirmed_customer_name'] for r in results),
                    'confirmed_customer_ids': [r['confirmed_customer_id'] for r in results]}
        if accepted:
            chosen = self.ensure_customer(session, allocation[0], customer_name).id
        date = record.get('date') or ''
        receipt_key = learning_receipt(record['raw'], chosen, date, record.get('amount', 0), resolve_channel(session, record.get('payer', 'BIDV')),
                                      record.get('reference', '')) if chosen else 'reject:' + record['entry_key']
        wrong = record.get('customer_id')
        mentioned_ids = {id_key(value) for value in self.normalize(record['raw']).customer_ids}
        historical_group = {id_key(value) for value in record.get('evidence', {}).get('history_allocation_customer_ids', [])}
        co_recipient = (accepted and chosen and wrong and len(mentioned_ids) > 1
                        and id_key(chosen) in mentioned_ids and id_key(wrong) in mentioned_ids)
        co_recipient = co_recipient or (accepted and chosen and wrong and len(historical_group) > 1
                                      and id_key(chosen) in historical_group and id_key(wrong) in historical_group)
        negative_receipt = fingerprint('negative-review:' + record['entry_key'] + ':' + str(wrong) + ':' + str(chosen))
        previous_negative = session.scalar(select(KnowledgeReceipt.id).where(KnowledgeReceipt.fingerprint == negative_receipt))
        if not previous_negative and wrong and wrong != chosen and not co_recipient and session.get(Customer, wrong):
            normalized = self.normalize(record['raw']).dict()
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
            repeat_hash = recurring_negative_key(recurring_signature(self.normalize(record['raw']),
                resolve_channel(session, record.get('payer', 'BIDV')))[0])
            repeat_negative = session.scalar(select(HardNegative).where(HardNegative.transaction_fingerprint == repeat_hash,
                                                                         HardNegative.customer_id == wrong))
            if repeat_negative:
                repeat_negative.count += 1
            else:
                session.add(HardNegative(transaction_fingerprint=repeat_hash, customer_id=wrong,
                                         pattern_id=matched.id if matched and matched.customer_id == wrong else None, count=1))
        learned = False
        review_route = None
        explicit_route = bool(payment_mode)
        if chosen:
            # An explicit later acceptance supersedes an earlier rejection of this
            # exact content/customer. Do not keep the corrected customer blocked forever.
            session.execute(delete(HardNegative).where(HardNegative.customer_id == chosen,
                HardNegative.transaction_fingerprint == fingerprint(self.normalize(record['raw']).normalized)))
            if not payment_mode and record.get('payment_mode') in ('proxy', 'self'):
                payment_mode = record.get('payment_mode') or ''
                provider_kind = provider_kind or record.get('provider_kind') or ''
                provider_name = provider_name or record.get('provider_name') or ''
            customer = session.get(Customer, chosen)
            source = record.get('source', '')
            if record.get('sheet'):
                source += ' [' + record['sheet'] + ']'
            row = ExcelTransaction(record.get('row_index') or 0, record['raw'], date=date,
                amount=record.get('amount', 0), payer=record.get('payer', 'BIDV'),
                reference=record.get('reference', ''), sheet=record.get('sheet', ''),
                source=source + ' (người dùng xác nhận)', label_status='confirmed',
                payment_mode=payment_mode, provider_kind=provider_kind, provider_name=provider_name)
            self.learn(session, row, chosen, customer_name or customer.canonical_name, receipt_key=receipt_key,
                       learn_name_alias=not record.get('_multiple_allocation', False))
            review_route = payment_route(record['raw'], row.payment_mode, row.provider_kind, row.provider_name)
            learned = True
        return {**record, **(review_route or {}),
                'payment_mode_evidence': ({**payment_hint(record['raw']),
                    'source': 'user_confirmation' if explicit_route else 'confirmed_link' if review_route['payment_mode'] != 'unknown' else 'unresolved',
                    'reason_vi': ('Kiểu thanh toán được người dùng chọn khi xác nhận.' if explicit_route else
                        'Kiểu thanh toán lấy từ mẫu hoặc liên kết lịch sử đã xác nhận.' if review_route['payment_mode'] != 'unknown' else
                        'Chưa có nhãn đã xác nhận để phân biệt tự trả và thu hộ.')} if chosen else record.get('payment_mode_evidence', {})),
                'confirmed_customer_id': chosen,
                'confirmed_customer_name': session.get(Customer, chosen).canonical_name if chosen else '',
                'status': 'confirmed' if chosen else 'rejected', 'learned': learned,
                'reviewed_at': now()}
