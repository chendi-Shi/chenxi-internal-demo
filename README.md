# MCP + Retrieval Policy Demo

本仓库负责在既有 MCP / 检索服务契约之上完成 Retrieval Policy 的配置体验、效果评测与 token 观测。

当前状态：**本地 Demo 与阶段 F 交付已完成；阶段 E 真实 MCP / ChatGPT 验收等待外部环境**。项目不接触系统原始内容文件，也不在前端复制检索排序逻辑。

## 最快打开 Dashboard

环境要求：Node.js 22.18 或更高版本。

```bash
git clone git@github.com:Yuanw7/internal-demo-system.git
cd internal-demo-system
npm install
npm run demo
```

终端会显示类似下面的临时地址：

```text
Local:   http://localhost:xxxxx/
Network: http://192.168.x.x:xxxxx/
```

- 本机浏览器打开 `Local` 地址。
- 同一受信任局域网内的其他设备打开 `Network` 地址。
- 页面默认使用“官方虚构 Fixture”，无需上游服务和 Token；点击“检索证据”即可测试。
- 测试结束在终端按 `Ctrl+C`。该命令不会创建公网入口或常驻服务。

若只允许本机访问，运行 `npm run dev`。验收生产构建时运行：

```bash
npm run build
npm run preview:lan
```

## 两个部分如何接在一起

### 责任边界

| 部分 | 负责人 | 主要职责 |
|---|---|---|
| A：Retrieval Hub | 上游同事 | PDF 基础处理、分块和索引；统一检索/ranking；HTTP API；MCP `search`、`fetch`、`search_documents`；ChatGPT MCP 接入与引用 |
| B：本仓库 | Dashboard | 查看数据源与索引状态；编辑 Retrieval Policy；发送带 metadata/时间/来源过滤的查询；展示服务端顺序、分数、版本和正文 |

两部分必须共用 A 的同一个 Hub、数据库和 `PolicyState`。Dashboard 通过 HTTP 更新 Policy；下一次 HTTP 和 MCP 查询都由 A 的统一检索逻辑读取新版本。MCP tools 保持只读，Dashboard 不计算或修改服务端排序。

```text
PDF → A: 解析/分块/索引 → Hub ranking ─┬→ HTTP API → B: Dashboard
                                        └→ MCP Server → ChatGPT
                         PolicyState ← HTTP PUT /api/policy
```

接口事实来源是 [OpenAPI 1.0.0](docs/contracts/openapi.json)，示例数据在 [examples.json](docs/contracts/examples.json)。如果 A 修改字段、路径或错误码，应重新导出完整 OpenAPI 交付包，再在本仓库运行 `npm test`；不要只在两边口头约定字段。

### 1. A 启动 PDF、索引、HTTP 与 MCP

以下命令在 **A 的 Retrieval Hub/Python 仓库**运行，不是在本 Dashboard 仓库运行。具体实现交接见 [上游 Dashboard handoff](docs/upstream/DASHBOARD_HANDOFF.md)。

macOS/Linux 示例：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mcp]'
.venv/bin/python -m research_agent.hub_cli demo

export RESEARCH_HUB_READ_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export RESEARCH_HUB_ADMIN_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m research_agent.hub_cli serve --cors-origin http://localhost:5173
```

导入允许使用的 PDF 目录：

```bash
.venv/bin/python -m research_agent.hub_cli ingest /absolute/path/to/inbox \
  --source-id capital_iq \
  --source-name "Capital IQ"
```

当前 LocalFilesAdapter 支持有文本层的 PDF；扫描件/OCR、增量同步、失败重试和真实数据权限仍由 A 负责。不要把原始 PDF、Token 或真实正文提交到本仓库。

默认 HTTP 地址为 `http://127.0.0.1:8765`，接口文档为 `http://127.0.0.1:8765/docs`。若 Dashboard 实际地址不是 `http://localhost:5173`，A 必须把 `--cors-origin` 改为终端显示的精确 Dashboard Origin，例如 `http://127.0.0.1:5173`；协议、主机和端口必须全部匹配。

本地 MCP stdio 的启动配置使用 Python 绝对路径，核心命令为：

```bash
/absolute/path/to/.venv/bin/python -m research_agent.hub_cli \
  --data-dir /absolute/path/to/data/hub stdio
```

HTTP MCP 地址为 `<Hub Base URL>/mcp`，使用 Read Token。`--data-dir`、`--base-url` 等全局参数必须放在 `stdio`/`serve` 子命令之前。

### 2. B 启动并连接 Dashboard

在本仓库运行：

```bash
npm install
npm run dev
```

浏览器打开终端显示的地址，然后：

1. 在“连接环境”把“运行模式”改为“上游 HTTP 服务”。
2. Base URL 填 A 提供的地址，默认是 `http://127.0.0.1:8765`。
3. Read Token 必填，用于状态、来源、搜索、正文和读取 Policy。
4. Admin Token 可选；只有保存 Retrieval Policy 时需要。
5. 点击“连接并载入”。顶部显示“服务已连接”后即可查询。

Token 只保存在当前页面内存，不写入 localStorage 或构建产物。当前浏览器直连 Admin Token 只适合受控本地演示；生产环境应由 Dashboard 服务端代理持有凭据。

跨设备测试时，Dashboard 和 Hub 都必须监听局域网可达地址，Base URL 不能填另一台设备上的 `127.0.0.1`，A 还需允许 Dashboard 的精确 CORS Origin。

### 3. 让 ChatGPT 通过 MCP 搜索、读取和引用

在目标 ChatGPT/Codex MCP 客户端中配置 A 的 stdio 命令或可达的 HTTP `/mcp` 地址。连接成功后应能看到：

- `search({query})`：返回轻量 `id/title/url`，用于发现候选文档；
- `fetch({id})`：读取最终需要引用的少量完整文档；
- `search_documents({request})`：高级诊断工具，输入与 `POST /api/search` 一致并返回 `policy_version` 与评分明细。

推荐调用顺序是 `search → 选择少量结果 → fetch → 基于返回 URL 引用`。不要对所有候选都执行完整 fetch。远程 ChatGPT 无法访问 `127.0.0.1`；远程验收需要受控可达地址、认证和可访问的引用 URL，不能把公网 tunnel 或 Token 提交到 Git。

### 4. 两人联调验收

1. A 导入虚构或获准使用的 PDF，确认 `/api/status` 中 document/chunk/source 数量正确。
2. B 在 Live 模式执行固定查询，记录结果 ID 顺序和 `policy_version`。
3. B 修改来源权重或新鲜度，使用最新 `expected_version` 保存，再执行同一查询。
4. 确认 Dashboard 展示的是新的服务端顺序和分数组成，没有客户端重排。
5. ChatGPT 通过 MCP 执行同一查询；高级工具应与 HTTP 的结果顺序和 Policy 版本一致。
6. 对最终结果执行 `fetch`，确认 ID 来自 search、正文是纯文本、引用 URL 可达。
7. 记录 PDF 解析、弱相关公司名命中、OCR、长文 token、CORS、认证和引用可达性等问题。

可先运行只读 HTTP 检查：

```bash
export RESEARCH_HUB_BASE_URL=http://127.0.0.1:8765
export RESEARCH_HUB_READ_TOKEN='<read token>'
export RESEARCH_HUB_LIVE_QUERY='芯片'
npm --silent run check:live > /tmp/retrieval-http.json
```

取得同一次真实 MCP 搜索结果后比较顺序：

```bash
npm run check:mcp-parity -- /tmp/retrieval-http.json /tmp/retrieval-mcp.json
```

完整真实验收条件和记录模板见 [外部验收备忘录](docs/memos/EXTERNAL_ACCEPTANCE.md) 与 [MCP Live 模板](docs/evaluation/MCP_LIVE_TEMPLATE.md)。

## 常见接线问题

- **Dashboard 显示连接失败**：先检查 Hub `/healthz`、Read Token，以及浏览器控制台中的 CORS 错误。
- **能搜索但不能保存 Policy**：缺少 Admin Token，或保存期间发生 409 版本冲突；重新读取后人工核对，禁止自动覆盖。
- **ChatGPT 看不到文档**：确认 MCP 进程使用和 HTTP 服务相同的 `--data-dir`，并检查来源是否启用。
- **Dashboard 与 MCP 排序不同**：固定相同 query/filters，核对是否使用同一个数据库与 Policy 版本；标准 `search` 不返回 `policy_version`，需要 `search_documents` 做精确对比。
- **本机可用、远程引用打不开**：`127.0.0.1` 只属于调用方自己；需要受控可达的 Hub 和引用 URL。
- **自然语言查询无结果**：当前 FTS 面向关键词且要求查询词项出现，先用短关键词建立基线。
- **长 PDF 占用过多上下文**：标准 `fetch` 返回完整正文，不能由 Dashboard 静默截断；应向 A 提出 passage/range fetch 契约方案。

## 项目导航

- [MISSION.md](MISSION.md)：目标、范围和完成定义
- [ROADMAP.md](ROADMAP.md)：阶段划分、依赖和退出条件
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：整合第一部分接口后的系统架构
- [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md)：后续 coding 统一约定
- [docs/contracts/README.md](docs/contracts/README.md)：接口契约来源、优先级和变更流程
- [docs/PHASE_A_REPORT.md](docs/PHASE_A_REPORT.md)：阶段 A 交付与风险登记
- [docs/PHASE_B_REPORT.md](docs/PHASE_B_REPORT.md)：阶段 B 实现、测试与限制
- [docs/PHASE_C_REPORT.md](docs/PHASE_C_REPORT.md)：阶段 C Dashboard 与浏览器验收
- [docs/PHASE_UI_REPORT.md](docs/PHASE_UI_REPORT.md)：买方分析师工作台优化进度与验收
- [docs/PHASE_D_REPORT.md](docs/PHASE_D_REPORT.md)：阶段 D 策略实验、token 基线与限制
- [docs/PHASE_E_REPORT.md](docs/PHASE_E_REPORT.md)：真实 HTTP/MCP 联调状态与只读检查
- [docs/DELIVERY.md](docs/DELIVERY.md)：可运行 Demo、架构、MCP、ranking、问题与扩展结论
- [docs/memos/DATA_ENGINEERING_AND_SCALE.md](docs/memos/DATA_ENGINEERING_AND_SCALE.md)：真实 PDF 与规模化后续备忘录
- [docs/memos/EXTERNAL_ACCEPTANCE.md](docs/memos/EXTERNAL_ACCEPTANCE.md)：真实 HTTP/MCP/PDF 验收的前置条件与执行清单
- [docs/evaluation/BASELINE.md](docs/evaluation/BASELINE.md)：可重复生成的评测结果
- [docs/evaluation/MCP_LIVE_TEMPLATE.md](docs/evaluation/MCP_LIVE_TEMPLATE.md)：真实 MCP 联调证据模板
- [.agents/skills/mcp-retrieval-demo/SKILL.md](.agents/skills/mcp-retrieval-demo/SKILL.md)：项目级 Codex Skill

## 基线校验

```bash
npm test
```

该命令执行地基校验、生成物漂移检查、TypeScript 严格类型检查和单元测试；不连接服务、不访问网络、不处理文档原文。

## Dashboard 的其他启动方式

```bash
npm run dev
```

默认使用官方虚构 fixture，无需 Token。切换到上游 HTTP 服务时，Token 只保存在当前页面内存中。

### 局域网临时验收

```bash
npm run demo
```

Vite 会监听所有本机网络接口、由系统分配一个临时可用端口，并显示 `Network` 访问地址。用同一局域网内另一台设备打开该地址，即可先在 fixture 模式验收完整 Dashboard，不需要上游服务或凭据。

`npm run demo` 与 `npm run dev:lan` 等价。这条命令只适合受信任局域网内的临时测试，结束后按 `Ctrl+C` 停止。不要配置路由器端口转发或公网 tunnel。若要测试构建产物，可先执行 `npm run build`，再执行 `npm run preview:lan`。

Live 模式跨设备测试还需满足两点：上游服务可通过宿主机局域网地址访问，并允许 Dashboard 的精确 Origin。浏览器运行在另一台设备时，Base URL 不能使用 `127.0.0.1`。在确认上游监听参数前，本仓库不猜测或修改其启动方式。

后续部署选项记录在 [`docs/memos/DEPLOYMENT_OPTIONS.md`](docs/memos/DEPLOYMENT_OPTIONS.md)，不进入当前实现。

## 运行评测

```bash
npm run evaluate
```

输出机器可读 JSON 与 Markdown 基线。评测只消费契约 fixture，不会调用模型或修改真实服务。

## Live HTTP 只读检查

上游服务与 Read Token 就绪后，按照 [阶段 E 报告](docs/PHASE_E_REPORT.md) 配置环境变量并运行：

```bash
npm run check:live
```

该命令不会修改策略或导入数据；真实 MCP 一致性仍要求当前环境已经配置对应 MCP Server。

取得实际 MCP 搜索结果后，可按阶段 E 报告运行 `npm run check:mcp-parity -- <http.json> <mcp.json>`，自动比较结果 ID、顺序及可用的 Policy 版本。
