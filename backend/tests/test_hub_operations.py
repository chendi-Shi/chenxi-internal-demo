import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_agent.hub import Hub, HubError
from research_agent.hub_cli import main
from research_agent.hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SourceDefinition,
)
from research_agent.hub_runtime import init_local, load_credentials


def test_init_preserves_keys_and_redacts_output(workspace, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_HUB_READ_TOKEN", raising=False)
    monkeypatch.delenv("RESEARCH_HUB_ADMIN_TOKEN", raising=False)
    directory = workspace / "runtime"
    assert main(["--data-dir", str(directory), "init"]) == 0
    first = capsys.readouterr()
    path = directory / "access.local.json"
    raw = path.read_bytes()
    creds = load_credentials(path)
    assert creds.read_token != creds.admin_token
    assert creds.read_token not in first.out
    assert creds.admin_token not in first.out
    assert not init_local(directory, path, "http://127.0.0.1:8765")["credentials_created"]
    assert path.read_bytes() == raw
    client = json.loads((directory / "mcp-client.local.json").read_text(encoding="utf-8"))
    assert "internal-research" in client["mcpServers"]
    assert creds.admin_token not in json.dumps(client)
    monkeypatch.setenv("RESEARCH_HUB_READ_TOKEN", "changed-" + "x" * 40)
    assert load_credentials(path).read_token.startswith("changed-")
    with pytest.raises(ValidationError):
        monkeypatch.setenv("RESEARCH_HUB_READ_TOKEN", "unsafe\nheader")
        load_credentials(path)


def test_normalized_cli_import_and_errors(workspace, capsys):
    common = ["--data-dir", str(workspace / "hub")]
    assert (
        main(common + ["source", "--id", "internal_demo", "--name", "Demo", "--kind", "demo"]) == 0
    )
    capsys.readouterr()
    sample = Path("samples/hub_ingest.json")
    assert main(common + ["ingest-json", str(sample)]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == 1
    assert main(common + ["ingest-json", str(sample)]) == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] == 1
    assert main(common + ["search", "芯片"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    bad = workspace / "bad.json"
    bad.write_text('{"documents":[{"body":"private-should-not-echo"}]}', encoding="utf-8")
    assert main(common + ["ingest-json", str(bad)]) == 2
    error = capsys.readouterr().err
    assert "private-should-not-echo" not in error and "validation_error" in error
    assert main(common + ["ingest-json", str(workspace / "missing.json")]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_import_does_not_reenable_disabled_source(workspace, capsys):
    hub = Hub(workspace / "hub")
    hub.upsert_source(SourceDefinition(id="private", name="Private", enabled=False))
    path = workspace / "note.txt"
    path.write_text("芯片数据", encoding="utf-8")
    assert (
        main(
            [
                "--data-dir",
                str(hub.db.parent),
                "ingest",
                str(path),
                "--source-id",
                "private",
                "--source-name",
                "Private",
            ]
        )
        == 0
    )
    assert hub.sources()[0]["enabled"] is False
    assert hub.search(SearchRequest(query="芯片"))["total"] == 0


def test_batch_transaction_rolls_back_failed_index(workspace, monkeypatch):
    import research_agent.hub as module

    hub = Hub(workspace)
    hub.upsert_source(SourceDefinition(id="a", name="A"))
    batch = IngestRequest(
        source_id="a", documents=[DocumentInput(external_id="one", title="a", body="old")]
    )
    doc_id = hub.ingest(batch)["document_ids"][0]
    original = module.search_tokens

    def fail_on_second(text):
        if "fail-marker" in text:
            raise RuntimeError("simulated_index_failure")
        return original(text)

    monkeypatch.setattr(module, "search_tokens", fail_on_second)
    update = IngestRequest(
        source_id="a",
        documents=[
            DocumentInput(external_id="one", title="a", body="updated"),
            DocumentInput(external_id="two", title="b", body="fail-marker"),
        ],
    )
    with pytest.raises(RuntimeError):
        hub.ingest(update)
    assert hub.fetch(doc_id)["text"] == "old"
    assert hub.stats()["documents"] == 1
    assert hub.search(SearchRequest(query="old"))["total"] == 1
    assert hub.search(SearchRequest(query="updated"))["total"] == 0


def test_concurrent_policy_updates_do_not_overwrite(workspace):
    hub = Hub(workspace)

    def update(boost):
        try:
            return hub.update_policy(
                PolicyUpdate(expected_version=1, policy=RetrievalPolicy(recency_boost=boost))
            )["version"]
        except HubError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, [1, 2]))
    assert outcomes.count(2) == 1
    assert outcomes.count("policy_version_conflict") == 1


def test_identical_metadata_different_key_order_is_unchanged(workspace):
    hub = Hub(workspace)
    hub.upsert_source(SourceDefinition(id="a", name="a"))
    doc = DocumentInput(external_id="a", title="a", body="text", metadata={"a": "1", "b": "2"})
    hub.ingest(IngestRequest(source_id="a", documents=[doc]))
    doc.metadata = {"b": "2", "a": "1"}
    assert hub.ingest(IngestRequest(source_id="a", documents=[doc]))["unchanged"] == 1


def test_http_size_limit_storage_error_and_openapi_contract(workspace, monkeypatch):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from research_agent.hub_api import create_app

    hub = Hub(workspace)
    app = create_app(hub, "r" * 40, "a" * 40)
    headers = {"Authorization": "Bearer " + "a" * 40}
    with TestClient(app) as client:
        too_large = client.post("/api/ingest/documents", content=b"x" * 12_000_001, headers=headers)
        assert too_large.status_code == 413
        assert too_large.json()["error"]["code"] == "request_too_large"
        assert too_large.headers["cache-control"] == "no-store"

        def fail():
            raise sqlite3.OperationalError("secret-file-name")

        monkeypatch.setattr(hub, "stats", fail)
        failed = client.get("/api/status", headers=headers)
        assert failed.status_code == 503
        assert "secret-file-name" not in failed.text
    contract = json.loads(Path("docs/contracts/openapi.json").read_text(encoding="utf-8"))
    assert app.openapi() == contract
