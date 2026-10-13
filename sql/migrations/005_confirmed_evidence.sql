-- Upgrade a database already on migration 004. Stop the application first.
-- Then run: python scripts/reindex_knowledge.py
-- Reimport approved FN files afterwards to enrich old compact settlement memberships.
-- New database: use sql/create_current.sql alone; do not run migrations.
BEGIN;
SELECT pg_advisory_xact_lock(731026);

ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS channel_key TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS channel_key TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS normalizer_version TEXT NOT NULL DEFAULT '';
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS label_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ix_payment_templates_channel_key ON payment_templates(channel_key);
CREATE INDEX IF NOT EXISTS ix_transaction_patterns_channel_key ON transaction_patterns(channel_key);

ALTER TABLE customer_id_slots ADD COLUMN IF NOT EXISTS channel_key TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE customer_id_slots DROP CONSTRAINT IF EXISTS customer_id_slots_context_key;
DO $constraint$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_id_slots_channel_context'
                   AND conrelid = 'customer_id_slots'::regclass) THEN
        ALTER TABLE customer_id_slots ADD CONSTRAINT uq_id_slots_channel_context UNIQUE (channel_key, context);
    END IF;
END;
$constraint$;

CREATE TABLE IF NOT EXISTS collection_memberships (
    id SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES payment_templates(id),
    customer_id VARCHAR(100) NOT NULL REFERENCES customers(id),
    seen_count INTEGER NOT NULL DEFAULT 1,
    last_period VARCHAR(7) NOT NULL DEFAULT 'unknown',
    last_seen VARCHAR(40) NOT NULL,
    CONSTRAINT uq_collection_membership UNIQUE (template_id, customer_id)
);
CREATE INDEX IF NOT EXISTS ix_collection_memberships_template_id ON collection_memberships(template_id);
CREATE INDEX IF NOT EXISTS ix_collection_memberships_customer_id ON collection_memberships(customer_id);

DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON collection_memberships TO invoice_app;
        GRANT USAGE, SELECT ON SEQUENCE collection_memberships_id_seq TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
