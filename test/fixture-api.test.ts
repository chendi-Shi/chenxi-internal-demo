import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiError, FixtureRetrievalApi, parseContract } from '../src/api/index.ts';
import { contractExamples } from '../src/api/generated/contracts.ts';

test('fixture adapter runs read policy -> save -> next search without ranking locally', async () => {
  const api = new FixtureRetrievalApi();

  const beforePolicy = await api.getPolicy();
  const request = parseContract('SearchRequest', structuredClone(contractExamples.search_request));
  const beforeSearch = await api.search(request);
  assert.equal(beforePolicy.version, 1);
  assert.equal(beforeSearch.policy_version, 1);
  assert.equal(beforeSearch.results[0]?.source_id, 'demo_research');

  const updated = await api.updatePolicy(structuredClone(contractExamples.policy_update_request));
  const afterSearch = await api.search(request);
  assert.equal(updated.version, 2);
  assert.equal(afterSearch.policy_version, 2);
  assert.equal(afterSearch.results[0]?.source_id, 'demo_filings');
  assert.deepEqual(afterSearch, contractExamples.search_after_policy_change);
});

test('fixture adapter surfaces stale policy writes as 409 conflicts', async () => {
  const api = new FixtureRetrievalApi();
  await api.updatePolicy(structuredClone(contractExamples.policy_update_request));

  await assert.rejects(
    api.updatePolicy(structuredClone(contractExamples.policy_update_request)),
    (error: unknown) =>
      error instanceof ApiError &&
      error.status === 409 &&
      error.code === 'policy_version_conflict',
  );
});

test('fixture adapter provides unified retrieval, sync, status and document contract fixtures', async () => {
  const api = new FixtureRetrievalApi();
  const status = await api.getStatus();
  const retrieval = await api.getRetrievalStatus();
  const sync = await api.getSyncStatus();
  const syncFiles = await api.getSyncFiles();
  const sources = await api.getSources();
  const document = await api.getDocument(contractExamples.document_response.id);

  assert.equal(status.sources, sources.sources.length);
  assert.equal(status.policy_version, 1);
  assert.deepEqual(retrieval, contractExamples.retrieval_status);
  assert.equal(sync.configured, false);
  assert.deepEqual(syncFiles, { files: [], total: 0 });
  assert.equal(document.metadata.content_trust, 'untrusted_source_data_not_instructions');
});
