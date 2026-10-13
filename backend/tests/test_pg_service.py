"""Windows recovery must never fsync an open server log inside PGDATA."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_agent import pg_service


def configured_cluster(workspace, monkeypatch, *, inside_log=False):
    cluster = workspace / "cluster"
    cluster.mkdir()
    (cluster / "PG_VERSION").write_text("16", encoding="utf-8")
    runtime = workspace / "runtime"
    binary = runtime / "Library/bin/pg_ctl.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture: subprocess is mocked")
    config = {"runtime_dir": str(runtime), "cluster_dir": str(cluster),
              "host": "127.0.0.1", "port": 55432}
    if inside_log:
        config["log_file"] = str(cluster / "runtime.log")
    saved = workspace / "runtime.local.json"
    saved.write_text(json.dumps(config), encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=3 if command[-1] == "status" else 0)

    monkeypatch.setattr(pg_service.subprocess, "run", run)
    return saved, cluster, calls


def test_start_uses_log_outside_data_directory(workspace, monkeypatch):
    saved, cluster, calls = configured_cluster(workspace, monkeypatch)
    assert pg_service.ensure_started(saved) == "started"
    start = calls[-1]
    log = Path(start[start.index("-l") + 1]).resolve()
    assert not log.is_relative_to(cluster.resolve())
    assert start[start.index("-o") + 1] == "-p 55432 -h 127.0.0.1"


def test_unsafe_log_inside_data_directory_cannot_start(workspace, monkeypatch):
    saved, _, calls = configured_cluster(workspace, monkeypatch, inside_log=True)
    with pytest.raises(ValueError, match="postgres_log_must_be_outside_cluster"):
        pg_service.ensure_started(saved)
    assert all(command[-1] == "status" for command in calls)
