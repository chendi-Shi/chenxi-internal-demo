# Roadmap

## 2026-10-13：PostgreSQL 检索升级 — 本地消融与 MCP 验收完成

PostgreSQL + pgvector、快照迁移、统一 HTTP/MCP adapter 和 lexical/dense/hybrid/RRF/rerank/规则 query rewrite 六阶段均已实现。真实资料库 22 题 × 3 轮 × 6 阶段共 396 次正式测量完成，失败和降级为 0；结果和负增益见 [真实库匿名报告](docs/evaluation/POSTGRES_REAL_REPORT.md)。已有向量覆盖仅 6.74%，20 条有答案问题为 agent 核对原文的非盲测小样本，不能据此宣称全库泛化或答案生成准确率。

本机选择 RRF 混合 profile，并完成官方 SDK 实际 stdio 启动、三个只读工具、中英文请求、HTTP 排序与策略一致性、来源/metadata 过滤和 PDF 引用验收，见 [MCP 验收记录](docs/evaluation/POSTGRES_MCP_ACCEPTANCE.md)。已修复 Windows stdio 首次 NumPy 原生导入等待；最终后端 83 项测试与前端 33 项单元测试通过。当前会话需重载 MCP 配置才会发现新工具，ChatGPT 云隧道目前没有运行，云端账号联调仍未验收。

复现命令、运行开关、预热与中断恢复规则见 [检索实验说明](docs/POSTGRES_RETRIEVAL_BASELINE.md)；[虚构样例报告](docs/evaluation/POSTGRES_SYNTHETIC_REPORT.md)保留为功能诊断。下一步是独立人工标注集、全量 passage 向量覆盖、多语言重排、LLM query rewrite 与增量 PostgreSQL 快照同步；策略管理数据和 fetch 原文目前仍在 SQLite。

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

## 阶段 E：MCP / ChatGPT 联调 — 本地完成，云端进行中

2026-10-13 更新：PostgreSQL/RRF 本地实际 stdio MCP、HTTP TestClient 与原文引用一致性已验收，证据见 [POSTGRES_MCP_ACCEPTANCE.md](docs/evaluation/POSTGRES_MCP_ACCEPTANCE.md)。下文的 9 月记录保留为当时状态；云端 ChatGPT 传输、账号和外部引用 URL 验收仍独立安排。

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

### 2026-10-15：RAG 组件归因与公开 GPU 评估

- 已完成：17 组 SQLite 实际检索消融，1,700 次正式调用；历史报告重新分析；指标、配对置信区间、零/负收益和逐题证据分开记录。
- 已实现：独立 PostgreSQL 消融入口；固定公开中英文基准和模型的可恢复 GPU runner、实际 GPU 执行证明、预算/检查点/失败记录。最长 48 小时正式结果按完成状态补录，未完成配置不填分数。
- 交付文档：[消融方法](docs/RAG_ABLATION.md)、[公开 GPU 协议](docs/PUBLIC_GPU_ABLATION.md)、[实习面试与证据缺口](docs/RAG_INTERVIEW_PREP.md)。
- 后续独立验收：生成答案与引用质量、长文 chunking、HNSW 对 exact 的速度/质量、真实服务并发和多租户权限。输入截断不等于 chunking；缓存回放耗时不等于在线 SLO。

- 开始下一阶段前确认上一阶段退出条件。
- 接口字段或语义变化先更新契约并记录影响，再改客户端。
- 需要公网、真实凭据、付费 API 或真实数据时单独确认。
- 发现上游能力缺口时先写清可复现案例，不在客户端创建影子实现。
