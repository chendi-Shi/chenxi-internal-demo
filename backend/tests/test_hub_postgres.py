"""Observable shared-adapter behavior with a fictional PostgreSQL snapshot."""
import json
import os
from datetime import UTC, datetime

import pytest

from research_agent.hub import Hub
from research_agent.hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SearchResponse,
    SourceDefinition,
)
from research_agent.retrieval_lab import Candidate


@pytest.fixture
def pg_hub(workspace, monkeypatch):
    from research_agent import pg_lab

    hub = Hub(workspace / "pg-hub", retrieval_backend="postgres", retrieval_stage="rrf")
    for source, date in (("a", "2026-08-01T00:00:00Z"), ("b", "2026-09-01T00:00:00Z")):
        hub.upsert_source(SourceDefinition(id=source, name=source))
        body = "Synthetic fixture. Chip orders grew and demand expanded."
        hub.ingest(IngestRequest(source_id=source, documents=[DocumentInput(
            external_id="one", title=f"Fictional {source}", body=body,
            published_at=date, metadata={"company": source}, pages=[(1, 0, len(body))])]))
    with hub.connection() as conn:
        imported = [dict(row) for row in conn.execute(
            "SELECT d.id,d.fingerprint,s.enabled FROM hub_documents d "
            "JOIN hub_sources s ON s.id=d.source_id")]
        candidates = [Candidate(chunk_id=row["id"], document_id=row["document_id"],
                                title="fixture", text=row["text"], score=1.0, page=row["page"])
                      for row in conn.execute("SELECT * FROM hub_chunks ORDER BY id")]

    class Rows:
        def fetchall(self):
            return imported

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql):
            return Rows()

    class Store:
        def __init__(self, conn):
            self.conn = conn
            self.snapshot = {"chunk_count": len(candidates), "semantic_config": None}

        def lexical(self, query, filters, limit):
            return candidates[:limit]

        def dense(self, query, filters, limit):
            return list(reversed(candidates))[:limit]

    monkeypatch.setattr(pg_lab, "connect", Connection)
    monkeypatch.setattr(pg_lab, "PostgresStore", Store)
    return hub


def test_postgres_results_preserve_policy_version_original_citation_and_filters(pg_hub):
    request = SearchRequest(query="chip", metadata={"company": "b"},
                            since="2026-09-01T08:00:00+08:00", limit=1)
    result = SearchResponse.model_validate(pg_hub.search(request))
    assert result.total == 1
    item = result.results[0]
    assert item.source_id == "b" and item.citation.page == 1
    original = pg_hub.fetch(item.id)["text"]
    assert item.snippet == original[item.citation.start:item.citation.end]
    assert "postgres_stage=rrf" in result.retrieval.warning
    assert pg_hub.search(SearchRequest(query="chip", source_ids=["a"],
                                    until="2026-08-01T00:00:00Z"))["total"] == 0
    pg_hub.update_policy(PolicyUpdate(expected_version=1,
        policy=RetrievalPolicy(source_weights={"a": 0, "b": 8}, recency_boost=0)))
    changed = pg_hub.search(SearchRequest(query="chip"))
    assert changed["policy_version"] == 2 and changed["total"] == 1
    assert changed["results"][0]["source_id"] == "b"
    row = changed["results"][0]
    assert row["score"] == pytest.approx(8 * row["score_details"]["relevance"])


def test_postgres_snapshot_disabled_source_falls_back_to_live_authority(pg_hub):
    pg_hub.upsert_source(SourceDefinition(id="a", name="a", enabled=False))
    result = pg_hub.search(SearchRequest(query="chip"))
    assert "postgres_snapshot_stale_using_sqlite" in result["retrieval"]["warning"]
    assert {r["source_id"] for r in result["results"]} == {"b"}


def test_postgres_driver_failure_falls_back_without_leaking_credentials(pg_hub, monkeypatch):
    from research_agent import pg_lab

    def unavailable():
        raise RuntimeError("postgres://account:private-password@server/db")

    monkeypatch.setattr(pg_lab, "connect", unavailable)
    result = pg_hub.search(SearchRequest(query="chip"))
    assert result["total"] == 2
    assert result["retrieval"] == {"mode": "bilingual_fallback",
                                    "warning": "postgres_unavailable_using_sqlite"}
    assert "private-password" not in str(result)


def test_negative_rerank_logits_have_positive_monotonic_policy_relevance(pg_hub, monkeypatch):
    adapter = pg_hub._postgres
    adapter.stage = "rerank"
    monkeypatch.setattr(adapter, "_providers", lambda: (lambda query, pool: [-5.0, -2.0], None))
    result = pg_hub.search(SearchRequest(query="chip"), now=datetime(2026, 9, 22, tzinfo=UTC))
    scores = [r["score_details"]["relevance"] for r in result["results"]]
    assert 0 < scores[1] < scores[0]
    assert "postgres_stage=rerank" in result["retrieval"]["warning"]


def test_missing_reranker_degrades_to_rrf_inside_postgres(pg_hub):
    pg_hub._postgres.stage = "rerank"
    pg_hub._postgres.reranker_dir = None
    result = pg_hub.search(SearchRequest(query="chip"))
    assert result["total"] == 2
    warning = result["retrieval"]["warning"]
    assert "postgres_stage=rerank" in warning and "rerank_failed_using_rrf" in warning
    assert "using_sqlite" not in warning


def test_cli_loads_only_runtime_settings_from_private_postgres_config(workspace, monkeypatch):
    from research_agent.hub_cli import configure_postgres

    for key in ("CHENXI_DATABASE_URL", "CHENXI_RETRIEVAL_BACKEND", "CHENXI_RETRIEVAL_STAGE",
                "CHENXI_PG_CANDIDATE_LIMIT", "CHENXI_PG_RERANKER_DIR"):
        monkeypatch.setenv(key, "temporary-test-value")
    config = workspace / "pg-experiment.local.json"
    url = "postgresql://fixture:private-test-secret@127.0.0.1:5433/fixture"
    config.write_text(json.dumps({"database_url": url, "stage": "rewrite", "candidate_limit": 100,
                                  "reranker_dir": "models/reranker", "password": "unused",
                                  "host": "unused-management-field"}), encoding="utf-8")
    assert configure_postgres(config) is None
    assert os.environ["CHENXI_DATABASE_URL"] == url
    assert os.environ["CHENXI_RETRIEVAL_BACKEND"] == "postgres"
    assert os.environ["CHENXI_RETRIEVAL_STAGE"] == "rewrite"
    assert os.environ["CHENXI_PG_CANDIDATE_LIMIT"] == "100"
    assert os.environ["CHENXI_PG_RERANKER_DIR"] == str((workspace / "models/reranker").resolve())


@pytest.mark.asyncio
async def test_postgres_http_and_mcp_share_ranking_and_fetch(pg_hub, monkeypatch):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from research_agent.hub_api import create_app
    from research_agent.hub_mcp import create_mcp

    original_search = pg_hub._postgres.search
    monkeypatch.setattr(pg_hub._postgres, "search", lambda hub, request, now=None:
                        original_search(hub, request, datetime(2026, 9, 22, tzinfo=UTC)))
    pg_hub.update_policy(PolicyUpdate(expected_version=1,
        policy=RetrievalPolicy(source_weights={"a": .1, "b": 8}, recency_boost=0)))
    app = create_app(pg_hub, "r" * 40, "a" * 40)
    with TestClient(app) as client:
        http = client.post("/api/search", json={"query": "chip"},
                           headers={"Authorization": "Bearer " + "r" * 40}).json()
    mcp = create_mcp(pg_hub)
    _, advanced = await mcp.call_tool("search_documents", {"request": {"query": "chip"}})
    assert http["results"] == advanced["results"] and advanced["policy_version"] == 2
    _, compact = await mcp.call_tool("search", {"query": "chip"})
    assert [r["id"] for r in compact["results"]] == [r["id"] for r in http["results"]]
    _, fetched = await mcp.call_tool("fetch", {"id": compact["results"][0]["id"]})
    assert fetched["text"] == pg_hub.fetch(compact["results"][0]["id"])["text"]
