import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiError,
  ApiProtocolError,
  MissingCredentialError,
  RetrievalApiClient,
  parseContract,
  type FetchLike,
} from '../src/api/index.ts';
import { contractExamples } from '../src/api/generated/contracts.ts';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

test('client uses one contract-shaped boundary and the correct credentials', async () => {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const responses = [
    { documents: 2, chunks: 2, sources: 2, policy_version: 1 },
    contractExamples.retrieval_status,
    { configured: false, last_cycle: null, files: {}, worker_recent: false },
    { files: [], total: 0 },
    contractExamples.sources_response,
    contractExamples.search_response,
    contractExamples.document_response,
    contractExamples.policy_response,
    contractExamples.policy_update_response,
  ];
  const fetchFn: FetchLike = async (input, init) => {
    calls.push({ url: String(input), init });
    const response = responses.shift();
    assert.ok(response);
    return jsonResponse(response);
  };
  const client = new RetrievalApiClient({
    baseUrl: 'http://example.test/',
    readToken: 'read-token',
    adminToken: 'admin-token',
    fetchFn,
  });

  await client.getStatus();
  await client.getRetrievalStatus();
  await client.getSyncStatus();
  await client.getSyncFiles({ state: 'failed', limit: 10, offset: 20 });
  await client.getSources();
  await client.search(parseContract('SearchRequest', structuredClone(contractExamples.search_request)));
  await client.getDocument(contractExamples.document_response.id);
  await client.getPolicy();
  await client.updatePolicy(structuredClone(contractExamples.policy_update_request));

  assert.deepEqual(
    calls.map((call) => [call.init?.method, call.url]),
    [
      ['GET', 'http://example.test/api/status'],
      ['GET', 'http://example.test/api/retrieval'],
      ['GET', 'http://example.test/api/sync'],
      ['GET', 'http://example.test/api/sync/files?state=failed&limit=10&offset=20'],
      ['GET', 'http://example.test/api/sources'],
      ['POST', 'http://example.test/api/search'],
      ['GET', `http://example.test/api/documents/${contractExamples.document_response.id}`],
      ['GET', 'http://example.test/api/policy'],
      ['PUT', 'http://example.test/api/policy'],
    ],
  );
  assert.equal((calls[0]?.init?.headers as Record<string, string>).Authorization, 'Bearer read-token');
  assert.equal((calls[8]?.init?.headers as Record<string, string>).Authorization, 'Bearer admin-token');
  assert.deepEqual(
    JSON.parse(String(calls[8]?.init?.body)),
    contractExamples.policy_update_request,
  );
});

for (const status of [401, 403, 404, 409, 413, 422]) {
  test(`client maps ${status} error responses`, async () => {
    const client = new RetrievalApiClient({
      baseUrl: 'http://example.test',
      readToken: 'read-token',
      fetchFn: async () => jsonResponse(contractExamples.error_response, status),
    });

    await assert.rejects(
      client.search(parseContract('SearchRequest', structuredClone(contractExamples.search_request))),
      (error: unknown) =>
        error instanceof ApiError &&
        error.status === status &&
        error.code === 'policy_version_conflict',
    );
  });
}

test('client rejects unknown response fields instead of silently accepting drift', async () => {
  const client = new RetrievalApiClient({
    baseUrl: 'http://example.test',
    readToken: 'read-token',
    fetchFn: async () => jsonResponse({ ...contractExamples.sources_response, unexpected: true }),
  });

  await assert.rejects(
    client.getSources(),
    (error: unknown) => error instanceof ApiProtocolError && error.cause instanceof Error,
  );
});

test('client refuses policy mutation without an admin credential', async () => {
  const client = new RetrievalApiClient({
    baseUrl: 'http://example.test',
    readToken: 'read-token',
    fetchFn: async () => assert.fail('fetch must not be called'),
  });

  await assert.rejects(
    client.updatePolicy(structuredClone(contractExamples.policy_update_request)),
    MissingCredentialError,
  );
});

test('anonymous demo mode omits Authorization for read and policy requests', async () => {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const client = new RetrievalApiClient({
    baseUrl: 'https://demo.example.test',
    readToken: '',
    anonymousDemo: true,
    fetchFn: async (input, init) => {
      calls.push({ url: String(input), init });
      return jsonResponse(
        calls.length === 1
          ? { documents: 2, chunks: 2, sources: 2, policy_version: 1 }
          : contractExamples.policy_update_response,
      );
    },
  });

  await client.getStatus();
  await client.updatePolicy(structuredClone(contractExamples.policy_update_request));

  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal((call.init?.headers as Record<string, string>).Authorization, undefined);
  }
});
