"""Optional local E5 / BGE-M3 embeddings. No cloud inference or document upload."""

from __future__ import annotations

import json
import math
import struct
from functools import lru_cache

import httpx

from .models import digest

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "bge-m3:latest"


class SemanticUnavailable(ValueError):
    pass


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hub_vectors (
            chunk_id INTEGER NOT NULL REFERENCES hub_chunks(id) ON DELETE CASCADE,
            model_digest TEXT NOT NULL, content_hash TEXT NOT NULL, vector BLOB NOT NULL,
            PRIMARY KEY(chunk_id,model_digest)
        );
        CREATE TABLE IF NOT EXISTS hub_semantic (
            id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL
        );
    """)


def configuration(conn):
    row = conn.execute("SELECT config_json FROM hub_semantic WHERE id=1").fetchone()
    return json.loads(row[0]) if row else None


def model_digest():
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(OLLAMA_URL + "/api/tags")
            response.raise_for_status()
        item = next(x for x in response.json()["models"] if x["name"] == MODEL)
        if item.get("remote_host") or not item.get("digest"):
            raise ValueError("local_model_required")
        return item["digest"]
    except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise SemanticUnavailable("local_embedding_model_unavailable") from exc


def normalize(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 4096:
        raise SemanticUnavailable("invalid_embedding")
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise SemanticUnavailable("invalid_embedding")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise SemanticUnavailable("invalid_embedding")
    return tuple(v / norm for v in vector)


def embed(texts: list[str], *, timeout=15):
    try:
        vectors = []
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            # Small inference requests let interactive queries interleave with an index build.
            # Database checkpoints can still use a larger batch independently.
            for offset in range(0, len(texts), 2):
                response = client.post(
                    OLLAMA_URL + "/api/embed",
                    json={
                        "model": MODEL,
                        "input": texts[offset : offset + 2],
                        "truncate": False,
                        "keep_alive": "30m",
                    },
                )
                response.raise_for_status()
                vectors.extend(response.json()["embeddings"])
        if len(vectors) != len(texts):
            raise ValueError("embedding_count_mismatch")
        result = [normalize(vector) for vector in vectors]
        if len({len(vector) for vector in result}) != 1:
            raise ValueError("embedding_dimension_mismatch")
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SemanticUnavailable("local_embedding_request_failed") from exc


@lru_cache(maxsize=128)
def cached_query(model_id: str, query: str):
    return embed([query])[0]


def query_vector(config, query):
    if config.get("engine") == "e5":
        try:
            from .hub_e5 import runtime

            return normalize(runtime(config).encode([query], query=True)[0])
        except Exception as exc:
            # Provider-specific ONNX exceptions are not all RuntimeError subclasses.
            raise SemanticUnavailable("local_e5_model_unavailable_or_changed") from exc
    if model_digest() != config["model_digest"]:
        raise SemanticUnavailable("embedding_model_changed_reindex_required")
    vector = cached_query(config["model_digest"], query)
    if len(vector) != config["dimensions"]:
        raise SemanticUnavailable("embedding_dimension_mismatch")
    return vector


def pack(vector):
    return struct.pack(f"<{len(vector)}f", *vector)


def similarity(blob, query):
    if len(blob) != len(query) * 4:
        raise SemanticUnavailable("embedding_dimension_mismatch")
    vector = struct.unpack(f"<{len(query)}f", blob)
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, query, strict=True))))


def embedding_text(row):
    return row["title"] + "\n" + row["text"]


def build_index(
    hub, *, batch_size=8, progress=None, engine="ollama", model_directory=None, existing_config=None
):
    """Resumable batches; skip cached content, never hold a write lock during inference."""
    e5_config = None
    if existing_config:
        engine = existing_config.get("engine", "ollama")
    if engine == "e5":
        from .hub_e5 import prepare, runtime

        e5_config = existing_config or prepare(model_directory or hub.db.parent / "models" / "e5")
        runtime(e5_config)  # Verify and load before touching the index.
    elif engine != "ollama":
        raise SemanticUnavailable("unknown_embedding_engine")
    identity = e5_config["model_digest"] if e5_config else model_digest()
    with hub.connection() as conn:
        rows = conn.execute(
            "SELECT c.id,d.title,c.text,v.content_hash FROM hub_chunks c "
            "JOIN hub_documents d ON d.id=c.document_id LEFT JOIN hub_vectors v "
            "ON v.chunk_id=c.id AND v.model_digest=? ORDER BY c.id",
            (identity,),
        ).fetchall()
    pending = [r for r in rows if r["content_hash"] != digest(embedding_text(r))]
    completed = 0
    dimensions = None
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        texts = [embedding_text(r) for r in batch]
        vectors = runtime(e5_config).encode(texts) if e5_config else embed(texts, timeout=180)
        dimensions = len(vectors[0])
        if not e5_config and model_digest() != identity:
            raise SemanticUnavailable("embedding_model_changed_reindex_required")
        with hub.connection() as conn:
            for row, vector in zip(batch, vectors, strict=True):
                current = conn.execute(
                    "SELECT d.title,c.text FROM hub_chunks c JOIN hub_documents d "
                    "ON d.id=c.document_id WHERE c.id=?",
                    (row["id"],),
                ).fetchone()
                # An update may reuse a chunk row ID. Never attach an old vector to new text.
                if current and embedding_text(current) == embedding_text(row):
                    conn.execute(
                        "INSERT OR REPLACE INTO hub_vectors VALUES(?,?,?,?)",
                        (row["id"], identity, digest(embedding_text(row)), pack(vector)),
                    )
        completed += len(batch)
        if progress:
            progress(completed, len(pending))
    with hub.connection() as conn:
        if dimensions is None:
            row = conn.execute(
                "SELECT length(vector)/4 FROM hub_vectors WHERE model_digest=? LIMIT 1", (identity,)
            ).fetchone()
            if not row:
                raise SemanticUnavailable("no_chunks_to_index")
            dimensions = row[0]
        config = e5_config or {
            "model": MODEL,
            "model_digest": identity,
            "dimensions": dimensions,
            "minimum_similarity": 0.5,
        }
        conn.execute("INSERT OR REPLACE INTO hub_semantic VALUES(1,?)", (json.dumps(config),))
    return {"processed": completed, "cached": len(rows) - len(pending), **index_status(hub)}


def index_status(hub):
    with hub.connection() as conn:
        config = configuration(conn)
        total = conn.execute("SELECT count(*) FROM hub_chunks").fetchone()[0]
        indexed = (
            conn.execute(
                "SELECT count(*) FROM hub_vectors WHERE model_digest=?", (config["model_digest"],)
            ).fetchone()[0]
            if config
            else 0
        )
    return {
        "enabled": config is not None,
        "model": config["model"] if config else None,
        "indexed_chunks": indexed,
        "total_chunks": total,
        "pending_chunks": total - indexed,
    }
