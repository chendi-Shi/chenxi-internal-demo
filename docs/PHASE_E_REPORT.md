# Phase E Live Integration Report

日期：2026-09-24

## 当前状态

阶段 E 仍在进行中。2026-09-24 的前置检查结果：

- `http://127.0.0.1:8765/healthz` 无服务监听；
- `RESEARCH_HUB_READ_TOKEN` 未配置；
- `RESEARCH_HUB_ADMIN_TOKEN` 未配置；
- 当前会话没有可调用的 Retrieval Hub `search`、`fetch`、`search_documents` MCP tools。

因此不能声明真实 HTTP、MCP、metadata filter 或 ChatGPT 引用链路通过。

## 只读 Live 检查

上游启动并在当前 shell 配置 Read Token 后运行：

```bash
export RESEARCH_HUB_BASE_URL=http://127.0.0.1:8765
export RESEARCH_HUB_READ_TOKEN='<read token>'
export RESEARCH_HUB_LIVE_QUERY='芯片'
npm run check:live
```

命令只执行 health、status、sources、policy、search 和首条结果 fetch，不更新策略、不导入数据。输出不包含 Token 或完整正文，只记录 source IDs、result IDs、Policy 版本一致性和 payload/token 指标。

## 通过条件

- health 返回成功；
- status、policy 和 search 的 Policy version 一致；
- search 返回符合契约的服务端顺序；
- 若有结果，首条文档 fetch 符合契约；
- HTTP payload 指标可复现。

## 仍需人工或 MCP 环境完成

1. 在 Dashboard 使用 Admin Token 更新策略，再运行相同查询确认新版本。
2. 通过已配置的 MCP tools 执行相同 `search`，比较 HTTP/MCP 结果 ID 顺序。
3. 对首条最终引用结果执行 MCP `fetch` 并完成带 URL 的回答。
4. 使用真实上游响应验证 metadata filter。
5. 记录实际 MCP tool schema、调用次数和平台 usage，而不是 HTTP envelope 估算。

缺少上述环境时，这些项目保持 `not_run`，不会由 fixture 结果替代。

真实联调记录使用 [`evaluation/MCP_LIVE_TEMPLATE.md`](evaluation/MCP_LIVE_TEMPLATE.md)，确保 Policy、排序、fetch、引用和 token 证据使用同一套字段，并避免写入凭据或真实正文。

## HTTP/MCP 排序一致性检查

把 `npm run check:live` 的 JSON 输出和真实 MCP tool result 分别保存到临时文件后运行：

```bash
npm --silent run check:live > /tmp/retrieval-http.json
npm run check:mcp-parity -- /tmp/retrieval-http.json /tmp/retrieval-mcp.json
```

检查器支持标准 MCP envelope、标准 `search` payload 和高级 `search_documents` payload。标准 `search` 契约没有 `policy_version`，因此只核对结果 ID 及顺序，并将版本检查标记为 `not_available`；高级工具同时核对 `policy_version`。这一区别不会被客户端填补或猜测。

真实响应捕获文件可能包含内部文档标识，不应提交；仓库已忽略 `artifacts/live/`。
