import json
import time

import pytest
from fastapi.testclient import TestClient

from research_agent.hub import Hub, HubError
from research_agent.hub_api import create_app
from research_agent.hub_models import SearchRequest
from research_agent.hub_sync import (
    FileBinding,
    SyncConfig,
    Synchronizer,
    SyncRoot,
    load_config,
    sync_status,
    worker_lock,
)


def setup(workspace, **kwargs):
    inbox = workspace / "inbox"
    inbox.mkdir()
    hub = Hub(workspace / "hub")
    config = SyncConfig(
        roots=[SyncRoot(path=inbox, source_id="research", source_name="Research")],
        settle_seconds=0,
        semantic=False,
        retry_seconds=10,
        max_attempts=2,
        **kwargs,
    )
    return inbox, hub, config


def test_incremental_restart_update_and_missing_retention(workspace, monkeypatch):
    inbox, hub, config = setup(workspace)
    file = inbox / "memo.txt"
    file.write_text("Revenue grew strongly this quarter", encoding="utf-8")
    first = Synchronizer(hub, config).cycle()
    assert first["created"] == 1
    hit = hub.search(SearchRequest(query="revenue"))["results"][0]
    import research_agent.hub_sync as module

    original = module.parse_bytes
    monkeypatch.setattr(module, "parse_bytes", lambda *a: pytest.fail("unchanged file parsed"))
    assert Synchronizer(hub, config).cycle()["skipped"] == 1
    monkeypatch.setattr(module, "parse_bytes", original)
    file.write_text("Revenue declined during this quarter", encoding="utf-8")
    assert Synchronizer(hub, config).cycle()["updated"] == 1
    assert "declined" in hub.fetch(hit["id"])["text"]
    file.unlink()
    Synchronizer(hub, config).cycle()
    assert sync_status(hub)["files"] == {"missing": 1}
    assert hub.fetch(hit["id"])


def test_retries_persist_and_changed_file_recovers(workspace):
    inbox, hub, config = setup(workspace)
    bad = inbox / "bad.json"
    bad.write_text("not json: SECRET", encoding="utf-8")
    (inbox / "ok.txt").write_text("Valid company revenue", encoding="utf-8")
    now = time.time() + 1
    assert Synchronizer(hub, config).cycle(now=now)["created"] == 1
    assert sync_status(hub)["files"] == {"ok": 1, "retrying": 1}
    assert not Synchronizer(hub, config).cycle(now=now + 5)["errors"]
    Synchronizer(hub, config).cycle(now=now + 11)
    assert sync_status(hub)["files"] == {"ok": 1, "failed": 1}
    assert not Synchronizer(hub, config).cycle(now=now + 100)["errors"]
    assert "SECRET" not in json.dumps(sync_status(hub))
    bad.write_text('[{"title":"Fixed","body":"Recovered revenue"}]', encoding="utf-8")
    assert Synchronizer(hub, config).cycle(now=now + 101)["created"] == 1
    assert sync_status(hub)["files"] == {"ok": 2}


def test_manual_retry_resets_attempts_and_root_unavailable_not_missing(workspace):
    inbox, hub, config = setup(workspace)
    (inbox / "bad.json").write_text("broken", encoding="utf-8")
    now = time.time() + 1
    sync = Synchronizer(hub, config)
    sync.cycle(now=now)
    sync.cycle(now=now + 11)
    assert sync_status(hub)["files"] == {"failed": 1}
    sync.cycle(now=now + 12, retry_failed=True)
    assert sync_status(hub)["files"] == {"retrying": 1}
    inbox.rename(inbox.with_name("offline"))
    result = sync.cycle(now=now + 100)
    assert result["errors"][0]["error"] == "scan_incomplete"
    assert "missing" not in sync_status(hub)["files"]


def test_bindings_and_settling(workspace):
    inbox, hub, config = setup(workspace)
    file = inbox / "hashed.txt"
    file.write_text("A valid revenue report", encoding="utf-8")
    config.roots[0].bindings["hashed.txt"] = FileBinding(
        external_id="legacy:123", name="report_2026-09-01.txt"
    )
    config.settle_seconds = 30
    Synchronizer(hub, config).cycle(now=time.time())
    assert hub.stats()["documents"] == 0
    Synchronizer(hub, config).cycle(now=time.time() + 31)
    hit = hub.search(SearchRequest(query="revenue"))["results"][0]
    fetched = hub.fetch(hit["id"])
    assert fetched["metadata"]["external_id"] == "legacy:123"
    assert fetched["metadata"]["published_at"].startswith("2026-09-01")


def test_worker_lock_exclusion_and_release(workspace):
    with worker_lock(workspace):
        with pytest.raises(HubError, match="already_running"), worker_lock(workspace):
            pass
    with worker_lock(workspace):
        pass


def test_sync_api_auth_and_schema(workspace):
    _, hub, config = setup(workspace)
    app = create_app(hub, "r" * 40, "a" * 40)
    with TestClient(app) as client:
        assert client.get("/api/sync").status_code == 401
        headers = {"Authorization": "Bearer " + "r" * 40}
        assert client.get("/api/sync", headers=headers).json()["configured"] is False
        Synchronizer(hub, config).cycle()
        body = client.get("/api/sync", headers=headers).json()
        assert body["configured"] and body["last_cycle"]["finished_at"]
        assert str(workspace) not in json.dumps(body)
        assert client.get("/api/sync/files").status_code == 401
        assert client.get("/api/sync/files?state=bad", headers=headers).status_code == 422
        assert client.get("/api/sync/files?limit=999", headers=headers).status_code == 422
        assert client.get("/api/sync/files", headers=headers).json() == {"files": [], "total": 0}


def test_config_paths_resolve_from_config_location(workspace):
    path = workspace / "sync.json"
    path.write_text(
        json.dumps({"roots": [{"path": "inbox", "source_id": "test", "source_name": "Test"}]}),
        encoding="utf-8",
    )
    assert load_config(path).roots[0].path == (workspace / "inbox").resolve()


def test_semantic_failure_retries_without_reingestion(workspace, monkeypatch):
    inbox, hub, config = setup(workspace)
    config.semantic = True
    (inbox / "file.txt").write_text("Local semantic update", encoding="utf-8")
    import research_agent.hub_semantic as semantic

    monkeypatch.setattr(semantic, "configuration", lambda conn: {"engine": "e5"})
    monkeypatch.setattr(semantic, "index_status", lambda h: {"pending_chunks": 1})
    calls = []

    def build(hub, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("model offline")

    monkeypatch.setattr(semantic, "build_index", build)
    assert Synchronizer(hub, config).cycle()["semantic"] == "retry_next_cycle"
    result = Synchronizer(hub, config).cycle()
    assert result["semantic"] == "ready" and result["skipped"] == 1
    assert len(calls) == 2 and calls[0]["existing_config"] == {"engine": "e5"}


def test_bad_multi_record_file_is_atomic_and_disabled_source_stays_disabled(workspace):
    from research_agent.hub_models import SourceDefinition

    inbox, hub, config = setup(workspace)
    (inbox / "records.json").write_text(
        json.dumps(
            [
                {"title": "valid", "body": "Valid revenue"},
                {"title": "invalid", "body": ""},
            ]
        ),
        encoding="utf-8",
    )
    Synchronizer(hub, config).cycle()
    assert hub.stats()["documents"] == 0
    hub.upsert_source(SourceDefinition(id="research", name="Research", enabled=False))
    (inbox / "good.txt").write_text("Valid revenue", encoding="utf-8")
    result = Synchronizer(hub, config).cycle()
    assert result["errors"] == [{"source_id": "research", "error": "source_disabled"}]
    assert hub.stats()["documents"] == 0


def test_read_failure_obeys_backoff(workspace, monkeypatch):
    from pathlib import Path

    inbox, hub, config = setup(workspace)
    file = inbox / "locked.txt"
    file.write_text("Locked valid report", encoding="utf-8")
    original = Path.read_bytes
    calls = []

    def read(path):
        if path.name == "locked.txt":
            calls.append(path)
            raise PermissionError("SECRET")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    now = time.time() + 1
    Synchronizer(hub, config).cycle(now=now)
    Synchronizer(hub, config).cycle(now=now + 5)
    assert len(calls) == 1
    Synchronizer(hub, config).cycle(now=now + 11)
    assert len(calls) == 2 and sync_status(hub)["files"] == {"failed": 1}
    monkeypatch.setattr(Path, "read_bytes", original)
    assert Synchronizer(hub, config).cycle(now=now + 12, retry_failed=True)["created"] == 1


def test_source_inventory_does_not_mistake_partial_archives_for_complete(workspace):
    from zipfile import ZipFile

    from research_agent.hub_inventory import inventory

    with ZipFile(workspace / "complete.zip", "w") as archive:
        archive.writestr("example.pdf", b"test fixture")
    (workspace / "partial.zip.qkdownloading").write_bytes(b"partial")
    (workspace / "broken.zip").write_bytes(b"not a zip")
    result = inventory(workspace)
    states = {e["file"]: e["state"] for e in result["files"]}
    assert states["complete.zip"] == "zip_crc_verified"
    assert states["partial.zip.qkdownloading"] == "incomplete_download"
    assert states["broken.zip"] == "unreadable_or_invalid"
    assert result["incomplete_downloads"] == 1


def test_two_roots_cannot_silently_overwrite_same_external_id(workspace):
    inbox, hub, config = setup(workspace)
    second = workspace / "second"
    second.mkdir()
    config.roots.append(SyncRoot(path=second, source_id="research", source_name="Research"))
    (inbox / "same.txt").write_text("Original revenue report", encoding="utf-8")
    (second / "same.txt").write_text("Conflicting different report", encoding="utf-8")
    result = Synchronizer(hub, config).cycle()
    assert result["created"] == 1 and result["errors"]
    found = hub.search(SearchRequest(query="revenue"))["results"]
    assert len(found) == 1 and hub.fetch(found[0]["id"])["text"] == "Original revenue report"
