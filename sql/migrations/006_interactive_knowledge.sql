-- Upgrade v7/005 manually as the database owner. No reconciliation history is stored.
BEGIN;
CREATE TABLE IF NOT EXISTS bank_aliases (
	id SERIAL NOT NULL,
	alias VARCHAR(100) NOT NULL,
	normalized_alias VARCHAR(100) NOT NULL,
	channel_key VARCHAR(40) NOT NULL,
	note TEXT NOT NULL,
	used_at VARCHAR(40) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (normalized_alias)
);

CREATE INDEX IF NOT EXISTS ix_bank_aliases_channel_key ON bank_aliases (channel_key);

CREATE TABLE IF NOT EXISTS knowledge_changes (
	id SERIAL NOT NULL,
	actor VARCHAR(100) NOT NULL,
	action VARCHAR(60) NOT NULL,
	entity_key VARCHAR(160) NOT NULL,
	before JSONB NOT NULL,
	after JSONB NOT NULL,
	reversible BOOLEAN NOT NULL,
	reverted_by INTEGER,
	created_at VARCHAR(40) NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_knowledge_changes_created_at ON knowledge_changes (created_at);

CREATE INDEX IF NOT EXISTS ix_knowledge_changes_entity_key ON knowledge_changes (entity_key);

DO $permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
        GRANT USAGE ON SCHEMA public TO invoice_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO invoice_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoice_app;
        -- Audit entries are immutable except for their explicit undo link.
        REVOKE UPDATE, DELETE ON knowledge_changes FROM invoice_app;
        GRANT UPDATE(reverted_by) ON knowledge_changes TO invoice_app;
    END IF;
END;
$permissions$;
COMMIT;
