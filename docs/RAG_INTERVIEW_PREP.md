# 大模型应用 / RAG 工程实习：项目证据与追问准备

更新：2026-10-15。目标是用 `chenxi-internal-demo` 解释检索、实验和工程取舍，并能现场核验自己的结论。下文的回答模板应按本人实际负责部分调整。

本项目当前最充分的证据是**检索与策略链路、受控消融、接口一致性和实验可复现性**。已完成的 SQLite 实跑、历史 PostgreSQL 观察、待产出结果的公开 GPU 实验分别列出；尚未完成生成答案质量、生产并发 SLO 或 HNSW 对照。

## 1. 官方招聘资料能支持什么准备方向

以下是有限的官方资料样本，用来判断能力方向，**不是“高频面试题”的频率统计**，也不代表所有公司或所有岗位的统一要求。

| 官方资料 | 能直接支持的能力方向 | 对这个项目的准备动作 |
|---|---|---|
| 百度“秒哒大模型算法实习生 J93721”，页面发布日期 2026-07-21 | 工作涉及 RAG、Agent 和应用落地；要求 Python、算法基础，列出向量检索、知识库、LLM 应用等相关经验；关注效果、效率和可用性 | 能从业务需求推导检索方案，解释评测结果，现场定位一次错误，并说清未实现的生成闭环。[岗位原文](https://talent.baidu.com/jobs/detail/INTERN/320200cd-893d-4076-b39d-1f77f7a79948) |
| Microsoft 官方 Technical interviewing | 明确覆盖问题拆解、设计、编码、测试；AI/ML 部分涉及评估、profiling 和优化 | 准备可运行代码、边界条件、复杂度与性能测量；不能只背术语。[官方指南](https://careers.microsoft.com/v2/global/en/hiring-tips/technical-interviewing) |
| Microsoft 官方 Student interviewing | 关注基础知识、批判性思考、解决问题和学习能力，具体形式随岗位变化 | 把项目讲成“假设—实现—反例—修正”，准备承认未知并提出验证办法。[学生面试说明](https://careers.microsoft.com/v2/global/en/hiring-tips/student-interviewing) |
| Amazon 官方 Applied Scientist interview prep | 强调模型假设、实验、实现、技术深度与量化项目复盘 | 可借鉴实验论证方式；这是 Applied Scientist 材料，不把其中流程或学历要求套到 RAG 应用实习。[官方指南](https://www.amazon.jobs/content/en/how-we-hire/applied-scientist-interview-prep) |

据此，本项目的准备优先级是：**先讲清指标和证据，再讲组件取舍；同时补齐 Python/SQL、服务设计、测试和失败恢复。** 岗位页面只能说明招聘方公开表达的需求，无法推导某道题会被问到的概率。

## 2. 项目证据账本：什么可以说，什么还需要补

| 证据 | 当前状态 | 可支持的陈述 | 不支持的陈述 |
|---|---|---|---|
| [SQLite 消融](evaluation/RAG_SQLITE_ABLATION.md) | 2026-10-15 实跑：17 配置 × 20 题 × 5 轮 = 1,700 次正式 `Hub.search`；另有 340 次预热 | 真实调用当前生产 ingest/search/策略路径；26 篇虚构短文、16 个有答案问题、4 个过滤控制；基线 nDCG@5≈0.8493 | 1,700 个独立问题、公开真实业务质量、embedding/重排增益、生产延迟 |
| [历史结果复核](evaluation/RAG_HISTORICAL_ANALYSIS.md) | 已生成，输入为原有报告 | 可计算真实库聚合配置差值；可在虚构库逐题结果上做配对 CI/W/T/L；保留负增益 | 真实库逐题 CI、2026-10-15 重新跑了私有库、独立人工盲标结论 |
| [真实库原报告](evaluation/POSTGRES_REAL_REPORT.md) | 历史测量日期 2026-10-13 | 20 个有答案问题 + 2 个过滤控制；向量覆盖 6.742%；阶段比较存在明确限制 | 全量向量库结论、中文天然弱于英文、稠密模型一定更差 |
| [公开数据配置](../backend/samples/rag_ablation/public_datasets.json) 与[加载器](../backend/benchmarks/public_benchmark_data.py) | 固定 revision、下载哈希、完整 corpus、分割校验和预锁定问题机制已实现；三个数据集已规范化并核验 | FiQA test 648、SciFact test 300、T2 官方 dev 已锁定 1,000；不按 qrels 删减语料 | 已完成 1,948 题模型实测；T2 是独立 test；已排除预训练数据污染 |
| [公开 GPU 实验矩阵](../backend/samples/rag_ablation/public_gpu_matrix.json) 与[runner](../backend/benchmarks/run_public_gpu_ablation.py) | 已实现；本文件未引用任何尚未产出的公开模型完成报告 | 计划 2 embedding × 2 输入长度 × 3 数据集 × 18 配置；可恢复、分离实际推理 telemetry 与缓存回放 | 把计划数量写成已完成结果；把回放 p95 写成在线推理 p95 |
| [HTTP/MCP 本地验收](evaluation/POSTGRES_MCP_ACCEPTANCE.md)、[架构](ARCHITECTURE.md) | 仓库保留本地验收记录和契约 | HTTP/MCP 共用后端检索与策略版本，Dashboard 不自行重排 | 云端 ChatGPT 账号、外部 URL 与生产权限体系全部验收 |

公开实验的当前完成状态与全部实测收益统一看[自动导出报告](evaluation/PUBLIC_GPU_ABLATION.md)。数据校验还发现 FiQA 官方语料含 38 个空文档，其中 1 条选定测试正例标注指向空文档；它们全部保留，未为改善指标删除。该发现展示的是数据审计能力，不是模型收益。

### 组件增益应如何讲

百分点（pp）是指标差值乘 100；不能与相对百分比混用。以下表格来自各自的固定样本，**不能跨数据集比较数值大小**。

| 对照 | 已观察结果 | 可辩护的解释 | 尚未验证的解释 |
|---|---|---|---|
| 历史真实库：normalized hybrid − lexical | nDCG@10 **−18.226 pp** | 这套覆盖与融合配置在该题集退化 | “混合检索必然无用” |
| 历史真实库：RRF − normalized hybrid | nDCG@10 **+4.242 pp**，Recall@10 **+20.000 pp** | 同一历史实验中，替换融合方式改善了这两个指标；MRR@10 仍下降约 0.958 pp | “RRF 全面优于所有基线”；它的总体 nDCG 仍低于 lexical |
| 历史真实库：rerank − RRF | 总体 nDCG@10 **−0.401 pp**；英文 **+6.395 pp**，中文 **−7.197 pp** | 总体均值掩盖了不同题组的方向差异，应检查模型、截断、候选和标注 | 把退化全归因于语言；中英题目的主题不同，尚未做受控语言对照 |
| 历史真实库：rewrite − rerank | nDCG@10 **+1.577 pp**；英文不变，中文 **+3.155 pp** | 该规则扩展路径的历史增量为正；只覆盖已选问题 | “LLM rewrite 提升了效果”；当前扩展是确定性规则 |
| 当前 SQLite：去掉 concept expansion / title guard | 两者 nDCG@5 差值均为 **0** | 当前小样本的相关性指标未暴露作用；title guard 关闭后 payload 仍发生变化 | “这两个组件可以删除” |
| 当前 SQLite：alpha 来源权重 ×10 | nDCG@5 **+2.31 pp**，W/T/L = **1/15/0** | 一个问题改善推动了均值；fixture 正例来源不均衡 | “整体检索提升 2.31%”“证明最优来源权重为 10” |
| 当前 SQLite：返回 top-3 相较 top-10 | nDCG@5 相同；平均详细响应约 **958.4 vs 1420.1 字节** | 这批短文中保留前三条即可覆盖相同的前五位相关性；响应体较小 | token 账单下降相同比例、长文场景也能维持答案质量 |

最有价值的追问是“**什么证据能推翻我的解释**”。例如怀疑 reranker 的语言适配，应在同一中文题集、同一候选、同一截断与池大小下比较重排模型，而不是拿另一组英文题的均值作证据。

## 3. 优先补齐的证据缺口

P0/P1/P2 是本项目的工作优先级建议，不是招聘方的评分表。

| 优先级 | 缺口与现状 | 下一步可交付工作 | 验收标准 |
|---|---|---|---|
| P0 | 公开真实语料的组件效果尚无完成报告 | 执行已锁定矩阵，保留全部配置、失败尝试、分数据集配对差值；用代表性正负样例解释变化 | 报告绑定数据/模型/源码哈希；完整 query ID 对齐；完成项和未完成项分开；不挑获胜模型 |
| P0 | 只有 retrieval 指标，无法回答“答案是否正确” | 加最小生成评测：固定模型/版本/prompt、无检索与检索对照、正确证据与干扰证据对照；标注事实、证据支持和引用 | 同一问题输出可追溯到 source/passage；分别报告答案正确性、忠实度、引用正确率/覆盖率、拒答错误和 token；保留人工抽检 |
| P0 | “为什么变好/变坏”的机制证据不足 | 给每个直接对照补候选覆盖、首个相关文档排名、重排前后位移、规则触发率、截断情况和逐题样例 | 每个主要结论能指向具体问题与排名，明确观测、推测和反证；没有改善也报告 |
| P1 | 256/512 是输入截断实验，尚无 chunking 实验 | 固定长文集合，比较 token chunk 大小与 overlap，保留 parent ID/页码/offset；固定文档级去重与聚合规则 | 同时报告 passage 证据覆盖、文档级 Recall/nDCG、索引体积、构建时间、上下文重复率；不更换 qrels 来迎合配置 |
| P1 | exact dense 已实现，HNSW 质量/性能未测 | 用相同 vectors 建 HNSW，以 exact top-k 为参照扫描 `ef_search`；固定过滤与硬件 | 分别报告 ANN Recall@k（相对 exact）、qrel 指标、p50/p95、吞吐、构建时间与内存；不可混称两种 recall |
| P1 | 公开 runner 的回放时间不等于线上响应 | 另建不读 score cache 的端到端基准，预热后交错配置，分单请求与并发压测 | 分段追踪 query encode/召回/融合/重排/生成/网络；记录失败、timeout、冷启动与缓存命中条件 |
| P1 | 有 metadata/source 过滤，无生产多租户 ACL 证据 | 增加权限来源、租户隔离、跨用户缓存隔离、撤权/删文档回归；与 fetch 同时检查 | 召回、重排输入、fetch 与引用都无越权；不能只在 UI 隐藏结果；若改变契约先走仓库契约流程 |
| P1 | 公共 qrels 可能不完整；模型训练污染无法据此排除 | 保留官方 split、记录训练集已知关系；补领域自建盲标/时间留出集与未标注结果抽查 | 不调 test 参数；标注者不知配置；记分歧与仲裁；公开基准不宣称完全未见数据 |
| P2 | 更新/删除、索引迁移、策略缓存缺少系统负载证据 | 增量同步、幂等重试、删除传播、embedding 版本迁移与回滚演练 | 原文和索引版本可追踪；中断恢复不混合模型；删除后 search/fetch 一致不可见 |
| P2 | 小卡能运行不等于已经优化到最优 | 先分解瓶颈，再比较 batch、长度、精度、缓存；预先设质量退化阈值 | 实测峰值显存、吞吐和质量曲线；记录 provider 执行证据；不将两种训练目标的模型比较称为纯参数量消融 |

生成评测应区分检索上下文质量、模型是否忠实使用上下文以及答案本身的质量；这是 [RAGAs 原论文](https://aclanthology.org/2024.eacl-demo.16/) 提供的有用分类。自动裁判可辅助扩展评测，但本项目仍应保存裁判版本、判定依据并用人工样本校准，不能把一个自动分数直接当作正确率。

## 4. 二十个项目追问与可辩护回答

### 1）你做的到底是什么，RAG 闭环到哪一步？

**回答：**项目是内部文档 Retrieval Hub 加策略 Dashboard。已做文档接入、索引、搜索、fetch、来源/新鲜度策略、HTTP/MCP 共用后端，并为检索阶段补可复现消融。当前测量主要回答“能否找到并排好证据”，没有把“能返回文档”包装成“生成答案正确”。原始 RAG 方法将检索与生成结合，这也说明仅有 retrieval 指标不能覆盖生成部分。[RAG 原论文](https://arxiv.org/abs/2005.11401)

**展示：**[架构图](ARCHITECTURE.md)、一个固定问题的 search→fetch→证据定位。追问“加生成器要测什么”时，回答第 16 题的四类结果，而不是只演示一条成功回答。

### 2）为什么 HTTP、MCP、Dashboard 要共用同一个后端？

**回答：**否则同一个 query 在不同入口可能用不同 ranking 或策略。这里 Dashboard 只呈现后端结果，不重新算 score；修改策略必须携带最近读取的 `expected_version`，遇到 409 不能静默覆盖；搜索返回的 `policy_version` 进入界面和评测记录。这个版本链可以解释“我改了权重，下一次查询为什么变化”。

**展示：**[契约说明](contracts/README.md)与[本地验收](evaluation/POSTGRES_MCP_ACCEPTANCE.md)。云端连接验收和本地 stdio 验收分开回答。

### 3）为什么同时保留 lexical 和 dense？是不是 embedding 一定更好？

**回答：**词法路径对精确名称、编号和术语匹配有价值；dense 用学习到的表示处理部分表达差异，但会受模型、领域、截断和索引覆盖影响。是否互补要看同一题集的候选覆盖与错误重合。历史真实库只有 6.742% passage 向量覆盖，dense Recall@10=0.2375 不能独立说明模型质量。公开矩阵使用完整 corpus 编码，正是为了去掉这项覆盖混淆。

**追问准备：**当前 SQLite 是 FTS5 BM25；历史 PostgreSQL lexical 是 `ts_rank`，不是 BM25。公开 runner 复用生产词项处理并使用 SQLite FTS5，不宣称它重跑了同一 PostgreSQL 引擎。

### 4）embedding 的输入、池化和相似度有什么容易做错的地方？

**回答：**先固定模型 revision、tokenizer、前缀、长度、pooling 和归一化。E5 对 query/passage 的前缀有要求，中文也要遵守；padding 位置不能参与 mean pooling。当前实现按 attention mask 平均、L2 归一化，再比较点积；对单位向量点积等于 cosine。E5 官方卡给出了这些输入与池化约定。[E5 模型卡](https://huggingface.co/intfloat/multilingual-e5-small)

**展示：**[`gpu_onnx.py`](../backend/benchmarks/gpu_onnx.py) 的 `mean_pool_normalize` 与[模型配置](../backend/samples/rag_ablation/public_models.json)。分数不是概率；相似度大于某值不能直接当作“答案置信度”。

### 5）为什么用 RRF？k=60 有什么意义？

**回答：**RRF 按各路排名相加：`sum(1 / (k + rank))`，未出现在某路的文档不给该路贡献。它降低了对不同打分尺度可比性的要求，但也丢掉原始分数间距。k 越大，头部相邻名次的相对差异越小；60 是当前配置，不是证明最优的常数。公开矩阵预设 10/60/100 对照，结果出来前不选赢家。[RRF 原论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)

**项目证据：**历史真实库 RRF 比 normalized hybrid 的 nDCG@10 高 4.242 pp，但比 lexical 低 13.984 pp。可以说改善了某种融合方式，不能说战胜所有基线。

### 6）reranker 能补回没有召回的文档吗？怎样定位瓶颈？

**回答：**在固定候选池里重排不能召回池外文档。先查 relevant document 是否进入候选池，再查候选截断、最终排序与返回 k。候选池扩大可能提高可达到的相关性上限，也会增加重排工作量和干扰项；效果必须实测。CrossEncoder 对 query-document 联合编码，适合对候选集合精排，成本也随待评分配对增加。[Sentence Transformers 官方检索与重排说明](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)

**展示：**公开矩阵的 candidate 25/50/100/200 和 rerank pool 20/50/100。它们是两个不同截断；不能把候选池变化全部归因于模型。

### 7）历史 rerank 为什么没有总体提升？

**回答：**先陈述事实：nDCG@10 总体 −0.401 pp，英文题组上升，中文题组下降。可能因素包括英语 MS MARCO 训练的适配、证据位置被截断、代表 passage 选择、候选池截断和不完整 qrels。这些是待验证假设；没有真实库逐题记录，不能给每个退化问题编原因。

**验证设计：**固定同一题集、同一候选与池大小，比英语与多语 reranker；再查看正例重排前后位置和实际送入模型的文本。中英文题目主题不相同，所以这里没有识别出“语言本身”的因果影响。

### 8）query rewrite 的收益怎么独立测？

**回答：**现有 rewrite 是固定双语规则，包含原 query 与有限扩展，不是 LLM 生成。应先记录触发率，再做 RRF、RRF+rewrite、RRF+rerank、RRF+rewrite+rerank 四格对照。交互项为 `both − rewrite_only − rerank_only + neither`；正交互只表示 nDCG 尺度上超加性，不保证 both 相对基线一定为正。

**展示：**[`rag_ablation.py`](../backend/src/research_agent/rag_ablation.py) 的 interaction 计算。历史六阶段缺 rewrite-only；新的四格补的是可识别性，不是在结果出来前宣布规则有效。规则不触发的 query 也留在总体分母，并另报激活切片。

### 9）Recall、MRR、nDCG 为什么都要报？能手算吗？

**回答：**Recall 看找回多少已标正例；MRR 看第一个正例出现多早；nDCG 同时考虑等级和位置。一个简单例子：A 的 grade=2、B 的 grade=1，返回 `[不相关, B, A]`。Recall@3=1，MRR@3=1/2；DCG@3=`1/log2(3)+3/log2(4)`，IDCG@3=`3+1/log2(3)`，nDCG@3≈0.587。用 gain=`2^grade−1`，公式和实现必须一致。

**边界：**按唯一 document ID 计数；无正 qrels 的控制题不混入相关性平均；未标注文档在当前记分中按 0，但这不等于现实中不相关。指标上升也不直接等于答案正确率上升。

### 10）五次重复为什么不是五倍样本量？CI 跨 0 怎么解释？

**回答：**同一问题重复执行主要增加运行稳定性观察，不增加独立相关性问题。先在每个问题内平均重复，再对同一 query 的配置差做配对重采样；有主题 cluster 时以 cluster 为单位。本次 SQLite 相关性样本是 16 题，不是 1,700 题。CI 跨 0 表示在这个重采样方案下方向不稳定；不跨 0 也不能消除选题偏差、标注偏差或多重比较问题。

**项目边界：**真实库匿名 JSON 只有聚合结果，不能从均值和轮数还原逐题 CI。所有比较目前作探索性描述，没有据此声称统计显著或总体泛化。

### 11）怎么防止评测泄漏和只测容易题？

**回答：**锁定官方 revision 和 qrels，先于检索固定问题集合，不按分数挑题；保留完整 corpus，不能只取相关文档。FiQA/SciFact 用官方 test，train/dev query ID 不得交叉；T2 公开版本只有 dev，必须如实称 dev 评测，1,000 题按固定 seed 的 hash 顺序选择。参数若根据 test 结果调整，就不能再把它叫未经选择的 test 结论。

**剩余风险：**这些措施防止本项目的数据处理和选题泄漏，不能证明预训练模型没见过公开基准。还需要记录模型训练数据关系，并用独立领域/时间留出样本补证。[BEIR 原论文](https://arxiv.org/abs/2104.08663)、[本项目加载器](../backend/benchmarks/public_benchmark_data.py)

### 12）256/512 tokens 是 chunk size 消融吗？

**回答：**当前公开 runner 是每篇文档的 title+text 被 encoder 截断到 256 或 512，仍然是一文档一向量；reranker 配对长度另固定为 512。这是输入截断对照，不是把长文切成多个 chunk。真正 chunking 对照应改变切分边界/overlap，同时固定 parent document qrels 与聚合去重规则，记录页码和 offset。

**解释“为什么”：**更多输入可能保留尾部证据，也可能增加无关内容和成本。生成器能接受更长上下文，也不代表能同样利用所有位置；相关研究给出了位置敏感性的实验，但本项目还没测生成器的这种现象。[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/)

### 13）你用了向量检索，为什么还不能说用了 HNSW？

**回答：**当前公开 runner 对 GPU 编码得到的全部 vectors 在 CPU 做 exact dot-product scan，并稳定处理 top-k 并列；它是精确参照，没有 HNSW 索引。HNSW 是近似搜索的另一层取舍。后续需固定 vectors 与距离，测 ANN top-k 与 exact top-k 的重合率，同时单独报 qrel relevance、内存和查询成本。pgvector 官方也明确区分 exact 与 approximate 搜索。[pgvector 文档](https://github.com/pgvector/pgvector#indexing)

**现场准备：**能解释 exact 扫描约 O(Nd)，以及 top-k 的堆/partition 选择；不要把“精确近邻召回 100%”误说成“相关文档召回 100%”。

### 14）metadata filter、来源权重和权限是同一回事吗？

**回答：**filter 决定哪些内容允许进入本次结果；来源权重影响合法候选里的排序。租户 ACL 应来自可信身份与服务端授权，不应由普通 query 参数自行决定。当前项目有过滤、禁用来源与 read/admin 边界，但没有生产企业 ACL 的完整证据。越权控制必须覆盖候选、重排输入、fetch、引用和缓存，不能只隐藏 UI 条目。

**ANN 追问：**近似索引加 filter 可能返回不足 k；应检查执行计划、过滤选择性与搜索预算。pgvector 的文档说明近似索引过滤和 iterative scan 的行为，这需要版本对应的测试，不能套用本项目 exact 路径的结论。[pgvector filtering](https://github.com/pgvector/pgvector#filtering)

### 15）关闭一个组件后指标不变，是否应该删掉它？

**回答：**先查组件是否触发、是否改变候选、是否只影响低于评估截止位的结果，以及样本是否覆盖它的目标场景。当前关闭 concept expansion 或 title guard 的 nDCG@5 都没变，但 title guard 关闭后平均 payload 从约 1420 字节变为 2277 字节。排序指标没变不等于行为没变，也不等于组件有已证明的收益。

**下一步：**增加预先设计的规则触发和歧义反例，分别报告总体与触发切片；不根据某次结果临时改题再称验证成功。

### 16）能正确返回空结果，是否证明会拒答、不会幻觉？

**回答：**不能。当前四个 SQLite 控制是“不可能过滤条件”，只验证过滤与空返回。检索失败、语料确实无答案、用户问题歧义、证据冲突是不同情形。生成评测应分别看答案正确性、陈述是否被证据支持、引用是否对应、该答/该拒的错误率；同时构造无证据、干扰证据、冲突证据与有证据场景。

**反例：**一段话忠实复述了错误或过期来源，可以“忠实”但不正确；答案碰巧正确却没有被提供证据支持，也不能说引用可靠。当前仓库尚无这组生成实测，不能填写幻觉率下降数字。

### 17）文档中出现“忽略规则、泄露凭据”怎么办？引用怎样可信？

**回答：**入库文本、snippet、正文都是不可信数据。UI 按纯文本渲染；未来生成器需要把证据作为资料边界，不把其中指令当作权限。工具权限和租户隔离由服务端控制，不能靠 prompt 替代。引用要保留稳定文档 ID、版本、页码/offset 与实际使用 passage，让用户能回到支持该陈述的原文位置。

**现有与待补：**当前契约支持 fetch 原文和本地引用验收；生产级注入攻击集、撤权传播、逐陈述引用正确率还需实测。全文 fetch 不可静默截断；若增加 passage fetch，应走契约变更与跨入口验收。

### 18）3060 Ti 跑起来之后，怎样证明用了 GPU，性能怎么讲？

**回答：**“可用 provider 列表有 GPU”不是执行证据。当前实现要求 ONNX profile 中实际 Node kernel 使用 DML，并记录模型图/tokenizer 哈希、runtime、adapter 等信息；仍可能有 CPU 算子。需要同时看设备与资源 telemetry，不能把 host profile duration 当纯 GPU kernel 时长。

**计时边界：**公开矩阵缓存真实的 query vectors、召回候选和 pair scores，便于比较质量；它的 pipeline p50/p95 是缓存辅助回放。真实推理 telemetry 另记，但不能简单把不同批次的统计值相加当在线 p95。当前 dense scan 是 CPU exact，GPU 负责 encoder/reranker。线上成本要另测实际 tokenizer 数、推理/网络/等待、并发、峰值显存与缓存条件。

### 19）两天实验中断、数据更新或模型变了怎么办？

**回答：**用唯一 query/配置记录和事务 checkpoint，只将成功且无降级的结果标完成；中断后校验数据、qrels、模型、源码和配置指纹，继续缺失项。失败尝试单独保留，不能从报告里消失。停止预算是 incomplete 状态，不是完成。模型变更后不能直接复用旧 embedding 或旧 pair scores。

**生产延伸：**策略更新、源文件更新和 embedding 模型更新是不同版本维度。缓存键必须体现影响输出的版本；增量索引要能幂等重试、删除与回滚。当前实验恢复机制可以展示，生产全链增量一致性需单独做故障演练。

### 20）如果要求现场写代码和讲一个失败，你准备展示什么？

**回答：**代码可从“去重并稳定返回 top-k”“手算并实现 nDCG”“按 query 聚合重复后计算配对差”“缓存键防止模型版本混用”中选一项，说明复杂度并覆盖空输入、重复 ID、并列、k 超范围、NaN、缺失 qrels。应能解释自己使用的 Python/SQL，而不只打开最终报告。

**失败复盘：**可讲历史 rerank 没带来总体收益：先看到总体几乎持平，再切语言组发现方向相反；承认旧报告无逐题证据，补同候选多语重排与四格消融，保留负结果。另一例是来源权重 ×10 仅让 1/16 题改善，因而没有把它直接设为“最优策略”。按“问题—假设—实现—结果—限制—下一步”组织，说明本人实际贡献。

## 5. 简历和面试现场的可用表达

以下模板适合在自己能解释代码、实验和边界的前提下按实际工作改写：

> 为文档检索 Hub 补充可复现消融评测：在固定虚构样例上执行 17 个配置、1,700 次生产搜索调用，按独立 query 统计 Recall/MRR/nDCG、配对 bootstrap 与逐题退化；验证策略版本、来源及 metadata 过滤，记录延迟边界和 payload。

> 复核 PostgreSQL lexical/dense/fusion/rerank/rewrite 历史结果，定位向量覆盖不足、分语言结果方向不同和不完整实验设计；实现固定公开数据/模型 revision、完整语料、预锁定问题及可恢复 GPU 实验流程。

第二条可写“实现流程”；公开模型报告完整产出后才能增加具体分数、样本数和资源指标。不要把原报告日期改成自己的新实测日期，也不要写“幻觉率降低”“生产 p95 降低”或“最优模型”而没有对应证据。

面试前建议完成一次 30 分钟自测：5 分钟画请求和数据链路，10 分钟手写一个指标/排序/缓存函数并测试，10 分钟复盘一正一负结果，5 分钟解释生成、ANN、权限和成本四个尚未闭合的边界。能清楚说出下一项实验如何区分两种解释，比堆砌组件名称更能展示这个项目的工作深度。

## 6. 阅读与证据入口

项目事实优先查[SQLite 实跑报告](evaluation/RAG_SQLITE_ABLATION.md)、[历史复核报告](evaluation/RAG_HISTORICAL_ANALYSIS.md)、[架构](ARCHITECTURE.md)、[公开矩阵](../backend/samples/rag_ablation/public_gpu_matrix.json)和[可恢复 runner](../backend/benchmarks/run_public_gpu_ablation.py)。招聘方材料与技术原始来源已就近链接在对应论点后；页面状态核对日期为 2026-10-15。若未来公开实验、契约或实现变化，应同步更新本页的“已完成/待补”状态和数值。
