"""Opt-in PostgreSQL retrieval for the shared HTTP/MCP Hub.

PostgreSQL owns candidate retrieval in this adapter. The existing local Hub remains
the authority for source controls, policy versions, original passages and fetch.
An outdated snapshot or a failed provider falls back to the local retrieval path.
No exception text from database drivers is exposed in the public diagnostics.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from . import hub_semantic
from .hub_models import RetrievalPolicy
from .retrieval_lab import STAGES, run_pipeline


class PostgresRetrievalUnavailable(ValueError):
    """Public, credential-free reason for falling back to the local Hub."""


class PostgresRetrieval:
    def __init__(self, *, stage: str | None = None, candidate_limit: int | None = None,
                 reranker_dir: Path | None = None):
        self.stage = stage or os.environ.get("CHENXI_RETRIEVAL_STAGE", "rrf")
        self.candidate_limit = candidate_limit or int(
            os.environ.get("CHENXI_PG_CANDIDATE_LIMIT", "100"))
        configured = os.environ.get("CHENXI_PG_RERANKER_DIR")
        self.reranker_dir = reranker_dir or (Path(configured) if configured else None)
        if self.stage not in STAGES or not 50 <= self.candidate_limit <= 1000:
            raise ValueError("invalid_postgres_retrieval_config")
        self._reranker = None
        self._rewriter = None

    def _providers(self):
        # Model initialization is inside the provider call so run_pipeline's
        # rerank/rewrite fallbacks also cover a missing or damaged local model.
        return (self._run_reranker if self.stage in {"rerank", "rewrite"} else None,
                self._run_rewriter if self.stage == "rewrite" else None)

    def _run_reranker(self, query, rows):
        if self._reranker is None:
            from .retrieval_providers import OnnxReranker

            if self.reranker_dir is None:
                raise ValueError("postgres_reranker_not_configured")
            self._reranker = OnnxReranker(self.reranker_dir)
        return self._reranker(query, rows)

    def _run_rewriter(self, query):
        if self._rewriter is None:
            from .retrieval_providers import RuleQueryRewriter

            self._rewriter = RuleQueryRewriter()
        return self._rewriter(query)

    @staticmethod
    def _validate_snapshot(sqlite_conn, store):
        current = {
            row["id"]: (row["fingerprint"], bool(row["enabled"]))
            for row in sqlite_conn.execute(
                "SELECT d.id,d.fingerprint,s.enabled FROM hub_documents d "
                "JOIN hub_sources s ON s.id=d.source_id")
        }
        imported = {
            row["id"]: (row["fingerprint"], bool(row["enabled"]))
            for row in store.conn.execute(
                "SELECT id,fingerprint,enabled FROM retrieval_lab.documents").fetchall()
        }
        chunks = sqlite_conn.execute("SELECT count(*) FROM hub_chunks").fetchone()[0]
        if current != imported or chunks != store.snapshot["chunk_count"]:
            raise PostgresRetrievalUnavailable("postgres_snapshot_stale")
        config = hub_semantic.configuration(sqlite_conn)
        if store.snapshot["semantic_config"] != config:
            raise PostgresRetrievalUnavailable("postgres_semantic_snapshot_stale")
        if config:
            vectors = sqlite_conn.execute("SELECT count(*) FROM hub_vectors WHERE model_digest=?",
                                          (config["model_digest"],)).fetchone()[0]
            if vectors != store.snapshot["vector_count"]:
                raise PostgresRetrievalUnavailable("postgres_semantic_snapshot_stale")

    @staticmethod
    def _eligible(row, request):
        if not row["enabled"]:
            return False
        if request.source_ids and row["source_id"] not in request.source_ids:
            return False
        date = row["published_at"]
        if request.since and (not date or date < request.since):
            return False
        if request.until and (not date or date >= request.until):
            return False
        attributes = json.loads(row["metadata_json"])
        return all(attributes.get(k) == v for k, v in request.metadata.items())

    def search(self, hub, request, now: datetime | None = None):
        # Importing psycopg is deferred: the unchanged default SQLite installation
        # does not require the optional database/model dependencies.
        from .pg_lab import PostgresStore, connect

        started = time.perf_counter()
        now = now or datetime.now(UTC)
        rerank, rewrite = self._providers()
        filters = {k: getattr(request, k) for k in ("source_ids", "since", "until", "metadata")}
        with hub.connection() as sqlite_conn:
            sqlite_conn.execute("BEGIN")
            saved = hub.policy(sqlite_conn)
            policy = RetrievalPolicy.model_validate(saved["policy"])
            # Remove zero-weight sources before either retrieval branch applies
            # LIMIT. Excluding them only after fusion can starve allowed sources.
            eligible = [row["id"] for row in sqlite_conn.execute(
                "SELECT id FROM hub_sources WHERE enabled=1 ORDER BY id")
                if policy.source_weights.get(row["id"], 1.0) > 0
                and (not request.source_ids or row["id"] in request.source_ids)]
            if not eligible:
                # PostgresStore treats [] as no restriction, so an empty
                # eligibility set must return immediately rather than become [].
                return {"results": [], "total": 0, "matched_chunks": 0,
                        "policy_version": saved["version"],
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "retrieval": {"mode": "bilingual" if self.stage == "lexical" else "hybrid",
                                      "warning": f"postgres_stage={self.stage};no_eligible_sources"}}
            filters["source_ids"] = eligible
            with connect() as pg_conn:
                pg_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                # Wide full-text questions need more than five seconds on the local
                # CPU baseline. Keep a finite SQL budget and explicit Hub fallback.
                pg_conn.execute("SET LOCAL statement_timeout = '30000ms'")
                store = PostgresStore(pg_conn)
                self._validate_snapshot(sqlite_conn, store)
                output = run_pipeline(
                    store, request.query, stage=self.stage, filters=filters,
                    limit=self.candidate_limit, candidate_limit=self.candidate_limit,
                    rerank=rerank, rewrite=rewrite)
            candidates = output["results"]
            ids = [row.chunk_id for row in candidates]
            originals = {}
            # A batch below the conservative SQLite variable limit.
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                placeholders = ",".join("?" for _ in batch)
                originals.update({row["chunk_id"]: row for row in sqlite_conn.execute(
                    "SELECT d.id,d.title,d.source_id,d.published_at,d.url,d.metadata_json,"
                    "s.enabled,c.id AS chunk_id,c.start,c.end,c.page,c.text "
                    "FROM hub_chunks c JOIN hub_documents d ON d.id=c.document_id "
                    "JOIN hub_sources s ON s.id=d.source_id WHERE c.id IN ("
                    + placeholders + ")", batch)})
            results = []
            reranked = self.stage in {"rerank", "rewrite"} and not any(
                warning.startswith("rerank_failed") for warning in output["warnings"])
            for candidate in candidates:
                row = originals.get(candidate.chunk_id)
                if row is None or row["id"] != candidate.document_id or row["text"] != candidate.text:
                    raise PostgresRetrievalUnavailable("postgres_passage_snapshot_stale")
                if not self._eligible(row, request):
                    continue
                weight = policy.source_weights.get(row["source_id"], 1.0)
                if weight == 0:
                    continue
                value = float(candidate.score)
                if not math.isfinite(value):
                    raise PostgresRetrievalUnavailable("postgres_invalid_score")
                # CrossEncoder logits are ranking signals, not confidence. Softplus
                # is monotonic and positive, so source/recency multipliers preserve
                # ordering without turning all negative logits into identical zeros.
                relevance = (max(value, 0) + math.log1p(math.exp(-abs(value)))) if reranked else max(0.0, value)
                freshness = 0.0
                if row["published_at"]:
                    age = max(0, (now - datetime.fromisoformat(row["published_at"])).total_seconds() / 86400)
                    freshness = math.pow(0.5, age / policy.half_life_days)
                multiplier = 1 + policy.recency_boost * freshness
                results.append({
                    "id": row["id"], "title": row["title"],
                    "url": row["url"] or f"{hub.base_url}/api/documents/{row['id']}",
                    "source_id": row["source_id"], "published_at": row["published_at"],
                    "snippet": row["text"],
                    "citation": {"start": row["start"], "end": row["end"], "page": row["page"]},
                    "score": relevance * weight * multiplier,
                    "score_details": {"relevance": relevance, "source_weight": weight,
                                      "freshness": freshness, "recency_multiplier": multiplier},
                })
        results.sort(key=lambda row: (-row["score"], row["id"]))
        warnings = [f"postgres_stage={self.stage}",
                    f"candidate_limit={self.candidate_limit};totals_are_candidate_pool_counts",
                    *output["warnings"]]
        if self.stage != "lexical" and store.snapshot.get("vector_count", 0) < store.snapshot["chunk_count"]:
            warnings.append(f"semantic_coverage={store.snapshot.get('vector_count', 0)}/{store.snapshot['chunk_count']}")
        mode = "bilingual" if self.stage == "lexical" else "hybrid"
        if "dense_failed_using_lexical" in output["warnings"]:
            mode = "bilingual_fallback"
        return {"results": results[:request.limit or policy.default_limit],
                "total": len(results), "matched_chunks": output.get("matched_chunks", len(candidates)),
                "policy_version": saved["version"],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "retrieval": {"mode": mode, "warning": ";".join(warnings)}}
