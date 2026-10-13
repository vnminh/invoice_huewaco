-- Stop the web service; run manually as owner on a database upgraded through 007. Additive only.
-- Learned name positions are filled by the next knowledge import, or now by running
-- scripts/reindex_knowledge.py (it rebuilds ID and name positions together).
BEGIN;
SELECT pg_advisory_xact_lock(731026);
CREATE TABLE IF NOT EXISTS customer_name_slots (
    id SERIAL PRIMARY KEY,
    channel_key TEXT NOT NULL,
    context TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    boundaries JSONB NOT NULL DEFAULT '{}'::jsonb,
    example TEXT NOT NULL DEFAULT '',
    updated_at VARCHAR(40) NOT NULL,
    CONSTRAINT ck_name_slots_counts CHECK (hits >= 0 AND hits <= total),
    CONSTRAINT uq_name_slots_channel_context UNIQUE (channel_key, context)
);
DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON customer_name_slots TO invoice_app;
        GRANT USAGE, SELECT ON SEQUENCE customer_name_slots_id_seq TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
