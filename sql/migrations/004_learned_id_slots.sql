-- Upgrade a shared-template database (after migration 003) to learned customer-ID positions
-- and compact batch settlements. Run yourself with the service stopped; additive only.
-- New databases: use sql/create_current.sql alone instead.
-- Knowledge learned by an older version must be reimported into a fresh database: the
-- feature extractor changed (rules-v6-learned-id-slots) and the application refuses to mix them.
BEGIN;
SELECT pg_advisory_xact_lock(731026);

ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS max_transfer_customers INTEGER NOT NULL DEFAULT 1;
ALTER TABLE payment_templates ADD COLUMN IF NOT EXISTS settlement_rows INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS customer_id_slots (
    id SERIAL PRIMARY KEY,
    context TEXT NOT NULL UNIQUE,
    hits INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    example TEXT NOT NULL DEFAULT '',
    updated_at VARCHAR(40) NOT NULL,
    CONSTRAINT ck_id_slots_counts CHECK (hits >= 0 AND hits <= total)
);

DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON customer_id_slots TO invoice_app;
        GRANT USAGE, SELECT ON SEQUENCE customer_id_slots_id_seq TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
