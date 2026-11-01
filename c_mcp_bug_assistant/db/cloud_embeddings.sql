-- Run on Cloud SQL for PostgreSQL (not needed for local mode).
-- Adds semantic search: Vertex AI text-embedding-005 called from inside SQL.
-- Prerequisite: instance flag cloudsql.enable_google_ml_integration=on and the
-- Cloud SQL service account granted roles/aiplatform.user.
\connect ticketsdb
CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE;
CREATE EXTENSION IF NOT EXISTS vector CASCADE;
GRANT EXECUTE ON FUNCTION embedding TO postgres;

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS embedding vector(768)
    GENERATED ALWAYS AS (embedding('text-embedding-005', description)) STORED;
