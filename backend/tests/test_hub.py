import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from research_agent.hub import Hub, HubError
from research_agent.hub_adapters import LocalFilesAdapter, sync_adapter
from research_agent.hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SourceDefinition,
)


@pytest.fixture
def hub(workspace):
    service = Hub(workspace / "hub")
    for sid in ("a", "b"):
        service.upsert_source(SourceDefinition(id=sid, name=sid))
        service.ingest(
            IngestRequest(
                source_id=sid,
                documents=[
                    DocumentInput(
                        external_id="one",
                        title="芯片订单",
                        body="芯片订单增长。完全相同的测试文本。",
                        published_at="2026-09-01T00:00:00Z",
                        metadata={"company": sid},
                    )
                ],
            )
        )
    return service


def test_policy_changes_ranking_and_survives_restart(hub):
    request = SearchRequest(query="芯片", limit=1)
    old = hub.search(request)
    first = old["results"][0]["source_id"]
    second = "b" if first == "a" else "a"
    hub.update_policy(
        PolicyUpdate(
            expected_version=1,
            policy=RetrievalPolicy(source_weights={first: 0.5, second: 8}, recency_boost=0),
        )
    )
    new = Hub(hub.db.parent).search(request)
    assert new["results"][0]["source_id"] == second
    assert new["policy_version"] == 2
    assert new["total"] == 2
    row = new["results"][0]
    assert row["score"] == pytest.approx(row["score_details"]["relevance"] * 8)
    with pytest.raises(HubError, match="conflict"):
        hub.update_policy(PolicyUpdate(expected_version=1, policy=RetrievalPolicy()))


def test_filters_dates_and_literal_queries(hub):
    assert hub.search(SearchRequest(query="芯片", metadata={"company": "a"}))["total"] == 1
    assert hub.search(SearchRequest(query="芯片", source_ids=["b"]))["total"] == 1
    assert hub.search(SearchRequest(query="芯片", until="2026-09-01T00:00:00Z"))["total"] == 0
    assert hub.search(SearchRequest(query="芯片", since="2026-09-01T08:00:00+08:00"))["total"] == 2
    assert hub.search(SearchRequest(query='" OR * --'))["total"] == 0
    assert hub.search(SearchRequest(query="!!!"))["total"] == 0
    assert hub.search(SearchRequest(query="完全不匹配"))["total"] == 0
    with pytest.raises(ValidationError):
        SearchRequest(query="x", since="2026-09-01")


def test_incremental_update_removes_old_index_and_keeps_id(hub):
    old = hub.search(SearchRequest(query="芯片", source_ids=["a"]))["results"][0]
    batch = IngestRequest(
        source_id="a", documents=[DocumentInput(external_id="one", title="new", body="消费复苏")]
    )
    assert hub.ingest(batch)["updated"] == 1
    assert hub.ingest(batch)["unchanged"] == 1
    assert hub.search(SearchRequest(query="芯片", source_ids=["a"]))["total"] == 0
    assert hub.search(SearchRequest(query="消费"))["results"][0]["id"] == old["id"]
    assert hub.fetch(old["id"])["text"] == "消费复苏"
    hub.delete_document(old["id"])
    assert hub.search(SearchRequest(query="消费"))["total"] == 0
    with pytest.raises(HubError, match="not_found"):
        hub.fetch(old["id"])


def test_chunk_offsets_pages_and_disabled_source(hub):
    body = "首页内容\n\n" + "芯片订单。" * 400
    doc_id = hub.ingest(
        IngestRequest(
            source_id="a",
            documents=[
                DocumentInput(
                    external_id="long",
                    title="long",
                    body=body,
                    pages=[(1, 0, 4), (2, 6, len(body))],
                )
            ],
        )
    )["document_ids"][0]
    rows = hub.search(SearchRequest(query="芯片", source_ids=["a"]))["results"]
    row = next(r for r in rows if r["id"] == doc_id)
    assert row["citation"]["page"] == 2
    assert row["snippet"] == body[row["citation"]["start"] : row["citation"]["end"]]
    hub.upsert_source(SourceDefinition(id="a", name="a", enabled=False))
    assert hub.search(SearchRequest(query="芯片", source_ids=["a"]))["total"] == 0
    with pytest.raises(HubError):
        hub.fetch(doc_id)


def test_recency_and_unknown_dates(hub):
    for external_id, published_at in (("new", "2026-09-20T00:00:00Z"), ("unknown", "")):
        hub.ingest(
            IngestRequest(
                source_id="a",
                documents=[
                    DocumentInput(
                        external_id=external_id,
                        title="新材料",
                        body="新材料项目",
                        published_at=published_at,
                    )
                ],
            )
        )
    rows = hub.search(SearchRequest(query="新材料"), now=datetime(2026, 9, 22, tzinfo=UTC))[
        "results"
    ]
    assert rows[0]["published_at"] != ""
    assert rows[1]["score_details"]["freshness"] == 0
    assert rows[1]["score_details"]["recency_multiplier"] == 1


def test_adapter_boundary_and_validation(hub, workspace):
    path = workspace / "input.json"
    path.write_text(json.dumps([{"title": "测试", "body": "新文件内容"}]), encoding="utf-8")
    assert sync_adapter(hub, "a", LocalFilesAdapter(path))["created"] == 1
    assert sync_adapter(hub, "a", LocalFilesAdapter(path))["unchanged"] == 1
    with pytest.raises(ValidationError):
        RetrievalPolicy(source_weights={"a": float("nan")})
    with pytest.raises(ValidationError):
        DocumentInput(external_id="x", title="t", body="b", url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        DocumentInput(external_id="x", title="t", body="b", pages=[(1, 0, 10)])
    with pytest.raises(HubError, match="unknown_source"):
        hub.update_policy(
            PolicyUpdate(expected_version=1, policy=RetrievalPolicy(source_weights={"none": 1}))
        )


def test_http_auth_contract_cors_and_errors(hub):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from research_agent.hub_api import create_app

    read = {"Authorization": "Bearer " + "r" * 40}
    admin = {"Authorization": "Bearer " + "a" * 40}
    app = create_app(hub, "r" * 40, "a" * 40, ["http://localhost:5173"])
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/sources").status_code == 401
        assert client.post("/mcp", json={}).status_code == 401
        assert client.get("/api/sources", headers=read).status_code == 200
        body = {"expected_version": 1, "policy": {"source_weights": {"b": 10}}}
        assert client.put("/api/policy", json=body, headers=read).status_code == 403
        assert client.put("/api/policy", json=body, headers=admin).json()["version"] == 2
        assert client.put("/api/policy", json=body, headers=admin).status_code == 409
        response = client.post("/api/search", json={"query": "芯片"}, headers=read)
        assert response.json()["results"][0]["source_id"] == "b"
        assert response.headers["cache-control"] == "no-store"
        bad = client.post("/api/ingest/documents", json={"private": "do-not-echo"}, headers=admin)
        assert bad.status_code == 422 and "do-not-echo" not in bad.text
        assert client.get("/api/documents/missing", headers=read).status_code == 404
        preflight = client.options(
            "/api/search",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/search"]["post"]["security"]


def test_public_demo_serves_dashboard_without_headers_and_limits_writes(hub, workspace):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from research_agent.hub_api import create_app

    dashboard = workspace / "dashboard"
    (dashboard / "assets").mkdir(parents=True)
    (dashboard / "index.html").write_text("demo dashboard", encoding="utf-8")
    (dashboard / "assets" / "app.js").write_text("demo asset", encoding="utf-8")
    app = create_app(
        hub,
        "r" * 40,
        "a" * 40,
        public_demo=True,
        dashboard_dir=dashboard,
        root_path="/studios/test/demo",
    )

    with TestClient(app, base_url="http://localhost") as client:
        prefix = "/studios/test/demo"
        assert client.get(prefix + "/").text == "demo dashboard"
        assert client.get(prefix + "/assets/app.js").text == "demo asset"
        assert client.get(prefix + "/api/status").json()["documents"] == 2
        assert client.post(prefix + "/api/search", json={"query": "芯片"}).status_code == 200

        policy_update = {
            "expected_version": 1,
            "policy": {"source_weights": {"a": 0.1, "b": 10}, "recency_boost": 0},
        }
        assert client.put(prefix + "/api/policy", json=policy_update).json()["version"] == 2
        results = client.post(prefix + "/api/search", json={"query": "芯片"}).json()
        assert results["policy_version"] == 2
        assert results["results"][0]["source_id"] == "b"

        # The demo can change only its synthetic retrieval policy.
        assert client.post(prefix + "/api/sources", json={"id": "x", "name": "x"}).status_code == 403
        assert client.post(
            prefix + "/api/ingest/documents", json={"source_id": "a", "documents": []}
        ).status_code == 403
        assert client.delete(prefix + "/api/documents/unknown").status_code == 403
        assert client.post(prefix + "/mcp", json={}).status_code != 401


@pytest.mark.asyncio
async def test_mcp_tools_are_read_only_and_share_policy(hub):
    pytest.importorskip("mcp")
    from research_agent.hub_mcp import create_mcp

    server = create_mcp(hub)
    tools = await server.list_tools()
    assert {t.name for t in tools} == {"search", "fetch", "search_documents"}
    assert all(t.annotations.readOnlyHint and t.outputSchema for t in tools)
    content, structured = await server.call_tool("search", {"query": "芯片"})
    assert json.loads(content[0].text) == structured
    doc_id = structured["results"][0]["id"]
    _, fetched = await server.call_tool("fetch", {"id": doc_id})
    assert fetched["text"] == hub.fetch(doc_id)["text"]
    hub.update_policy(
        PolicyUpdate(expected_version=1, policy=RetrievalPolicy(source_weights={"a": 0}))
    )
    _, advanced = await server.call_tool("search_documents", {"request": {"query": "芯片"}})
    assert advanced["policy_version"] == 2
    assert advanced["results"][0]["source_id"] == "b"
