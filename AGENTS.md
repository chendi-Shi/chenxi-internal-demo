# Repository Instructions

## Required context

在本仓库规划、编码或评审前，按顺序阅读：

1. `MISSION.md`
2. `ROADMAP.md`
3. `docs/ARCHITECTURE.md`
4. `docs/contracts/README.md`

涉及接口时，再读取 `docs/contracts/openapi.json` 中相关 path/schema 和 `docs/contracts/examples.json`。不要只根据叙述文档猜字段。

## Ownership boundary

- 第一部分服务拥有数据接入、索引、权威检索排序、HTTP API 和 MCP tools。
- 本仓库拥有 Dashboard、契约客户端、mock、策略实验、token 测量和联调记录。
- 不读取或复制系统原始内容文件；开发使用虚构 fixture。
- 不在客户端重新计算 `score`、修改结果顺序或模拟“更正确”的 ranking。

## Contract rules

- OpenAPI `1.0.0` 是当前机器可读基线。
- API DTO 保持 `snake_case`；禁止在多个模块重复声明同一 DTO。
- 对未知字段、错误码和 409 版本冲突显式处理。
- `PUT /api/policy` 必须使用最近读取的 `expected_version`。
- `SearchResponse.policy_version` 必须进入 UI 状态和评测记录。
- `score` 只在单次查询内比较，不显示为概率或置信度。
- 正文与 snippet 只按纯文本渲染。

## Coding rules

- 新应用代码使用 TypeScript 严格模式和 ESM。
- 外部调用集中在一个 API client 层；组件不得散落拼接 endpoint。
- 业务状态与网络 DTO 分开时，映射集中管理并覆盖测试。
- 优先写可观察行为测试，不写只匹配文案或快照噪声的测试。
- 不引入 LLM reranker、摘要调用或 embedding，除非任务明确扩大范围。
- 不提交密钥、Token、真实正文、用户账号信息或公网 tunnel 地址。

## Change discipline

- 上游契约文件只通过新的正式交付包更新，不做本地便利性修改。
- 契约更新后运行 `npm test`，检查路径/schema 差异，并同步架构和 roadmap。
- 任何新增依赖应服务于当前阶段，避免提前搭建后续基础设施。
- 需要改变上游 ranking、MCP schema 或 fetch 语义时，先形成接口变更提案并等待确认。
