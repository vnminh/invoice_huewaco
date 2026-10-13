"""Allowlisted knowledge changes, optimistic concurrency and reversible small corrections."""
import hashlib
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select

from .access import actor, expected_revision
from .db import Alias, BankAlias, KnowledgeChange, Metadata, Pattern, Posting, now
from .identifiers import channel_key
from .text import fold

REVISION_KEY = 'interactive_revision'
BANK_CHANNELS = ('bidv', 'vcb', 'mb', 'vietinbank', 'agribank', 'eximbank', 'vpbank',
                 'abbank', 'sacombank', 'shb', 'momo', 'viettelpay', 'payoo', 'vnpay', 'zalopay',
                 'napas', 'vnpt', 'shopee', 'shopeepay', 'buu dien')


def revision(session):
    record = session.get(Metadata, REVISION_KEY)
    return record.value if record else '0'


def require_revision(session):
    supplied = expected_revision.get()
    if supplied is None:
        raise HTTPException(428, 'Mở lại danh sách kiến thức trước khi chỉnh sửa.')
    if supplied != revision(session):
        raise HTTPException(409, 'Kiến thức đã thay đổi. Làm mới danh sách và kiểm tra lại trước khi lưu.')


def record_change(session, action, entity, before, after, *, reversible=False):
    change = KnowledgeChange(actor=actor.get(), action=action, entity_key=entity,
                             before=before, after=after, reversible=reversible)
    session.add(change)
    touch_revision(session)
    session.flush()
    return change


def touch_revision(session):
    """Invalidate stale forms even after a cancelled import with committed chunks."""
    record = session.get(Metadata, REVISION_KEY)
    value = uuid.uuid4().hex
    if record:
        record.value = value
    else:
        session.add(Metadata(key=REVISION_KEY, value=value))


def alias_key(value):
    key = ' '.join(fold(value).split())
    if not 2 <= len(key) <= 100 or not re.fullmatch(r'[a-z0-9][a-z0-9 ._()/&-]*', key):
        raise HTTPException(422, 'Bí danh ngân hàng cần 2–100 ký tự chữ, số hoặc dấu phân cách thông thường.')
    return key


def resolve_channel(session, value, *, learning=False):
    # A processing session has a fixed mapping; editing is blocked while jobs run.
    if 'bank_alias_map' not in session.info:
        session.info['bank_alias_map'] = dict(session.execute(select(BankAlias.normalized_alias, BankAlias.channel_key)).all())
    key = ' '.join(fold(value or '').split())
    if learning and key in session.info['bank_alias_map']:
        alias = session.scalar(select(BankAlias).where(BankAlias.normalized_alias == key))
        if alias and not alias.used_at:
            alias.used_at = now()
    return session.info['bank_alias_map'].get(key, channel_key(value))


def alias_snapshot(alias):
    return {'id': alias.id, 'customer_id': alias.customer_id, 'alias': alias.alias,
            'normalized_alias': alias.normalized_alias, 'confidence': alias.confidence}


def bank_snapshot(alias):
    return {'id': alias.id, 'alias': alias.alias, 'normalized_alias': alias.normalized_alias,
            'channel_key': alias.channel_key, 'note': alias.note, 'used_at': alias.used_at}


def sync_alias_postings(session, customer_id):
    # Rebuild only name postings. Never expose derived tokens/weights as editable fields.
    from .core import text_tokens
    names = list(session.scalars(select(Alias.normalized_alias).where(Alias.customer_id == customer_id)))
    tokens = {'name:' + word for name in names for word in text_tokens(name)}
    patterns = list(session.scalars(select(Pattern.id).where(Pattern.customer_id == customer_id)))
    for start in range(0, len(patterns), 250):
        ids = patterns[start:start + 250]
        session.execute(delete(Posting).where(Posting.pattern_id.in_(ids), Posting.token.like('name:%')))
        session.add_all(Posting(pattern_id=pid, token=token, weight=2.) for pid in ids for token in tokens)


def scope_digest(session, template_ids):
    """Identities AND values of the scope; equal counts cannot hide a changed set."""
    from .db import CollectionMember, PaymentTemplate
    digest = hashlib.sha256()
    for model, columns in (
        (PaymentTemplate, (PaymentTemplate.id, PaymentTemplate.payment_mode, PaymentTemplate.provider_kind, PaymentTemplate.provider_name)),
        (Pattern, (Pattern.id, Pattern.template_id, Pattern.customer_id, Pattern.payment_mode, Pattern.provider_kind, Pattern.provider_name)),
        (CollectionMember, (CollectionMember.id, CollectionMember.template_id, CollectionMember.customer_id))):
        key = model.id if model is PaymentTemplate else model.template_id
        for row in session.execute(select(*columns).where(key.in_(template_ids)).order_by(model.id)):
            digest.update(json.dumps([model.__tablename__, *row], ensure_ascii=False).encode())
    return digest.hexdigest()
