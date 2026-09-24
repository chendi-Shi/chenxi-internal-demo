# Phase D Report

日期：2026-09-22

## 结果

阶段 D 已完成可重复运行的 Policy 与 token 评测基线。评测只消费第一部分交付的虚构 fixture，不调用真实模型、不连接真实服务、不修改上游 ranking。

执行命令：

```bash
npm run evaluate
```

生成：

- `artifacts/evaluation/latest.json`：机器可读结果；
- `docs/evaluation/BASELINE.md`：供评审阅读的基线报告。

`npm run check:evaluation` 会检查生成物漂移，并已进入完整 `npm test` 质量门。

## Policy 实验

### Source weight — Passed

使用官方 v1/v2 响应验证：

- Policy version 从 1 变为 2；
- Top 1 从 `demo_research` 变为 `demo_filings`；
- 服务端 score details 反映 0.5 / 3.0 权重；
- 候选集合和 relevance 分量保持不变；
- 保存的 PolicyState 与搜索结果版本一致。

这里不重新计算最终分，只比较上游提供的字段和顺序。

### Freshness — Passed

官方 v1 响应恰好提供了受控对照：两条结果的 relevance 和 source weight 相同，但发布日期不同。

- 2026-09-01 文档 freshness：`0.849832...`；
- 2026-08-01 文档 freshness：`0.669337...`；
- `recency_boost=0.25` 时，新文档最终分更高并排名第一。

因此不需要在本地生成或模拟 ranking，即可验证 freshness 行为。

### Metadata filter — Not run

交付包中存在 metadata 字段和空 metadata 查询，但没有带 metadata filter 的 SearchResponse。

评测已固定计划请求：

```json
{"query":"芯片","source_ids":[],"since":"","until":"","metadata":{"sector":"科技"},"limit":10}
```

仅凭 schema 和文档字段存在不能声称过滤通过，因此该实验稳定输出 `not_run`，等待阶段 E 真实上游补录。

## Token 测量方法

同时记录：

- Unicode 字符数；
- UTF-8 bytes；
- 启发式 tokens。

启发式公式：

```text
ceil(ASCII 字符 / 4 + 汉字数 + 其他非 ASCII 字符数)
```

该值只用于同一 fixture 下的相对比较，不是模型专用 tokenizer，也不是账单 token。

MCP result 按兼容 envelope 统计，即包含 `structuredContent` 和重复 JSON 文本 `content`。

## 核心结果

| 指标 | 结果 |
|---|---:|
| 标准 search result | 约 216 tokens |
| 详细 search_documents result | 约 779 tokens |
| 标准 search 相对减少 | 72.3% |
| search + fetch 两工具 schema | 约 242 tokens |
| 再暴露 search_documents 的 schema 增量 | 约 330 tokens |
| 一次 search + 一次 fetch 完整流程 | 约 855 tokens |
| 短文本 fetch 占流程比例 | 44.6% |

## Token 优化结论

1. ChatGPT 默认只加载标准 `search` 和 `fetch`。
2. 标准 search 保持 `id/title/url`，详细 score 和 snippet 留给 Dashboard HTTP 流程。
3. 只 fetch 最终需要引用的文档，不能对全部候选自动逐个读取。
4. 高级 `search_documents` 应按需或延迟加载，避免固定 schema 开销。
5. 长文 fetch 若在阶段 E 超预算，应向上游提出 passage/range fetch；客户端不得静默截断。

## 验证

- Policy experiments：2 passed，0 failed，1 not run；
- 单元测试：21 passed；
- TypeScript strict：通过；
- Evaluation drift check：通过；
- Production build：通过。

## 阶段 E 输入

阶段 E 需要补齐：

- 真实上游 metadata filter 响应；
- HTTP 与 MCP 排序一致性；
- 实际 MCP tool definition 与调用次数；
- 模型相关 tokenizer 或平台 usage；
- 完整 fetch 对真实长文上下文的影响；
- 引用 URL 可达性和认证行为。
