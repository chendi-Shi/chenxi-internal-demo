import { contractExamples } from '../api/generated/contracts.ts';
import { evaluatePolicyFixtures, type PolicyExperimentResult } from './policy-evaluator.ts';
import {
  makeMcpEnvelope,
  measureJson,
  sumMetrics,
  tokenEstimationMethod,
  type PayloadMetric,
} from './token-metrics.ts';
import { allMcpToolDefinitions, minimalMcpToolDefinitions } from './tool-schemas.ts';

export interface EvaluationReport {
  report_version: '1.0';
  evidence: {
    api_version: '1.2.0';
    fixture: 'docs/contracts/examples.json';
    claim_scope: string;
  };
  experiment_summary: {
    passed: number;
    failed: number;
    not_run: number;
  };
  experiments: PolicyExperimentResult[];
  token_estimation: typeof tokenEstimationMethod;
  token_metrics: PayloadMetric[];
  token_comparisons: {
    lean_search_vs_detailed_percent_reduction: number;
    advanced_tool_schema_increment_tokens: number;
    one_search_one_fetch_estimated_tokens: number;
    fetch_share_of_standard_flow_percent: number;
  };
}

export function buildEvaluationReport(): EvaluationReport {
  const experiments = evaluatePolicyFixtures();
  const minimalSchemas = measureJson('mcp_tool_schemas_search_fetch', minimalMcpToolDefinitions);
  const allSchemas = measureJson('mcp_tool_schemas_all_three', allMcpToolDefinitions);
  const searchArguments = measureJson('mcp_search_arguments', contractExamples.mcp_search_request);
  const searchResult = measureJson(
    'mcp_search_result_envelope',
    makeMcpEnvelope(contractExamples.mcp_search_response),
  );
  const detailedSearchResult = measureJson(
    'mcp_search_documents_result_envelope',
    makeMcpEnvelope(contractExamples.search_response),
  );
  const fetchArguments = measureJson('mcp_fetch_arguments', {
    id: contractExamples.document_response.id,
  });
  const fetchResult = measureJson(
    'mcp_fetch_result_envelope',
    makeMcpEnvelope(contractExamples.document_response),
  );
  const standardFlow = sumMetrics('standard_flow_one_search_one_fetch', [
    minimalSchemas,
    searchArguments,
    searchResult,
    fetchArguments,
    fetchResult,
  ]);
  const tokenMetrics = [
    minimalSchemas,
    allSchemas,
    searchArguments,
    searchResult,
    detailedSearchResult,
    fetchArguments,
    fetchResult,
    standardFlow,
  ];
  const passed = experiments.filter((experiment) => experiment.status === 'passed').length;
  const failed = experiments.filter((experiment) => experiment.status === 'failed').length;
  const notRun = experiments.filter((experiment) => experiment.status === 'not_run').length;
  const leanReduction =
    100 * (1 - searchResult.estimated_tokens / detailedSearchResult.estimated_tokens);
  const fetchShare = 100 * (fetchResult.estimated_tokens / standardFlow.estimated_tokens);

  return {
    report_version: '1.0',
    evidence: {
      api_version: '1.2.0',
      fixture: 'docs/contracts/examples.json',
      claim_scope:
        'Results validate provided fixture evidence only; they do not claim a live upstream or ChatGPT integration.',
    },
    experiment_summary: { passed, failed, not_run: notRun },
    experiments,
    token_estimation: tokenEstimationMethod,
    token_metrics: tokenMetrics,
    token_comparisons: {
      lean_search_vs_detailed_percent_reduction: Number(leanReduction.toFixed(1)),
      advanced_tool_schema_increment_tokens:
        allSchemas.estimated_tokens - minimalSchemas.estimated_tokens,
      one_search_one_fetch_estimated_tokens: standardFlow.estimated_tokens,
      fetch_share_of_standard_flow_percent: Number(fetchShare.toFixed(1)),
    },
  };
}

export function renderEvaluationMarkdown(report: EvaluationReport): string {
  const experimentRows = report.experiments
    .map(
      (experiment) =>
        `| ${experiment.id} | ${experiment.status} | ${experiment.policy_versions.map((version) => `v${version}`).join(' → ')} | ${experiment.assertions.length} |`,
    )
    .join('\n');
  const assertionSections = report.experiments
    .map((experiment) => {
      const rows = experiment.assertions.length
        ? experiment.assertions
            .map(
              (assertion) =>
                `| ${assertion.passed ? 'PASS' : 'FAIL'} | ${assertion.name} | ${assertion.evidence} |`,
            )
            .join('\n')
        : `| NOT RUN | 缺少上游响应证据 | ${experiment.limitation ?? ''} |`;
      return `### ${experiment.title}\n\n| 状态 | 断言 | 证据 |\n|---|---|---|\n${rows}`;
    })
    .join('\n\n');
  const metricRows = report.token_metrics
    .map(
      (metric) =>
        `| ${metric.label} | ${metric.characters} | ${metric.utf8_bytes} | ${metric.estimated_tokens} |`,
    )
    .join('\n');

  return `# Phase D Evaluation Baseline\n\n` +
    `证据范围：仅验证 \`${report.evidence.fixture}\` 中的虚构样例，不代表真实上游或 ChatGPT 实测。\n\n` +
    `## Policy 实验摘要\n\n` +
    `- Passed：${report.experiment_summary.passed}\n` +
    `- Failed：${report.experiment_summary.failed}\n` +
    `- Not run：${report.experiment_summary.not_run}\n\n` +
    `| 实验 | 状态 | Policy version | 断言数 |\n|---|---|---|---|\n${experimentRows}\n\n` +
    `${assertionSections}\n\n` +
    `## Token 估算\n\n` +
    `方法：\`${report.token_estimation.formula}\`。${report.token_estimation.caveat}\n\n` +
    `MCP result 以兼容 envelope 计量，即同时包含 \`structuredContent\` 与 JSON 文本 \`content\`。\n\n` +
    `| Payload | Unicode 字符 | UTF-8 bytes | 估算 tokens |\n|---|---:|---:|---:|\n${metricRows}\n\n` +
    `## 结论\n\n` +
    `- 标准轻量 \`search\` 相比详细 \`search_documents\` 样例少约 ${report.token_comparisons.lean_search_vs_detailed_percent_reduction}% tool-result tokens。\n` +
    `- 同时暴露高级搜索工具增加约 ${report.token_comparisons.advanced_tool_schema_increment_tokens} 个工具 schema tokens。\n` +
    `- 一次标准 search + 一次 fetch（含两工具 schema 和参数）约 ${report.token_comparisons.one_search_one_fetch_estimated_tokens} tokens。\n` +
    `- 当前短文样例中 fetch 占完整标准流程约 ${report.token_comparisons.fetch_share_of_standard_flow_percent}%；真实长 PDF 的占比会更高。\n` +
    `- Metadata filter 仍缺少过滤后响应，必须在阶段 E 用真实上游补录，不能从字段存在推断功能通过。\n\n` +
    `## Token 降低建议\n\n` +
    `1. ChatGPT 默认只暴露标准 \`search\` 与 \`fetch\`；高级搜索按需加载。\n` +
    `2. \`search\` 保持 \`id/title/url\`，分数与 snippet 留在 Dashboard HTTP 流程。\n` +
    `3. 只 fetch 最终需要引用的文档，避免对全部候选逐个读取。\n` +
    `4. 阶段 E 记录模型实际工具调用次数和真实 tokenizer 数据，替换当前启发式估算。\n` +
    `5. 若长文 fetch 成为主要成本，向上游提 passage/range fetch 契约，不在客户端静默截断。\n`;
}
