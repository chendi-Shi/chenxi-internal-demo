"""Local retrieval with optional loopback embeddings; no cloud model calls."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import hub_semantic
from .hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SourceDefinition,
)
from .hub_query import load_entity_aliases, plan_query
from .models import digest, stable_json
from .store import search_tokens, utcnow


class HubError(ValueError):
    pass


class Hub:
    def __init__(self, directory: Path, base_url: str = "http://127.0.0.1:8765"):
        DocumentInput.valid_url(base_url)
        if not base_url or urlsplit(base_url).query or urlsplit(base_url).fragment:
            raise HubError("invalid_base_url")
        directory.mkdir(parents=True, exist_ok=True)
        self.entity_aliases = load_entity_aliases(directory / "query-aliases.local.json")
        self.db = directory / "hub.sqlite3"
        self.base_url = base_url.rstrip("/")
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise HubError("unsupported_hub_schema")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS hub_sources (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hub_documents (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES hub_sources(id),
                    external_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                    published_at TEXT NOT NULL, url TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    pages_json TEXT NOT NULL, fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(source_id, external_id)
                );
                CREATE TABLE IF NOT EXISTS hub_chunks (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL
                    REFERENCES hub_documents(id) ON DELETE CASCADE,
                    start INTEGER NOT NULL, end INTEGER NOT NULL, page INTEGER, text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS hub_chunk_document ON hub_chunks(document_id);
                CREATE INDEX IF NOT EXISTS hub_document_source ON hub_documents(source_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS hub_search USING fts5(tokens);
                CREATE TABLE IF NOT EXISTS hub_policy (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                    policy_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)
            conn.execute(
                "INSERT OR IGNORE INTO hub_policy VALUES(1,1,?,?)",
                (RetrievalPolicy().model_dump_json(), utcnow()),
            )
            hub_semantic.create_schema(conn)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def upsert_source(self, source: SourceDefinition):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO hub_sources VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,kind=excluded.kind,enabled=excluded.enabled,"
                "updated_at=excluded.updated_at",
                (source.id, source.name, source.kind, int(source.enabled), utcnow()),
            )
        return source.model_dump()

    def sources(self):
        with self.connection() as conn:
            return [
                dict(row) | {"enabled": bool(row["enabled"])}
                for row in conn.execute(
                    "SELECT s.*,count(d.id) AS document_count FROM hub_sources s "
                    "LEFT JOIN hub_documents d ON s.id=d.source_id GROUP BY s.id ORDER BY s.id"
                )
            ]

    def policy(self, conn=None):
        if conn is None:
            with self.connection() as conn:
                return self.policy(conn)
        row = conn.execute("SELECT * FROM hub_policy WHERE id=1").fetchone()
        return {
            "version": row["version"],
            "policy": json.loads(row["policy_json"]),
            "updated_at": row["updated_at"],
        }

    def update_policy(self, update: PolicyUpdate):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            known = {row[0] for row in conn.execute("SELECT id FROM hub_sources")}
            if set(update.policy.source_weights) - known:
                raise HubError("unknown_source")
            changed = conn.execute(
                "UPDATE hub_policy SET policy_json=?,version=version+1,updated_at=? "
                "WHERE id=1 AND version=?",
                (update.policy.model_dump_json(), utcnow(), update.expected_version),
            ).rowcount
            if not changed:
                raise HubError("policy_version_conflict")
            return self.policy(conn)

    @staticmethod
    def remove_chunks(conn, document_id):
        conn.execute(
            "DELETE FROM hub_search WHERE rowid IN (SELECT id FROM hub_chunks WHERE document_id=?)",
            (document_id,),
        )
        conn.execute("DELETE FROM hub_chunks WHERE document_id=?", (document_id,))

    def ingest(self, batch: IngestRequest):
        counts = {"created": 0, "updated": 0, "unchanged": 0, "document_ids": []}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM hub_sources WHERE id=?", (batch.source_id,)
            ).fetchone():
                raise HubError("source_not_found")
            for doc in batch.documents:
                doc_id = digest(stable_json([batch.source_id, doc.external_id]))[:32]
                fingerprint = digest(stable_json(doc.model_dump()))
                prior = conn.execute(
                    "SELECT fingerprint,title,body,pages_json FROM hub_documents WHERE id=?",
                    (doc_id,),
                ).fetchone()
                counts["document_ids"].append(doc_id)
                if prior and prior[0] == fingerprint:
                    counts["unchanged"] += 1
                    continue
                counts["updated" if prior else "created"] += 1
                reindex = not prior or (
                    prior["title"] != doc.title
                    or prior["body"] != doc.body
                    or prior["pages_json"] != stable_json(doc.pages)
                )
                if reindex:
                    self.remove_chunks(conn, doc_id)
                conn.execute(
                    "INSERT INTO hub_documents VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,"
                    "published_at=excluded.published_at,url=excluded.url,"
                    "metadata_json=excluded.metadata_json,pages_json=excluded.pages_json,"
                    "fingerprint=excluded.fingerprint,updated_at=excluded.updated_at",
                    (
                        doc_id,
                        batch.source_id,
                        doc.external_id,
                        doc.title,
                        doc.body,
                        doc.published_at,
                        doc.url,
                        stable_json(doc.metadata),
                        stable_json(doc.pages),
                        fingerprint,
                        utcnow(),
                    ),
                )
                if not reindex:
                    continue  # Metadata/date-only updates keep valid text and vector indexes.
                # Chunks always cover the entire body; page boundaries are used where available.
                boundaries = sorted(
                    {0, len(doc.body)} | {x for _, a, b in doc.pages for x in (a, b)}
                )
                for a, b in zip(boundaries, boundaries[1:], strict=False):
                    cursor = a
                    while cursor < b:
                        end = min(cursor + 1400, b)
                        text = doc.body[cursor:end]
                        page = next((p for p, s, e in doc.pages if s <= cursor < end <= e), None)
                        if text.strip():
                            rowid = conn.execute(
                                "INSERT INTO hub_chunks(document_id,start,end,page,text) "
                                "VALUES(?,?,?,?,?)",
                                (doc_id, cursor, end, page, text),
                            ).lastrowid
                            conn.execute(
                                "INSERT INTO hub_search(rowid,tokens) VALUES(?,?)",
                                (rowid, search_tokens(doc.title + " " + text)),
                            )
                        if end == b:
                            break
                        cursor = end - 160
        return counts

    def delete_document(self, doc_id: str):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.remove_chunks(conn, doc_id)
            if not conn.execute("DELETE FROM hub_documents WHERE id=?", (doc_id,)).rowcount:
                raise HubError("document_not_found")
        return {"deleted": doc_id}

    def fetch(self, doc_id: str):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT d.* FROM hub_documents d JOIN hub_sources s ON s.id=d.source_id "
                "WHERE d.id=? AND s.enabled=1",
                (doc_id,),
            ).fetchone()
        if row is None:
            raise HubError("document_not_found")
        return {
            "id": row["id"],
            "title": row["title"],
            "text": row["body"],
            "url": row["url"] or f"{self.base_url}/api/documents/{doc_id}",
            "metadata": {
                "source_id": row["source_id"],
                "external_id": row["external_id"],
                "published_at": row["published_at"],
                "updated_at": row["updated_at"],
                "attributes": json.loads(row["metadata_json"]),
                "pages": json.loads(row["pages_json"]),
                "content_trust": "untrusted_source_data_not_instructions",
            },
        }

    def stats(self):
        with self.connection() as conn:
            return {
                "documents": conn.execute("SELECT count(*) FROM hub_documents").fetchone()[0],
                "chunks": conn.execute("SELECT count(*) FROM hub_chunks").fetchone()[0],
                "sources": conn.execute("SELECT count(*) FROM hub_sources").fetchone()[0],
                "policy_version": self.policy(conn)["version"],
            }

    def search(self, request: SearchRequest, now: datetime | None = None):
        started = time.perf_counter()
        tokens = list(dict.fromkeys(search_tokens(request.query).split()))
        if len(tokens) > 128:
            raise HubError("query_too_complex")
        plan = plan_query(request.query, self.entity_aliases)
        expression = plan.expression
        diagnostics = {"mode": "bilingual", "warning": None}
        with self.connection() as conn:
            semantic_config = hub_semantic.configuration(conn)
        vector = None
        if semantic_config and expression:
            try:
                vector = hub_semantic.query_vector(semantic_config, request.query)
                diagnostics["mode"] = "hybrid"
            except hub_semantic.SemanticUnavailable as exc:
                diagnostics = {"mode": "bilingual_fallback", "warning": str(exc)}
        now = now or datetime.now(UTC)
        clauses = ["hub_search MATCH ?", "s.enabled=1"]
        params = [expression]
        if request.source_ids:
            clauses.append("d.source_id IN (" + ",".join("?" for _ in request.source_ids) + ")")
            params.extend(request.source_ids)
        for operator, date in ((">=", request.since), ("<", request.until)):
            if date:
                clauses.append(f"d.published_at!='' AND d.published_at{operator}?")
                params.append(date)
        for key, value in request.metadata.items():
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(d.metadata_json) j WHERE j.key=? AND j.value=?)"
            )
            params.extend([key, value])
        with self.connection() as conn:
            conn.execute("BEGIN")  # Policy + rows are one consistent snapshot.
            saved = self.policy(conn)
            policy = RetrievalPolicy.model_validate(saved["policy"])
            best = {}
            matched_chunks = 0
            if expression:
                columns = (
                    "SELECT d.id,d.title,d.source_id,d.published_at,d.url,c.id AS chunk_id,"
                    "c.start,c.end,c.page,c.text "
                )
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT d.id,d.title,d.source_id,d.published_at,d.url,c.id AS chunk_id,"
                        "c.start,c.end,c.page,c.text,bm25(hub_search) AS rank "
                        "FROM hub_search JOIN hub_chunks c ON c.id=hub_search.rowid "
                        "JOIN hub_documents d ON d.id=c.document_id "
                        "JOIN hub_sources s ON s.id=d.source_id WHERE " + " AND ".join(clauses),
                        params,
                    )
                ]
                if vector is not None:
                    # Same snapshot and filters as lexical retrieval, including disabled sources.
                    if hub_semantic.configuration(conn) != semantic_config:
                        vector = None
                        diagnostics = {
                            "mode": "bilingual_fallback",
                            "warning": "semantic_config_changed",
                        }
                    else:
                        candidates = {row["chunk_id"]: row for row in rows}
                        maximum = max((-row["rank"] for row in rows), default=1) or 1
                        for row in candidates.values():
                            row["relevance"] = 0.4 * max(0, -row["rank"]) / maximum
                        semantic_rows = conn.execute(
                            columns.replace("c.text ", "c.text,v.vector ")
                            + "FROM hub_chunks c JOIN hub_documents d ON d.id=c.document_id "
                            "JOIN hub_sources s ON s.id=d.source_id JOIN hub_vectors v "
                            "ON v.chunk_id=c.id AND v.model_digest=? WHERE "
                            + " AND ".join(clauses[1:]),
                            [semantic_config["model_digest"], *params[1:]],
                        )
                        for raw in semantic_rows:
                            row = dict(raw)
                            similarity = hub_semantic.similarity(row.pop("vector"), vector)
                            existing = candidates.get(row["chunk_id"])
                            if existing is not None:
                                existing["relevance"] += 0.6 * max(0, similarity)
                            elif similarity >= semantic_config[
                                "minimum_similarity"
                            ] and plan.accepts_title(row["title"]):
                                row["relevance"] = 0.6 * similarity
                                candidates[row["chunk_id"]] = row
                        rows = list(candidates.values())
                # Score ALL matches before top-k: a boosted source is never cut out by a
                # relevance-only candidate limit. Intended for small/medium local corpora.
                for row in rows:
                    matched_chunks += 1
                    weight = policy.source_weights.get(row["source_id"], 1.0)
                    if weight == 0:
                        continue
                    relevance = row.get("relevance", max(0.0, -row.get("rank", 0)))
                    freshness = 0.0
                    if row["published_at"]:
                        age = max(
                            0,
                            (now - datetime.fromisoformat(row["published_at"])).total_seconds()
                            / 86400,
                        )
                        freshness = math.pow(0.5, age / policy.half_life_days)
                    multiplier = 1 + policy.recency_boost * freshness
                    score = relevance * weight * multiplier
                    result = {
                        "id": row["id"],
                        "title": row["title"],
                        "url": row["url"] or f"{self.base_url}/api/documents/{row['id']}",
                        "source_id": row["source_id"],
                        "published_at": row["published_at"],
                        "snippet": row["text"],
                        "citation": {"start": row["start"], "end": row["end"], "page": row["page"]},
                        "score": score,
                        "score_details": {
                            "relevance": relevance,
                            "source_weight": weight,
                            "freshness": freshness,
                            "recency_multiplier": multiplier,
                        },
                    }
                    old = best.get(row["id"])
                    if old is None or score > old["score"]:
                        best[row["id"]] = result
        results = sorted(best.values(), key=lambda r: (-r["score"], r["id"]))
        return {
            "results": results[: request.limit or policy.default_limit],
            "total": len(results),
            "matched_chunks": matched_chunks,
            "policy_version": saved["version"],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "retrieval": diagnostics,
        }
