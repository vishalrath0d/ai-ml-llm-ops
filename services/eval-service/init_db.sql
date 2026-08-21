-- Reference schema for eval-service.
--
-- NOT required for normal operation: the FastAPI app calls
-- Base.metadata.create_all() on startup and creates these tables
-- automatically if they don't already exist (see app/database.py,
-- app/models.py, and the lifespan hook in app/main.py). This file exists so
-- the schema is easy to read/review, and as an option for anyone who'd
-- rather pre-provision it explicitly (e.g. as a postgres
-- docker-entrypoint-initdb.d script) instead of relying on create_all().

CREATE TABLE IF NOT EXISTS scenarios (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    opening_message   TEXT NOT NULL,
    success_criteria  TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS test_runs (
    id                       SERIAL PRIMARY KEY,
    scenario_id              INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    conversation_transcript  JSONB NOT NULL DEFAULT '[]'::jsonb,
    score_percent            DOUBLE PRECISION NOT NULL DEFAULT 0,
    result                   VARCHAR(16) NOT NULL,
    reason                   TEXT NOT NULL DEFAULT '',
    latency_ms               INTEGER NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_test_runs_scenario_id ON test_runs (scenario_id);
