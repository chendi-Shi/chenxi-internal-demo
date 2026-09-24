# Dashboard and Backend Handoff

The backend and dashboard now live in this repository. Follow the root [`README.md`](../../README.md) to run both services together. Backend setup, data ingestion, sync and local MCP operation are described in [`../../backend/docs/HUB_RUNBOOK.md`](../../backend/docs/HUB_RUNBOOK.md).

Both components use the same version 1.2.0 contract. Update backend models first, export the contract, copy it to both contract directories, then run the TypeScript and Python test suites.
