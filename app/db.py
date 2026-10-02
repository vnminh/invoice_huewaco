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
    display_name: Mapped[str] = mapped_column(Text, default='')
    description: Mapped[str] = mapped_column(Text, default='')
    payment_mode: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_kind: Mapped[str] = mapped_column(String(16), default='unknown')
    provider_name: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (
        CheckConstraint("payment_mode IN ('unknown', 'proxy', 'self')", name='ck_templates_payment_mode'),
        CheckConstraint("provider_kind IN ('unknown', 'bank', 'wallet', 'other')", name='ck_templates_provider_kind'))


class Pattern(Base):
    __tablename__ = 'transaction_patterns'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey('payment_templates.id'), index=True)
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
