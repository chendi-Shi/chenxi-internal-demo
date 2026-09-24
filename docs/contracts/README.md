# Interface Contracts

## Source of truth

`openapi.json` is the machine-readable HTTP contract shared by the Retrieval Hub and Dashboard. Its current API version is 1.2.0. The backend exports the same contract to `backend/docs/contracts/`; the root copy is used to generate the TypeScript client types and validate mock fixtures.

`examples.json` contains synthetic data only. Do not add private document text, local paths, credentials, database files, or user-specific company aliases to either contract file.

## Priority

1. `openapi.json` determines fields, types, status codes, and authentication.
2. `examples.json` provides known fixtures and does not extend the schema.
3. Architecture documents explain semantics and responsibility boundaries.
4. The Dashboard client follows the contract and does not silently change protocol behavior.

When machine-readable and narrative descriptions disagree, fix the contract and implementation together before using the affected feature.

## Current contract

- API prefix: `/api`
- API version: `1.2.0`
- Authentication: HTTP Bearer; read and admin access are separate
- Policy update: optimistic lock using `expected_version`
- Retrieval status: `GET /api/retrieval`
- File sync status: `GET /api/sync` and `GET /api/sync/files`
- MCP tools: read-only `search`, `fetch`, and `search_documents`
- JSON: `snake_case`; reject unknown fields where defined by the schemas

## Change process

1. Update backend models or API behavior.
2. Export the complete OpenAPI contract and synthetic examples from the backend.
3. Copy both files to this directory and `backend/docs/contracts/`.
4. Run `npm test` from the repository root and the Python tests from `backend/`.
5. Review path, schema, required-field, error-code, and authentication changes.
6. Update `docs/ARCHITECTURE.md` and the roadmap when the integration behavior changes.

Do not patch only the generated TypeScript types or manually add a frontend-only field. The backend contract remains authoritative.
