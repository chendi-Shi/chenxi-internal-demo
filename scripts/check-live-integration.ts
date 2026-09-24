import { RetrievalApiClient } from '../src/api/client.ts';
import { makeMcpEnvelope, measureJson, tokenEstimationMethod } from '../src/evaluation/token-metrics.ts';

const baseUrl = process.env.RESEARCH_HUB_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8765';
const readToken = process.env.RESEARCH_HUB_READ_TOKEN;
const query = process.env.RESEARCH_HUB_LIVE_QUERY?.trim() || '芯片';

if (!readToken) {
  console.error(JSON.stringify({
    status: 'blocked',
    reason: 'missing_read_token',
    required: ['RESEARCH_HUB_READ_TOKEN'],
    optional: ['RESEARCH_HUB_BASE_URL', 'RESEARCH_HUB_LIVE_QUERY'],
  }, null, 2));
  process.exitCode = 2;
} else {
  const healthResponse = await fetch(`${baseUrl}/healthz`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(5_000),
  });
  if (!healthResponse.ok) throw new Error(`Health check failed with HTTP ${healthResponse.status}`);

  const api = new RetrievalApiClient({ baseUrl, readToken, timeoutMs: 15_000 });
  const [status, sources, policy] = await Promise.all([
    api.getStatus(),
    api.getSources(),
    api.getPolicy(),
  ]);
  const search = await api.search({
    query,
    source_ids: [],
    since: '',
    until: '',
    metadata: {},
    limit: policy.policy.default_limit ?? 10,
  });
  const firstDocument = search.results[0]
    ? await api.getDocument(search.results[0].id)
    : undefined;

  const versionsMatch =
    status.policy_version === policy.version && search.policy_version === policy.version;
  if (!versionsMatch) {
    throw new Error(
      `Policy version mismatch: status=${status.policy_version}, policy=${policy.version}, search=${search.policy_version}`,
    );
  }

  const searchMetric = measureJson('live_http_search_response', search);
  const fetchMetric = firstDocument
    ? measureJson('live_http_fetch_response', firstDocument)
    : undefined;
  const mcpSearchEstimate = measureJson('live_mcp_search_envelope_estimate', makeMcpEnvelope({
    results: search.results.map(({ id, title, url }) => ({ id, title, url })),
  }));
  const mcpFetchEstimate = firstDocument
    ? measureJson('live_mcp_fetch_envelope_estimate', makeMcpEnvelope(firstDocument))
    : undefined;

  console.log(JSON.stringify({
    status: 'passed',
    base_url: baseUrl,
    query,
    policy_version: policy.version,
    source_ids: sources.sources.map((source) => source.id),
    document_count: status.documents,
    matched_chunks: search.matched_chunks,
    result_ids: search.results.map((result) => result.id),
    fetched_document_id: firstDocument?.id ?? null,
    token_estimation: tokenEstimationMethod,
    payload_metrics: [searchMetric, fetchMetric, mcpSearchEstimate, mcpFetchEstimate].filter(
      (metric) => metric !== undefined,
    ),
    limitations: [
      'This command verifies live HTTP only; MCP equality still requires configured MCP tools.',
      'MCP metrics are envelope estimates built from HTTP payloads, not observed MCP transport usage.',
      'The command is read-only and does not update Retrieval Policy.',
    ],
  }, null, 2));
}
