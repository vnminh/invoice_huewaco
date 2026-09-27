-- MANUAL UPGRADE ONLY. Stop the backend before running this script.
-- Export legacy operational data first with scripts/archive_legacy_results.py.
-- This removes jobs/transactions/feedback, including their old rows.
-- Learned customer/pattern/numeric knowledge remains intact.
-- Do not use CASCADE: unexpected external dependencies must stop the migration.
BEGIN;
SET LOCAL lock_timeout = '10s';

ALTER TABLE public.numeric_features ALTER COLUMN numeric_value TYPE TEXT;
ALTER TABLE public.retrieval_postings ALTER COLUMN token TYPE TEXT;

ALTER TABLE public.numeric_features ADD COLUMN IF NOT EXISTS value_digest VARCHAR(64);
UPDATE public.numeric_features
SET value_digest = encode(sha256(convert_to(numeric_value, 'UTF8')), 'hex')
WHERE value_digest IS NULL;
ALTER TABLE public.numeric_features ALTER COLUMN value_digest SET NOT NULL;
ALTER TABLE public.numeric_features DROP CONSTRAINT IF EXISTS numeric_features_pattern_id_slot_index_numeric_value_key;
DROP INDEX IF EXISTS public.ix_numeric_features_numeric_value;
CREATE UNIQUE INDEX IF NOT EXISTS uq_numeric_features_pattern_slot_digest
ON public.numeric_features(pattern_id, slot_index, value_digest);
CREATE INDEX IF NOT EXISTS ix_numeric_features_value_digest ON public.numeric_features(value_digest);

ALTER TABLE public.retrieval_postings ADD COLUMN IF NOT EXISTS token_digest VARCHAR(64);
UPDATE public.retrieval_postings
SET token_digest = encode(sha256(convert_to(token, 'UTF8')), 'hex')
WHERE token_digest IS NULL;
ALTER TABLE public.retrieval_postings ALTER COLUMN token_digest SET NOT NULL;
ALTER TABLE public.retrieval_postings DROP CONSTRAINT IF EXISTS retrieval_postings_token_pattern_id_key;
DROP INDEX IF EXISTS public.ix_retrieval_postings_token;
CREATE UNIQUE INDEX IF NOT EXISTS uq_retrieval_postings_digest_pattern
ON public.retrieval_postings(token_digest, pattern_id);
CREATE INDEX IF NOT EXISTS ix_retrieval_postings_token_digest ON public.retrieval_postings(token_digest);

DROP TABLE IF EXISTS public.feedback;
DROP TABLE IF EXISTS public.transactions;
DROP TABLE IF EXISTS public.jobs;
COMMIT;
