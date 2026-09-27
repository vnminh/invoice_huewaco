-- Run manually in invoice_filter as the table owner or an administrator.
-- Preserves existing values, constraints, and indexes. Safe to run again.
-- Stop the backend/import workers before applying this schema change.
BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE public.numeric_features ALTER COLUMN numeric_value TYPE TEXT;
ALTER TABLE public.retrieval_postings ALTER COLUMN token TYPE TEXT;
COMMIT;
