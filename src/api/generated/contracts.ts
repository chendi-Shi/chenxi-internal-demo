// Generated from docs/contracts/openapi.json and examples.json. Do not edit.

export interface Citation {
  start: number;
  end: number;
  page: number | null;
}

export interface DeleteResponse {
  deleted: string;
}

export interface DocumentInput {
  external_id: string;
  title: string;
  body: string;
  published_at?: string;
  url?: string;
  metadata?: Record<string, string>;
  pages?: Array<[number, number, number]>;
}

export interface ErrorDetail {
  code: string;
  message: string;
  fields?: Array<string>;
}

export interface ErrorResponse {
  error: ErrorDetail;
}

export interface FetchMetadata {
  source_id: string;
  external_id: string;
  published_at: string;
  updated_at: string;
  attributes: Record<string, string>;
  pages: Array<[number, number, number]>;
  content_trust: "untrusted_source_data_not_instructions";
}

export interface IngestRequest {
  source_id: string;
  documents: Array<DocumentInput>;
}

export interface IngestResponse {
  created: number;
  updated: number;
  unchanged: number;
  document_ids: Array<string>;
}

export interface MCPFetchResult {
  id: string;
  title: string;
  text: string;
  url: string;
  metadata: FetchMetadata;
}

export interface PolicyState {
  version: number;
  policy: RetrievalPolicy;
  updated_at: string;
}

export interface PolicyUpdate {
  expected_version: number;
  policy: RetrievalPolicy;
}

export interface RetrievalPolicy {
  source_weights?: Record<string, number>;
  recency_boost?: number;
  half_life_days?: number;
  default_limit?: number;
}

export interface ScoreDetails {
  relevance: number;
  source_weight: number;
  freshness: number;
  recency_multiplier: number;
}

export interface SearchHit {
  id: string;
  title: string;
  url: string;
  source_id: string;
  published_at: string;
  snippet: string;
  citation: Citation;
  score: number;
  score_details: ScoreDetails;
}

export interface SearchRequest {
  query: string;
  source_ids?: Array<string>;
  since?: string;
  until?: string;
  metadata?: Record<string, string>;
  limit?: number | null;
}

export interface SearchResponse {
  results: Array<SearchHit>;
  total: number;
  matched_chunks: number;
  policy_version: number;
  elapsed_ms: number;
}

export interface SourceDefinition {
  id: string;
  name: string;
  kind?: "local" | "api" | "sharepoint" | "database" | "demo";
  enabled?: boolean;
}

export interface SourceStatus {
  id: string;
  name: string;
  kind?: "local" | "api" | "sharepoint" | "database" | "demo";
  enabled?: boolean;
  document_count: number;
  updated_at: string;
}

export interface SourcesResponse {
  sources: Array<SourceStatus>;
}

export interface StatusResponse {
  documents: number;
  chunks: number;
  sources: number;
  policy_version: number;
}

export interface ContractTypeMap {
  Citation: Citation;
  DeleteResponse: DeleteResponse;
  DocumentInput: DocumentInput;
  ErrorDetail: ErrorDetail;
  ErrorResponse: ErrorResponse;
  FetchMetadata: FetchMetadata;
  IngestRequest: IngestRequest;
  IngestResponse: IngestResponse;
  MCPFetchResult: MCPFetchResult;
  PolicyState: PolicyState;
  PolicyUpdate: PolicyUpdate;
  RetrievalPolicy: RetrievalPolicy;
  ScoreDetails: ScoreDetails;
  SearchHit: SearchHit;
  SearchRequest: SearchRequest;
  SearchResponse: SearchResponse;
  SourceDefinition: SourceDefinition;
  SourceStatus: SourceStatus;
  SourcesResponse: SourcesResponse;
  StatusResponse: StatusResponse;
}

export type ContractSchemaName = keyof ContractTypeMap;

export const contractSchemas = {
  "Citation": {
    "properties": {
      "start": {
        "type": "integer",
        "title": "Start"
      },
      "end": {
        "type": "integer",
        "title": "End"
      },
      "page": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "Page"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "start",
      "end",
      "page"
    ],
    "title": "Citation"
  },
  "DeleteResponse": {
    "properties": {
      "deleted": {
        "type": "string",
        "title": "Deleted"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "deleted"
    ],
    "title": "DeleteResponse"
  },
  "DocumentInput": {
    "properties": {
      "external_id": {
        "type": "string",
        "maxLength": 500,
        "minLength": 1,
        "title": "External Id"
      },
      "title": {
        "type": "string",
        "maxLength": 500,
        "minLength": 1,
        "title": "Title"
      },
      "body": {
        "type": "string",
        "maxLength": 1000000,
        "minLength": 1,
        "title": "Body"
      },
      "published_at": {
        "type": "string",
        "title": "Published At",
        "default": ""
      },
      "url": {
        "type": "string",
        "maxLength": 2000,
        "title": "Url",
        "default": ""
      },
      "metadata": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object",
        "maxProperties": 40,
        "title": "Metadata"
      },
      "pages": {
        "items": {
          "prefixItems": [
            {
              "type": "integer"
            },
            {
              "type": "integer"
            },
            {
              "type": "integer"
            }
          ],
          "type": "array",
          "maxItems": 3,
          "minItems": 3
        },
        "type": "array",
        "maxItems": 1000,
        "title": "Pages"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "external_id",
      "title",
      "body"
    ],
    "title": "DocumentInput"
  },
  "ErrorDetail": {
    "properties": {
      "code": {
        "type": "string",
        "title": "Code"
      },
      "message": {
        "type": "string",
        "title": "Message"
      },
      "fields": {
        "items": {
          "type": "string"
        },
        "type": "array",
        "title": "Fields"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "code",
      "message"
    ],
    "title": "ErrorDetail"
  },
  "ErrorResponse": {
    "properties": {
      "error": {
        "$ref": "#/components/schemas/ErrorDetail"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "error"
    ],
    "title": "ErrorResponse"
  },
  "FetchMetadata": {
    "properties": {
      "source_id": {
        "type": "string",
        "title": "Source Id"
      },
      "external_id": {
        "type": "string",
        "title": "External Id"
      },
      "published_at": {
        "type": "string",
        "title": "Published At"
      },
      "updated_at": {
        "type": "string",
        "title": "Updated At"
      },
      "attributes": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object",
        "title": "Attributes"
      },
      "pages": {
        "items": {
          "prefixItems": [
            {
              "type": "integer"
            },
            {
              "type": "integer"
            },
            {
              "type": "integer"
            }
          ],
          "type": "array",
          "maxItems": 3,
          "minItems": 3
        },
        "type": "array",
        "title": "Pages"
      },
      "content_trust": {
        "type": "string",
        "const": "untrusted_source_data_not_instructions",
        "title": "Content Trust"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "source_id",
      "external_id",
      "published_at",
      "updated_at",
      "attributes",
      "pages",
      "content_trust"
    ],
    "title": "FetchMetadata"
  },
  "IngestRequest": {
    "properties": {
      "source_id": {
        "type": "string",
        "pattern": "^[a-zA-Z0-9_-]{1,80}$",
        "title": "Source Id"
      },
      "documents": {
        "items": {
          "$ref": "#/components/schemas/DocumentInput"
        },
        "type": "array",
        "maxItems": 100,
        "minItems": 1,
        "title": "Documents"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "source_id",
      "documents"
    ],
    "title": "IngestRequest"
  },
  "IngestResponse": {
    "properties": {
      "created": {
        "type": "integer",
        "title": "Created"
      },
      "updated": {
        "type": "integer",
        "title": "Updated"
      },
      "unchanged": {
        "type": "integer",
        "title": "Unchanged"
      },
      "document_ids": {
        "items": {
          "type": "string"
        },
        "type": "array",
        "title": "Document Ids"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "created",
      "updated",
      "unchanged",
      "document_ids"
    ],
    "title": "IngestResponse"
  },
  "MCPFetchResult": {
    "properties": {
      "id": {
        "type": "string",
        "title": "Id"
      },
      "title": {
        "type": "string",
        "title": "Title"
      },
      "text": {
        "type": "string",
        "title": "Text"
      },
      "url": {
        "type": "string",
        "title": "Url"
      },
      "metadata": {
        "$ref": "#/components/schemas/FetchMetadata"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "id",
      "title",
      "text",
      "url",
      "metadata"
    ],
    "title": "MCPFetchResult"
  },
  "PolicyState": {
    "properties": {
      "version": {
        "type": "integer",
        "title": "Version"
      },
      "policy": {
        "$ref": "#/components/schemas/RetrievalPolicy"
      },
      "updated_at": {
        "type": "string",
        "title": "Updated At"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "version",
      "policy",
      "updated_at"
    ],
    "title": "PolicyState"
  },
  "PolicyUpdate": {
    "properties": {
      "expected_version": {
        "type": "integer",
        "minimum": 1,
        "title": "Expected Version"
      },
      "policy": {
        "$ref": "#/components/schemas/RetrievalPolicy"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "expected_version",
      "policy"
    ],
    "title": "PolicyUpdate"
  },
  "RetrievalPolicy": {
    "properties": {
      "source_weights": {
        "patternProperties": {
          "^[a-zA-Z0-9_-]{1,80}$": {
            "type": "number",
            "maximum": 10,
            "minimum": 0
          }
        },
        "type": "object",
        "maxProperties": 200,
        "title": "Source Weights"
      },
      "recency_boost": {
        "type": "number",
        "maximum": 3,
        "minimum": 0,
        "title": "Recency Boost",
        "default": 0.25
      },
      "half_life_days": {
        "type": "number",
        "maximum": 3650,
        "minimum": 1,
        "title": "Half Life Days",
        "default": 90
      },
      "default_limit": {
        "type": "integer",
        "maximum": 50,
        "minimum": 1,
        "title": "Default Limit",
        "default": 10
      }
    },
    "additionalProperties": false,
    "type": "object",
    "title": "RetrievalPolicy"
  },
  "ScoreDetails": {
    "properties": {
      "relevance": {
        "type": "number",
        "title": "Relevance"
      },
      "source_weight": {
        "type": "number",
        "title": "Source Weight"
      },
      "freshness": {
        "type": "number",
        "title": "Freshness"
      },
      "recency_multiplier": {
        "type": "number",
        "title": "Recency Multiplier"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "relevance",
      "source_weight",
      "freshness",
      "recency_multiplier"
    ],
    "title": "ScoreDetails"
  },
  "SearchHit": {
    "properties": {
      "id": {
        "type": "string",
        "title": "Id"
      },
      "title": {
        "type": "string",
        "title": "Title"
      },
      "url": {
        "type": "string",
        "title": "Url"
      },
      "source_id": {
        "type": "string",
        "title": "Source Id"
      },
      "published_at": {
        "type": "string",
        "title": "Published At"
      },
      "snippet": {
        "type": "string",
        "title": "Snippet"
      },
      "citation": {
        "$ref": "#/components/schemas/Citation"
      },
      "score": {
        "type": "number",
        "title": "Score"
      },
      "score_details": {
        "$ref": "#/components/schemas/ScoreDetails"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "id",
      "title",
      "url",
      "source_id",
      "published_at",
      "snippet",
      "citation",
      "score",
      "score_details"
    ],
    "title": "SearchHit"
  },
  "SearchRequest": {
    "properties": {
      "query": {
        "type": "string",
        "maxLength": 500,
        "minLength": 1,
        "title": "Query"
      },
      "source_ids": {
        "items": {
          "type": "string",
          "pattern": "^[a-zA-Z0-9_-]{1,80}$"
        },
        "type": "array",
        "maxItems": 200,
        "title": "Source Ids"
      },
      "since": {
        "type": "string",
        "title": "Since",
        "default": ""
      },
      "until": {
        "type": "string",
        "title": "Until",
        "default": ""
      },
      "metadata": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object",
        "maxProperties": 20,
        "title": "Metadata"
      },
      "limit": {
        "anyOf": [
          {
            "type": "integer",
            "maximum": 50,
            "minimum": 1
          },
          {
            "type": "null"
          }
        ],
        "title": "Limit"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "query"
    ],
    "title": "SearchRequest"
  },
  "SearchResponse": {
    "properties": {
      "results": {
        "items": {
          "$ref": "#/components/schemas/SearchHit"
        },
        "type": "array",
        "title": "Results"
      },
      "total": {
        "type": "integer",
        "title": "Total"
      },
      "matched_chunks": {
        "type": "integer",
        "title": "Matched Chunks"
      },
      "policy_version": {
        "type": "integer",
        "title": "Policy Version"
      },
      "elapsed_ms": {
        "type": "number",
        "title": "Elapsed Ms"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "results",
      "total",
      "matched_chunks",
      "policy_version",
      "elapsed_ms"
    ],
    "title": "SearchResponse"
  },
  "SourceDefinition": {
    "properties": {
      "id": {
        "type": "string",
        "pattern": "^[a-zA-Z0-9_-]{1,80}$",
        "title": "Id"
      },
      "name": {
        "type": "string",
        "maxLength": 200,
        "minLength": 1,
        "title": "Name"
      },
      "kind": {
        "type": "string",
        "enum": [
          "local",
          "api",
          "sharepoint",
          "database",
          "demo"
        ],
        "title": "Kind",
        "default": "api"
      },
      "enabled": {
        "type": "boolean",
        "title": "Enabled",
        "default": true
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "id",
      "name"
    ],
    "title": "SourceDefinition"
  },
  "SourceStatus": {
    "properties": {
      "id": {
        "type": "string",
        "pattern": "^[a-zA-Z0-9_-]{1,80}$",
        "title": "Id"
      },
      "name": {
        "type": "string",
        "maxLength": 200,
        "minLength": 1,
        "title": "Name"
      },
      "kind": {
        "type": "string",
        "enum": [
          "local",
          "api",
          "sharepoint",
          "database",
          "demo"
        ],
        "title": "Kind",
        "default": "api"
      },
      "enabled": {
        "type": "boolean",
        "title": "Enabled",
        "default": true
      },
      "document_count": {
        "type": "integer",
        "title": "Document Count"
      },
      "updated_at": {
        "type": "string",
        "title": "Updated At"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "id",
      "name",
      "document_count",
      "updated_at"
    ],
    "title": "SourceStatus"
  },
  "SourcesResponse": {
    "properties": {
      "sources": {
        "items": {
          "$ref": "#/components/schemas/SourceStatus"
        },
        "type": "array",
        "title": "Sources"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "sources"
    ],
    "title": "SourcesResponse"
  },
  "StatusResponse": {
    "properties": {
      "documents": {
        "type": "integer",
        "title": "Documents"
      },
      "chunks": {
        "type": "integer",
        "title": "Chunks"
      },
      "sources": {
        "type": "integer",
        "title": "Sources"
      },
      "policy_version": {
        "type": "integer",
        "title": "Policy Version"
      }
    },
    "additionalProperties": false,
    "type": "object",
    "required": [
      "documents",
      "chunks",
      "sources",
      "policy_version"
    ],
    "title": "StatusResponse"
  }
} as const;

export const contractExamples = {
  "note": "虚构联调样例，不是内部投研数据。耗时置零，日期和评分仅作示例。",
  "sources_response": {
    "sources": [
      {
        "id": "demo_filings",
        "name": "虚构样例 · 公司公告",
        "kind": "demo",
        "enabled": true,
        "updated_at": "2026-09-22T03:03:38+00:00",
        "document_count": 1
      },
      {
        "id": "demo_research",
        "name": "虚构样例 · 研究纪要",
        "kind": "demo",
        "enabled": true,
        "updated_at": "2026-09-22T03:03:38+00:00",
        "document_count": 1
      }
    ]
  },
  "search_request": {
    "query": "芯片",
    "source_ids": [],
    "since": "",
    "until": "",
    "metadata": {},
    "limit": 10
  },
  "search_response": {
    "results": [
      {
        "id": "eb72a59f140664e38ac656cc5f14f770",
        "title": "虚构样例 · 研究纪要：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/eb72a59f140664e38ac656cc5f14f770",
        "source_id": "demo_research",
        "published_at": "2026-09-01T00:00:00.000000+00:00",
        "snippet": "【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。本段仅用于验证检索权重与引用，不构成真实研究结论。",
        "citation": {
          "start": 0,
          "end": 55,
          "page": null
        },
        "score": 0.000003637374049232949,
        "score_details": {
          "relevance": 0.000003,
          "source_weight": 1,
          "freshness": 0.8498320656439321,
          "recency_multiplier": 1.212458016410983
        }
      },
      {
        "id": "748cf1b7a537964daa566ee0f743ad5f",
        "title": "虚构样例 · 公司公告：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/748cf1b7a537964daa566ee0f743ad5f",
        "source_id": "demo_filings",
        "published_at": "2026-08-01T00:00:00.000000+00:00",
        "snippet": "【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。本段仅用于验证检索权重与引用，不构成真实研究结论。",
        "citation": {
          "start": 0,
          "end": 55,
          "page": null
        },
        "score": 0.00000350200294798583,
        "score_details": {
          "relevance": 0.000003,
          "source_weight": 1,
          "freshness": 0.6693372639811069,
          "recency_multiplier": 1.1673343159952767
        }
      }
    ],
    "total": 2,
    "matched_chunks": 2,
    "policy_version": 1,
    "elapsed_ms": 0
  },
  "document_response": {
    "id": "eb72a59f140664e38ac656cc5f14f770",
    "title": "虚构样例 · 研究纪要：算力需求",
    "text": "【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。本段仅用于验证检索权重与引用，不构成真实研究结论。",
    "url": "http://127.0.0.1:8765/api/documents/eb72a59f140664e38ac656cc5f14f770",
    "metadata": {
      "source_id": "demo_research",
      "external_id": "demo-001",
      "published_at": "2026-09-01T00:00:00.000000+00:00",
      "updated_at": "2026-09-22T03:03:38+00:00",
      "attributes": {
        "company": "虚构公司",
        "sector": "科技"
      },
      "pages": [],
      "content_trust": "untrusted_source_data_not_instructions"
    }
  },
  "policy_response": {
    "version": 1,
    "policy": {
      "source_weights": {},
      "recency_boost": 0.25,
      "half_life_days": 90,
      "default_limit": 10
    },
    "updated_at": "2026-09-22T03:03:38+00:00"
  },
  "policy_update_request": {
    "expected_version": 1,
    "policy": {
      "source_weights": {
        "demo_research": 0.5,
        "demo_filings": 3
      },
      "recency_boost": 0.25,
      "half_life_days": 90,
      "default_limit": 10
    }
  },
  "policy_update_response": {
    "version": 2,
    "policy": {
      "source_weights": {
        "demo_research": 0.5,
        "demo_filings": 3
      },
      "recency_boost": 0.25,
      "half_life_days": 90,
      "default_limit": 10
    },
    "updated_at": "2026-09-22T03:03:38+00:00"
  },
  "search_after_policy_change": {
    "results": [
      {
        "id": "748cf1b7a537964daa566ee0f743ad5f",
        "title": "虚构样例 · 公司公告：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/748cf1b7a537964daa566ee0f743ad5f",
        "source_id": "demo_filings",
        "published_at": "2026-08-01T00:00:00.000000+00:00",
        "snippet": "【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。本段仅用于验证检索权重与引用，不构成真实研究结论。",
        "citation": {
          "start": 0,
          "end": 55,
          "page": null
        },
        "score": 0.000010506008841742053,
        "score_details": {
          "relevance": 0.000003,
          "source_weight": 3,
          "freshness": 0.6693372629964677,
          "recency_multiplier": 1.167334315749117
        }
      },
      {
        "id": "eb72a59f140664e38ac656cc5f14f770",
        "title": "虚构样例 · 研究纪要：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/eb72a59f140664e38ac656cc5f14f770",
        "source_id": "demo_research",
        "published_at": "2026-09-01T00:00:00.000000+00:00",
        "snippet": "【虚构测试数据】算力需求持续增长，数据中心建设推动芯片采购。本段仅用于验证检索权重与引用，不构成真实研究结论。",
        "citation": {
          "start": 0,
          "end": 55,
          "page": null
        },
        "score": 0.000001818687024147665,
        "score_details": {
          "relevance": 0.000003,
          "source_weight": 0.5,
          "freshness": 0.8498320643937731,
          "recency_multiplier": 1.2124580160984433
        }
      }
    ],
    "total": 2,
    "matched_chunks": 2,
    "policy_version": 2,
    "elapsed_ms": 0
  },
  "mcp_search_request": {
    "query": "芯片"
  },
  "mcp_search_response": {
    "results": [
      {
        "id": "eb72a59f140664e38ac656cc5f14f770",
        "title": "虚构样例 · 研究纪要：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/eb72a59f140664e38ac656cc5f14f770"
      },
      {
        "id": "748cf1b7a537964daa566ee0f743ad5f",
        "title": "虚构样例 · 公司公告：算力需求",
        "url": "http://127.0.0.1:8765/api/documents/748cf1b7a537964daa566ee0f743ad5f"
      }
    ]
  },
  "mcp_advanced_request": {
    "request": {
      "query": "芯片",
      "source_ids": [],
      "since": "",
      "until": "",
      "metadata": {},
      "limit": 10
    }
  },
  "source_upsert_request": {
    "id": "internal_notes",
    "name": "内部纪要",
    "kind": "api",
    "enabled": true
  },
  "ingest_request": {
    "source_id": "internal_notes",
    "documents": [
      {
        "external_id": "record-001",
        "title": "虚构导入样例",
        "body": "【虚构】数据中心与芯片需求研究。",
        "published_at": "2026-09-01T00:00:00Z",
        "url": "",
        "metadata": {
          "company": "虚构公司"
        },
        "pages": []
      }
    ]
  },
  "error_response": {
    "error": {
      "code": "policy_version_conflict",
      "message": "policy_version_conflict",
      "fields": []
    }
  }
} as const;
