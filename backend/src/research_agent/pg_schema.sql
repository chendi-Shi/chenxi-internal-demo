CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS retrieval_lab;
CREATE TABLE IF NOT EXISTS retrieval_lab.documents (
    id text PRIMARY KEY, source_id text NOT NULL, title text NOT NULL,
    body text NOT NULL, published_at timestamptz, url text NOT NULL,
    metadata jsonb NOT NULL, enabled boolean NOT NULL,
    fingerprint text NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_lab.chunks (
    id bigint PRIMARY KEY,
    document_id text NOT NULL REFERENCES retrieval_lab.documents(id) ON DELETE CASCADE,
    start_offset integer NOT NULL, end_offset integer NOT NULL,
    page integer, text text NOT NULL, tokens tsvector NOT NULL,
    CHECK (end_offset >= start_offset)
);
CREATE INDEX IF NOT EXISTS chunks_document ON retrieval_lab.chunks(document_id);
CREATE TABLE IF NOT EXISTS retrieval_lab.embeddings (
    chunk_id bigint NOT NULL REFERENCES retrieval_lab.chunks(id) ON DELETE CASCADE,
    model_digest text NOT NULL, embedding vector NOT NULL,
    PRIMARY KEY(chunk_id, model_digest)
);
CREATE TABLE IF NOT EXISTS retrieval_lab.snapshot (
    id integer PRIMARY KEY CHECK(id=1), imported_at timestamptz NOT NULL,
    document_count bigint NOT NULL, chunk_count bigint NOT NULL,
    vector_count bigint NOT NULL, semantic_config jsonb
);
