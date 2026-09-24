# Roadmap

更新日期：2026-09-24

路线图覆盖合并后的 Retrieval Hub（A）和 Dashboard（B）。先固定共同契约，再分别验证后端、页面和端到端检索；真实 ChatGPT 账号连接仍单独安排。

## 阶段 A：地基与接口契约固化 — 已完成

目标：让后续 coding 有唯一使命、架构边界、接口事实来源和执行规范。

交付：

- `MISSION.md`、`ROADMAP.md`、`AGENTS.md`；
- 集成架构与工程规范；
- 合并后 OpenAPI、examples 与 HTTP/MCP 责任边界统一；
- 后端与 Dashboard 同仓库，契约版本 1.2.0；
- 项目级 `mcp-retrieval-demo` Skill；
- 零依赖 foundation/contract 校验；
- 接口缺口及 token 风险清单。

退出条件：

- `npm test` 通过；
- OpenAPI 版本、核心路径和核心 schema 可自动验证；
- Skill 结构验证通过；
- 架构明确本地数据不入 Git、HTTP/MCP 共用后端 ranking、Dashboard 不重排。

## 阶段 B：契约客户端与 mock 基线 — 已完成

目标：不依赖真实后端即可稳定开发和测试。

计划交付：

- TypeScript 严格模式工程骨架；
- 单一 API client，覆盖来源、搜索、文档、策略、语义索引和文件同步状态；
- 从 OpenAPI 派生或校验的边界类型；
- 基于 `examples.json` 的 mock server 或 fixture adapter；
- 401、403、404、409、413、422 错误映射测试。

退出条件：

- mock 下可跑通“读策略 → 保存 → 再搜索”；
- 409 不会自动覆盖；
- API DTO 字段与 OpenAPI 一致，不出现第二套手写协议。

完成证据见 [`docs/PHASE_B_REPORT.md`](docs/PHASE_B_REPORT.md)。

## 阶段 C：Retrieval Policy Dashboard — 已完成

目标：可视化配置并证明下一次检索受策略影响。

计划交付：

- 数据源状态、策略编辑、搜索结果与文档读取四个最小区域；
- `source_weights`、`recency_boost`、`half_life_days`、`default_limit` 编辑；
- query 级 `source_ids/since/until/metadata/limit` 过滤；
- 策略版本、分项评分和前后排名对比；
- 输入校验、冲突提示和纯文本安全渲染。

退出条件：

- 同一查询修改来源权重后排序变化可见；
- 新鲜度配置变化有可解释结果；
- 页面不包含任何本地重排实现。

完成证据见 [`docs/PHASE_C_REPORT.md`](docs/PHASE_C_REPORT.md)。

## 阶段 D：Policy 效果与 token 评测 — 已完成

目标：用固定样例回答“策略是否有效、代价是多少”。

计划交付：

- 受控查询集和期望结果；
- source weight、freshness、metadata filter 三类实验；
- 排名、延迟、响应字节和估算 token 报告；
- 相关性退化保护和失败样例记录。

退出条件：

- 所有实验可重复运行；
- 结果包含 `policy_version`；
- token 数据按 search、fetch、工具 schema 分开记录。

完成证据见 [`docs/PHASE_D_REPORT.md`](docs/PHASE_D_REPORT.md) 和 [`docs/evaluation/BASELINE.md`](docs/evaluation/BASELINE.md)。Metadata filter 的实验规范可重复运行，但由于上游交付包没有过滤后的 SearchResponse，结果诚实标记为 `not_run`，留待阶段 E 补录。

## 阶段 E：MCP / ChatGPT 联调 — 进行中

目标：验证本地 Hub 的 HTTP/MCP 行为，并在具备条件后验证目标 ChatGPT 环境。

计划交付：

- `search → fetch → 引用` 端到端记录；
- HTTP 与 MCP 排序一致性检查；
- URL 可达性、认证、全文 fetch token 风险实测；
- 实际限制和问题清单。

退出条件：

- 至少一组带引用回答成功；
- 策略更新后 MCP 下一次查询使用新版本；
- 若完整 `fetch` 超出预算，形成明确的上游契约变更建议。

当前本地 Hub 和 Dashboard 已在同仓库实现；fixture 页面可独立运行，HTTP 服务可本机启动。真实 GPT 账号、可访问的外部引用 URL 和目标环境认证仍未连接，因此 ChatGPT 端到端引用验收保持 `not_run`。

2026-09-24 补充：已提供 `npm run check:live` 只读检查，覆盖 health、status、sources、policy、search、首条 fetch、Policy 版本一致性及 payload/token 指标。当前本机 8765 无上游服务、环境变量无 Read/Admin Token、会话无 Retrieval Hub MCP tools，因此真实 HTTP/MCP 项保持 `not_run`；证据见 `docs/PHASE_E_REPORT.md`。

## 阶段 F：交付与规模化建议 — 已完成（本仓库范围）

目标：形成可运行 Demo、复现说明和每天新增数百份文档时的升级路线。

计划交付：

- 一键本地运行说明；
- 架构、工具、ranking、问题和评测总结；
- 增量入库、队列、ANN、ACL、审计与索引版本建议；
- 未完成项、责任人和接口变更记录。

完成证据：本地 fixture Demo、合并后安装说明、架构/MCP/ranking、问题清单和规模化建议见 `README.md`、`docs/DELIVERY.md` 和 `docs/ARCHITECTURE.md`。真实文档集质量评估及 GPT 账号端到端验收需要相应受控环境，状态与前置条件记录在 `docs/memos/EXTERNAL_ACCEPTANCE.md`。

## 阶段推进规则

- 开始下一阶段前确认上一阶段退出条件。
- 接口字段或语义变化先更新契约并记录影响，再改客户端。
- 需要公网、真实凭据、付费 API 或真实数据时单独确认。
- 发现上游能力缺口时先写清可复现案例，不在客户端创建影子实现。
