# Retrieval Policy Demo 交付说明

日期：2026-09-24

## 1. 可运行 Demo

环境要求：Python 3.11+、Node.js 22.18+。本仓库现包含 Retrieval Hub 与 Dashboard；快速启动请按根目录 `README.md` 分别启动后端和前端。

```bash
npm install
npm run demo
```

命令会监听 `0.0.0.0`，由系统分配临时端口，并在终端显示本机与局域网地址。默认 fixture 模式不需要 Token、外部服务或真实文档；测试结束按 `Ctrl+C`。

完整质量门：

```bash
npm test
```

Live 模式通过页面连接已有 Retrieval Hub HTTP API。浏览器跨设备时，Base URL 必须是宿主机可达地址而不是 `127.0.0.1`，上游也必须允许 Dashboard 的精确 CORS Origin。

## 2. 整体架构

系统保持单一权威检索边界：

```text
获准本地文档 → backend/ Retrieval Hub（接入 / 索引 / ranking / policy）
                              ├─ HTTP API → src/ Dashboard / Policy Eval
                              └─ MCP Server → GPT 客户端（账号连接另行配置）
```

合并仓库的 `backend/` 实现数据接入、索引、ranking、HTTP 与 MCP；`src/` 实现 Dashboard、契约客户端、fixture、策略实验和 token 测量。所有浏览器 HTTP 调用集中在 `src/api/client.ts`；OpenAPI DTO 保持 `snake_case`；Dashboard 不读取原始目录，也不实现第二套检索或排序。

策略更新流程是 `GET /api/policy` → 编辑 → `PUT /api/policy`，保存时携带最近读取的 `expected_version`。下一次搜索返回的 `policy_version` 进入 UI 和评测记录；409 冲突不会自动覆盖。状态卡同时展示语义索引覆盖率与文件同步状态。

## 3. MCP tools 设计

| Tool | 输入与输出重点 | 使用建议 |
|---|---|---|
| `search({query})` | 轻量返回 `id/title/url` | 默认发现候选，控制 token |
| `fetch({id})` | 返回完整 `id/title/text/url/metadata` | 只读取最终需要引用的少量文档 |
| `search_documents({request})` | 对齐 `POST /api/search`，支持 filter 与详细评分 | 高级诊断按需使用，不默认暴露 |

HTTP 和 MCP 必须共享同一策略状态和 ranking 实现。MCP tools 保持只读；管理策略仍由受管理权限保护的 HTTP API 更新。标准流程是先 `search`，再对确需引用的结果 `fetch`，最后基于返回 URL 形成引用。

## 4. Retrieval / ranking

上游负责文档解析、分块、SQLite FTS5 召回和权威排序。当前交付文档给出的评分语义为：

```text
relevance = max(0, -BM25)
freshness = 0.5 ^ (age_days / half_life_days)
score = relevance × source_weight × (1 + recency_boost × freshness)
```

Dashboard 只展示服务端返回的顺序、`score` 和 `score_details`。前后排名变化通过相同结果 ID 做位置对照，不重新计算 score，也不改变服务端顺序。query 级 `source_ids/since/until/metadata/limit` 是过滤条件，不属于持久化 Retrieval Policy。

fixture 模式同样不在客户端执行公式：它只重放正式交付包里的 v1/v2 SearchResponse，用于稳定验证 UI 和契约行为。

## 5. 本次实际验收

浏览器在局域网临时端口的 `dist/` 生产预览上完成：

1. 页面载入 2 个来源、2 个文档与 Policy v1。
2. 查询“芯片”返回两条 v1 服务端顺序结果。
3. `fetch`/文档读取展示纯文本正文、metadata 和 `content_trust`。
4. 保存契约示例策略后得到 Policy v2，并自动再次查询。
5. `demo_filings` 从第 2 升至第 1，权重显示 3.00；`demo_research` 从第 1 降至第 2，权重显示 0.50。
6. UI 显示 `↑ 1` / `↓ 1`，没有客户端重排。
7. 文档 metadata 建议可精确回填下一次查询过滤条件，不修改持久 Policy。
8. 1674×854 桌面视口和 390×844 移动视口均无横向溢出；移动端结果卡完整显示。

`npm test` 同时通过 foundation、生成物漂移、评测漂移、TypeScript strict、26 个行为测试和生产构建。

## 6. 实际遇到的问题

- 当前可重复验收基于虚构 fixture，不等于真实上游 MCP / ChatGPT 端到端已通过。
- fixture 只支持正式样例查询和 v1/v2 策略，不能代替任意查询或真实 PDF 召回测试。
- loopback 引用 URL 对远程 ChatGPT 不可达；跨设备 Live 模式还受监听地址、CORS 和 Bearer 认证约束。
- `fetch` 返回完整正文，没有分页或 token 上限；真实长 PDF 可能显著占用上下文。
- metadata filter 有请求契约，但交付 fixture 没有过滤后响应，目前只能标记 `not_run`。
- FTS 查询要求与自然语言召回、PDF 解析质量和公司名弱相关命中，仍需在真实上游数据工程环境评测。
- 浏览器端持有 admin Token 只适合临时受控测试；正式形态需要服务端代理和权限控制。

## 7. 每天新增数百份文档时的优化方向

这些是未来工作，不在当前 Demo 内提前实现：

- 接入层：增量扫描、内容 hash 去重、幂等任务、失败重试和死信记录。
- 处理层：PDF 解析/OCR worker 池、批量处理、背压、资源限额与解析质量指标。
- 索引层：事务批量写入、FTS 索引维护、索引版本、可重建流程和冷热分层。
- 检索层：召回/延迟监控、受控 query set、公司实体与时间过滤质量；需要 ANN 或混合检索时先由上游形成接口方案。
- 安全层：文档级 ACL、租户隔离、凭据托管、审计和删除传播。
- MCP/token：passage/range fetch、分页、按需加载高级 tool、只 fetch 最终引用文档。
- 运维层：队列积压、吞吐、失败率、索引新鲜度、备份恢复和容量规划。

本地授权 PDF 实验与规模化扩展计划见 `docs/memos/DATA_ENGINEERING_AND_SCALE.md`。

## 8. 完成边界

本仓库责任范围内的 Demo、Dashboard、契约客户端、fixture、策略与 token 评测、生产构建验收、架构说明和规模化建议已经完成。阶段 F 因此关闭。

尚未声明完成的项目是目标 ChatGPT 账号的 `search → fetch → 引用`、对真实文档集合的检索质量评估及引用可达性验收。它们的必要输入、通过条件和复现命令统一记录在 `docs/memos/EXTERNAL_ACCEPTANCE.md`；没有这些条件时继续保持 `not_run`。
