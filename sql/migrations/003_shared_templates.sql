-- Upgrade a current knowledge-only database (after migration 002) to shared templates.
-- Run yourself with the service stopped. Does not delete learned patterns/numbers/receipts.
-- New databases: use sql/create_current.sql alone instead.
BEGIN;
SELECT pg_advisory_xact_lock(731026);

CREATE TABLE IF NOT EXISTS payment_templates (
    id SERIAL PRIMARY KEY,
    fingerprint VARCHAR(64) NOT NULL UNIQUE,
    template_text TEXT NOT NULL,
    structure TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    payment_mode VARCHAR(16) NOT NULL DEFAULT 'unknown',
    provider_kind VARCHAR(16) NOT NULL DEFAULT 'unknown',
    provider_name TEXT NOT NULL DEFAULT '',
    created_at VARCHAR(40) NOT NULL
);

ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(16) NOT NULL DEFAULT 'unknown';
ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS provider_kind VARCHAR(16) NOT NULL DEFAULT 'unknown';
ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS provider_name TEXT NOT NULL DEFAULT '';

ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS template_id INTEGER;
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(16) NOT NULL DEFAULT 'unknown';
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS provider_kind VARCHAR(16) NOT NULL DEFAULT 'unknown';
ALTER TABLE transaction_patterns ADD COLUMN IF NOT EXISTS provider_name TEXT NOT NULL DEFAULT '';

-- Backfill exact existing layouts. No approximate merge, and no pooling numeric values.
INSERT INTO payment_templates (fingerprint, template_text, structure, display_name, description,
                               payment_mode, provider_kind, provider_name, created_at)
SELECT DISTINCT encode(sha256(convert_to(template_text || chr(31) || structure, 'UTF8')), 'hex'),
       template_text, structure, '', '', 'unknown', 'unknown', '',
       to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'
FROM transaction_patterns
WHERE template_id IS NULL
ON CONFLICT (fingerprint) DO NOTHING;

UPDATE transaction_patterns AS p
SET template_id = t.id
FROM payment_templates AS t
WHERE p.template_id IS NULL
  AND t.fingerprint = encode(sha256(convert_to(p.template_text || chr(31) || p.structure, 'UTF8')), 'hex')
  AND t.template_text = p.template_text AND t.structure = p.structure;

-- Old data remain unknown: a bank sheet/protocol alone is not a verified proxy/self label.

ALTER TABLE transaction_patterns ALTER COLUMN template_id SET NOT NULL;
DO $constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND conrelid = 'transaction_patterns'::regclass
                   AND confrelid = 'payment_templates'::regclass
                   AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'transaction_patterns'::regclass AND attname = 'template_id')]::smallint[]) THEN
        ALTER TABLE transaction_patterns ADD CONSTRAINT fk_patterns_shared_template
            FOREIGN KEY (template_id) REFERENCES payment_templates(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_patterns_payment_mode' AND conrelid = 'transaction_patterns'::regclass) THEN
        ALTER TABLE transaction_patterns ADD CONSTRAINT ck_patterns_payment_mode CHECK (payment_mode IN ('unknown', 'proxy', 'self'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_patterns_provider_kind' AND conrelid = 'transaction_patterns'::regclass) THEN
        ALTER TABLE transaction_patterns ADD CONSTRAINT ck_patterns_provider_kind CHECK (provider_kind IN ('unknown', 'bank', 'wallet', 'other'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_templates_payment_mode' AND conrelid = 'payment_templates'::regclass) THEN
        ALTER TABLE payment_templates ADD CONSTRAINT ck_templates_payment_mode CHECK (payment_mode IN ('unknown', 'proxy', 'self'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_templates_provider_kind' AND conrelid = 'payment_templates'::regclass) THEN
        ALTER TABLE payment_templates ADD CONSTRAINT ck_templates_provider_kind CHECK (provider_kind IN ('unknown', 'bank', 'wallet', 'other'));
    END IF;
END;
$constraints$;
CREATE INDEX IF NOT EXISTS ix_transaction_patterns_template_id ON transaction_patterns(template_id);
CREATE INDEX IF NOT EXISTS ix_patterns_template_customer ON transaction_patterns(template_id, customer_id);

INSERT INTO retrieval_postings (token, token_digest, pattern_id, weight)
SELECT 'template:' || t.fingerprint,
       encode(sha256(convert_to('template:' || t.fingerprint, 'UTF8')), 'hex'), p.id, 2.0
FROM transaction_patterns AS p JOIN payment_templates AS t ON t.id = p.template_id
ON CONFLICT (token_digest, pattern_id) DO NOTHING;

DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON payment_templates TO invoice_app;
        GRANT USAGE, SELECT ON SEQUENCE payment_templates_id_seq TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
