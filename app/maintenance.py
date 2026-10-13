"""Explicit, restartable rebuilding of derived knowledge indexes (never filter history)."""
from copy import deepcopy
import json

from sqlalchemy import delete, select, text, update

from .core import CONFIRMABLE_TYPES, fingerprint, pattern_identity
from .db import (Alias, CollectionMember, Customer, HardNegative, KnowledgeReceipt, Metadata,
                 NumericFeature, NumericSlot, Pattern, PaymentTemplate, Payer, RecurringLink, sessions)
from .identifiers import CUSTOMER_ID, confirmed_customer_id, id_key
from .interactive import resolve_channel, touch_revision


def _canonicalize_customers(session):
    """Restore leading zeroes in legacy confirmed labels, including their foreign keys."""
    legacy = []
    for old_id in session.scalars(select(Customer.id).execution_options(yield_per=2000)):
        cid = confirmed_customer_id(old_id)
        if cid != old_id:
            legacy.append((old_id, cid))
    for old_id, cid in legacy:
        old = session.get(Customer, old_id)
        target = session.get(Customer, cid)
        if target is None:
            name = cid if old.canonical_name == old.id else old.canonical_name
            target = Customer(id=cid, canonical_name=name, normalized_name=cid if name == cid else old.normalized_name)
            session.add(target)
            session.flush()
        for model, key_fields in ((Alias, ('normalized_alias',)), (Payer, ('payer_name',)),
                                  (CollectionMember, ('template_id',)), (HardNegative, ('transaction_fingerprint',)),
                                  (RecurringLink, ('pattern_id', 'signature_hash'))):
            for record in list(session.scalars(select(model).where(model.customer_id == old.id))):
                duplicate = session.scalar(select(model).where(model.customer_id == cid,
                    *(getattr(model, field) == getattr(record, field) for field in key_fields)))
                if duplicate is None:
                    record.customer_id = cid
                else:
                    if model is Payer:
                        session.execute(update(Pattern).where(Pattern.payer_id == record.id).values(payer_id=duplicate.id))
                    for field in ('seen_count', 'count'):
                        if hasattr(duplicate, field):
                            setattr(duplicate, field, getattr(duplicate, field) + getattr(record, field))
                    if hasattr(duplicate, 'confidence'):
                        duplicate.confidence = max(duplicate.confidence, record.confidence)
                    session.delete(record)
        for pattern in list(session.scalars(select(Pattern).where(Pattern.customer_id == old.id))):
            if session.scalar(select(Pattern.id).where(Pattern.customer_id == cid, Pattern.fingerprint == pattern.fingerprint)):
                pattern.fingerprint = fingerprint(pattern.fingerprint + ':restored:' + str(pattern.id))
            pattern.customer_id = cid
        session.execute(update(KnowledgeReceipt).where(KnowledgeReceipt.customer_id == old.id).values(customer_id=cid))
        session.flush()
        session.delete(old)
    session.flush()


def reindex_knowledge(engine, core, batch_size=200, progress=None):
    """Preserve approved customer links, source examples, counts, routes and receipts.

    Old numeric positions cannot be safely reinterpreted by SQL alone. Re-parse the
    original confirmed example, rebuild its vector/slots/postings, then rebuild contextual
    ID statistics. Distinct old links are kept even if the new parser gives them one shape.
    The application refuses to use the database while this operation is incomplete.
    """
    factory = sessions(engine)
    with factory.begin() as session:
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
        model = session.get(Metadata, 'embedding_model')
        if model and model.value != core.embedder.name:
            raise ValueError('Dùng đúng EMBEDDING_MODEL đã lưu trong kho trước khi cập nhật chỉ mục.')
        maintenance = session.get(Metadata, 'knowledge_maintenance')
        if maintenance is None:
            session.add(Metadata(key='knowledge_maintenance', value='running'))
        else:
            maintenance.value = 'running'
        _canonicalize_customers(session)
    last_id, count = 0, 0
    while True:
        with factory() as session:
            examples = session.execute(select(Pattern.id, Pattern.raw_example).where(Pattern.id > last_id)
                                       .order_by(Pattern.id).limit(batch_size)).all()
        if not examples:
            break
        parsed = {pattern_id: core.normalize(raw) for pattern_id, raw in examples}
        core.embedder.prepare([norm.semantic_text for norm in parsed.values()])
        with factory.begin() as session:
            if engine.dialect.name == 'postgresql':
                session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
            for pattern_id, _ in examples:
                pattern = session.get(Pattern, pattern_id)
                norm = parsed[pattern_id]
                customer = session.get(Customer, pattern.customer_id)
                payer = session.get(Payer, pattern.payer_id) if pattern.payer_id else None
                channel = resolve_channel(session, pattern.channel_key if pattern.channel_key != 'unknown' else payer.payer_name if payer else '')
                for number in norm.numbers:
                    if number['numeric_type'] in CONFIRMABLE_TYPES | {'CUSTOMER_ID'} and CUSTOMER_ID.fullmatch(number['value']) and id_key(number['value']) == customer.id:
                        number['confirmed_customer_value'] = True
                        next(s for s in norm.segments if s.get('slot') == number['slot'])['confirmed_customer_value'] = True
                old_template = session.get(PaymentTemplate, pattern.template_id)
                aliases = list(session.scalars(select(Alias.alias).where(Alias.customer_id == customer.id)))
                template = core.ensure_template(session, norm, [customer.canonical_name] + aliases, channel)
                for field in ('display_name', 'description', 'provider_name'):
                    if not getattr(template, field) and getattr(old_template, field):
                        setattr(template, field, getattr(old_template, field))
                if template.payment_mode == 'unknown' and old_template.payment_mode != 'unknown':
                    template.payment_mode, template.provider_kind, template.provider_name = (
                        old_template.payment_mode, old_template.provider_kind, old_template.provider_name)
                digest = fingerprint(json.dumps([channel, norm.template, norm.structure, pattern_identity(norm.segments)], ensure_ascii=False))
                duplicate = session.scalar(select(Pattern.id).where(Pattern.id != pattern.id,
                    Pattern.customer_id == customer.id, Pattern.fingerprint == digest))
                pattern.fingerprint = digest if not duplicate else fingerprint(digest + ':preserved:' + str(pattern.id))
                pattern.channel_key, pattern.template_id, pattern.normalizer_version = channel, template.id, core.extractor.name
                pattern.normalized_text, pattern.template_text, pattern.structure = norm.normalized, norm.template, norm.structure
                pattern.segments, pattern.embedding = deepcopy(norm.segments), core.embedder.encode(norm.semantic_text)
                pattern.embedding_model = core.embedder.name
                pattern.label_metadata = {'mentioned_customer_ids': norm.customer_ids,
                    'explicit_ids_agree': not norm.customer_ids or customer.id in norm.customer_ids,
                    'multiple_mentions': len(norm.customer_ids) > 1, 'reindexed': True}
                previous = list(session.scalars(select(NumericFeature).where(NumericFeature.pattern_id == pattern.id)))
                confidence = {(n.numeric_value, n.numeric_type): n.exact_value_confidence for n in previous}
                session.execute(delete(NumericFeature).where(NumericFeature.pattern_id == pattern.id))
                session.execute(delete(NumericSlot).where(NumericSlot.pattern_id == pattern.id))
                for number in norm.numbers:
                    if number['numeric_type'] == 'AMOUNT':
                        continue
                    score = confidence.get((number['value'], number['numeric_type']), .2)
                    session.add(NumericSlot(pattern_id=pattern.id, slot_index=number['slot'],
                        slot_confidence=score, seen_count=pattern.seen_count, last_period=pattern.last_period))
                    session.add(NumericFeature(pattern_id=pattern.id, slot_index=number['slot'], numeric_value=number['value'],
                        numeric_type=number['numeric_type'], exact_value_confidence=score,
                        seen_count=pattern.seen_count, missing_count=0, last_period=pattern.last_period))
                core.index_pattern(session, pattern, norm, customer, replace=True)
                from .recurring import negative_key
                recurring_blocks = {negative_key(digest) for digest in session.scalars(select(RecurringLink.signature_hash)
                    .where(RecurringLink.pattern_id == pattern.id))}
                for negative in session.scalars(select(HardNegative).where(HardNegative.pattern_id == pattern.id)):
                    if negative.transaction_fingerprint in recurring_blocks:
                        continue
                    new_hash = fingerprint(norm.normalized)
                    duplicate = session.scalar(select(HardNegative).where(HardNegative.id != negative.id,
                        HardNegative.customer_id == negative.customer_id, HardNegative.transaction_fingerprint == new_hash))
                    if duplicate:
                        duplicate.count += negative.count
                        session.delete(negative)
                    else:
                        negative.transaction_fingerprint = new_hash
                session.flush()
                last_id, count = pattern.id, count + 1
        if progress:
            progress(count)
    from .id_slots import rebuild
    with factory.begin() as session:
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
        slots = rebuild(session)
        from .name_slots import rebuild as rebuild_names
        slots = {**slots, **rebuild_names(session)}
        touch_revision(session)
        for key, value in (('feature_extractor', core.extractor.name), ('embedding_model', core.embedder.name),
                           ('knowledge_maintenance', 'completed')):
            record = session.get(Metadata, key)
            if record is None:
                session.add(Metadata(key=key, value=value))
            else:
                record.value = value
    from .recurring import rebuild as rebuild_recurring
    memory = rebuild_recurring(engine, batch_size=batch_size)
    return {'patterns_reindexed': count, 'id_slots': slots, 'recurring_memory': memory}


def rebuild_positions(engine, check_cancel=None):
    """Recount learned ID and name positions from the knowledge already stored.

    Light maintenance for existing databases (e.g. after migration 008): reads stored
    confirmed narratives and names only. Patterns, numbers and embeddings are unchanged,
    so no semantic model is loaded. Uses the same code as the end of every import.
    """
    from .id_slots import rebuild as rebuild_ids
    from .name_slots import rebuild as rebuild_names
    with sessions(engine).begin() as session:
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
        result = {'id_slots': rebuild_ids(session, check_cancel=check_cancel),
                  'name_slots': rebuild_names(session, check_cancel=check_cancel)}
        touch_revision(session)
    return result
