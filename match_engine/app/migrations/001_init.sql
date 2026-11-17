-- Match Engine schema. Idempotent: safe to run on every start.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- array_to_string is only STABLE; generated columns need an IMMUTABLE expression.
CREATE OR REPLACE FUNCTION niti_skills_text(text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT coalesce(array_to_string($1, ' '), '') $$;

CREATE TABLE IF NOT EXISTS jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source        TEXT NOT NULL,                 -- rapidapi | adzuna | manual
    external_id   TEXT,
    dedup_key     TEXT NOT NULL UNIQUE,          -- normalised title|company|location
    title         TEXT NOT NULL,
    company       TEXT,
    location      TEXT,
    remote        BOOLEAN DEFAULT false,
    description   TEXT DEFAULT '',
    url           TEXT,
    salary_min    NUMERIC,
    salary_max    NUMERIC,
    salary_text   TEXT,
    skills        TEXT[] DEFAULT '{}',           -- canonical skills extracted from the posting
    min_years     NUMERIC,                       -- parsed experience requirement, if stated
    posted_at     TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    last_seen_at  TIMESTAMPTZ DEFAULT now(),
    is_active     BOOLEAN DEFAULT true,
    content_hash  TEXT NOT NULL,
    embedded_hash TEXT,                          -- content_hash at the time of the last embedding
    embedding     vector(384),
    search_tsv    tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', niti_skills_text(skills)), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED
);
CREATE INDEX IF NOT EXISTS idx_jobs_tsv ON jobs USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs (is_active, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_embedding ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_source_ext ON jobs (source, external_id);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    fetched     INT DEFAULT 0,
    inserted    INT DEFAULT 0,
    updated     INT DEFAULT 0,
    expired     INT DEFAULT 0,
    embedded    INT DEFAULT 0,
    errors      JSONB DEFAULT '[]'::jsonb
);

-- Click / save / apply events, used for personalisation and learning-to-rank.
CREATE TABLE IF NOT EXISTS job_interactions (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL,
    job_id     TEXT NOT NULL,
    event      TEXT NOT NULL CHECK (event IN ('impression','click','save','apply','dismiss')),
    position   INT,
    score      NUMERIC,
    features   JSONB,                            -- scoring components at the time of the event
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON job_interactions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_job ON job_interactions (job_id);

-- Long-term memory for the career coach (one row per remembered fact).
CREATE TABLE IF NOT EXISTS coach_memory (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL,
    kind       TEXT NOT NULL,                    -- preference | goal | constraint | note
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, kind, key)
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coach_messages_user ON coach_messages (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS request_logs (
    id          BIGSERIAL PRIMARY KEY,
    route       TEXT NOT NULL,
    method      TEXT NOT NULL,
    status      INT NOT NULL,
    latency_ms  NUMERIC NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_request_logs_time ON request_logs (created_at DESC);
