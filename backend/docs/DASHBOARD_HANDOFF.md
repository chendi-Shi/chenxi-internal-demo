# 给 Dashboard 开发者 B

共同架构：[MCP_ARCHITECTURE.md](MCP_ARCHITECTURE.md)。机器可读契约：[OpenAPI](contracts/openapi.json)。
请求、响应样例：[examples.json](contracts/examples.json)。

## 2026-09-23 本地交付更新（API 1.2.0）

新增两个只读接口，搜索和策略字段兼容上一版：

- `GET /api/sync`：`configured`、最近一轮 `last_cycle`、各状态数量 `files`。
- `GET /api/sync/files?state=failed&limit=50&offset=0`：文件相对名、来源、状态、失败次数、
  下一次重试时间与错误码；返回 `total`，每页最多 200 条。

状态：`ok` 已完成、`settling` 等待文件稳定、`retrying` 等待重试、`failed` 达到重试上限、
`missing` 文件消失但库内保留。`next_retry` 是 Unix 秒，仅对 `retrying` 有实际调度意义；
`failed` 需改正文件或管理员人工重试。`worker_recent` 仅表示最近有活动，不能当作进程存活证明。
`semantic=retry_next_cycle` 表示文件已入库而模型暂不可用，下一轮会重试补索引。
未配置语义模型时为 `not_configured`，需管理员先执行一次 `index-semantic`。
文件同步路径和重试操作由本机管理员控制，B 的前端不提交任意文件路径。
相对文件名也须按纯文本渲染。压缩包未下载完成的核对报告单独交付，不计入成功同步文件数。

以下为此前已提供的语义检索接口：

原有请求字段和策略字段保持不变。新增 `GET /api/retrieval`，使用读 Token，
返回语义索引是否启用、模型名、已索引/待索引片段数。新增搜索响应字段 `retrieval`：

- `mode=bilingual`：中英文术语扩展 + 关键词检索。
- `mode=hybrid`：关键词与本机 E5（可选 BGE-M3） 语义检索融合。
- `mode=bilingual_fallback`：本地模型不可用或模型版本变化，保留关键词结果；`warning` 给出原因码。

B 可用这两个字段显示检索状态，不要将 `score` 当作概率。
来源权重、时间偏好、过滤和 `policy_version` 在两种检索模式中一致生效。
本地验收请在合并仓库中分别运行前端 Node.js 测试和本目录所述 Python 测试。真实账号接入状态见 [ChatGPT 连接说明](CHATGPT_CONNECTION.md)。

## 本地启动

在仓库根目录使用 Python 3.11+；现有虚拟环境可直接复用。

```powershell
.venv/Scripts/python.exe -m pip install -e ".[dev,mcp]"
.venv/Scripts/python.exe -m research_agent.hub_cli init
.venv/Scripts/python.exe -m research_agent.hub_cli demo
.venv/Scripts/python.exe -m research_agent.hub_cli serve --cors-origin http://localhost:5173
```

服务地址 `http://127.0.0.1:8765`；交互式接口文档 `/docs`，点击 Authorize 输入对应 Token。
Token 通过 `Authorization: Bearer <token>` 传递。读 Token 用于查询，管理 Token 用于导入/删除/策略修改。
Token 保存在 `data/hub/access.local.json`，init 重跑不会覆盖；环境变量可覆盖对应字段。
配置数据持久化在 `data/hub/hub.sqlite3`。完整操作见 [A 运行手册](HUB_RUNBOOK.md)。

该服务不包含 Dashboard 页面，默认没有前端 Origin 白名单。若 B 使用 127.0.0.1 而非 localhost，需明确添加对应 Origin。
两个不同电脑不能直接通过这个 loopback 地址联调；前期各自启动相同样例，后期再安排受控测试环境。

## 前端最小调用

本地开发示例。生产版请由 Dashboard 服务端持有 Token、校验用户权限后转发请求。

```javascript
async function search(baseUrl, readToken, query) {
  const response = await fetch(`${baseUrl}/api/search`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', Authorization: `Bearer ${readToken}`},
    body: JSON.stringify({query, source_ids: [], metadata: {}, limit: 10}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error.code);
  return payload;
}
```

检索结果的 snippet、标题和原文按纯文本渲染，不直接插入 HTML。

## 保存配置

1. GET `/api/policy`，保留返回的 `version`。
2. 编辑完整 policy 对象。
3. PUT `/api/policy`，body 为 `{expected_version: version, policy: editedPolicy}`。
4. 成功后更新本地 version，再执行搜索；检查 `policy_version` 一致。
5. 返回 409 时读取最新配置并提示用户核对，禁止自动覆盖。

前端首次只需三块：数据源列表、搜索结果及原文、检索策略编辑。
布局、组件显隐、已保存查询由 B 管理，不放进 `/api/policy`。
如果后续需要多用户视图持久化，由 B 的应用服务新增 `/dashboard/views` 等独立接口；当前不承诺该接口已实现。

## A 的数据导入与 MCP

```powershell
# 本地文件：TXT / MD / JSON / JSONL / EML / 有文本层的 PDF
.venv/Scripts/python.exe -m research_agent.hub_cli ingest ./inbox --source-id internal_notes --source-name "内部纪要"

# 本地 MCP 客户端配置的 executable 使用 Python 绝对路径；args 如下。
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub stdio

# 重生成契约；模型是唯一事实来源。
.venv/Scripts/python.exe -m research_agent.hub_cli export-openapi docs/contracts/openapi.json
.venv/Scripts/python.exe -m pytest tests/test_hub.py -q
```

HTTP MCP 地址 `/mcp`，要求读 Token。远程 ChatGPT 所需身份连接与网络部署单独安排。
CLI 全局参数 `--data-dir`、`--base-url` 必须放在子命令之前。
默认引用地址随 serve 的监听端口变化；代理部署时显式指定 `--base-url`。

接口增减或字段变更先更新共同架构和模型，再重新生成 OpenAPI，并告知另一位开发者。
