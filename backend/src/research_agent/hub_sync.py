"""Durable local file polling. Checkpoints advance only after successful ingestion.

One OS lock per data directory excludes competing workers and releases on process exit.
Missing files are reported, never implicitly deleted from the research collection.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field, model_validator

from .config import Settings
from .hub import Hub, HubError
from .hub_adapters import filename_date
from .hub_models import DocumentInput, IngestRequest, SourceDefinition
from .models import StrictModel, digest, stable_json
from .parsing import SUPPORTED, parse_bytes


class FileBinding(StrictModel):
    external_id: str
    name: str


class SyncRoot(StrictModel):
    path: Path
    source_id: str
    source_name: str
    external_prefix: str = ""
    # Migration map for PDFs previously recovered under hashed filenames.
    bindings: dict[str, FileBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_source(self):
        SourceDefinition(id=self.source_id, name=self.source_name, kind="local")
        return self


class SyncConfig(StrictModel):
    roots: list[SyncRoot] = Field(min_length=1)
    interval_seconds: int = Field(default=60, ge=5, le=86400)
    settle_seconds: int = Field(default=30, ge=0, le=3600)
    retry_seconds: int = Field(default=60, ge=1, le=86400)
    max_attempts: int = Field(default=5, ge=1, le=100)
    semantic: bool = True


def load_config(path: Path):
    config = SyncConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    # Relative roots are relative to the config, independent of the working directory.
    for root in config.roots:
        root.path = (path.parent / root.path).resolve()
    pairs = [(r.source_id, str(r.path)) for r in config.roots]
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate_sync_root")
    return config


@contextmanager
def worker_lock(directory: Path):
    path = directory / "sync.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise HubError("sync_worker_already_running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Synchronizer:
    def __init__(self, hub: Hub, config: SyncConfig):
        self.hub, self.config = hub, config
        self.db = hub.db.parent / "sync.sqlite3"
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    root TEXT NOT NULL, source TEXT NOT NULL, path TEXT NOT NULL,
                    sha TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,
                    next_retry REAL NOT NULL, error TEXT NOT NULL, document_ids TEXT NOT NULL,
                    seen REAL NOT NULL, PRIMARY KEY(root,source,path)
                );
                CREATE TABLE IF NOT EXISTS runtime (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def runtime(self, payload):
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO runtime VALUES(1,?)", (json.dumps(payload),))

    def save(self, key, sha, state, attempts, next_retry, error, ids, now):
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?)",
                (*key, sha, state, attempts, next_retry, error, json.dumps(ids), now),
            )

    def cycle(self, *, now=None, retry_failed=False):
        now = time.time() if now is None else now
        report = {
            "started_at": now,
            "finished_at": None,
            "pid": os.getpid(),
            "interval_seconds": self.config.interval_seconds,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "errors": [],
            "semantic": "not_requested",
            "semantic_error": None,
        }
        self.runtime(report)
        for root in self.config.roots:
            base = root.path.resolve()
            # os.walk's onerror prevents inaccessible folders being mistaken for deletion.
            paths, scan_errors = [], []
            if base.is_dir():
                for directory, dirs, files in os.walk(base, onerror=scan_errors.append):
                    dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
                    paths.extend(Path(directory) / name for name in files)
            else:
                scan_errors.append("root_unavailable")
            if scan_errors:
                report["errors"].append({"source_id": root.source_id, "error": "scan_incomplete"})
            known = {s["id"]: s for s in self.hub.sources()}
            if root.source_id not in known:
                self.hub.upsert_source(
                    SourceDefinition(id=root.source_id, name=root.source_name, kind="local")
                )
            elif not known[root.source_id]["enabled"]:
                report["errors"].append({"source_id": root.source_id, "error": "source_disabled"})
                continue
            seen = set()
            for path in sorted(paths):
                if path.suffix.lower() not in SUPPORTED:
                    continue
                relative = path.relative_to(base).as_posix()
                seen.add(relative)
                key = (str(base), root.source_id, relative)
                with self.connection() as conn:
                    old = conn.execute(
                        "SELECT * FROM files WHERE root=? AND source=? AND path=?", key
                    ).fetchone()
                sha, ids = "", json.loads(old["document_ids"]) if old else []
                attempts = old["attempts"] if old else 0
                if (
                    old
                    and not old["sha"]
                    and not retry_failed
                    and (old["state"] == "failed" or now < old["next_retry"])
                ):
                    continue
                try:
                    if not path.resolve().is_relative_to(base):
                        raise ValueError("outside_root")
                    before = path.stat()
                    sha = f"stat:{before.st_size}:{before.st_mtime_ns}"
                    if (
                        old
                        and old["sha"] == sha
                        and not retry_failed
                        and (old["state"] == "failed" or now < old["next_retry"])
                    ):
                        continue
                    if now - before.st_mtime < self.config.settle_seconds:
                        self.save(key, old["sha"] if old else "", "settling", 0, 0, "", ids, now)
                        continue
                    if before.st_size > Settings().max_file_mb * 1024 * 1024:
                        raise ValueError("file_size_limit")
                    raw = path.read_bytes()
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        self.save(key, "", "settling", 0, 0, "", ids, now)
                        continue
                    sha = hashlib.sha256(raw).hexdigest()
                    same = old is not None and sha == old["sha"]
                    if same and old["state"] == "ok":
                        report["skipped"] += 1
                        continue
                    if (
                        same
                        and not retry_failed
                        and (old["state"] == "failed" or now < old["next_retry"])
                    ):
                        continue
                    attempts = attempts if same and not retry_failed else 0
                    binding = root.bindings.get(relative)
                    name = binding.name if binding else path.name
                    parsed = parse_bytes(name, raw, Settings())
                    if not parsed or (binding and len(parsed) != 1):
                        raise ValueError("invalid_record_count")
                    documents = []
                    for index, doc in enumerate(parsed):
                        external_id = (
                            binding.external_id
                            if binding
                            else (f"{root.external_prefix}{relative}#{index}")
                        )
                        identity = digest(stable_json([root.source_id, external_id]))[:32]
                        with self.connection() as conn:
                            owner = conn.execute(
                                "SELECT 1 FROM files f WHERE source=? AND NOT(root=? AND path=?) "
                                "AND EXISTS(SELECT 1 FROM json_each(f.document_ids) WHERE value=?)",
                                (root.source_id, str(base), relative, identity),
                            ).fetchone()
                        if owner:
                            raise ValueError("sync_external_id_owned_by_another_file")
                        with self.hub.connection() as conn:
                            prior = conn.execute(
                                "SELECT metadata_json FROM hub_documents "
                                "WHERE source_id=? AND external_id=?",
                                (root.source_id, external_id),
                            ).fetchone()
                        metadata = json.loads(prior[0]) if prior else {}
                        metadata.update(
                            {
                                "file_name": name[:1000],
                                "parser_warnings": ";".join(doc.warnings)[:1000],
                                "date_source": "document"
                                if doc.published_at
                                else (
                                    "filename_date_utc_midnight"
                                    if filename_date(name)
                                    else "unknown"
                                ),
                            }
                        )
                        documents.append(
                            DocumentInput(
                                external_id=external_id,
                                title=doc.title,
                                body=doc.body,
                                published_at=doc.published_at or filename_date(name),
                                url=doc.url,
                                pages=doc.pages,
                                metadata=metadata,
                            )
                        )
                    # One file is transactional, including multi-record JSON: no partial success.
                    result = self.hub.ingest(
                        IngestRequest(source_id=root.source_id, documents=documents)
                    )
                    ids = sorted(set(ids) | set(result["document_ids"]))
                    self.save(key, sha, "ok", 0, 0, "", ids, now)
                    for field in ("created", "updated", "unchanged"):
                        report[field] += result[field]
                except Exception:
                    # Parser libraries raise provider-specific exceptions. Never log raw input.
                    attempts = attempts + 1 if old and old["sha"] == sha else 1
                    if retry_failed:
                        attempts = 1
                    state = "failed" if attempts >= self.config.max_attempts else "retrying"
                    delay = min(3600, self.config.retry_seconds * 2 ** min(attempts - 1, 12))
                    self.save(
                        key,
                        sha,
                        state,
                        attempts,
                        now + delay,
                        "file_ingest_failed_check_format_access_or_ocr",
                        ids,
                        now,
                    )
                    report["errors"].append({"source_id": root.source_id, "error": "file_failed"})
                self.runtime(report)
            if not scan_errors:
                with self.connection() as conn:
                    rows = conn.execute(
                        "SELECT path FROM files WHERE root=? AND source=?",
                        (str(base), root.source_id),
                    ).fetchall()
                    for row in rows:
                        if row[0] not in seen:
                            conn.execute(
                                "UPDATE files SET state='missing',error='file_missing_retained' "
                                "WHERE root=? AND source=? AND path=?",
                                (str(base), root.source_id, row[0]),
                            )
        if self.config.semantic:
            from .hub_semantic import build_index, configuration, index_status

            try:
                status = index_status(self.hub)
                with self.hub.connection() as conn:
                    config = configuration(conn)
                if not config:
                    report["semantic"] = "not_configured"
                elif status["pending_chunks"]:
                    report["semantic"] = "indexing"
                    self.runtime(report)
                    # Automatic sync never downloads models or changes the selected engine.
                    build_index(self.hub, existing_config=config)
                    report["semantic"] = "ready"
                else:
                    report["semantic"] = "ready"
            except Exception:
                report["semantic"] = "retry_next_cycle"
                report["semantic_error"] = "local_embedding_unavailable"
        report["finished_at"] = time.time()
        self.runtime(report)
        return report


def sync_status(hub):
    path = hub.db.parent / "sync.sqlite3"
    if not path.exists():
        return {"configured": False, "last_cycle": None, "files": {}, "worker_recent": False}
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute("SELECT payload FROM runtime WHERE id=1").fetchone()
        payload = json.loads(row[0]) if row else None
        counts = dict(conn.execute("SELECT state,count(*) FROM files GROUP BY state"))
    finally:
        conn.close()
    last = (payload.get("finished_at") or payload["started_at"]) if payload else 0
    return {
        "configured": True,
        "last_cycle": payload,
        "files": counts,
        "worker_recent": bool(
            payload and time.time() - last < max(180, payload["interval_seconds"] * 3)
        ),
    }


def sync_files(hub, *, state=None, limit=50, offset=0):
    path = hub.db.parent / "sync.sqlite3"
    if not path.exists():
        return {"files": [], "total": 0}
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    where, params = (" WHERE state=?", [state]) if state else ("", [])
    try:
        total = conn.execute("SELECT count(*) FROM files" + where, params).fetchone()[0]
        rows = conn.execute(
            "SELECT source AS source_id,path AS file,state,attempts,next_retry,error "
            "FROM files" + where + " ORDER BY source,path LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {"files": [dict(row) for row in rows], "total": total}
    finally:
        conn.close()


def sync_loop(worker, *, watch=False, retry_failed=False, stop_event=None):
    while stop_event is None or not stop_event.is_set():
        try:
            result = worker.cycle(retry_failed=retry_failed)
        except Exception:
            if not watch:
                raise
            result = {"errors": [{"error": "sync_cycle_failed"}]}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not watch:
            return result
        retry_failed = False
        if stop_event is not None:
            stop_event.wait(worker.config.interval_seconds)
        else:
            time.sleep(worker.config.interval_seconds)


def run_sync(hub, config_path, *, watch=False, retry_failed=False):
    config = load_config(config_path)
    with worker_lock(hub.db.parent):
        return sync_loop(Synchronizer(hub, config), watch=watch, retry_failed=retry_failed)
