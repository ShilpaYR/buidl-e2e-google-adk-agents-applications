-- QuantumRoast internal bug-ticket database (PostgreSQL 15+).
CREATE DATABASE ticketsdb;
\connect ticketsdb

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     SERIAL PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    description   TEXT,
    assignee      VARCHAR(100),
    priority      VARCHAR(50)  NOT NULL DEFAULT 'P3 - Low',
    status        VARCHAR(50)  NOT NULL DEFAULT 'Open',
    creation_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_time  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_priority CHECK (priority IN ('P0 - Critical','P1 - High','P2 - Medium','P3 - Low')),
    CONSTRAINT valid_status   CHECK (status   IN ('Open','In Progress','Closed','Resolved'))
);

-- Keep updated_time correct on every UPDATE.
CREATE OR REPLACE FUNCTION touch_updated_time() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_time = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tickets_touch ON tickets;
CREATE TRIGGER tickets_touch BEFORE UPDATE ON tickets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_time();

-- Local keyword search index (the Cloud SQL variant adds vector embeddings, see cloud_embeddings.sql).
CREATE INDEX IF NOT EXISTS tickets_fts ON tickets
    USING GIN (to_tsvector('english', title || ' ' || COALESCE(description, '')));
