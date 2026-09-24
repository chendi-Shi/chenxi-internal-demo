"""Real SDK sessions over both supported transports; no model or external network."""

import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp")
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from research_agent.hub import Hub
from research_agent.hub_api import create_app
from research_agent.hub_cli import seed_demo

READ = "read-test-" + "r" * 40
ADMIN = "admin-test-" + "a" * 40


@contextmanager
def live_server(hub):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    hub.base_url = f"http://127.0.0.1:{port}"
    app = create_app(hub, READ, ADMIN)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "HTTP service did not start"
        yield hub.base_url
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "HTTP service did not shut down"


@pytest.mark.asyncio
async def test_http_sdk_session_and_dashboard_policy_change(workspace):
    hub = Hub(workspace / "http-hub")
    seed_demo(hub)
    with live_server(hub) as url:
        async with httpx.AsyncClient(
            base_url=url, headers={"Authorization": f"Bearer {READ}"}
        ) as web:
            async with streamable_http_client(url + "/mcp", http_client=web) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Internal Research Data"
                    tools = (await session.list_tools()).tools
                    assert {t.name for t in tools} == {"search", "fetch", "search_documents"}
                    first = await session.call_tool("search", {"query": "芯片"})
                    assert not first.isError
                    assert json.loads(first.content[0].text) == first.structuredContent
                    assert len(first.structuredContent["results"]) == 2
                    doc_id = first.structuredContent["results"][0]["id"]
                    document = await session.call_tool("fetch", {"id": doc_id})
                    assert document.structuredContent["text"] == hub.fetch(doc_id)["text"]
                    bad = await session.call_tool("fetch", {"id": "missing"})
                    assert bad.isError
                    policy = (await web.get("/api/policy")).json()
                    response = await web.put(
                        "/api/policy",
                        json={
                            "expected_version": policy["version"],
                            "policy": {"source_weights": {"demo_filings": 9, "demo_research": 0.1}},
                        },
                        headers={"Authorization": f"Bearer {ADMIN}"},
                    )
                    assert response.status_code == 200
                    query = {"query": "芯片", "limit": 2}
                    dashboard = (await web.post("/api/search", json=query)).json()
                    result = await session.call_tool("search_documents", {"request": query})
                    assert not result.isError
                    mcp = result.structuredContent
                    assert mcp["policy_version"] == dashboard["policy_version"] == 2
                    assert [r["id"] for r in mcp["results"]] == [
                        r["id"] for r in dashboard["results"]
                    ]
                    assert mcp["results"][0]["source_id"] == "demo_filings"
                    denied = await web.delete(f"/api/documents/{doc_id}")
                    assert denied.status_code == 403
                    deleted = await web.delete(
                        f"/api/documents/{doc_id}", headers={"Authorization": f"Bearer {ADMIN}"}
                    )
                    assert deleted.status_code == 200
                    assert (await session.call_tool("fetch", {"id": doc_id})).isError


@pytest.mark.asyncio
async def test_real_stdio_process_is_protocol_clean(workspace):
    hub = Hub(workspace / "stdio-hub")
    seed_demo(hub)
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_agent.hub_cli", "--data-dir", str(hub.db.parent.resolve()), "stdio"],
        cwd=str(root),
        env={"PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search", {"query": "芯片"})
            assert not result.isError
            assert len(result.structuredContent["results"]) == 2
            doc_id = result.structuredContent["results"][0]["id"]
            fetched = await session.call_tool("fetch", {"id": doc_id})
            assert not fetched.isError
            assert (
                fetched.structuredContent["metadata"]["content_trust"]
                == "untrusted_source_data_not_instructions"
            )
