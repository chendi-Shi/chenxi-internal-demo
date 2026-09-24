# A 部分：内部数据 MCP 后端运行手册

本地 MVP 已实现：导入 → 索引 → 可配置检索 → HTTP / MCP 搜索和原文读取。
不需要模型 API Key 即可运行服务、Dashboard 和协议测试。仓库不包含真实资料、凭据或本机验收报告。

## 1. 安装和启动

Python 3.11+；在解压后的项目目录或仓库根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -c requirements-dev.lock -e ".[mcp]"
.venv/Scripts/python.exe -m research_agent.hub_cli init
.venv/Scripts/python.exe -m research_agent.hub_cli serve
```

本机项目已有虚拟环境，完成依赖安装后可双击根目录 `start_hub.cmd`。
它初始化本地配置并启动服务，不自动导入数据。Ctrl+C 停止服务。

- 文档界面：`http://127.0.0.1:8765/docs`
- HTTP MCP：`http://127.0.0.1:8765/mcp`
- 健康检查：`http://127.0.0.1:8765/healthz`
- 本地凭据：`data/hub/access.local.json`，读 Token 和管理 Token 不同。
- MCP 客户端配置：`data/hub/mcp-client.local.json`，含本机绝对路径，不含 HTTP Token。
- 数据库：`data/hub/hub.sqlite3`。使用全局 `--data-dir` 可以指定另一个数据目录。

`init` 重跑保留已有凭据和 MCP 配置，不自动轮换密钥。环境变量
`RESEARCH_HUB_READ_TOKEN` / `RESEARCH_HUB_ADMIN_TOKEN` 可覆盖凭据文件中的相应字段。
凭据文件是本地明文，已被 Git 忽略；共享交接包时不包含该文件。若项目在同步盘上，该文件也可能被同步。
使用已有密钥管理方式时可只配置环境变量而不创建凭据文件。

## 2. 用独立样例目录联调

```powershell
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub init
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub demo
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub search "芯片"
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub serve --cors-origin http://localhost:5173 --cors-origin http://127.0.0.1:5173
```

此时凭据在 `data/hub/access.local.json`。样例全部明确标为虚构。
`demo` 只用于样例库；它会重新启用、更新两个 demo 来源，不应在真实库中反复执行。
默认只监听 127.0.0.1；两个不同电脑不能直接互连，可各自启动样例后联调相同契约。

## 3. 接入数据的两个入口

### 标准 JSON / 内部 API 推送

先注册来源，再提交 `IngestRequest`。`external_id` 使用原系统的稳定记录主键。
来源种类 `database` / `sharepoint` 仅为分类，不会自动连接对应系统。

```powershell
.venv/Scripts/python.exe -m research_agent.hub_cli source --id internal_demo --name "虚构标准记录样例" --kind demo
.venv/Scripts/python.exe -m research_agent.hub_cli ingest-json samples/hub_ingest.json
.venv/Scripts/python.exe -m research_agent.hub_cli status
```

对应管理接口是 `POST /api/sources` 和 `POST /api/ingest/documents`。
标准输入样例见 `samples/hub_ingest.json`。校验先于写入，批次数据库与索引写入在同一个事务里。
相同 `source_id + external_id` 重复提交跳过未变内容；更新会替换旧索引，ID 不变。
该版本不提供文档历史版本读取。

### 文件适配器

```powershell
.venv/Scripts/python.exe -m research_agent.hub_cli ingest ./inbox --source-id internal_notes --source-name "内部纪要"
```

支持 TXT、MD、JSON、JSONL、EML、带文本层 PDF。扫描 PDF 需先 OCR，邮件附件不自动导入。
坏文件记录在返回的 `errors` 中，并返回非零退出码；同目录其他可读文件继续导入。
文件多记录数据按文件内数组位置标识；需要稳定业务主键时使用标准 JSON 推送入口。
重复文件导入不会重新启用已禁用来源，也不会改写已有来源名称和种类。

接 SharePoint 或数据库只需实现 `DataAdapter.iter_documents()`，返回 `DocumentInput`。
接口定义在 `hub_adapters.py`，无需修改 MCP 或 Dashboard。

## 4. MCP 客户端连接

本地客户端可参考 `init` 生成的 `mcp-client.local.json` 中的 command / args / env。
该 JSON 使用常见 `mcpServers` 结构，具体客户端格式可能不同；不会自动写入现有客户端设置。
stdio command 指向本机 Python，不能直接作为 ChatGPT 网页上的远程服务地址。

HTTP 客户端使用 `/mcp` 与读 Token，支持 Streamable HTTP：

- `search(query)`：标准结果列表，包含引用 URL。
- `fetch(id)`：完整原文及来源元数据。
- `search_documents(request)`：筛选条件、原文片段、分数明细、策略版本。

修改策略使用管理 HTTP API，MCP 不提供写工具。远程 ChatGPT 按
[官方 MCP 指引](https://developers.openai.com/api/docs/mcp) 和
[私有网络 Tunnel 指引](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 安排连接。
当前不包含 OAuth 或用户级文档权限，不宣称已完成远程账号连接。

## 5. 常见问题

| 情况 | 处理 |
|---|---|
| credentials_required_run_init | 先运行 init，或设置两项 Token 环境变量 |
| 401 / 403 | 分别检查读 Token 和管理 Token，不能用读 Token 改策略 |
| source_not_found | 先注册来源，核对 source_id |
| policy_version_conflict | 重新 GET 策略，用最新版本核对后保存 |
| storage_unavailable | 检查数据库目录权限、空间和占用；不要删除真实数据库 |
| parse_failed_check_format_or_ocr | 检查 UTF-8、文件格式、PDF 是否有文本层 |
| 空结果 | 核对来源/日期筛选；使用公司名和指标。已支持有限中英术语扩展，语义模式可处理更多措辞 |
| 引用地址是 localhost | 本地样例正常；实际使用设置可访问的原文 URL 或 --base-url |

改端口时，不指定 `--base-url` 会自动生成对应 localhost 引用地址；代理部署时显式指定 `--base-url`。
备份前停止所有 HTTP 和 stdio 进程，再复制整个数据目录（含可能存在的 WAL/SHM 文件）。
权重 0 只排除搜索；enabled=false 同时阻止搜索与已知 ID 读取。

## 6. 验证

从 `backend/` 运行 `python -m pytest -q` 和 `python -m ruff check src tests`。
测试使用临时目录和虚构文档；它们不会读取你的资料目录。协议行为测试不等于真实 GPT 账号或企业权限验收。

## 7. 本地服务与语义索引

双击 `backend/start_hub.cmd` 可初始化并启动默认本地数据目录 `data/hub`。该命令不会自动导入任何文件。

```powershell
# 推荐 CPU 引擎：安装可选依赖，首次索引时下载固定的 E5 模型
.venv/Scripts/python.exe -m pip install -c requirements-semantic.lock -e ".[mcp,semantic]"
# 生成或补齐索引；每批落盘，失败后重跑复用已完成片段
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub index-semantic --engine e5
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub retrieval-status
# 必要时只用中英文关键词，保留已生成的向量以便重新启用
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub disable-semantic
```

默认 E5 int8 ONNX 在服务进程内运行，使用固定版本与 SHA-256 校验的公开模型。
首次建库下载模型；查询只读取本地权重，不联网、不调用云端模型。长片段按 token 窗口覆盖全文并池化，不静默丢弃尾部文本。
可选 BGE-M3 只通过 `127.0.0.1:11434` 调用本机 Ollama。
模型摘要与向量绑定，模型被替换时回退关键词并提示重新索引，避免混用向量空间。
正文、标题、页码变化会清除旧向量；日期/metadata 更新保留有效向量。
新增/更新文档立刻进入关键词索引；启用文件同步且已有模型配置时，后台自动补齐向量。
未启用同步时重跑 `index-semantic` 补齐。
`GET /api/retrieval` 的 `pending_chunks` 必须为 0 才代表全库向量齐备。
可选切回 BGE-M3：先 `ollama pull bge-m3`，再运行 `index-semantic --engine ollama`；
两种模型的向量空间严格分开，不混用。
首次建库会占用 CPU；可以先提供关键词检索，构建完成再启用混合检索。

文件适配器只在文件名包含一个明确日期时补充 `published_at`，并写入
`metadata.date_source=filename_date_utc_midnight`。这是日期精度约定，不是已知的发布时间/时区。
有多个不同日期或无法识别时保持未知，不使用文件修改时间冒充报告日期。

## 8. 可重复的业务样本验收

`deploy/verify_hub.py` 连接真实 HTTP 服务和官方 MCP SDK，检验目标文档、片段偏移、页码、
fetch 中的参考数字，以及 HTTP/MCP 排序一致性。它不调用 ChatGPT、不生成或评估模型答案。
标签文件为 JSON 数组，单条示例：

```json
{"id":"case-1","query":"某公司 资本开支","title_prefix":"目标文档标题","page":7,"contains":["参考数字"]}
```

```powershell
$CredentialFile = Read-Host "Enter the local credentials JSON path"
$CasesFile = Read-Host "Enter the private labelled test-cases JSON path"
$ReportFile = Read-Host "Enter the local report output path"
.venv/Scripts/python.exe deploy/verify_hub.py --credentials $CredentialFile --cases $CasesFile --output $ReportFile
```

标签文件应保存在私有本地目录，不要将企业文档标题、片段或标签提交到代码仓。
通用回归：`.venv/Scripts/python.exe -m pytest -q`。
生成交接包：`.venv/Scripts/python.exe deploy/package_hub.py`。

模型来源：[官方 multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)。
使用固定 revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` 的 int8 ONNX 文件；
模型和 tokenizer 均有 SHA-256 校验，校验值在 `hub_e5.py`。运行时不下载模型。

## 9. 自动文件同步与重试（2026-09-23）

管理员将监听目录保存在本地配置 `data/hub/sync.local.json` 中；不要将含有本机目录的配置提交到代码仓。
监听已配置的本地文件目录；如迁移已有文档，请通过 `bindings` 保留原有文档 ID。
新安装参考 `samples/hub_sync.json`；相对目录以配置文件所在目录为基准。
同一来源的不同根目录应配置不同 `external_prefix`，或使用明确的 `bindings` 保持已有 ID，
避免同名文件占用同一外部 ID。不要移动本机恢复目录或删除映射配置。

```powershell
# 持续服务：同步与 API 同一进程，Ctrl+C 一起停止
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub serve --sync-config data/hub/sync.local.json
# 查看状态，不影响运行中的后台同步
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub sync-status
# 下列一次性操作应先停止使用同一数据目录的后台同步，避免抢占锁
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub sync-once --config data/hub/sync.local.json
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub sync-once --config data/hub/sync.local.json --retry-failed
# 也可只运行同步，不启动 API
.venv/Scripts/python.exe -m research_agent.hub_cli --data-dir data/hub watch --config data/hub/sync.local.json
```

- 默认每轮完成后等 60 秒，文件修改时间距当前至少 30 秒才读取；读取前后再核对大小和时间。
- SHA-256 检查点成功入库后写入，失败不推进成功状态。意外退出后重放仍由稳定 ID 保证不重复新增。
- 文件失败延迟 60、120、240、480 秒重试，第 5 次失败转 `failed`；延迟上限一小时。
  相同失败内容不反复解析。修正内容后自动重试，无法读取的文件达到上限后可人工重试。
- 文件解析结果按单文件事务导入；多记录文件仍受 100 份/200 万字符限制，过大时需拆分。
- 缺失文件保留库内文档并标记 `missing`；目录不可读标记扫描不完整，不推断文件全部被删。
- 禁用来源不会被同步自动启用。新配置在重启后加载。同步不作为 Windows 开机任务安装。
- 自动补索引复用当前模型配置，不自动下载权重；首次语义配置仍需执行 `index-semantic`。
- `GET /api/sync` 和 `/api/sync/files` 需要读权限，不返回绝对根目录或正文。
- 目录中 ZIP/RAR、临时下载文件不作为成功解析文件；完整压缩包先解压，扫描 PDF 先 OCR。
- 同一资料若同时放入多个监听目录会形成多个来源记录；为每个根目录配置唯一来源或明确的 `external_prefix`。

## 10. 原始资料完整性核对

执行只读清单检查时，把私有目录和输出报告路径传给 `audit-source`。报告会包含本机路径和文件哈希，应保存在本地，不能提交到 Git。ZIP CRC 检查只能证明压缩成员字节完整，不代表 PDF 可解析或资料集完整；RAR 需要先解压再逐文件核验。
