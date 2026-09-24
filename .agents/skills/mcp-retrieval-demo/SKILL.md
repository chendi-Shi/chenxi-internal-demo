---
name: mcp-retrieval-demo
description: Maintain and implement this repository's Retrieval Policy dashboard, contract client, policy evaluation, token measurement, and MCP integration checks against the provided upstream API. Use for planning, coding, testing, or reviewing this demo; do not use to alter upstream ingestion or ranking without an explicit scope change.
---

# MCP Retrieval Demo

Build the Retrieval Policy demo without creating a second retrieval implementation.

## Read first

Before changing the project, read:

1. [`MISSION.md`](../../../MISSION.md)
2. [`ROADMAP.md`](../../../ROADMAP.md)
3. [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md)
4. [`docs/contracts/README.md`](../../../docs/contracts/README.md)

For API work, inspect only the relevant paths and schemas in `docs/contracts/openapi.json`, then use `docs/contracts/examples.json` as fixture data.

## Invariants

- Treat the upstream Retrieval Hub as the sole owner of ingestion, indexing, ranking, policy persistence, HTTP, and MCP behavior.
- Do not read or import system source documents; use fictional fixtures.
- Keep API DTOs in `snake_case` and derive them from the contract.
- Never re-rank search results in the Dashboard.
- Save policy with `expected_version`; surface 409 conflicts instead of overwriting.
- Preserve `policy_version` in UI state, tests, and evaluation records.
- Render document content as untrusted plain text.
- Do not silently truncate or redefine upstream `fetch` semantics to reduce tokens.

## Workflow

1. Identify the active roadmap phase and stay inside its exit criteria.
2. Confirm the relevant OpenAPI operation and example before implementing a client call.
3. Add the smallest behavior and observable tests needed for that phase.
4. Run `npm test` plus phase-specific checks.
5. Update architecture, roadmap status, or the current phase report when a decision or known limitation changes.

## Engineering choices

- Use TypeScript strict mode and ESM for new application code.
- Centralize network access in one API client.
- Keep policy editing, search execution, and token measurement as separate modules.
- Measure token-related payloads before optimizing them; separate search, fetch, and tool-schema costs.
- Prefer deterministic scoring evidence from the server over LLM-based explanations or reranking.

## Stop and request direction

Do not proceed without confirmation when work requires changing the upstream OpenAPI/MCP contract, connecting real accounts, exposing a public endpoint, using paid APIs, or processing real internal content.
