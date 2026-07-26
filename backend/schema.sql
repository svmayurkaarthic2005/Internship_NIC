-- =============================================================
-- SIS Copilot — Reference Schema DDL
-- =============================================================
-- Run against the sis_chatbot database before first startup,
-- OR let SQLAlchemy's Base.metadata.create_all() handle it.
--
-- Prerequisite: the pgvector OS package must be installed.
--   Debian/Ubuntu: sudo apt install postgresql-<ver>-pgvector
--   Windows (pgAdmin): available in PostgreSQL 15+ installer
-- =============================================================

-- ── Extensions ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";    -- uuid_generate_v4()

-- ── knowledge_embeddings ────────────────────────────────────
-- Stores document chunks and their vector embeddings for
-- semantic similarity search (replaces ChromaDB).
-- Embedding model : nomic-embed-text (768 dimensions)
-- Index type      : HNSW cosine similarity

CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    chunk_id    TEXT        UNIQUE NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector(768) NOT NULL,

    -- Metadata (directly filterable without JSON parsing)
    source      TEXT,                           -- e.g. "SIS Question Bank"
    category    TEXT,                           -- e.g. "workflow"
    section     TEXT,                           -- e.g. "Field Visit Scheduling"
    language    TEXT        DEFAULT 'en',        -- en / ta / tanglish
    page        INTEGER     DEFAULT 0,

    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- ── Indexes ─────────────────────────────────────────────────

-- HNSW index for approximate cosine-distance nearest-neighbour search
-- m=16, ef_construction=64: good defaults for datasets < 5M vectors
CREATE INDEX IF NOT EXISTS idx_ke_embedding_hnsw
    ON knowledge_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_ke_category ON knowledge_embeddings (category);
CREATE INDEX IF NOT EXISTS idx_ke_language  ON knowledge_embeddings (language);
CREATE INDEX IF NOT EXISTS idx_ke_chunk_id  ON knowledge_embeddings (chunk_id);

-- ── updated_at trigger ──────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ke_updated_at ON knowledge_embeddings;
CREATE TRIGGER trg_ke_updated_at
    BEFORE UPDATE ON knowledge_embeddings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
