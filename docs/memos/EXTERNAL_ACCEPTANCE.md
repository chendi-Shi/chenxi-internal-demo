# 外部验收备忘录

状态：等待外部前置条件；本仓库内可执行交付已完成。

更新日期：2026-09-24

## 当前实测状态

- `http://127.0.0.1:8765/healthz` 无服务监听。
- `RESEARCH_HUB_BASE_URL`、`RESEARCH_HUB_READ_TOKEN`、`RESEARCH_HUB_ADMIN_TOKEN` 均未配置。
- 当前 Codex 会话没有 Retrieval Hub 的 `search`、`fetch`、`search_documents` Custom MCP Tools。
- MCP resource 清单中也没有 Retrieval Hub Server。
- 公开仓库不包含用户的原始 PDF、数据库或本机数据清单。

因此真实 HTTP、MCP、ChatGPT 引用、metadata filter 和 PDF 数据工程实验保持 `not_run`。Fixture 结果不会替代这些证据。

## 需要提供的外部输入

### 真实 HTTP / MCP

1. 可访问的 Retrieval Hub Base URL。
2. Read Token；策略更新复测另需 Admin Token。
3. 已配置到目标客户端的 Retrieval Hub MCP Server，并暴露 `search`、`fetch`；如需 Policy 版本精确对比，还需 `search_documents`。
4. 一条能稳定命中的脱敏测试查询。
5. 目标客户端能够访问的引用 URL；不得使用只对宿主机有效的 loopback URL 作为最终引用证据。

### 真实 PDF 集合受控实验

1. 获准测试的实体与允许使用的名称/别名规则。
2. 明确的时间范围和时区口径。
3. 数据使用权限与允许输出的聚合指标范围。
4. 本地受控的导入环境；Git 仓库不保存原始 PDF。

## 到位后的执行顺序

```bash
export RESEARCH_HUB_BASE_URL='http://<controlled-host>:<port>'
export RESEARCH_HUB_READ_TOKEN='<read token>'
export RESEARCH_HUB_LIVE_QUERY='<de-identified query>'
npm --silent run check:live > /tmp/retrieval-http.json
```

随后在已配置的 Custom MCP Tools 中执行同一查询，把原始 MCP tool result 保存到临时文件：

```bash
npm run check:mcp-parity -- /tmp/retrieval-http.json /tmp/retrieval-mcp.json
```

最后只对最终需要引用的结果执行 MCP `fetch`，验证返回 ID 来自 search、URL 对目标客户端可达、回答包含引用，并把结论填入 `docs/evaluation/MCP_LIVE_TEMPLATE.md` 的副本。临时响应或内部文档 ID 不提交到仓库。

策略更新复测必须从 `GET /api/policy` 读取最新版本，使用 Admin Token 和最近读取的 `expected_version` 保存；更新后分别运行 HTTP 与 MCP 查询。测试结束应恢复原策略，恢复操作同样使用当时最新版本，409 时停止而不是覆盖他人修改。

## 完成判定

阶段 E 只有同时满足以下条件才可改为已完成：

- 真实 HTTP 检查通过并记录 payload/token 指标。
- HTTP 与 MCP 候选 ID 和顺序一致；高级工具可用时 Policy 版本也一致。
- MCP `search → fetch → 引用` 至少成功一组，引用 URL 对目标客户端可达。
- 策略更新后，HTTP 与 MCP 下一次查询体现新策略，且原策略已安全恢复或记录保留原因。
- 真实 metadata filter 有上游响应证据。
- 若执行真实 PDF 实验，测试实体、时间范围、别名规则和数据权限均已版本化记录在受控环境中。

## 当前无需再决定的事项

- 本地临时部署继续使用系统分配端口，不固定端口、不常驻、不开放公网。
- Dashboard 不新增客户端 reranker、embedding、LLM 摘要或 PDF 解析器。
- 标准 MCP `search` 不提供 `policy_version` 时明确标记 `not_available`，不由客户端猜测。
