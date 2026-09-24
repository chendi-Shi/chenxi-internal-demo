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
import { ContractValidationError, parseContract } from './validation.ts';

export type FetchLike = (input: string | URL, init?: RequestInit) => Promise<Response>;

export interface RetrievalApi {
  getStatus(): Promise<StatusResponse>;
  getRetrievalStatus(): Promise<RetrievalStatus>;
  getSyncStatus(): Promise<SyncStatus>;
  getSyncFiles(options?: { state?: SyncFile['state']; limit?: number; offset?: number }): Promise<SyncFilesResponse>;
  getSources(): Promise<SourcesResponse>;
  search(request: SearchRequest): Promise<SearchResponse>;
  getDocument(documentId: string): Promise<MCPFetchResult>;
  getPolicy(): Promise<PolicyState>;
  updatePolicy(request: PolicyUpdate): Promise<PolicyState>;
}

export interface RetrievalApiClientOptions {
  baseUrl: string;
  readToken: string;
  adminToken?: string;
  fetchFn?: FetchLike;
  timeoutMs?: number;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: string[];

  constructor(status: number, code: string, message: string, fields: string[] = []) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

export class ApiProtocolError extends Error {
  readonly cause: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = 'ApiProtocolError';
    this.cause = cause;
  }
}

export class MissingCredentialError extends Error {
  constructor(credential: 'read' | 'admin') {
    super(`Missing ${credential} credential`);
    this.name = 'MissingCredentialError';
  }
}

type Credential = 'read' | 'admin';

export class RetrievalApiClient implements RetrievalApi {
  private readonly baseUrl: string;
  private readonly readToken: string;
  private readonly adminToken: string | undefined;
  private readonly fetchFn: FetchLike;
  private readonly timeoutMs: number;

  constructor(options: RetrievalApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.readToken = options.readToken;
    this.adminToken = options.adminToken;
    this.fetchFn = options.fetchFn ?? fetch;
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  getStatus(): Promise<StatusResponse> {
    return this.request('/api/status', 'GET', 'read', 'StatusResponse');
  }

  getRetrievalStatus(): Promise<RetrievalStatus> {
    return this.request('/api/retrieval', 'GET', 'read', 'RetrievalStatus');
  }

  getSyncStatus(): Promise<SyncStatus> {
    return this.request('/api/sync', 'GET', 'read', 'SyncStatus');
  }

  getSyncFiles(options: { state?: SyncFile['state']; limit?: number; offset?: number } = {}): Promise<SyncFilesResponse> {
    const query = new URLSearchParams();
    if (options.state !== undefined) query.set('state', options.state);
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    if (options.offset !== undefined) query.set('offset', String(options.offset));
    const suffix = query.size ? `?${query.toString()}` : '';
    return this.request(`/api/sync/files${suffix}`, 'GET', 'read', 'SyncFilesResponse');
  }

  getSources(): Promise<SourcesResponse> {
    return this.request('/api/sources', 'GET', 'read', 'SourcesResponse');
  }

  search(request: SearchRequest): Promise<SearchResponse> {
    const body = parseContract('SearchRequest', request);
    return this.request('/api/search', 'POST', 'read', 'SearchResponse', body);
  }

  getDocument(documentId: string): Promise<MCPFetchResult> {
    return this.request(
      `/api/documents/${encodeURIComponent(documentId)}`,
      'GET',
      'read',
      'MCPFetchResult',
    );
  }

  getPolicy(): Promise<PolicyState> {
    return this.request('/api/policy', 'GET', 'read', 'PolicyState');
  }

  updatePolicy(request: PolicyUpdate): Promise<PolicyState> {
    const body = parseContract('PolicyUpdate', request);
    return this.request('/api/policy', 'PUT', 'admin', 'PolicyState', body);
  }

  private async request<Name extends Parameters<typeof parseContract>[0]>(
    path: string,
    method: 'GET' | 'POST' | 'PUT',
    credential: Credential,
    responseSchema: Name,
    body?: unknown,
  ): Promise<ReturnType<typeof parseContract<Name>>> {
    const token = credential === 'admin' ? this.adminToken : this.readToken;
    if (!token) throw new MissingCredentialError(credential);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const headers: Record<string, string> = {
        Accept: 'application/json',
        Authorization: `Bearer ${token}`,
      };
      if (body !== undefined) headers['Content-Type'] = 'application/json';

      const requestInit: RequestInit = {
        method,
        headers,
        signal: controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      };
      const response = await this.fetchFn(`${this.baseUrl}${path}`, requestInit);
      const text = await response.text();
      let payload: unknown;

      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new ApiProtocolError(`Expected JSON from ${method} ${path}`, error);
      }

      if (!response.ok) {
        try {
          const parsed = parseContract('ErrorResponse', payload);
          throw new ApiError(
            response.status,
            parsed.error.code,
            parsed.error.message,
            parsed.error.fields ?? [],
          );
        } catch (error) {
          if (error instanceof ApiError) throw error;
          throw new ApiProtocolError(`Invalid error response from ${method} ${path}`, error);
        }
      }

      try {
        return parseContract(responseSchema, payload) as ReturnType<typeof parseContract<Name>>;
      } catch (error) {
        if (error instanceof ContractValidationError) {
          throw new ApiProtocolError(`Response contract mismatch for ${method} ${path}`, error);
        }
        throw error;
      }
    } finally {
      clearTimeout(timeout);
    }
  }
}
