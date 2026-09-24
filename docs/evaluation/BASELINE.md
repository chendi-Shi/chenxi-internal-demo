# Phase D Evaluation Baseline

证据范围：仅验证 `docs/contracts/examples.json` 中的虚构样例，不代表真实上游或 ChatGPT 实测。

## Policy 实验摘要

- Passed：2
- Failed：0
- Not run：1

| 实验 | 状态 | Policy version | 断言数 |
|---|---|---|---|
| source_weight | passed | v1 → v2 | 5 |
| freshness | passed | v1 | 3 |
| metadata_filter | not_run | v1 | 0 |

### 来源权重改变下一次检索排序

| 状态 | 断言 | 证据 |
|---|---|---|
| PASS | policy version increments | before=v1, after=v2 |
| PASS | preferred source becomes rank 1 | demo_research → demo_filings |
| PASS | configured source weights are reflected by server results | demo_filings=3.0, demo_research=0.5 |
| PASS | relevance guard preserves candidates and relevance components | same_result_set=true, relevance_unchanged=true |
| PASS | saved policy matches evaluated version | policy=1→2 |

### 相关性与来源权重相同时优先新文档

| 状态 | 断言 | 证据 |
|---|---|---|
| PASS | comparison holds relevance and source weight constant | relevance=0.000003, source_weight=1 |
| PASS | newer document has greater freshness | 2026-09-01T00:00:00.000000+00:00 freshness=0.8498320656439321; 2026-08-01T00:00:00.000000+00:00 freshness=0.6693372639811069 |
| PASS | newer document ranks first under positive recency boost | recency_boost=0.25, score=0.000003637374049232949>0.00000350200294798583 |

### Metadata 精确过滤

| 状态 | 断言 | 证据 |
|---|---|---|
| NOT RUN | 缺少上游响应证据 | examples.json contains metadata fields but no filtered SearchResponse; claiming a pass would invent upstream behavior. |

## Token 估算

方法：`ceil(ascii_characters / 4 + han_characters + other_non_ascii_characters)`。Transparent payload estimate only; it is not a model-specific billing tokenizer.

MCP result 以兼容 envelope 计量，即同时包含 `structuredContent` 与 JSON 文本 `content`。

| Payload | Unicode 字符 | UTF-8 bytes | 估算 tokens |
|---|---:|---:|---:|
| mcp_tool_schemas_search_fetch | 968 | 968 | 242 |
| mcp_tool_schemas_all_three | 2350 | 2350 | 588 |
| mcp_search_arguments | 14 | 18 | 5 |
| mcp_search_result_envelope | 696 | 804 | 216 |
| mcp_search_documents_result_envelope | 2392 | 2940 | 805 |
| mcp_fetch_arguments | 41 | 41 | 11 |
| mcp_fetch_result_envelope | 1072 | 1370 | 381 |
| standard_flow_one_search_one_fetch | 2791 | 3201 | 855 |

## 结论

- 标准轻量 `search` 相比详细 `search_documents` 样例少约 73.2% tool-result tokens。
- 同时暴露高级搜索工具增加约 346 个工具 schema tokens。
- 一次标准 search + 一次 fetch（含两工具 schema 和参数）约 855 tokens。
- 当前短文样例中 fetch 占完整标准流程约 44.6%；真实长 PDF 的占比会更高。
- Metadata filter 仍缺少过滤后响应，必须在阶段 E 用真实上游补录，不能从字段存在推断功能通过。

## Token 降低建议

1. ChatGPT 默认只暴露标准 `search` 与 `fetch`；高级搜索按需加载。
2. `search` 保持 `id/title/url`，分数与 snippet 留在 Dashboard HTTP 流程。
3. 只 fetch 最终需要引用的文档，避免对全部候选逐个读取。
4. 阶段 E 记录模型实际工具调用次数和真实 tokenizer 数据，替换当前启发式估算。
5. 若长文 fetch 成为主要成本，向上游提 passage/range fetch 契约，不在客户端静默截断。

