-- Stop the web service; run manually as owner on a database upgraded through 006.
-- Then run scripts/rebuild_recurring_memory.py before restarting the service.
BEGIN;
CREATE TABLE IF NOT EXISTS recurring_links (
    id SERIAL PRIMARY KEY,
    pattern_id INTEGER NOT NULL REFERENCES transaction_patterns(id),
    customer_id VARCHAR(100) NOT NULL REFERENCES customers(id),
    channel_key TEXT NOT NULL,
    signature_hash VARCHAR(64) NOT NULL,
    signature JSONB NOT NULL,
    raw_example TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row INTEGER,
    example_date VARCHAR(40) NOT NULL,
    seen_count INTEGER NOT NULL,
    last_period VARCHAR(7) NOT NULL,
    last_seen VARCHAR(40) NOT NULL,
    CONSTRAINT uq_recurring_pattern_signature UNIQUE(pattern_id, signature_hash)
);
CREATE INDEX IF NOT EXISTS ix_recurring_links_pattern_id ON recurring_links(pattern_id);
CREATE INDEX IF NOT EXISTS ix_recurring_links_customer_id ON recurring_links(customer_id);
CREATE INDEX IF NOT EXISTS ix_recurring_channel_signature ON recurring_links(channel_key, signature_hash);
DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON recurring_links TO invoice_app;
        GRANT USAGE, SELECT ON SEQUENCE recurring_links_id_seq TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
