# Retrieval Hub Backend

This directory contains the local backend used by the repository's dashboard. It imports approved local PDF/text files, builds a SQLite full-text index and exposes the shared Retrieval API plus read-only MCP tools.

Python 3.11+ is required. From this directory, create a virtual environment and install the development and MCP dependencies:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev,mcp]"
.venv\Scripts\python.exe -m research_agent.hub_cli init
.venv\Scripts\python.exe -m research_agent.hub_cli serve --cors-origin http://localhost:5173
```

The default service URL is `http://127.0.0.1:8765`; API docs are at `/docs` and Streamable HTTP MCP is at `/mcp`. `init` stores local credentials under `data/hub/`. The `data/` directory is Git-ignored. No files are imported until an explicit `ingest` or `serve --sync-config` command is run.

Run the Python tests with `.venv/Scripts/python.exe -m pytest` and static checks with `.venv/Scripts/python.exe -m ruff check src tests`. The public repository includes fictional fixtures only; local documents, credentials, sync config, model weights, and acceptance reports must remain in the local data directory.

For the full Dashboard workflow, see the repository [README](../README.md). Backend operations are in [HUB_RUNBOOK.md](docs/HUB_RUNBOOK.md), and the machine-readable API snapshot is in [contracts/openapi.json](docs/contracts/openapi.json).
