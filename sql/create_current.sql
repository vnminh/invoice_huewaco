-- Standalone current schema: run this file alone for a new database.
-- No init.sql or migration script is required before or after this file for a new database.
-- Run inside a dedicated database as its owner / extension-capable administrator.
-- From your Linux user shell: sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/create_current.sql
-- Windows PowerShell: psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/create_current.sql"
-- Requires PostgreSQL 15+ and pgvector installed on the PostgreSQL server.
-- This script does not create users/databases, drop data, or start any services.
-- Existing older schemas are not upgraded by CREATE TABLE IF NOT EXISTS.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS customers (
	id VARCHAR(100) NOT NULL, 
	canonical_name TEXT NOT NULL, 
	normalized_name TEXT NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_customers_normalized_name ON customers (normalized_name);

CREATE TABLE IF NOT EXISTS payment_templates (
	id SERIAL NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	template_text TEXT NOT NULL, 
	structure TEXT NOT NULL, 
	display_name TEXT NOT NULL, 
	description TEXT NOT NULL, 
	payment_mode VARCHAR(16) NOT NULL, 
	provider_kind VARCHAR(16) NOT NULL, 
	provider_name TEXT NOT NULL, 
	created_at VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_templates_payment_mode CHECK (payment_mode IN ('unknown', 'proxy', 'self')), 
	CONSTRAINT ck_templates_provider_kind CHECK (provider_kind IN ('unknown', 'bank', 'wallet', 'other')), 
	UNIQUE (fingerprint)
);

CREATE TABLE IF NOT EXISTS knowledge_metadata (
	key VARCHAR(100) NOT NULL, 
	value TEXT NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS customer_aliases (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	alias TEXT NOT NULL, 
	normalized_alias TEXT NOT NULL, 
	confidence FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (customer_id, normalized_alias), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);

CREATE INDEX IF NOT EXISTS ix_customer_aliases_customer_id ON customer_aliases (customer_id);

CREATE INDEX IF NOT EXISTS ix_customer_aliases_normalized_alias ON customer_aliases (normalized_alias);

CREATE TABLE IF NOT EXISTS payer_entities (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	payer_name TEXT NOT NULL, 
	payer_type VARCHAR(30) NOT NULL, 
	confidence FLOAT NOT NULL, 
	seen_count INTEGER NOT NULL, 
	last_seen VARCHAR(40) NOT NULL, 
	last_period VARCHAR(7) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (customer_id, payer_name), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);

CREATE INDEX IF NOT EXISTS ix_payer_entities_payer_name ON payer_entities (payer_name);

CREATE INDEX IF NOT EXISTS ix_payer_entities_customer_id ON payer_entities (customer_id);

CREATE TABLE IF NOT EXISTS knowledge_receipts (
	id SERIAL NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	period VARCHAR(7) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (fingerprint), 
	FOREIGN KEY(customer_id) REFERENCES customers (id)
);

CREATE TABLE IF NOT EXISTS transaction_patterns (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	template_id INTEGER NOT NULL, 
	payment_mode VARCHAR(16) NOT NULL, 
	provider_kind VARCHAR(16) NOT NULL, 
	provider_name TEXT NOT NULL, 
	payer_id INTEGER, 
	fingerprint VARCHAR(64) NOT NULL, 
	normalized_text TEXT NOT NULL, 
	template_text TEXT NOT NULL, 
	raw_example TEXT NOT NULL, 
	source_file TEXT NOT NULL, 
	source_row INTEGER, 
	example_date VARCHAR(40) NOT NULL, 
	structure TEXT NOT NULL, 
	segments JSONB NOT NULL, 
	embedding vector(384) NOT NULL, 
	embedding_model TEXT NOT NULL, 
	seen_count INTEGER NOT NULL, 
	confidence FLOAT NOT NULL, 
	last_seen VARCHAR(40) NOT NULL, 
	last_period VARCHAR(7) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (customer_id, fingerprint), 
	CONSTRAINT ck_patterns_payment_mode CHECK (payment_mode IN ('unknown', 'proxy', 'self')), 
	CONSTRAINT ck_patterns_provider_kind CHECK (provider_kind IN ('unknown', 'bank', 'wallet', 'other')), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(template_id) REFERENCES payment_templates (id), 
	FOREIGN KEY(payer_id) REFERENCES payer_entities (id)
);

CREATE INDEX IF NOT EXISTS ix_transaction_patterns_customer_id ON transaction_patterns (customer_id);

CREATE INDEX IF NOT EXISTS ix_patterns_template_customer ON transaction_patterns (template_id, customer_id);

CREATE INDEX IF NOT EXISTS ix_transaction_patterns_template_id ON transaction_patterns (template_id);

CREATE TABLE IF NOT EXISTS numeric_slots (
	id SERIAL NOT NULL, 
	pattern_id INTEGER NOT NULL, 
	slot_index INTEGER NOT NULL, 
	slot_confidence FLOAT NOT NULL, 
	seen_count INTEGER NOT NULL, 
	last_period VARCHAR(7) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (pattern_id, slot_index), 
	FOREIGN KEY(pattern_id) REFERENCES transaction_patterns (id)
);

CREATE INDEX IF NOT EXISTS ix_numeric_slots_pattern_id ON numeric_slots (pattern_id);

CREATE TABLE IF NOT EXISTS numeric_features (
	id SERIAL NOT NULL, 
	pattern_id INTEGER NOT NULL, 
	slot_index INTEGER NOT NULL, 
	numeric_value TEXT NOT NULL, 
	value_digest VARCHAR(64) NOT NULL, 
	numeric_type VARCHAR(30) NOT NULL, 
	exact_value_confidence FLOAT NOT NULL, 
	seen_count INTEGER NOT NULL, 
	missing_count INTEGER NOT NULL, 
	last_period VARCHAR(7) NOT NULL, 
	last_seen VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_numeric_features_pattern_slot_digest UNIQUE (pattern_id, slot_index, value_digest), 
	FOREIGN KEY(pattern_id) REFERENCES transaction_patterns (id)
);

CREATE INDEX IF NOT EXISTS ix_numeric_features_pattern_id ON numeric_features (pattern_id);

CREATE INDEX IF NOT EXISTS ix_numeric_features_value_digest ON numeric_features (value_digest);

CREATE TABLE IF NOT EXISTS retrieval_postings (
	id SERIAL NOT NULL, 
	token TEXT NOT NULL, 
	token_digest VARCHAR(64) NOT NULL, 
	pattern_id INTEGER NOT NULL, 
	weight FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_retrieval_postings_digest_pattern UNIQUE (token_digest, pattern_id), 
	FOREIGN KEY(pattern_id) REFERENCES transaction_patterns (id)
);

CREATE INDEX IF NOT EXISTS ix_retrieval_postings_pattern_id ON retrieval_postings (pattern_id);

CREATE INDEX IF NOT EXISTS ix_retrieval_postings_token_digest ON retrieval_postings (token_digest);

CREATE TABLE IF NOT EXISTS hard_negatives (
	id SERIAL NOT NULL, 
	transaction_fingerprint VARCHAR(64) NOT NULL, 
	customer_id VARCHAR(100) NOT NULL, 
	pattern_id INTEGER, 
	count INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (transaction_fingerprint, customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(pattern_id) REFERENCES transaction_patterns (id)
);

CREATE INDEX IF NOT EXISTS ix_hard_negatives_transaction_fingerprint ON hard_negatives (transaction_fingerprint);

CREATE INDEX IF NOT EXISTS ix_customers_name_trgm ON customers USING gin(normalized_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_aliases_trgm ON customer_aliases USING gin(normalized_alias gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_patterns_template_trgm ON transaction_patterns USING gin(template_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_patterns_fts ON transaction_patterns USING gin(to_tsvector('simple', template_text));
CREATE INDEX IF NOT EXISTS ix_patterns_embedding_hnsw ON transaction_patterns USING hnsw(embedding vector_cosine_ops);
-- Grant access to the standard application login when it already exists.
-- No login or password is created by this script.
DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT USAGE ON SCHEMA public TO invoice_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO invoice_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
