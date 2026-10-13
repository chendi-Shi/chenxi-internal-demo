"""Policy exclusions must precede both bounded PostgreSQL retrieval branches."""
import pytest

from research_agent.hub import Hub
from research_agent.hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SourceDefinition,
)
from research_agent.retrieval_lab import Candidate


def crowded_snapshot(workspace, monkeypatch):
    from research_agent import pg_lab

    hub = Hub(workspace / "crowded-policy", retrieval_backend="postgres", retrieval_stage="rrf")
    for sid in ("blocked", "keep"):
        hub.upsert_source(SourceDefinition(id=sid, name=sid))
    # Both retrieval branches have > candidate_limit high-scoring zero-weight
    # documents ahead of the only lower-scoring allowed-source document.
    docs = [DocumentInput(external_id=str(index), title="Synthetic chip review",
                          body="Synthetic fixture. Chip demand expanded.") for index in range(101)]
    hub.ingest(IngestRequest(source_id="blocked", documents=docs[:100]))
    hub.ingest(IngestRequest(source_id="blocked", documents=docs[100:]))
    kept = hub.ingest(IngestRequest(source_id="keep", documents=[DocumentInput(
        external_id="allowed", title="Synthetic chip review",
        body="Synthetic fixture. Chip demand expanded.")]))["document_ids"][0]
    with hub.connection() as conn:
        imported = [dict(row) for row in conn.execute(
            "SELECT d.id,d.fingerprint,s.enabled FROM hub_documents d "
            "JOIN hub_sources s ON s.id=d.source_id")]
        rows = [dict(row) for row in conn.execute(
            "SELECT c.*,d.source_id FROM hub_chunks c JOIN hub_documents d ON d.id=c.document_id")]
    candidates = [(row["source_id"], Candidate(
        chunk_id=row["id"], document_id=row["document_id"], title="fixture",
        text=row["text"], score=1000.0 if row["source_id"] == "blocked" else 1.0)) for row in rows]
    candidates.sort(key=lambda item: (-item[1].score, item[1].chunk_id))
    calls = []

    class Result:
        def fetchall(self):
            return imported

    class Connection:
        def __enter__(self):
            calls.append(("connect", None))
            return self

        def __exit__(self, *_):
            pass

        def execute(self, _):
            return Result()

    class Store:
        def __init__(self, conn):
            self.conn = conn
            self.snapshot = {"chunk_count": len(candidates), "semantic_config": None, "vector_count": 0}

        def branch(self, name, filters, limit):
            calls.append((name, filters["source_ids"]))
            wanted = set(filters["source_ids"])
            return [item for source, item in candidates if not wanted or source in wanted][:limit]

        def lexical(self, query, filters, limit):
            return self.branch("lexical", filters, limit)

        def dense(self, query, filters, limit):
            return self.branch("dense", filters, limit)

    monkeypatch.setattr(pg_lab, "connect", Connection)
    monkeypatch.setattr(pg_lab, "PostgresStore", Store)
    hub.update_policy(PolicyUpdate(expected_version=1, policy=RetrievalPolicy(
        source_weights={"blocked": 0, "keep": 1}, recency_boost=0)))
    return hub, kept, calls


def test_zero_weight_sources_are_excluded_before_both_branch_candidate_limits(workspace, monkeypatch):
    hub, kept, calls = crowded_snapshot(workspace, monkeypatch)
    result = hub.search(SearchRequest(query="chip"))
    assert [hit["id"] for hit in result["results"]] == [kept]
    assert result["policy_version"] == 2
    assert ("lexical", ["keep"]) in calls and ("dense", ["keep"]) in calls
    assert "postgres_stage=rrf" in result["retrieval"]["warning"]
    assert "using_sqlite" not in result["retrieval"]["warning"]


@pytest.mark.parametrize("all_zero", [False, True])
def test_no_eligible_sources_returns_empty_without_issuing_unrestricted_queries(workspace, monkeypatch, all_zero):
    hub, _, calls = crowded_snapshot(workspace, monkeypatch)
    if all_zero:
        hub.update_policy(PolicyUpdate(expected_version=2, policy=RetrievalPolicy(
            source_weights={"blocked": 0, "keep": 0}, recency_boost=0)))
    request = SearchRequest(query="chip", source_ids=[] if all_zero else ["blocked"])
    result = hub.search(request)
    assert result["results"] == [] and result["total"] == result["matched_chunks"] == 0
    assert result["policy_version"] == (3 if all_zero else 2)
    assert calls == []
    assert "no_eligible_sources" in result["retrieval"]["warning"]
    assert "using_sqlite" not in result["retrieval"]["warning"]
