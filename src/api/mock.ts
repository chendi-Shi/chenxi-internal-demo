import { ApiError, type RetrievalApi } from './client.ts';
import { contractExamples } from './generated/contracts.ts';
import type {
  MCPFetchResult,
  PolicyState,
  PolicyUpdate,
  RetrievalStatus,
  SearchRequest,
  SearchResponse,
  SourcesResponse,
  StatusResponse,
  SyncFilesResponse,
  SyncFile,
  SyncStatus,
} from './generated/contracts.ts';
import { parseContract } from './validation.ts';

export class UnsupportedMockFixtureError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UnsupportedMockFixtureError';
  }
}

function cloneFixture<Name extends Parameters<typeof parseContract>[0]>(
  schemaName: Name,
  value: unknown,
): ReturnType<typeof parseContract<Name>> {
  return parseContract(schemaName, structuredClone(value)) as ReturnType<typeof parseContract<Name>>;
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export class FixtureRetrievalApi implements RetrievalApi {
  private policyState: PolicyState;

  constructor() {
    this.policyState = cloneFixture('PolicyState', contractExamples.policy_response);
  }

  async getStatus(): Promise<StatusResponse> {
    return parseContract('StatusResponse', {
      documents: contractExamples.sources_response.sources.reduce(
        (sum, source) => sum + source.document_count,
        0,
      ),
      chunks: contractExamples.search_response.matched_chunks,
      sources: contractExamples.sources_response.sources.length,
      policy_version: this.policyState.version,
    });
  }

  async getRetrievalStatus(): Promise<RetrievalStatus> {
    return cloneFixture('RetrievalStatus', contractExamples.retrieval_status);
  }

  async getSyncStatus(): Promise<SyncStatus> {
    return parseContract('SyncStatus', {
      configured: false,
      last_cycle: null,
      files: {},
      worker_recent: false,
    });
  }

  async getSyncFiles(_options: { state?: SyncFile['state']; limit?: number; offset?: number } = {}): Promise<SyncFilesResponse> {
    return parseContract('SyncFilesResponse', { files: [], total: 0 });
  }

  async getSources(): Promise<SourcesResponse> {
    return cloneFixture('SourcesResponse', contractExamples.sources_response);
  }

  async search(request: SearchRequest): Promise<SearchResponse> {
    const parsed = parseContract('SearchRequest', request);
    if (parsed.query !== contractExamples.search_request.query) {
      throw new UnsupportedMockFixtureError('Only the contract example query is available in the fixture adapter');
    }

    if (this.policyState.version === contractExamples.policy_response.version) {
      return cloneFixture('SearchResponse', contractExamples.search_response);
    }
    if (this.policyState.version === contractExamples.policy_update_response.version) {
      return cloneFixture('SearchResponse', contractExamples.search_after_policy_change);
    }
    throw new UnsupportedMockFixtureError('No search fixture exists for the current policy version');
  }

  async getDocument(documentId: string): Promise<MCPFetchResult> {
    if (documentId !== contractExamples.document_response.id) {
      throw new ApiError(404, 'document_not_found', 'document_not_found');
    }
    return cloneFixture('MCPFetchResult', contractExamples.document_response);
  }

  async getPolicy(): Promise<PolicyState> {
    return structuredClone(this.policyState);
  }

  async updatePolicy(request: PolicyUpdate): Promise<PolicyState> {
    const parsed = parseContract('PolicyUpdate', request);
    if (parsed.expected_version !== this.policyState.version) {
      throw new ApiError(409, 'policy_version_conflict', 'policy_version_conflict');
    }
    if (!sameJson(parsed, contractExamples.policy_update_request)) {
      throw new UnsupportedMockFixtureError('Only the contract policy update example is available');
    }

    this.policyState = cloneFixture('PolicyState', contractExamples.policy_update_response);
    return structuredClone(this.policyState);
  }
}
