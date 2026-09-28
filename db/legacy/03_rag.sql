-- Índice RAG (Fase 2): conocimiento del esquema para el agente, en pgvector.
-- IDEMPOTENTE: se ejecuta al crear el volumen (docker-entrypoint-initdb.d) y con
-- `make db-migrate` sobre un volumen existente.
--
-- Separación de privilegios:
--   lb_rag_rw  escribe el índice (make index); sin acceso a las tablas del ERP.
--   lb_ro      solo lee rag.chunks con una consulta FIJA del código; el SQL generado por el
--              LLM nunca llega aquí porque el guard solo permite el esquema public.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.chunks (
    id            BIGSERIAL PRIMARY KEY,
    embed_model   TEXT        NOT NULL,              -- p. ej. 'local:text-embedding-bge-m3'
    source        TEXT        NOT NULL,              -- p. ej. 'table:pedenc', 'defect:D3'
    kind          TEXT        NOT NULL,              -- ddl | table | rules | catalogs | defect | doc
    ref           TEXT,                              -- tabla / regla / defecto al que se refiere
    chunk_index   INTEGER     NOT NULL DEFAULT 0,
    content       TEXT        NOT NULL,
    content_hash  TEXT        NOT NULL,              -- sha256 del contenido: reindexado incremental
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    embedding     vector(1024) NOT NULL,
    indexed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (embed_model, source, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_embed_model ON rag.chunks (embed_model);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lb_rag_rw') THEN
        CREATE ROLE lb_rag_rw LOGIN PASSWORD 'lb_rag_rw';
    END IF;
END $$;

GRANT CONNECT ON DATABASE legacy TO lb_rag_rw;
GRANT USAGE ON SCHEMA rag TO lb_rag_rw, lb_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON rag.chunks TO lb_rag_rw;
GRANT USAGE, SELECT ON SEQUENCE rag.chunks_id_seq TO lb_rag_rw;
GRANT SELECT ON rag.chunks TO lb_ro;
ALTER ROLE lb_rag_rw SET statement_timeout = '30s';
