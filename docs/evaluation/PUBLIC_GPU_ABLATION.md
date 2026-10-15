# 公开数据 GPU 检索消融：增益与证据

分析日期：2026-10-15（Asia/Shanghai）。发布状态：**no_completed_results**；运行器状态：`running`。

只有完整 query/配置记录、聚合复核及实际 GPU 执行证据通过验证的 job 进入质量表。原始文本、模型文件和本机路径不在本报告内。

| 项目 | 计划 | 已核验完成 |
|---|---:|---:|
| dataset × model × length jobs | 12 | 0 |
| 配置单元 | 216 | 0 |
| query × 配置观测 | 140256 | 0 |

计划独立问题数：1948（按数据集分开）。已核验问题数：`{}`。重复配置不会增加独立问题数。

**当前没有可发布的完整公开模型质量结果。没有填入零分、模拟分数或预期增益；后续重新导出才会出现实测表格。**

## 跨模型与编码器输入长度

只比较 dense、RRF、RRF+rerank、RRF+rewrite+rerank 四个固定配置；要求相同数据/问题哈希、完整相同 query IDs、配置及运行指纹。模型参考为矩阵中的首个模型，长度参考为较短编码器输入。256/512 仅改变文档和 query 的 embedding 编码器输入；两组 cross-encoder reranker 都固定 512。

尚无兼容的已完成 job 配对；跨模型/长度结果保持待测。

## GPU 证据、成本与限制

各 job 的 DML compute kernel 事件及覆盖信息保存在 JSON 的 gpu_proofs；请求 GPU provider 本身不算执行证据。CPU 算子仍单独记录。

实际推理 telemetry 来源：`unavailable`。只取一个累计快照，绝不把每个 job 的累计值相加。详情见 JSON。
Pipeline p50/p95 混合首次推理和缓存回放，只保留在 JSON 作运行诊断，不能写成在线延迟。密集排序是 CPU exact scan；256/512 是 embedding 编码器输入截断，不是 chunking，reranker 均固定 512。初始化、生成、网络和生产并发不在该推理统计内。

T2 使用官方 dev；中英数据集主题和语料不同，不构成受控语言对照。所有 CI 都是探索性配对 query/cluster bootstrap，没有多重比较校正；只评估检索，没有生成正确率、忠实度或语义拒答结论。

未通过导出验证的 job 数：0；跳过的不兼容跨 job 对照：0。原因代码见 JSON，未通过项不进入质量均值。

[完整 JSON](PUBLIC_GPU_ABLATION.json) 包含 k=1/3/5/10、绝对值、百分点/相对变化、案例正例名次及来源哈希；[CSV](PUBLIC_GPU_ABLATION.csv) 便于筛选配置和指标。
