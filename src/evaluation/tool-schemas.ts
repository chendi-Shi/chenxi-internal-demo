import { contractSchemas } from '../api/generated/contracts.ts';

const searchResultSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['results'],
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'title', 'url'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          url: { type: 'string' },
        },
      },
    },
  },
} as const;

const fetchResultSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'title', 'text', 'url', 'metadata'],
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    text: { type: 'string' },
    url: { type: 'string' },
    metadata: { type: 'object' },
  },
} as const;

export const minimalMcpToolDefinitions = [
  {
    name: 'search',
    description: 'Search the document collection using the saved retrieval policy.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      required: ['query'],
      properties: { query: { type: 'string' } },
    },
    outputSchema: searchResultSchema,
  },
  {
    name: 'fetch',
    description: 'Fetch one document returned by search.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      required: ['id'],
      properties: { id: { type: 'string' } },
    },
    outputSchema: fetchResultSchema,
  },
] as const;

export const allMcpToolDefinitions = [
  ...minimalMcpToolDefinitions,
  {
    name: 'search_documents',
    description: 'Run advanced document search with per-query filters.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      required: ['request'],
      properties: { request: contractSchemas.SearchRequest },
    },
    outputSchema: contractSchemas.SearchResponse,
  },
] as const;
