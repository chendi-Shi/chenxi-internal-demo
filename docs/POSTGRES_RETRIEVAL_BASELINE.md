# PostgreSQL 检索实验路线

更新：2026-10-13。用户授权数据库路线升级为 PostgreSQL + pgvector，依次建立 baseline、混合检索、RRF、重排、query rewrite。

## 实现与当前状态

已提供隔离的 `retrieval_lab` PostgreSQL schema、SQLite 只读快照迁移、六个实验开关、评测 CLI 和失败行为测试。HTTP/MCP 可用 `--postgres-config` 统一开启 PostgreSQL 候选检索，默认安装保留 SQLite。策略、数据源控制和 fetch/citation 仍以 SQLite 权威原文校验，这是渐进迁移架构，并非所有管理数据已迁出 SQLite。

六阶段真实库测量和独立指标核算已完成，见 [匿名实测报告](evaluation/POSTGRES_REAL_REPORT.md)；官方 SDK 实际启动与中英文工具验收见 [MCP 验收](evaluation/POSTGRES_MCP_ACCEPTANCE.md)。运行选择 RRF 满足混合检索要求，但当前小样本的总体最佳仍是 lexical baseline。

| 阶段 | 唯一新增变量 | 实现 |
|---|---|---|
| B0 lexical | 无向量、无改写、无重排 | PostgreSQL GIN + `ts_rank` |
| B1 dense | 仅向量检索 | pgvector 精确 cosine；作为向量召回 baseline |
| B2 hybrid | 两路召回 | 分路最大值归一化后加和 |
| B3 rrf | 替换融合方法 | 每路 `1/(60+rank)` 累加；chunk 去重 |
| B4 rerank | 加入局部重排 | 本地 ONNX CrossEncoder 对前 50 个不同文档的代表 passage 打分 |
| B5 rewrite | 加入查询改写 | 原问题保留，自动双语规则扩展，再检索、RRF、重排 |

RRF 是混合检索的一种融合方式，用 B2/B3 分开评测才能看清增益。B5 的正式评测用 `RuleQueryRewriter` 自动扩展金融/科技双语概念，保留原文中的公司名、日期和全部原问题，不读取 qrels。也支持固定 rewrite map 供诊断。自动 LLM rewrite 提供者尚未接入，不能将规则改写称为 LLM 改写。CrossEncoder 为固定 revision 的英文 MS MARCO MiniLM ONNX 量化模型，中英文效果分别报告；本地 inference 不发送正文到外部服务。

参考：[pgvector 官方混合检索说明](https://github.com/pgvector/pgvector#hybrid-search)、[官方 RRF 示例](https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py)。PostgreSQL `ts_rank` 是词项频率全文排序，不是 BM25；不要在简历或报告中混称。宽泛自然语言查询采用它替代高开销的 cover-density `ts_rank_cd`。中文使用 `search_tokens` 的二元词项，仅在没有二元词时保留单字；英文使用字母数字词项并去除常见功能词，避免自然语言 OR 查询几乎命中全库。全文排序先选 ID/score 的 top-k，再读取 passage 正文；中英文改写增益单独测试。

## 本地复现

后端安装 `postgres` extra；重排实验额外安装 `rerank` extra。设置 `CHENXI_POSTGRES_PASSWORD` 和 `CHENXI_DATABASE_URL`（本地数据库，端口 55432；凭据只存在本地环境）。

下面的评测命令假设已有导入后的 SQLite 资料库、语义配置和对应本地模型文件。新资料库先按 [后端运行手册](../backend/docs/HUB_RUNBOOK.md) 导入，再建立语义索引；`pg_lab import-snapshot` 只复制已有向量，不会生成 embedding。模型目录必须能被当前主机访问。换机器或移动模型时先更新原资料库的模型配置，再重新导入 PostgreSQL 快照。完成下方依赖安装后，可用下列命令建立全 passage 索引并检查覆盖率；`document_samples` 是单独的代表片段策略，必须在报告中区别标注。

```powershell
python -m research_agent.hub_cli --data-dir "<原资料库目录>" index-semantic --engine e5 --model-dir "<本地E5模型>" --strategy all_chunks
python -m research_agent.hub_cli --data-dir "<原资料库目录>" retrieval-status
```

首次 E5 索引建立会下载并校验固定模型文件；之后推理仅使用本地文件。HTTP 服务还需在同一资料库运行 `hub_cli init` 生成读/管理凭据，并为 Dashboard 设置实际浏览器 origin；本地 stdio MCP 不使用这两个 HTTP token。数据库 schema 尚未导入或快照过期时，在线服务会标记 SQLite 降级，不能据此声称 PostgreSQL 检索已验收。

```powershell
# backend 目录
docker compose -f deploy/postgres/compose.yaml up -d --wait
python -m pip install -e ".[mcp,semantic,postgres,rerank]"
python -m research_agent.pg_lab prepare-reranker "<本地重排模型>"
python -m research_agent.pg_lab import-snapshot "<现有资料库>/hub.sqlite3"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage lexical --output "<本地报告>/B0.json"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage dense --output "<本地报告>/B1.json"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage hybrid --output "<本地报告>/B2.json"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage rrf --output "<本地报告>/B3.json"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage rerank --reranker-dir "<本地重排模型>" --output "<本地报告>/B4.json"
python -m research_agent.pg_lab evaluate "<人工标注查询集>.json" --stage rewrite --reranker-dir "<本地重排模型>" --rewrite-rules --output "<本地报告>/B5.json"
```

迁移在单次事务内替换专用实验 schema，失败整体回滚；不会修改 SQLite。已存在的向量按 model digest 导入，保留缺失状态，不把缺失向量填成零。当前全库只有部分 passage 向量和文档代表片段：向量覆盖率必须随结果报告，不能把实验描述成全量 passage dense baseline。模型目录失效时混合阶段记录 lexical fallback；dense baseline 直接报错。

## Baseline 查询集与报告

先人工标注至少 40 条真实问题，覆盖中文自然语言、英文、术语、公司/年份限定、多条件、语义近义词和明确无答案。开发集调参数，锁定测试集后再比较；全部阶段使用同一数据快照、embedding model、chunk、过滤条件、candidate limit、top-k。

```json
[{"id":"q001","query":"某公司的高带宽内存需求变化","filters":{},"qrels":{"人工确认的真实文档ID":2,"次相关文档ID":1}}]
```

这只是格式示意，不能直接当成真实标注。qrels 0=无关、1=相关、2=直接证据。CLI 记录逐题 Recall@10、MRR@10、nDCG@10、elapsed_ms、查询变体、降级告警、排名/页码和数据覆盖率，默认整套预热一次、重复三轮，中英指标单列。空 qrels 作为无答案控制，单独统计返回非空比例；目前只验证硬过滤无答案，不能把纯 top-k 返回视为“有答案”。报告不复制原文，但查询、ID、模型本地路径仍需保存在本地，不提交 Git。

每阶段第一条预热查询的延迟单独记录，不能把它称为整个服务冷启动：CrossEncoder 初始化发生在计时外，E5 可能在第一条查询内加载。预热后重复至少三次计算 p50/p95；报告非降级查询均值和整体均值，并列失败率。延迟包含该阶段 embedding/改写/重排推理开销。没有标注和重复运行的数字时保持 `not_run`。

### 中断后恢复

整套 CLI 支持 `--resume`：完成阶段先校验，未完成阶段从逐题 checkpoint 继续。新 checkpoint 绑定查询文件、数据快照、检索源码、模型 manifest、阶段与固定实验参数的 SHA-256；输入改变或已存 metrics 与当前 qrels 不符时拒绝复用。每次恢复都重新预热整套查询，追加的实际预热次数和进程会话数单独记录，不把三轮重复当作新的独立问题。`warmup_failures` 只统计抛出的异常，不统计预热结果内的 provider 降级告警；正式测量的失败与降级另列。

```powershell
python benchmarks/run_postgres_suite.py --config "<本地配置>.local.json" --queries "<查询集>.json" --reranker-dir "<模型目录>" --output-dir "<本机非同步报告目录>" --resume
```

旧版已完成报告缺少历史源码 fingerprint 时，只有显式追加 `--accept-legacy-complete` 才允许复用 lexical/dense/hybrid/rrf 四组；仍校验快照、逐题身份、qrels 指标、测量轮数、预热次数和失败/降级状态。此选项不会补造历史源码来源，也不接受缺少 fingerprint 的 rerank/rewrite 组。报告应披露这类历史来源限制。

每个 output 目录只能有一个运行中的 driver；CLI 本身不提供跨进程目录锁，使用者需保证这一前提。Windows 输出放在本机非同步目录，避免 OneDrive 文件锁干扰 fsync/原子替换。运行中只读取追加式进度日志，不持续打开 active checkpoint；原始查询、排名、checkpoint、模型路径和运行日志均留在本地，仅发布脱敏聚合报告。

## 调用顺序与兜底

问题 → 查询解析/保留原问题 → 可选 rewrite → 同过滤条件下关键词与向量召回 → 合并/去重 → RRF → 可选 rerank → 文档去重 → 引用返回。改写失败使用原问题；向量失败用关键词并标记；重排失败保留 RRF 顺序。离线评测的数据库异常计为失败；在线 Hub 的数据库异常按下文降级到同一原始资料库的 SQLite 索引并显式标记。

正式人工测试集是后续质量验收目标；本轮真实库小样本由 agent 直接核对原文标注，不能宣称人工盲测和泛化提升。接入时在后端统一切换 adapter，Dashboard 不实现第二套 ranking；接口保持 1.2.0。ANN/HNSW 应在精确 baseline 后作为单独实验，避免同时引入 ANN 召回损失。

## MCP 与 HTTP 的 PostgreSQL 配置

只在本地保存 JSON：`database_url`、`stage`、`candidate_limit`（默认100）、`reranker_dir`。不要把实际 DSN 写入 shell 历史或 Git。启动均使用同一份配置：

配置格式见 [`backend/deploy/postgres/runtime.example.json`](../backend/deploy/postgres/runtime.example.json)。把示例复制为资料库目录内的 `runtime.local.json`，填写本机 DSN；`reranker_dir` 相对该配置文件解析，默认 RRF 无需加载重排模型。使用 rerank/rewrite 时先下载并校验固定模型，再填写目录。

```powershell
python -m research_agent.hub_cli --data-dir "<原资料库目录>" --postgres-config "<本地配置>.local.json" stdio
python -m research_agent.hub_cli --data-dir "<原资料库目录>" --postgres-config "<本地配置>.local.json" serve
python benchmarks/run_postgres_suite.py --config "<本地配置>.local.json" --queries "<查询集>.json" --reranker-dir "<模型目录>" --output-dir "<本地报告目录>"
```

PostgreSQL 候选会回查原文 fingerprint、向量配置/数量和来源开关。快照过期或数据库故障回退到原 SQLite 检索并显式标记 `postgres_*_using_sqlite`，不显示成 PostgreSQL 成功。重排异常保留 PG RRF；embedding provider 异常使用 PG 全文检索。向量 SQL 使用局部 savepoint，异常回滚后仍能返回 PG 关键词结果，下一次请求可继续使用原连接。线上 adapter 的单条 SQL 限时 30 秒；关键词 SQL 超时或无法恢复的数据库故障走 SQLite 降级。模型初始化/推理不属于这项 SQL 时限。候选 `total` 反映受限候选池，不是全库匹配总数，诊断字段明确给出限额。权重为 0 的来源在两路召回的 LIMIT 之前排除，避免挤占可用来源候选。

Windows 本机演示采用独立 PostgreSQL 16.15 + pgvector 0.8.4；可用 Docker Compose 的 pg17配置另行部署。初始化后的 portable runtime 可通过 `python -m research_agent.pg_service <本地配置>` 启动，所需 `runtime_dir/cluster_dir/host/port` 仅存在本地配置。可选 `runtime_source_dir` 仅在运行时别名缺失时查找原安装源，不替换集群。PGDATA 应放在本机非同步目录；服务器日志必须在 PGDATA 外，启动器会拒绝内部日志路径，并避免后台进程继承等待 EOF 的输出管道。启动输出写 stderr，不污染 MCP 协议 stdout。

Dashboard 的搜索请求默认等待 120 秒，其余接口默认 10 秒；搜索时限可通过 `searchTimeoutMs` 单独配置。这是客户端等待预算，不代表模型推理有硬性 120 秒截止。

Windows stdio 的首次工具调用可能卡在 NumPy 原生扩展导入；本机无模型的 SDK 复现中，工具内首次导入超时，服务器启动前导入正常。`hub_cli` 在 `stdio/serve` 开始接收请求前按配置预载 NumPy、ONNX Runtime 和 tokenizers 库，模型仍延迟加载，纯词法部署不需要这些 extra。缺少依赖只输出固定诊断，保留查询时的显式降级。自定义服务器也应在启动传输线程前完成所需原生库导入。相关上游记录：[MCP Python SDK #2832](https://github.com/modelcontextprotocol/python-sdk/issues/2832)、[NumPy #24290](https://github.com/numpy/numpy/issues/24290)。

MCP 客户端应启动同一个 `hub_cli`，并把配置文件路径加入参数；实际私有目录由使用者填写：

```json
{
  "mcpServers": {
    "chenxi-library": {
      "command": "<backend虚拟环境>/python",
      "args": ["-m", "research_agent.hub_cli", "--data-dir", "<资料库目录>",
               "--postgres-config", "<资料库目录>/runtime.local.json", "stdio"]
    }
  }
}
```

保持三个只读工具：`search` 返回紧凑文档列表，`search_documents` 返回段落、过滤结果、页码/字符引用和诊断，`fetch` 读取原始文档。客户端只在配置重载后发现工具；注册文件存在不等于本轮会话已经发现或实际调用成功。远程 ChatGPT 接入还需要独立的受控公网传输，不能把本地 stdio 验收称为云端账号已连接。
