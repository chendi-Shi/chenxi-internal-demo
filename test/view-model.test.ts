import assert from 'node:assert/strict';
import test from 'node:test';

import { contractExamples } from '../src/api/generated/contracts.ts';
import { parseContract } from '../src/api/validation.ts';
import {
  createPolicyUpdate,
  createSearchRequest,
  diffPolicies,
  parseMetadataLines,
  RESEARCH_LENSES,
  rankHits,
  upsertMetadataLine,
} from '../src/ui/view-model.ts';

test('metadata lines become exact-match query metadata', () => {
  const metadata = parseMetadataLines('company=虚构公司\nsector=科技');
  assert.deepEqual({ ...metadata }, { company: '虚构公司', sector: '科技' });
  assert.throws(() => parseMetadataLines('broken-line'), /key=value/);
});

test('document metadata can be reused as an exact next-query filter', () => {
  assert.equal(
    upsertMetadataLine('sector=科技\ncompany=旧公司', 'company', '虚构公司'),
    'sector=科技\ncompany=虚构公司',
  );
  assert.equal(upsertMetadataLine('', 'ticker', 'DEMO'), 'ticker=DEMO');
  assert.throws(() => upsertMetadataLine('', 'company\nsector', '科技'), /单行 key=value/);
  assert.throws(() => upsertMetadataLine('', 'company', '虚构公司\nsector=科技'), /单行 key=value/);
});

test('buy-side research lenses stay concise enough for upstream full-text retrieval', () => {
  assert.deepEqual(
    RESEARCH_LENSES.map((lens) => lens.id),
    ['business-model', 'quarterly-change', 'cash-flow', 'moat', 'risk', 'tracking'],
  );
  assert.ok(RESEARCH_LENSES.every((lens) => lens.query.length > 0 && lens.query.length <= 20));
});

test('search form builds the OpenAPI SearchRequest shape', () => {
  const request = createSearchRequest({
    query: ' 芯片 ',
    source_ids: ['demo_research'],
    since: '',
    until: '',
    metadataText: 'sector=科技',
    limit: 5,
  });
  assert.deepEqual(request, {
    query: '芯片',
    source_ids: ['demo_research'],
    since: '',
    until: '',
    metadata: { sector: '科技' },
    limit: 5,
  });
});

test('policy update always uses the currently loaded version', () => {
  const state = parseContract('PolicyState', structuredClone(contractExamples.policy_response));
  const update = createPolicyUpdate(state, structuredClone(state.policy));
  assert.equal(update.expected_version, state.version);
  assert.deepEqual(update.policy, state.policy);
});

test('policy diff reports analyst inputs without predicting ranking', () => {
  const changes = diffPolicies(
    { source_weights: {}, recency_boost: 0.25, half_life_days: 90, default_limit: 10 },
    { source_weights: { source_a: 2 }, recency_boost: 0.5, half_life_days: 90, default_limit: 5 },
    ['source_a', 'source_b'],
  );
  assert.deepEqual(changes, [
    { field: 'source_weight', sourceId: 'source_a', before: 1, after: 2 },
    { field: 'recency_boost', before: 0.25, after: 0.5 },
    { field: 'default_limit', before: 10, after: 5 },
  ]);
});

test('rank comparison annotates server order without reordering it', () => {
  const before = parseContract('SearchResponse', structuredClone(contractExamples.search_response));
  const after = parseContract('SearchResponse', structuredClone(contractExamples.search_after_policy_change));
  const ranked = rankHits(after, before);

  assert.deepEqual(
    ranked.map((item) => item.hit.id),
    after.results.map((item) => item.id),
  );
  assert.deepEqual(
    ranked.map((item) => item.movement),
    [1, -1],
  );
});
