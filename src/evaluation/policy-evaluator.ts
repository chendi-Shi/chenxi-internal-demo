import { contractExamples } from '../api/generated/contracts.ts';
import { parseContract } from '../api/validation.ts';

export type ExperimentStatus = 'passed' | 'failed' | 'not_run';

export interface EvaluationAssertion {
  name: string;
  passed: boolean;
  evidence: string;
}

export interface PolicyExperimentResult {
  id: 'source_weight' | 'freshness' | 'metadata_filter';
  title: string;
  status: ExperimentStatus;
  query: string;
  policy_versions: number[];
  assertions: EvaluationAssertion[];
  limitation?: string;
  planned_request?: unknown;
}

function resultStatus(assertions: EvaluationAssertion[]): ExperimentStatus {
  return assertions.every((assertion) => assertion.passed) ? 'passed' : 'failed';
}

function assert(name: string, passed: boolean, evidence: string): EvaluationAssertion {
  return { name, passed, evidence };
}

export function evaluatePolicyFixtures(): PolicyExperimentResult[] {
  const before = parseContract(
    'SearchResponse',
    structuredClone(contractExamples.search_response),
  );
  const after = parseContract(
    'SearchResponse',
    structuredClone(contractExamples.search_after_policy_change),
  );
  const policyBefore = parseContract(
    'PolicyState',
    structuredClone(contractExamples.policy_response),
  );
  const policyAfter = parseContract(
    'PolicyState',
    structuredClone(contractExamples.policy_update_response),
  );

  const beforeById = new Map(before.results.map((hit) => [hit.id, hit]));
  const afterById = new Map(after.results.map((hit) => [hit.id, hit]));
  const sameResultSet =
    beforeById.size === afterById.size &&
    [...beforeById.keys()].every((id) => afterById.has(id));
  const relevanceUnchanged = [...beforeById.entries()].every(([id, hit]) => {
    return afterById.get(id)?.score_details.relevance === hit.score_details.relevance;
  });

  const sourceAssertions = [
    assert(
      'policy version increments',
      before.policy_version === 1 && after.policy_version === 2,
      `before=v${before.policy_version}, after=v${after.policy_version}`,
    ),
    assert(
      'preferred source becomes rank 1',
      before.results[0]?.source_id === 'demo_research' &&
        after.results[0]?.source_id === 'demo_filings',
      `${before.results[0]?.source_id ?? 'none'} → ${after.results[0]?.source_id ?? 'none'}`,
    ),
    assert(
      'configured source weights are reflected by server results',
      afterById.get('748cf1b7a537964daa566ee0f743ad5f')?.score_details.source_weight === 3 &&
        afterById.get('eb72a59f140664e38ac656cc5f14f770')?.score_details.source_weight === 0.5,
      'demo_filings=3.0, demo_research=0.5',
    ),
    assert(
      'relevance guard preserves candidates and relevance components',
      sameResultSet && relevanceUnchanged,
      `same_result_set=${sameResultSet}, relevance_unchanged=${relevanceUnchanged}`,
    ),
    assert(
      'saved policy matches evaluated version',
      policyBefore.version === before.policy_version && policyAfter.version === after.policy_version,
      `policy=${policyBefore.version}→${policyAfter.version}`,
    ),
  ];

  const newer = before.results[0];
  const older = before.results[1];
  const freshnessAssertions = [
    assert(
      'comparison holds relevance and source weight constant',
      newer?.score_details.relevance === older?.score_details.relevance &&
        newer?.score_details.source_weight === older?.score_details.source_weight,
      `relevance=${newer?.score_details.relevance ?? 'missing'}, source_weight=${newer?.score_details.source_weight ?? 'missing'}`,
    ),
    assert(
      'newer document has greater freshness',
      Boolean(
        newer &&
          older &&
          new Date(newer.published_at).getTime() > new Date(older.published_at).getTime() &&
          newer.score_details.freshness > older.score_details.freshness,
      ),
      `${newer?.published_at ?? 'missing'} freshness=${newer?.score_details.freshness ?? 'missing'}; ${older?.published_at ?? 'missing'} freshness=${older?.score_details.freshness ?? 'missing'}`,
    ),
    assert(
      'newer document ranks first under positive recency boost',
      (policyBefore.policy.recency_boost ?? 0) > 0 &&
        Boolean(newer && older && newer.score > older.score),
      `recency_boost=${policyBefore.policy.recency_boost ?? 0}, score=${newer?.score ?? 'missing'}>${older?.score ?? 'missing'}`,
    ),
  ];

  return [
    {
      id: 'source_weight',
      title: '来源权重改变下一次检索排序',
      status: resultStatus(sourceAssertions),
      query: contractExamples.search_request.query,
      policy_versions: [before.policy_version, after.policy_version],
      assertions: sourceAssertions,
    },
    {
      id: 'freshness',
      title: '相关性与来源权重相同时优先新文档',
      status: resultStatus(freshnessAssertions),
      query: contractExamples.search_request.query,
      policy_versions: [before.policy_version],
      assertions: freshnessAssertions,
    },
    {
      id: 'metadata_filter',
      title: 'Metadata 精确过滤',
      status: 'not_run',
      query: contractExamples.search_request.query,
      policy_versions: [before.policy_version],
      assertions: [],
      planned_request: {
        ...contractExamples.search_request,
        metadata: { sector: '科技' },
      },
      limitation:
        'examples.json contains metadata fields but no filtered SearchResponse; claiming a pass would invent upstream behavior.',
    },
  ];
}
