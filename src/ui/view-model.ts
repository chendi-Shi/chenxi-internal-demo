import type {
  PolicyState,
  PolicyUpdate,
  RetrievalPolicy,
  SearchHit,
  SearchRequest,
  SearchResponse,
} from '../api/generated/contracts.ts';
import { parseContract } from '../api/validation.ts';

export interface SearchFormValue {
  query: string;
  source_ids: string[];
  since: string;
  until: string;
  metadataText: string;
  limit: number;
}

export interface RankedHit {
  hit: SearchHit;
  currentRank: number;
  previousRank: number | null;
  movement: number | null;
}

export interface ResearchLens {
  id: string;
  label: string;
  query: string;
}

export interface PolicyChange {
  field: 'source_weight' | 'recency_boost' | 'half_life_days' | 'default_limit';
  sourceId?: string;
  before: number;
  after: number;
}

export const RESEARCH_LENSES: readonly ResearchLens[] = [
  { id: 'business-model', label: '商业模式', query: '商业模式' },
  { id: 'quarterly-change', label: '八季度变化', query: '季度变化' },
  { id: 'cash-flow', label: '利润与现金流', query: '现金流' },
  { id: 'moat', label: '竞争力', query: '竞争力' },
  { id: 'risk', label: '风险', query: '风险' },
  { id: 'tracking', label: '跟踪指标', query: '管理层指引' },
] as const;

export function clonePolicy(policy: RetrievalPolicy): RetrievalPolicy {
  return structuredClone(policy);
}

export function diffPolicies(
  current: RetrievalPolicy,
  draft: RetrievalPolicy,
  sourceIds: string[],
): PolicyChange[] {
  const changes: PolicyChange[] = [];
  for (const sourceId of sourceIds) {
    const before = current.source_weights?.[sourceId] ?? 1;
    const after = draft.source_weights?.[sourceId] ?? 1;
    if (before !== after) changes.push({ field: 'source_weight', sourceId, before, after });
  }

  const numericFields = [
    ['recency_boost', current.recency_boost ?? 0.25, draft.recency_boost ?? 0.25],
    ['half_life_days', current.half_life_days ?? 90, draft.half_life_days ?? 90],
    ['default_limit', current.default_limit ?? 10, draft.default_limit ?? 10],
  ] as const;
  for (const [field, before, after] of numericFields) {
    if (before !== after) changes.push({ field, before, after });
  }
  return changes;
}

export function createPolicyUpdate(state: PolicyState, policy: RetrievalPolicy): PolicyUpdate {
  return parseContract('PolicyUpdate', {
    expected_version: state.version,
    policy: structuredClone(policy),
  });
}

export function parseMetadataLines(input: string): Record<string, string> {
  const entries = new Map<string, string>();
  const lines = input.split(/\r?\n/);

  for (const [index, rawLine] of lines.entries()) {
    const line = rawLine.trim();
    if (!line) continue;
    const separator = line.indexOf('=');
    if (separator <= 0) throw new Error(`Metadata 第 ${index + 1} 行必须使用 key=value`);
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (!key) throw new Error(`Metadata 第 ${index + 1} 行缺少 key`);
    entries.set(key, value);
  }

  return Object.fromEntries(entries);
}

export function upsertMetadataLine(input: string, key: string, value: string): string {
  if (!key.trim() || /[=\r\n]/.test(key) || /[\r\n]/.test(value)) {
    throw new Error('Metadata 建议必须是单行 key=value');
  }
  const entries = parseMetadataLines(input);
  entries[key.trim()] = value.trim();
  return Object.entries(entries).map(([entryKey, entryValue]) => `${entryKey}=${entryValue}`).join('\n');
}

export function createSearchRequest(value: SearchFormValue): SearchRequest {
  return parseContract('SearchRequest', {
    query: value.query.trim(),
    source_ids: value.source_ids,
    since: value.since,
    until: value.until,
    metadata: parseMetadataLines(value.metadataText),
    limit: value.limit,
  });
}

export function rankHits(current: SearchResponse, previous?: SearchResponse): RankedHit[] {
  const previousRanks = new Map(
    previous?.results.map((hit, index) => [hit.id, index + 1]) ?? [],
  );

  return current.results.map((hit, index) => {
    const currentRank = index + 1;
    const previousRank = previousRanks.get(hit.id) ?? null;
    return {
      hit,
      currentRank,
      previousRank,
      movement: previousRank === null ? null : previousRank - currentRank,
    };
  });
}
