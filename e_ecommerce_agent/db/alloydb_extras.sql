-- AlloyDB for PostgreSQL only (production mode). Run after schema.sql + seed.sql.
-- Adds semantic product search: Vertex AI text-embedding-005 is called from SQL,
-- and the vectors are stored next to the rows (pgvector).
\connect store
CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE;
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(768);
UPDATE products
SET embedding = embedding('text-embedding-005',
                          name || '. ' || brand || '. ' || category || '. ' || coalesce(description, ''))::vector;

-- ScaNN index for fast approximate nearest-neighbour search at scale
CREATE EXTENSION IF NOT EXISTS alloydb_scann;
CREATE INDEX IF NOT EXISTS products_embedding_scann ON products
    USING scann (embedding cosine) WITH (num_leaves = 1);
