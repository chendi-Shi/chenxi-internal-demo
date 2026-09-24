import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';

const requiredFiles = [
  'MISSION.md',
  'ROADMAP.md',
  'AGENTS.md',
  'docs/ARCHITECTURE.md',
  'docs/ENGINEERING_STANDARDS.md',
  'docs/PHASE_B_REPORT.md',
  'docs/PHASE_C_REPORT.md',
  'docs/PHASE_D_REPORT.md',
  'docs/evaluation/BASELINE.md',
  'docs/contracts/README.md',
  'docs/contracts/openapi.json',
  'docs/contracts/examples.json',
  'docs/upstream/MCP_ARCHITECTURE.md',
  'docs/upstream/DASHBOARD_HANDOFF.md',
  '.agents/skills/mcp-retrieval-demo/SKILL.md',
  'src/api/client.ts',
  'src/api/mock.ts',
  'src/api/validation.ts',
  'src/api/generated/contracts.ts',
  'test/api-client.test.ts',
  'test/fixture-api.test.ts',
  'index.html',
  'src/main.ts',
  'src/styles.css',
  'src/ui/view-model.ts',
  'test/view-model.test.ts',
  'artifacts/evaluation/latest.json',
  'scripts/run-evaluation.ts',
  'src/evaluation/policy-evaluator.ts',
  'src/evaluation/report.ts',
  'src/evaluation/token-metrics.ts',
  'src/evaluation/tool-schemas.ts',
  'test/evaluation.test.ts',
];

const requiredPaths = [
  '/healthz',
  '/api/status',
  '/api/sources',
  '/api/search',
  '/api/documents/{document_id}',
  '/api/policy',
];

const requiredSchemas = [
  'PolicyState',
  'PolicyUpdate',
  'RetrievalPolicy',
  'SearchRequest',
  'SearchResponse',
  'SearchHit',
  'MCPFetchResult',
  'ErrorResponse',
];

const requiredExamples = [
  'sources_response',
  'search_request',
  'search_response',
  'document_response',
  'policy_response',
  'policy_update_request',
  'policy_update_response',
  'mcp_search_request',
  'mcp_search_response',
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

await Promise.all(requiredFiles.map((file) => access(file, constants.R_OK)));

const openapi = JSON.parse(await readFile('docs/contracts/openapi.json', 'utf8'));
const examples = JSON.parse(await readFile('docs/contracts/examples.json', 'utf8'));

assert(openapi.openapi === '3.1.0', `Expected OpenAPI 3.1.0, got ${openapi.openapi}`);
assert(openapi.info?.version === '1.0.0', `Expected API 1.0.0, got ${openapi.info?.version}`);

for (const path of requiredPaths) {
  assert(openapi.paths?.[path], `Missing required OpenAPI path: ${path}`);
}

for (const schema of requiredSchemas) {
  assert(openapi.components?.schemas?.[schema], `Missing required OpenAPI schema: ${schema}`);
}

for (const example of requiredExamples) {
  assert(Object.hasOwn(examples, example), `Missing required contract example: ${example}`);
}

const policySchema = openapi.components.schemas.RetrievalPolicy;
for (const field of ['source_weights', 'recency_boost', 'half_life_days', 'default_limit']) {
  assert(policySchema.properties?.[field], `Missing RetrievalPolicy field: ${field}`);
}

const policyUpdate = openapi.components.schemas.PolicyUpdate;
assert(policyUpdate.required?.includes('expected_version'), 'PolicyUpdate must require expected_version');
assert(policyUpdate.required?.includes('policy'), 'PolicyUpdate must require policy');

console.log(
  `Foundation valid: ${requiredFiles.length} files, ${requiredPaths.length} paths, ` +
    `${requiredSchemas.length} schemas, API ${openapi.info.version}.`,
);
