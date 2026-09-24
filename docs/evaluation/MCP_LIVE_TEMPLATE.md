# MCP Live Integration Record

状态：`not_run`

> 每次真实联调复制一份本模板。不要记录 Token、完整正文、用户账号或公网 tunnel 地址。

## 环境

| 字段 | 记录 |
|---|---|
| 执行时间 | `YYYY-MM-DD HH:MM TZ` |
| API version | `NA` |
| HTTP 环境 | `local / controlled-test` |
| MCP transport | `stdio / http` |
| ChatGPT / MCP client | `NA` |
| 查询 | `NA` |

## Policy 与排序

| 检查 | HTTP | MCP | 结果 |
|---|---|---|---|
| Policy version | `NA` | `NA / standard search 不提供` | `not_run` |
| Result IDs（按顺序） | `NA` | `NA` | `not_run` |
| 相同候选集合 |  |  | `not_run` |
| 完全相同顺序 |  |  | `not_run` |

执行证据：

```bash
npm run check:mcp-parity -- <http.json> <mcp.json>
```

## Search、fetch 与引用

| 检查 | 结果 | 证据或限制 |
|---|---|---|
| MCP search 成功 | `not_run` |  |
| MCP fetch 成功 | `not_run` |  |
| fetch ID 来自 search | `not_run` |  |
| 引用 URL 存在 | `not_run` |  |
| 引用 URL 对目标客户端可达 | `not_run` |  |
| 回答包含引用 | `not_run` |  |

只记录文档 ID 和 URL 可达性结论，不复制真实正文。

## Payload 与 token

| 类别 | UTF-8 bytes | 估算或实际 tokens | 测量方式 |
|---|---:|---:|---|
| search arguments | `NA` | `NA` |  |
| search result | `NA` | `NA` |  |
| fetch arguments | `NA` | `NA` |  |
| fetch result | `NA` | `NA` |  |
| tool schemas | `NA` | `NA` |  |

若平台提供实际 usage，必须与启发式估算分列，不能混写。

## Policy 更新复测

| 步骤 | 结果 |
|---|---|
| GET PolicyState 并记录 version | `not_run` |
| PUT 携带 expected_version | `not_run` |
| HTTP 下一次查询使用新 version | `not_run` |
| MCP 下一次查询体现新排序 | `not_run` |
| 恢复测试前策略或记录保留原因 | `not_run` |

## 问题与结论

- 实际问题：`NA`
- 上游接口缺口：`NA`
- 客户端规避：`NA`
- 是否满足阶段 E 退出条件：`否，not_run`
