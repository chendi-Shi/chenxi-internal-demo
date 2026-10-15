# 公开中英文检索消融：RTX 3060 Ti 8GB

实验登记日期：2026-10-15。目标是回答各组件带来多少检索收益、哪些问题受益或退化，以及计算代价。最长 48 小时为执行预算；提前完成即停止，不重复相同质量样本来填满时间。该文档登记协议，不包含尚未完成的成绩。

启动时的 [GPU 预检证据](evaluation/PUBLIC_GPU_PREFLIGHT.json) 已包含实际矩阵计算校验、两个 embedding 和两个 reranker 的真实 DirectML kernel 记录，并通过 encoder 与多语言 reranker 同时驻留的 512×8 联合推理。四个 tokenizer 对全部 1,948 个查询的配对编码预检也通过。这些小规模冷启动测试不是正式检索成绩；正式评估由后台 supervisor 执行。

## 数据与比较单位

| 数据集 | 完整语料文档数 | 评估查询数 | 划分与用途 |
|---|---:|---:|---|
| SciFact | 5,183 | 300 | 官方 test；英文科学论断检索 |
| FiQA | 57,638 | 648 | 官方 test；英文金融问答检索 |
| T2Retrieval | 118,605 | 1,000 | 官方 dev 中按固定种子抽样；中文开发集探索 |

所有候选都来自完整语料，不根据 qrels 挑选候选文档。固定版本、文件大小、LFS SHA-256 或 Git blob SHA-1、来源地址见 [数据清单](../backend/samples/rag_ablation/public_datasets.json)。下载器不执行远端数据集脚本。准备后的语料、题集、抽样种子、哈希和划分写入本地 manifest，恢复时重新核对。

实际准备发现 FiQA 官方语料中有 38 篇空文档，其中一个评估问题的正例指向空文档。本实验保留全部文档和原始 qrels，并在 manifest 中记录数量与 ID，不静默删除或改标；这是解释该题失败时必须考虑的数据局限。

1,948 个问题是独立评估单位，重复配置不会增加问题数。三个基准的领域、题型、语言同时不同，不能把分数差异解释成单独的语言效应。T2 的 dev 结果不能标成 held-out test。公开测试集用于预先登记的比较，不用来调出一个“最佳参数”再声称测试泛化收益。

## 固定实验矩阵

矩阵见 [public_gpu_matrix.json](../backend/samples/rag_ablation/public_gpu_matrix.json)，模型 revision 与每个文件哈希见 [public_models.json](../backend/samples/rag_ablation/public_models.json)。

- Embedding：multilingual E5-small 与 multilingual MiniLM；二者的训练目标也不同，因此这是模型选择敏感性，不是纯参数量消融。
- 编码截断长度：256、512 token；这是输入长度实验，不是 chunk size/overlap 实验。文档为标题加正文，每文档一个向量，没有 passage 聚合。
- 召回：FTS5 BM25、dense、normalized hybrid、RRF。Dense 使用 GPU 生成的真实向量，在 CPU 上做完整 exact cosine 参考检索；不声称 GPU ANN 性能。
- RRF 平滑参数：10、60、100；每路候选预算：25、50、100、200。
- Rerank：关闭、20、50、100 个候选；分别比较 lexical/dense/hybrid 后重排，以及固定候选上的英文与多语言 cross-encoder。
- 规则 query rewrite 与 rerank：完整 2×2 开关，单独估计主效应和交互；规则保留原查询，不是 LLM query rewrite。

预定为 3 数据集 × 2 embedding × 2 截断长度 × 18 配置，共 216 个结果单元、140,256 条 query/config 观测。完整语料共 181,426 篇，四种编码设置共 725,704 次文档编码。缓存只复用实际算出的向量与 query/document 分数，不能代替首次推理或伪造缺失结果。

## 每个组件如何报告“多少、为什么”

每一比较都保留 reference、variant、绝对值、百分点差、相对变化（基线非零时）、95% 配对 bootstrap 区间、胜/平/负问题数和实际样本数。Recall、MRR、nDCG 分别在 @1/@3/@5/@10 报告；nDCG 使用 `2^grade−1`，公共数据的原始等级不压成二值。未标注文档按零相关处理，讨论 qrels 不完整的局限。

| 组件 | 必须保持一致的部分 | 解释证据 |
|---|---|---|
| 添加 dense 或 lexical 通道 | 同一完整语料、查询、融合、后处理 | 两通道正例重叠/互补、候选召回变化；模型相似度与关键词漏召回案例 |
| normalized → RRF | 同一两路候选 | 正例名次改变、分数尺度敏感性；RRF 只使用排名并不保证更好 |
| 添加 rerank | 同一候选池和预算 | 重排前后的正例名次、rerank pool recall 与候选 oracle 上界；候选缺失时不能归咎于重排 |
| 添加 rewrite | 同一原查询、模型和预算 | 生成的规则变体、候选新增/丢失、无触发规则的比例；区分召回改善与查询漂移 |
| 扩大候选或重排预算 | 同一模型和后处理 | 多召回的正例、实际打分数量、增量计算成本，检查收益是否饱和 |
| 改变模型/截断长度 | 同一数据和下游参数 | 逐题正负变化，结合长文截断现象提出可验证解释；不可直接归因为“参数更大” |

结果解释分三层：**测量事实**（某问题的名次/指标改变）、**代码机制**（改动实际改变什么）、**待验证假设**（可能的语义/领域原因）。只看平均分不能证明原因。会保留零收益、负收益及置信区间跨零的结果，避免只展示最优行。大量探索比较不作未经多重比较校正的“显著胜出”宣称。

## GPU、成本与恢复

运行使用 Windows ONNX Runtime DirectML FP32 模型。会先执行数值可验证的矩阵乘法，再执行实际 tokenizer/encoder/cross-encoder pilot。必须有 ONNX profiling 中实际 `DmlExecutionProvider` kernel 事件；provider 注册、显存占用或设备可见均不足以证明 GPU 推理。CPU fallback 算子也记录。DirectML 按官方要求使用 sequential execution 并关闭 memory pattern。

每次真正的 embedding/rerank batch 单独记录耗时、条数和模型指纹；总建库耗时与 query pipeline 区分。缓存辅助的 pipeline p50/p95 **不是在线端到端推理延迟**，不能写成服务 SLA。后台采样记录整张卡的显存、利用率、温度、功耗，包括其他进程，不能当成此任务独占占用。

每个 batch 和 query 落盘，完成标记只在所有预定观测和 GPU 证据齐全后写入。指纹不匹配、NaN、降级、失败或数据改变会中止，而不是填零。达到 48 小时会标为 `budget_exhausted` 并保留有效检查点；已完成部分和未完成部分分开报告。恢复同一目录不能偷偷更新模型、矩阵或源码。

## 复现

本次 GPU 环境为 Windows x64、Python 3.12.10，精确依赖见 `backend/requirements-public-gpu.lock`。从仓库根目录，在单独 Python 3.12 环境中安装项目和 GPU 依赖：

```powershell
python -m pip install -c backend/requirements-dev.lock -e "./backend[dev,mcp]"
python -m pip install -c backend/requirements-public-gpu.lock -r backend/requirements-public-gpu.txt
python backend/benchmarks/check_gpu_runtime.py --output backend/artifacts/gpu-runtime.local.json
python backend/benchmarks/prepare_public_models.py --root backend/data/public-rag/models
python backend/benchmarks/public_benchmark_data.py all --output-root backend/data/public-rag
python backend/benchmarks/run_public_gpu_ablation.py --models-dir backend/data/public-rag/models --data-dir backend/data/public-rag --output-dir backend/artifacts/public-gpu/run --pilot
python backend/benchmarks/run_public_gpu_ablation.py --models-dir backend/data/public-rag/models --data-dir backend/data/public-rag --output-dir backend/artifacts/public-gpu/run --budget-hours 48
```

不要在此环境同时安装项目 semantic/rerank 的 CPU `onnxruntime` extras：它与 `onnxruntime-directml` 共享 Python 包名。原模型文件、数据、检查点和日志均在忽略目录内，不提交 GitHub。

持续执行可使用 `supervise_public_campaign.py`，参数为 `--models-dir`、`--data-dir`、`--output-dir` 和 `--budget-hours 48`。它顺序执行 runtime check、准备、pilot、正式实验，在 `campaign-status.local.json` 记录状态。Windows 启动后台任务须使用隐藏窗口。任务运行期间临时请求系统保持唤醒，不改永久电源设置；退出后释放。保持电脑开机和供电。

停止时在 supervisor 输出目录创建 `STOP` 文件；正式 worker 会在批次/查询边界保存并退出。重启前先核对 PID，确认没有活动进程，再移除 STOP 或陈旧锁；不得同时启动两个写同一输出目录的进程。

## 尚不覆盖的能力

本轮完整文档检索不能证明 chunk/overlap 最优、HNSW 索引性能、多租户 ACL、生成答案正确率或幻觉下降。原项目是证据检索工具层；这些项目的实际证据和下一步优先级见 [面试准备与缺口](RAG_INTERVIEW_PREP.md)。源自公共基准的成绩也不等于原私有资料库的成绩。

来源：[BEIR](https://github.com/beir-cellar/beir)、[SciFact](https://huggingface.co/datasets/BeIR/scifact)、[FiQA](https://huggingface.co/datasets/BeIR/fiqa)、[T2Retrieval](https://huggingface.co/datasets/C-MTEB/T2Retrieval)、[DirectML 官方要求](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)。
