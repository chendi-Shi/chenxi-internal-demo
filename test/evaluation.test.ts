import assert from 'node:assert/strict';
import test from 'node:test';

import { contractExamples } from '../src/api/generated/contracts.ts';
import { evaluatePolicyFixtures } from '../src/evaluation/policy-evaluator.ts';
import { compareHttpAndMcpOrder } from '../src/evaluation/mcp-parity.ts';
import { buildEvaluationReport } from '../src/evaluation/report.ts';
import { makeMcpEnvelope, measureText } from '../src/evaluation/token-metrics.ts';

test('source weight and freshness experiments pass without local reranking', () => {
  const experiments = evaluatePolicyFixtures();
  assert.equal(experiments.find((item) => item.id === 'source_weight')?.status, 'passed');
  assert.equal(experiments.find((item) => item.id === 'freshness')?.status, 'passed');
});

test('metadata filtering remains not_run without a filtered upstream response', () => {
  const experiment = evaluatePolicyFixtures().find((item) => item.id === 'metadata_filter');
  assert.equal(experiment?.status, 'not_run');
  assert.match(experiment?.limitation ?? '', /no filtered SearchResponse/);
});

test('token heuristic separates ASCII and Han characters transparently', () => {
  const metric = measureText('sample', 'abcd芯片');
  assert.equal(metric.ascii_characters, 4);
  assert.equal(metric.han_characters, 2);
  assert.equal(metric.estimated_tokens, 3);
  assert.equal(metric.utf8_bytes, 10);
});

test('MCP envelope includes structured and compatibility text payloads', () => {
  const envelope = makeMcpEnvelope({ results: [] }) as {
    structuredContent: unknown;
    content: Array<{ text: string }>;
  };
  assert.deepEqual(envelope.structuredContent, { results: [] });
  assert.equal(envelope.content[0]?.text, '{"results":[]}');
});

test('evaluation report keeps token categories separate', () => {
  const report = buildEvaluationReport();
  const labels = report.token_metrics.map((metric) => metric.label);
  assert.ok(labels.includes('mcp_search_result_envelope'));
  assert.ok(labels.includes('mcp_fetch_result_envelope'));
  assert.ok(labels.includes('mcp_tool_schemas_search_fetch'));
  assert.equal(report.experiment_summary.failed, 0);
});

test('standard MCP search preserves HTTP result order without inventing a policy version', () => {
  const result = compareHttpAndMcpOrder(
    contractExamples.search_response,
    { structuredContent: contractExamples.mcp_search_response },
  );
  assert.equal(result.status, 'passed');
  assert.equal(result.exact_order_match, true);
  assert.equal(result.policy_version_check, 'not_available');
  assert.equal(result.mcp.source, 'mcp_search');
});

test('advanced MCP search must match both HTTP order and policy version', () => {
  const matching = compareHttpAndMcpOrder(
    contractExamples.search_response,
    contractExamples.search_response,
  );
  assert.equal(matching.status, 'passed');
  assert.equal(matching.policy_version_check, 'matched');

  const mismatched = compareHttpAndMcpOrder(
    contractExamples.search_response,
    contractExamples.search_after_policy_change,
  );
  assert.equal(mismatched.status, 'failed');
  assert.equal(mismatched.same_result_ids, true);
  assert.equal(mismatched.exact_order_match, false);
  assert.equal(mismatched.policy_version_check, 'mismatch');
});
