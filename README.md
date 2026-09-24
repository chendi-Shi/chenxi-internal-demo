# Internal Research Retrieval Demo

A local demo that combines an internal-document Retrieval Hub with a configurable research dashboard. The Hub parses and indexes approved files, provides one retrieval policy to HTTP and MCP, and exposes sync/index status. The dashboard connects to that API to search evidence, inspect source documents, and adjust ranking policy.

The repository contains code and fictional fixtures only. Local documents, databases, sync paths, credentials, and semantic-model files belong in the ignored `backend/data/` directory.

## Architecture

```text
Approved local files → backend/ Retrieval Hub → SQLite / FTS5 / optional local vectors
                              ├─ HTTP API 1.2.0 → src/ Research Dashboard
                              └─ MCP search/fetch → GPT client (account connection is separate)
```

`backend/` owns ingestion, parsing, indexing, retrieval, server-side ranking, HTTP and MCP. `src/` owns the dashboard, contract-checked HTTP client, policy editor and evaluations. Both use the API contract in `docs/contracts/`; the backend’s exported copy is kept in `backend/docs/contracts/` and checked against it. The browser never reranks results.

## Run locally on Windows

Requirements: Python 3.11 or newer and Node.js 22.18 or newer.

In a PowerShell window, start the backend:

```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev,mcp]"
.venv\Scripts\python.exe -m research_agent.hub_cli init
.venv\Scripts\python.exe -m research_agent.hub_cli serve --cors-origin http://localhost:5173 --cors-origin http://127.0.0.1:5173
```

In a second PowerShell window, start the dashboard:

```powershell
npm install
npm run dev
```

Open the local address printed by Vite. Select **上游 HTTP 服务**, keep the default Base URL `http://127.0.0.1:8765`, then enter the read and admin tokens from `backend/data/hub/access.local.json`. The read token loads status, sources, policy, search results and documents; the admin token is needed only to save policy changes. The credentials are held in page memory and are never written to browser storage.

To try the backend without any internal data, use a separate demo database:

```powershell
cd backend
.venv\Scripts\python.exe -m research_agent.hub_cli --data-dir data/hub-demo init
.venv\Scripts\python.exe -m research_agent.hub_cli --data-dir data/hub-demo demo
.venv\Scripts\python.exe -m research_agent.hub_cli --data-dir data/hub-demo serve --cors-origin http://localhost:5173
```

The demo command imports explicitly fictional records. Do not run it against a database that contains real documents.

## Connect an approved PDF folder

Run the commands from `backend/`. Register a source and import a folder containing PDFs with a text layer:

```powershell
.venv\Scripts\python.exe -m research_agent.hub_cli source --id internal_pdfs --name "Approved internal PDFs" --kind local
.venv\Scripts\python.exe -m research_agent.hub_cli ingest "<local-folder>" --source-id internal_pdfs --source-name "Approved internal PDFs"
```

For continuous file sync, copy `samples/hub_sync.json` to `data/hub/sync.local.json`, edit its folder path locally, then start the server with `--sync-config data/hub/sync.local.json`. The config and its path stay local. Scanned PDFs need OCR before import. ZIP/RAR files must be extracted first.

## Dashboard and API

The dashboard displays document, chunk and source counts, semantic-index coverage and file-sync status. It can filter searches by source, date and metadata, read returned documents, and edit source weights, freshness and result limits. Policy writes use optimistic version checks. The backend supplies ranked results; the page explains them without changing their order.

The API reference is available at `http://127.0.0.1:8765/docs`. The dashboard contract is [OpenAPI 1.2.0](docs/contracts/openapi.json). Main endpoints include `/api/status`, `/api/retrieval`, `/api/sync`, `/api/sync/files`, `/api/sources`, `/api/search`, `/api/documents/{document_id}` and `/api/policy`.

The backend exposes read-only MCP tools `search`, `fetch` and `search_documents` through `/mcp` or local stdio. A real GPT account connection is a separate setup step and is not part of the local demo.

## Verify

From the repository root:

```bash
npm test
```

From `backend/`:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check src tests
```

All committed data examples are fictional. See [the architecture](docs/ARCHITECTURE.md), [the backend runbook](backend/docs/HUB_RUNBOOK.md), and [the contract change process](docs/contracts/README.md).
