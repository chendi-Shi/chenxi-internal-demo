export interface RetrievalOrderEvidence {
  result_ids: string[];
  policy_version: number | null;
  source: 'http_search_response' | 'live_http_summary' | 'mcp_search' | 'mcp_search_documents';
}

export interface McpParityResult {
  status: 'passed' | 'failed';
  exact_order_match: boolean;
  same_result_ids: boolean;
  policy_version_check: 'matched' | 'mismatch' | 'not_available';
  http: RetrievalOrderEvidence;
  mcp: RetrievalOrderEvidence;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseJsonText(value: unknown): unknown {
  if (typeof value !== 'string') return undefined;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return undefined;
  }
}

function unwrapMcpPayload(value: unknown): unknown {
  if (!isRecord(value)) return value;
  if ('structuredContent' in value) return value.structuredContent;
  const content = value.content;
  if (Array.isArray(content)) {
    for (const item of content) {
      if (isRecord(item) && item.type === 'text') {
        const parsed = parseJsonText(item.text);
        if (parsed !== undefined) return parsed;
      }
    }
  }
  return value;
}

function resultIds(results: unknown): string[] {
  if (!Array.isArray(results)) throw new Error('Expected a results array');
  return results.map((result, index) => {
    if (!isRecord(result) || typeof result.id !== 'string') {
      throw new Error(`Result ${index + 1} is missing a string id`);
    }
    return result.id;
  });
}

export function parseHttpOrderEvidence(value: unknown): RetrievalOrderEvidence {
  if (!isRecord(value)) throw new Error('HTTP evidence must be a JSON object');
  if (Array.isArray(value.result_ids)) {
    return {
      result_ids: resultIds(value.result_ids.map((id) => ({ id }))),
      policy_version: typeof value.policy_version === 'number' ? value.policy_version : null,
      source: 'live_http_summary',
    };
  }
  return {
    result_ids: resultIds(value.results),
    policy_version: typeof value.policy_version === 'number' ? value.policy_version : null,
    source: 'http_search_response',
  };
}

export function parseMcpOrderEvidence(value: unknown): RetrievalOrderEvidence {
  const payload = unwrapMcpPayload(value);
  if (!isRecord(payload)) throw new Error('MCP evidence must contain a JSON object payload');
  const policyVersion = typeof payload.policy_version === 'number' ? payload.policy_version : null;
  return {
    result_ids: resultIds(payload.results),
    policy_version: policyVersion,
    source: policyVersion === null ? 'mcp_search' : 'mcp_search_documents',
  };
}

export function compareHttpAndMcpOrder(httpValue: unknown, mcpValue: unknown): McpParityResult {
  const http = parseHttpOrderEvidence(httpValue);
  const mcp = parseMcpOrderEvidence(mcpValue);
  const exactOrderMatch =
    http.result_ids.length === mcp.result_ids.length &&
    http.result_ids.every((id, index) => id === mcp.result_ids[index]);
  const sameResultIds =
    http.result_ids.length === mcp.result_ids.length &&
    [...http.result_ids].sort().every((id, index) => id === [...mcp.result_ids].sort()[index]);
  const policyVersionCheck = mcp.policy_version === null || http.policy_version === null
    ? 'not_available'
    : mcp.policy_version === http.policy_version
      ? 'matched'
      : 'mismatch';
  return {
    status: exactOrderMatch && policyVersionCheck !== 'mismatch' ? 'passed' : 'failed',
    exact_order_match: exactOrderMatch,
    same_result_ids: sameResultIds,
    policy_version_check: policyVersionCheck,
    http,
    mcp,
  };
}
