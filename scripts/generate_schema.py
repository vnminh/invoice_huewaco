"""Generate SQL for the user to execute; never connects to a database."""
from pathlib import Path
import argparse
import sys

from sqlalchemy import create_mock_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', default='sql/create_current.sql', help='Standalone current-schema SQL output')
args = parser.parse_args()
path = Path(args.output)
if not path.is_absolute():
    path = Path(__file__).resolve().parents[1] / path

STATEMENTS = []
def capture(sql, *args, **kwargs):
    STATEMENTS.append(str(sql.compile(dialect=engine.dialect)).strip() + ';')

engine = create_mock_engine('postgresql+psycopg://', capture)
Base.metadata.create_all(engine)
header = f'''-- Standalone current schema: run this file alone for a new database.
-- No init.sql or migration script is required before or after this file for a new database.
-- Run inside a dedicated database as its owner / extension-capable administrator.
-- From your Linux user shell: sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < {args.output}
-- Windows PowerShell: psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "{args.output}"
-- Requires PostgreSQL 15+ and pgvector installed on the PostgreSQL server.
-- This script does not create users/databases, drop data, or start any services.
-- Existing older schemas are not upgraded by CREATE TABLE IF NOT EXISTS.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
'''
indexes = '''
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
'''
path.parent.mkdir(parents=True, exist_ok=True)
# Idempotent initialization; schema upgrades should use versioned migrations.
sql = '\n\n'.join(STATEMENTS).replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ')
sql = sql.replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ')
sql = sql.replace('CREATE UNIQUE INDEX ', 'CREATE UNIQUE INDEX IF NOT EXISTS ')
path.write_text(header + '\n' + sql + '\n' + indexes, encoding='utf-8')
print(path)
