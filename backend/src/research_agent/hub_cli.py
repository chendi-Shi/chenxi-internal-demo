"""Run `python -m research_agent.hub_cli --help` for the A-part entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .hub import Hub, HubError
from .hub_adapters import LocalFilesAdapter, sync_adapter
from .hub_models import DocumentInput, IngestRequest, SearchRequest, SourceDefinition
from .hub_runtime import init_local, load_credentials
from .hub_semantic import SemanticUnavailable


def seed_demo(hub: Hub):
    """Synthetic fixtures only; never represent these as internal research findings."""
    for source, name, date in (
        ("demo_research", "虚构样例 · 研究纪要", "2026-09-01T00:00:00Z"),
        ("demo_filings", "虚构样例 · 公司公告", "2026-08-01T00:00:00Z"),
    ):
        hub.upsert_source(SourceDefinition(id=source, name=name, kind="demo"))
        hub.ingest(
            IngestRequest(
                source_id=source,
                documents=[
                    DocumentInput(
                        external_id="demo-001",
                        title=f"{name}：算力需求",
                        body="【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。"
                        "本段仅用于验证检索权重与引用，不构成真实研究结论。",
                        published_at=date,
                        metadata={"company": "虚构公司", "sector": "科技"},
                    )
                ],
            )
        )
    return hub.stats()


def run(argv=None):
    parser = argparse.ArgumentParser(description="Internal data retrieval + MCP backend")
    parser.add_argument("--data-dir", type=Path, default=Path("data/hub"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--credentials", type=Path, help="Local credential file; environment overrides it"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Import clearly labelled synthetic samples")
    sub.add_parser("init", help="Create local credentials and a standalone MCP client config")
    sub.add_parser("status", help="Show counts and saved policy version")
    sub.add_parser("sync-status", help="Read durable file sync status")
    audit = sub.add_parser(
        "audit-source", help="Inventory files and validate complete ZIP archives"
    )
    audit.add_argument("path", type=Path)
    audit.add_argument("--output", type=Path, required=True)
    for command in ("sync-once", "watch"):
        sync = sub.add_parser(command, help="Incremental file sync with durable retries")
        sync.add_argument("--config", type=Path, required=True)
        sync.add_argument("--retry-failed", action="store_true")
    semantic = sub.add_parser("index-semantic", help="Build/resume local semantic index")
    semantic.add_argument("--engine", choices=["e5", "ollama"], default="e5")
    semantic.add_argument("--model-dir", type=Path, help="Local E5 model cache directory")
    sub.add_parser("retrieval-status", help="Show local semantic index coverage")
    sub.add_parser("disable-semantic", help="Use bilingual lexical retrieval only")
    search = sub.add_parser("search", help="Test local keyword retrieval")
    search.add_argument("query")
    source = sub.add_parser("source", help="Create/update a source before pushing documents")
    source.add_argument("--id", required=True)
    source.add_argument("--name", required=True)
    source.add_argument(
        "--kind", choices=["local", "api", "sharepoint", "database", "demo"], default="api"
    )
    source.add_argument("--disabled", action="store_true")
    records = sub.add_parser(
        "ingest-json", help="Import an IngestRequest JSON with stable external IDs"
    )
    records.add_argument("path", type=Path)
    ingest = sub.add_parser("ingest", help="Import a local file/folder; rerun to update")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source-id", required=True)
    ingest.add_argument("--source-name", required=True)
    serve = sub.add_parser("serve", help="Dashboard HTTP API and /mcp")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cors-origin", action="append", default=[])
    serve.add_argument(
        "--sync-config", type=Path, help="Run a file polling worker with this server"
    )
    sub.add_parser("stdio", help="Read-only MCP stdio for a local client or private tunnel")
    export = sub.add_parser("export-openapi", help="Write the interface contract for teammate B")
    export.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    port = getattr(args, "port", 8765)
    if not 1 <= port <= 65535:
        raise HubError("invalid_port")
    base_url = args.base_url or f"http://127.0.0.1:{port}"
    credentials_path = args.credentials or args.data_dir / "access.local.json"
    hub = Hub(args.data_dir, base_url)
    if args.command == "demo":
        result = seed_demo(hub)
    elif args.command == "init":
        result = init_local(args.data_dir, credentials_path, base_url)
    elif args.command == "status":
        result = hub.stats()
    elif args.command == "sync-status":
        from .hub_sync import sync_status

        result = sync_status(hub)
    elif args.command == "audit-source":
        from .hub_inventory import inventory

        result = inventory(args.path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.command in {"sync-once", "watch"}:
        from .hub_sync import run_sync

        result = run_sync(
            hub, args.config, watch=args.command == "watch", retry_failed=args.retry_failed
        )
        return 2 if result.get("errors") else 0
    elif args.command in {"index-semantic", "retrieval-status", "disable-semantic"}:
        from .hub_semantic import build_index, index_status

        if args.command == "index-semantic":
            result = build_index(
                hub,
                engine=args.engine,
                model_directory=args.model_dir,
                progress=lambda done, total: print(
                    f"Embedding chunks: {done}/{total}", file=sys.stderr, flush=True
                ),
            )
        else:
            if args.command == "disable-semantic":
                with hub.connection() as conn:
                    conn.execute("DELETE FROM hub_semantic")
            result = index_status(hub)
    elif args.command == "search":
        result = hub.search(SearchRequest(query=args.query))
    elif args.command == "source":
        result = hub.upsert_source(
            SourceDefinition(id=args.id, name=args.name, kind=args.kind, enabled=not args.disabled)
        )
    elif args.command == "ingest-json":
        if args.path.stat().st_size > 12_000_000:
            raise HubError("request_too_large")
        batch = IngestRequest.model_validate_json(args.path.read_text(encoding="utf-8-sig"))
        result = hub.ingest(batch)
    elif args.command == "ingest":
        # Importing files must never silently re-enable an administratively disabled source.
        if args.source_id not in {s["id"] for s in hub.sources()}:
            hub.upsert_source(
                SourceDefinition(id=args.source_id, name=args.source_name, kind="local")
            )
        adapter = LocalFilesAdapter(args.path)
        result = sync_adapter(hub, args.source_id, adapter)
        result["errors"] = adapter.errors
    elif args.command == "stdio":
        from .hub_mcp import create_mcp

        create_mcp(hub).run(transport="stdio")
        return 0
    else:
        from .hub_api import create_app

        if args.command == "export-openapi":
            # Schema-only placeholders; no server starts and no usable tokens are written.
            app = create_app(hub, "schema-read-" + "x" * 32, "schema-admin-" + "x" * 32)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return 0
        import uvicorn

        credentials = load_credentials(credentials_path)
        app = create_app(hub, credentials.read_token, credentials.admin_token, args.cors_origin)
        from contextlib import ExitStack
        from threading import Event, Thread

        worker, stop = None, Event()
        with ExitStack() as stack:
            if args.sync_config:
                from .hub_sync import Synchronizer, load_config, sync_loop, worker_lock

                config = load_config(args.sync_config)
                stack.enter_context(worker_lock(hub.db.parent))
                synchronizer = Synchronizer(hub, config)
                # Same process lifetime: a closed API cannot leave an orphan worker on Windows.
                worker = Thread(
                    target=sync_loop,
                    args=(synchronizer,),
                    kwargs={"watch": True, "stop_event": stop},
                    daemon=True,
                    name="hub-file-sync",
                )
                worker.start()
            try:
                uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
            finally:
                stop.set()
                if worker is not None:
                    worker.join(timeout=10)
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("errors") else 0


def main(argv=None):
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 130
    except ValidationError as exc:
        error = {
            "code": "validation_error",
            "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
        }
    except HubError as exc:
        error = {"code": str(exc)}
    except SemanticUnavailable as exc:
        error = {"code": str(exc)}
    except OSError:
        error = {"code": "file_or_runtime_error"}
    except ValueError:
        error = {"code": "invalid_input"}
    # Never print raw input, provider credentials or an input-containing validation traceback.
    print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
