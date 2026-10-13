# PostgreSQL / MCP local acceptance

Verified 2026-10-13 using the real indexed private PDF library and the selected PostgreSQL + pgvector RRF profile (`candidate_limit=100`). The official MCP SDK started a new server subprocess through the actual managed launcher for each case. Two Chinese questions and one English question passed. Source questions, document IDs, bodies, credentials and local paths are not published.

| Check | Result |
|---|---|
| stdio initialize and server identity | pass |
| `search`, `search_documents`, `fetch` discovery and read-only annotations | pass |
| Real Hub / HTTP / MCP filtered result order and policy version | pass |
| Compact `search` order versus the unfiltered Hub | pass |
| Source and metadata filters versus original fetched metadata | pass |
| Exact snippet character offsets and containing PDF page | pass |
| Original content marked as untrusted source data | pass |
| Provider degradation / fallback to SQLite | none in accepted cases |

HTTP parity used FastAPI TestClient with the real Hub and PostgreSQL database. It does not certify a deployed HTTP listener, proxy or cloud environment. MCP calls used the real stdio subprocess and transport. These are runtime consistency checks; relevance and ranking metrics are reported separately in [POSTGRES_REAL_REPORT.md](POSTGRES_REAL_REPORT.md), and generated-answer quality is not measured.

Two earlier end-to-end attempts timed out during first native NumPy import after Windows stdio had started. A separate official-SDK probe without models or databases reproduced the lazy-import timeout and passed when libraries were loaded before transport startup. The CLI now conditionally preloads native libraries before stdio/HTTP worker threads; E5 and cross-encoder models remain lazy. This also covers PostgreSQL lexical failing over to an already configured SQLite E5 path. Default installations without a native semantic configuration require no extra inference packages. No retrieval score, embedding recipe or benchmark ranking implementation was changed by this fix. Related upstream reports: [MCP Python SDK #2832](https://github.com/modelcontextprotocol/python-sdk/issues/2832), [NumPy #24290](https://github.com/numpy/numpy/issues/24290).

The local `chenxi-library` server is registered in Codex configuration. Registration and independent SDK calls do not establish discovery in an already running chat; the MCP client must reload its configuration. The previous ChatGPT tunnel launcher was corrected to invoke the same managed PostgreSQL entry point, but the cloud tunnel is not running and the ChatGPT account connection has not been revalidated. Local stdio requires no OpenAI API key; starting the separate secure tunnel follows its runtime-key flow.

Final backend validation: 83 tests passed; Ruff and Git whitespace checks passed. Frontend validation: 33 unit tests, contract/foundation/evaluation checks, TypeScript and Vite build passed. Two existing backend dependency deprecation warnings remain.

Anonymous machine-readable evidence: [POSTGRES_MCP_ACCEPTANCE.json](POSTGRES_MCP_ACCEPTANCE.json).
