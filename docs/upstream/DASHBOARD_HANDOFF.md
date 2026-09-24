# 给 Dashboard 开发者 B

共同架构：[MCP_ARCHITECTURE.md](MCP_ARCHITECTURE.md)。机器可读契约：[OpenAPI](contracts/openapi.json)。
请求、响应样例：[examples.json](contracts/examples.json)。

## 本地启动

在仓库根目录使用 Python 3.11+；现有虚拟环境可直接复用。

```powershell
.venv/Scripts/python.exe -m pip install -e ".[dev,mcp]"
.venv/Scripts/python.exe -m research_agent.hub_cli demo

# 示例在本机生成两把随机测试密钥；不要将实际密钥提交仓库。
$env:RESEARCH_HUB_READ_TOKEN = & .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:RESEARCH_HUB_ADMIN_TOKEN = & .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.venv/Scripts/python.exe -m research_agent.hub_cli serve --cors-origin http://localhost:5173
```

服务地址 `http://127.0.0.1:8765`；交互式接口文档 `/docs`，点击 Authorize 输入对应 Token。
Token 通过 `Authorization: Bearer <token>` 传递。读 Token 用于查询，管理 Token 用于导入/删除/策略修改。
Token 仅存于上述进程环境，重开终端需要重新设置。配置数据持久化在 `data/hub/hub.sqlite3`。

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
若修改监听端口，也需要同步修改 `--base-url`，避免生成错误的本地引用 URL。

接口增减或字段变更先更新共同架构和模型，再重新生成 OpenAPI，并告知另一位开发者。
