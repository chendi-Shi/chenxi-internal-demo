# ChatGPT 账号接入：后续步骤

真实账号接入另行安排。因此当前不创建远程账号连接，
也不把本地协议测试写成 ChatGPT 真实调用验收。

## 已具备的后端

- 标准只读 `search`、`fetch`，以及带筛选、片段、页码的 `search_documents`。
- HTTP Streamable MCP：`http://127.0.0.1:8765/mcp`，使用读 Token。
- stdio：本机 Python 运行 `-m research_agent.hub_cli --data-dir <数据库目录> stdio`。
- `data/hub/mcp-client.local.json` 记录本机命令；给别的电脑使用时须重新生成路径。

## 私有连接方案

参考 [OpenAI Secure MCP Tunnel 官方文档](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
（2026-09-22 核对）。可以通过出站隧道连接本地 stdio 或 HTTP MCP，不必公开内部服务器。
需要目标账号/工作区关联的 tunnel、运行凭据及相应权限；ChatGPT 开发者模式权限单独管理。
账号条件以实际管理界面为准。

准备接入时，先在 [Platform tunnel 设置](https://platform.openai.com/settings/organization/tunnels)
创建/选择对应 tunnel，按官方当前下载入口安装客户端，查看 `tunnel-client help quickstart`。
优先用本地 stdio 命令接入，避免额外配置 HTTP 读 Token 转发。
在 ChatGPT 的插件设置中选择 Tunnel，绑定相同的 tunnel；保持本机客户端与数据服务可用。
不要将 API Key、读 Token 或管理 Token 复制进对话、截图、Markdown 或源码交接包。

## 真实接入后的验收

1. ChatGPT 实际发现三个只读工具。
2. 用已标注问题调用搜索，随后调用 fetch 核对原文，不凭工具标题推断数字。
3. 引用须包含目标文档与页码；核对原文字符位置。
4. 从 HTTP 修改来源权重后再次调用，检查排序和 `policy_version`。
5. 禁用来源后，搜索与已有 ID 的 fetch 都不可读。

返回的 localhost 引用 URL 是本地受鉴权文档地址。隧道代理工具调用，不会自动把该 URL
变成可分享的网页。若需要浏览器中直接打开引用，还需接公司文档站点或受鉴权文档查看页面。
当前本地交付不包含用户级权限、OAuth 或企业部署。
