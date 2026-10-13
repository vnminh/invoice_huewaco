from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os

from sqlalchemy import (JSON, CheckConstraint, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import UserDefinedType


class Vector384(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return 'vector(384)'

    def bind_processor(self, dialect):
        return lambda value: '[' + ','.join(str(float(x)) for x in value) + ']' if value is not None else None

    def result_processor(self, dialect, coltype):
        def parse(value):
            if value is None:
                return None
            if isinstance(value, str):
                return [float(x) for x in value.strip('[]').split(',')]
            return list(value)
        return parse


DOC = JSON().with_variant(JSONB(), 'postgresql')
VECTOR = JSON().with_variant(Vector384(), 'postgresql')


def now():
    return datetime.now(timezone.utc).isoformat()


def numeric_digest_default(context):
    return hashlib.sha256(context.get_current_parameters()['numeric_value'].encode('utf-8')).hexdigest()


def token_digest_default(context):
    return hashlib.sha256(context.get_current_parameters()['token'].encode('utf-8')).hexdigest()


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, index=True)


class Alias(Base):
    __tablename__ = 'customer_aliases'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    alias: Mapped[str] = mapped_column(Text)
    normalized_alias: Mapped[str] = mapped_column(Text, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    __table_args__ = (UniqueConstraint('customer_id', 'normalized_alias'),)


class Payer(Base):
    __tablename__ = 'payer_entities'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    payer_name: Mapped[str] = mapped_column(Text, index=True)
    payer_type: Mapped[str] = mapped_column(String(30), default='bank')
    confidence: Mapped[float] = mapped_column(Float, default=0.2)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen: Mapped[str] = mapped_column(String(40), default=now)
    last_period: Mapped[str] = mapped_column(String(7), default='')
    __table_args__ = (UniqueConstraint('customer_id', 'payer_name'),)


class PaymentTemplate(Base):
    __tablename__ = 'payment_templates'
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    template_text: Mapped[str] = mapped_column(Text)
    structure: Mapped[str] = mapped_column(Text)
    channel_key: Mapped[str] = mapped_column(Text, default='unknown', index=True)
    display_name: Mapped[str] = mapped_column(Text, default='')
    description: Mapped[str] = mapped_column(Text, default='')
    payment_mode: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_kind: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_name: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    # Most confirmed customers ever allocated from ONE transfer (same narrative and unique
    # bank reference) of this layout. Large values identify provider batch settlements.
    max_transfer_customers: Mapped[int] = mapped_column(Integer, default=1)
    # Confirmed customer rows of batch settlements learned compactly (no per-customer pattern).
    settlement_rows: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("payment_mode IN ('unknown', 'proxy', 'self')", name='ck_templates_payment_mode'),
        CheckConstraint("provider_kind IN ('unknown', 'bank', 'wallet', 'other')", name='ck_templates_provider_kind'))


class Pattern(Base):
    __tablename__ = 'transaction_patterns'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey('payment_templates.id'), index=True)
    channel_key: Mapped[str] = mapped_column(Text, default='unknown', index=True)
    normalizer_version: Mapped[str] = mapped_column(Text, default='')
    # Approved labels are authoritative, even when codes mentioned in the text differ.
    # These diagnostics affect matching, never whether an approved example may be learned.
    label_metadata: Mapped[dict] = mapped_column(DOC, default=dict)
    payment_mode: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_kind: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_name: Mapped[str] = mapped_column(Text, default='')
    payer_id: Mapped[int | None] = mapped_column(ForeignKey('payer_entities.id'), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    normalized_text: Mapped[str] = mapped_column(Text)
    template_text: Mapped[str] = mapped_column(Text)
    raw_example: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str] = mapped_column(Text, default='')
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    example_date: Mapped[str] = mapped_column(String(40), default='')
    structure: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(DOC)
    embedding: Mapped[list] = mapped_column(VECTOR)
    embedding_model: Mapped[str] = mapped_column(Text)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.2)
    last_seen: Mapped[str] = mapped_column(String(40), default=now)
    last_period: Mapped[str] = mapped_column(String(7), default='')
    __table_args__ = (UniqueConstraint('customer_id', 'fingerprint'),
                     CheckConstraint("payment_mode IN ('unknown', 'proxy', 'self')", name='ck_patterns_payment_mode'),
                     CheckConstraint("provider_kind IN ('unknown', 'bank', 'wallet', 'other')", name='ck_patterns_provider_kind'),
                     Index('ix_patterns_template_customer', 'template_id', 'customer_id'))


class CollectionMember(Base):
    """Learned customers of a provider settlement layout, without duplicate vectors."""
    __tablename__ = 'collection_memberships'
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey('payment_templates.id'), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_period: Mapped[str] = mapped_column(String(7), default='unknown')
    last_seen: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (UniqueConstraint('template_id', 'customer_id', name='uq_collection_membership'),)


class NumericSlot(Base):
    __tablename__ = 'numeric_slots'
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey('transaction_patterns.id'), index=True)
    slot_index: Mapped[int] = mapped_column(Integer)
    slot_confidence: Mapped[float] = mapped_column(Float, default=0.2)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_period: Mapped[str] = mapped_column(String(7), default='')
    __table_args__ = (UniqueConstraint('pattern_id', 'slot_index'),)


class NumericFeature(Base):
    __tablename__ = 'numeric_features'
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey('transaction_patterns.id'), index=True)
    slot_index: Mapped[int] = mapped_column(Integer)
    # Preserve long bank/customer-code runs exactly; never truncate identifiers.
    numeric_value: Mapped[str] = mapped_column(Text)
    value_digest: Mapped[str] = mapped_column(String(64), index=True, default=numeric_digest_default)
    numeric_type: Mapped[str] = mapped_column(String(30))
    exact_value_confidence: Mapped[float] = mapped_column(Float, default=0.2)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    last_period: Mapped[str] = mapped_column(String(7), default='')
    last_seen: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (UniqueConstraint('pattern_id', 'slot_index', 'value_digest', name='uq_numeric_features_pattern_slot_digest'),)


class Posting(Base):
    __tablename__ = 'retrieval_postings'
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(Text)
    token_digest: Mapped[str] = mapped_column(String(64), index=True, default=token_digest_default)
    pattern_id: Mapped[int] = mapped_column(ForeignKey('transaction_patterns.id'), index=True)
    weight: Mapped[float] = mapped_column(Float)
    __table_args__ = (UniqueConstraint('token_digest', 'pattern_id', name='uq_retrieval_postings_digest_pattern'),)


class HardNegative(Base):
    __tablename__ = 'hard_negatives'
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'))
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey('transaction_patterns.id'), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('transaction_fingerprint', 'customer_id'),)


class RecurringLink(Base):
    """A confirmed, customer-specific observation; not a filtered-result history."""
    __tablename__ = 'recurring_links'
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey('transaction_patterns.id'), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    channel_key: Mapped[str] = mapped_column(Text)
    signature_hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[dict] = mapped_column(DOC)
    raw_example: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str] = mapped_column(Text, default='')
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    example_date: Mapped[str] = mapped_column(String(40), default='')
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    last_period: Mapped[str] = mapped_column(String(7), default='unknown')
    last_seen: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (UniqueConstraint('pattern_id', 'signature_hash', name='uq_recurring_pattern_signature'),
                     Index('ix_recurring_channel_signature', 'channel_key', 'signature_hash'))


class IdSlotStat(Base):
    """Learned from confirmed narratives: how often a number in this local layout is the customer ID."""
    __tablename__ = 'customer_id_slots'
    id: Mapped[int] = mapped_column(primary_key=True)
    context: Mapped[str] = mapped_column(Text)
    channel_key: Mapped[str] = mapped_column(Text, default='unknown')
    hits: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    example: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (CheckConstraint('hits >= 0 AND hits <= total', name='ck_id_slots_counts'),
                     UniqueConstraint('channel_key', 'context', name='uq_id_slots_channel_context'))


class NameSlotStat(Base):
    """Learned from confirmed narratives: where a bank layout writes a customer name."""
    __tablename__ = 'customer_name_slots'
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_key: Mapped[str] = mapped_column(Text, default='unknown')
    context: Mapped[str] = mapped_column(Text)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    # Units that ended confirmed names at this position, with counts (e.g. {".": 40, "chuyen": 12}).
    boundaries: Mapped[dict] = mapped_column(DOC, default=dict)
    example: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (CheckConstraint('hits >= 0 AND hits <= total', name='ck_name_slots_counts'),
                     UniqueConstraint('channel_key', 'context', name='uq_name_slots_channel_context'))


class Metadata(Base):
    __tablename__ = 'knowledge_metadata'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class KnowledgeReceipt(Base):
    __tablename__ = 'knowledge_receipts'
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'))
    period: Mapped[str] = mapped_column(String(7))


class BankAlias(Base):
    __tablename__ = 'bank_aliases'
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(100))
    normalized_alias: Mapped[str] = mapped_column(String(100), unique=True)
    channel_key: Mapped[str] = mapped_column(String(40), index=True)
    note: Mapped[str] = mapped_column(Text, default='')
    used_at: Mapped[str] = mapped_column(String(40), default='')


class KnowledgeChange(Base):
    """Knowledge corrections only; never reconciliation rows or uploaded result history."""
    __tablename__ = 'knowledge_changes'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity_key: Mapped[str] = mapped_column(String(160), index=True)
    before: Mapped[dict] = mapped_column(DOC)
    after: Mapped[dict] = mapped_column(DOC)
    reversible: Mapped[bool] = mapped_column(default=False)
    reverted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)


def make_engine(url: str):
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    engine = create_engine(url, pool_pre_ping=True,
                           connect_args={'check_same_thread': False, 'timeout': 60} if url.startswith('sqlite') else {'connect_timeout': 5})
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def sqlite_settings(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA cache_size=-4096')
    return engine


def default_url():
    return os.getenv('DATABASE_URL', 'postgresql+psycopg://invoice_app:change_me@localhost:5432/invoice_filter')


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)
