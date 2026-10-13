# PostgreSQL real-library diagnostic

Measured 2026-10-13 on the local private PDF library using the explicitly frozen final run. The same PostgreSQL snapshot and 22 locked cases were used for all six stages: 20 answerable questions (10 Chinese, 10 English) and two impossible-filter controls. Each stage planned one complete warmup pass per process session and three measured passes. An interrupted stage resumes only saved measured cases with matching data, providers and implementation fingerprints, and performs another complete warmup before continuation. Actual warmup and process-session counts are reported below. Failed or degraded measured cases, raised warmup exceptions and incomplete stage results block publication. This is an agent-labelled diagnostic on an answer-known sample, not independent human gold or a claim of general business accuracy.

Lexical ranking uses PostgreSQL `ts_rank` term-frequency ranking, with standard query stop-word removal, Chinese bigrams and late passage-text materialization. It is neither `ts_rank_cd` cover-density ranking nor BM25. Each warmup and measured query has a savepoint so a SQL exception does not poison subsequent queries.

Snapshot: 3,143 document records, 174,070 passages, 11,736 passage vectors (6.74% coverage). Disabled source records remain in snapshot counts but are filtered from retrieval. Dense searches the indexed vector subset; keyword retrieval searches the indexed passage corpus.

| Stage | Recall@10 | MRR@10 | nDCG@10 | nDCG change vs lexical (pp) | nDCG incremental change (pp) | nDCG relative change vs lexical | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.63750 | 0.39131 | 0.44477 | +0.000 | — | +0.00% | 1426.26 | 4742.63 |
| dense | 0.23750 | 0.10464 | 0.13593 | -30.883 | — | -69.44% | 156.06 | 258.30 |
| hybrid | 0.31250 | 0.25000 | 0.26251 | -18.226 | -18.226 | -40.98% | 1598.77 | 7109.84 |
| rrf | 0.51250 | 0.24042 | 0.30493 | -13.984 | +4.242 | -31.44% | 2415.12 | 7487.20 |
| rerank | 0.36250 | 0.27667 | 0.30092 | -14.385 | -0.401 | -32.34% | 18404.25 | 59427.57 |
| rewrite | 0.41250 | 0.28292 | 0.31669 | -12.808 | +1.577 | -28.80% | 15378.34 | 37245.69 |

The selected local mixed-retrieval profile is RRF. It fulfills the keyword-plus-vector requirement, has the highest Recall@10 and Chinese nDCG@10 among the mixed stages in this diagnostic, and avoids the cross-encoder dependency. This is an operational compromise, not a claim that it won the full comparison: lexical is stronger in overall Recall@10 and nDCG@10, while rewrite has a higher overall nDCG@10 than RRF. Selection on this answer-known sample does not establish independent generalization quality, and HTTP/MCP acceptance is checked separately.

Dense is an independent parallel baseline, so it has no incremental-chain delta. The comparison progression is lexical → hybrid → RRF → rerank → rewrite: hybrid compares with lexical, RRF with hybrid, rerank with RRF, and rewrite with rerank. Hybrid sums branch scores after dividing each positive score by its branch maximum. RRF replaces that score-normalized fusion with rank fusion using `1/(60+rank)`; it is not layered on top of hybrid scores. Rerank scores up to 50 distinct-document representative passages selected by RRF. Rewrite keeps the original query and adds up to two rule expansions, runs keyword and vector recall for each variant, and fuses all lists with RRF before reranking with the original question. All stages separately report absolute percentage-point and relative-percent changes against lexical. Negative deltas are retained. Query rewrite uses deterministic bilingual rules and does not use labels or corpus evidence.

| Stage | Chinese nDCG@10 | English nDCG@10 | Degraded measured runs | Failure rate | Successful empty filter-control runs |
|---|---:|---:|---:|---:|---:|
| lexical | 0.12640 | 0.76313 | 0 / 66 | 0.00% | 6 / 6 |
| dense | 0.00000 | 0.27187 | 0 / 66 | 0.00% | 6 / 6 |
| hybrid | 0.00000 | 0.52503 | 0 / 66 | 0.00% | 6 / 6 |
| rrf | 0.07197 | 0.53788 | 0 / 66 | 0.00% | 6 / 6 |
| rerank | 0.00000 | 0.60184 | 0 / 66 | 0.00% | 6 / 6 |
| rewrite | 0.03155 | 0.60184 | 0 / 66 | 0.00% | 6 / 6 |

The language groups contain different topics and are not parallel translations; their score difference cannot isolate a language effect. The cross-encoder is the English MS MARCO MiniLM model, pinned to revision `a09144355adeed5f58c8ed011d209bf8ee5a1fec`.

| Stage | Actual warmup runs | Additional warmup runs | Raised warmup exceptions | Process sessions | Data/provider/source fingerprint recorded |
|---|---:|---:|---:|---:|---|
| lexical | 22 | 0 | 0 | not recorded | no; legacy completed result explicitly revalidated |
| dense | 22 | 0 | 0 | not recorded | no; legacy completed result explicitly revalidated |
| hybrid | 22 | 0 | 0 | not recorded | no; legacy completed result explicitly revalidated |
| rrf | 22 | 0 | 0 | not recorded | no; legacy completed result explicitly revalidated |
| rerank | 22 | 0 | 0 | 1 | yes |
| rewrite | 44 | 22 | 0 | 2 | yes |

The first four stages were freshly measured on 2026-10-13; the snapshot import timestamp predates these measurements. Their healthy complete results were explicitly reused after validation of the unchanged snapshot, locked questions and qrels, per-case identities, ranking metrics and aggregates. Here "legacy" refers only to results written before checkpoint source fingerprints were introduced, not to measurements from an earlier date. Historical hashes and process-session counts are not invented. Resumed model-stage timing can span process sessions, so additional warmup does not make the timing a controlled speed experiment.

`warmup_failures` counts raised exceptions only. Provider warnings returned during warmup were not collected; zero raised exceptions cannot establish zero warmup degradation. The failure and degradation columns in the table above concern all 66 measured cases, whose per-case exceptions and provider warnings were retained and independently checked.

Only one driver may write a given output directory at a time; a local file lock prevents concurrent driver writers. Checkpoint continuation was relocated to a nonsynchronized local directory exposed by a junction after synchronization-related file locking, preserving earlier completed results. The report consumer reads completed stage artifacts, never active checkpoints. Checkpoint serialization and writes occur outside reported per-query retrieval timing.


Rule rewrite produced at least one additional query variant for 9 / 20 answerable questions (45.0%); Chinese 4 / 10, English 5 / 10. Mean variants per measured answerable query, including the original query: 1.45. Activation means a rule generated a variant; it is not a relevance-success rate.

The rerank stage scored a mean of 50.00 distinct-document representatives per answerable query (maximum 50, configured cap 50).
The rewrite stage scored a mean of 50.00 distinct-document representatives per answerable query (maximum 50, configured cap 50).

Ranking metrics are macro-averaged over the 20 answerable questions; the three repeats do not increase the independent sample size. Each stage has 66 measured runs and all six have 396, but there are only 22 independent questions, including the two filter controls. Latency aggregates cover all 66 measured runs per stage, including the empty filter controls. Overall ranking scores retain failed/degraded runs; the machine-readable report additionally includes nondegraded ranking averages.

Agent verified selected original bodies and evidence anchors before retrieval comparison. Qrels are non-exhaustive: unjudged documents are treated as zero and some other valid evidence may therefore be penalized. Full-body duplicates share labels but count as separate document IDs in recall. The two negative controls test impossible filters; they do not establish semantic abstention or answer-generation quality.

The benchmark compares raw staged retrieval rankings. Dashboard source-weight and recency Policy overlays are separate and may change online rankings; their consistency must be checked across HTTP and MCP independently of this offline benchmark.

Latency includes per-query embedding, rewrite and reranking work on the local CPU, plus any lazy initialization executed inside the retrieval timer. Setup performed before that timer, such as cross-encoder provider construction, is excluded. Lazy E5 or runtime initialization can contribute to the first warmup query; measured aggregates follow warmup. First-query timing records that first warmup query, does not isolate one initialization component, and is not the full service cold start. No response generation or network transport is measured. p50 is the median and p95 uses nearest rank. Stages run in fixed sequence on a shared CPU. Unrelated independent Python workloads were running on the same machine and were preserved; CPU load and task isolation were not controlled. Cache, contention, workload and process-session differences prevent treating stage latency differences as causal performance improvements. These timings are local observations, not a production throughput claim.

The anonymized machine-readable aggregate, language split, percentage-point and relative changes are in [POSTGRES_REAL_REPORT.json](POSTGRES_REAL_REPORT.json). No real questions, document IDs, source text, local model paths or credentials are published. Local raw results remain outside Git.
