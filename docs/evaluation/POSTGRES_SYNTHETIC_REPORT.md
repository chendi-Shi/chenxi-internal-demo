# Synthetic PostgreSQL function verification

Historical function diagnostic, measured before the subsequent natural-language stop-word / Chinese-bigram query-tokenization performance fix. Final real-library stage metrics use the revised implementation. This report preserves the actual earlier run and is not a claim that the final source reproduces these exact scores.

Measured 2026-10-09 on a separate `chenxi_synthetic` database with PostgreSQL + pgvector, multilingual E5 embeddings for all 26 fabricated passages, and the pinned local MS MARCO MiniLM CrossEncoder. The 20 locked queries include 16 answerable questions and four impossible-filter controls. These results do **not** measure retrieval quality on the private PDF library.

| Stage | Recall@10 | MRR@10 | nDCG@10 | nDCG change vs lexical (pp) | nDCG incremental change (pp) | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.96875 | 0.78125 | 0.83162 | +0.000 | — | 4.61 | 22.69 |
| dense | 1.00000 | 0.78125 | 0.83903 | +0.741 | — | 20.16 | 96.55 |
| hybrid | 0.96875 | 0.77083 | 0.82344 | -0.818 | -0.818 | 77.39 | 153.34 |
| rrf | 0.96875 | 0.78125 | 0.83162 | +0.000 | +0.818 | 94.07 | 184.51 |
| rerank | 0.96875 | 0.75000 | 0.80707 | -2.455 | -2.455 | 1919.86 | 4735.30 |
| rewrite | 0.96875 | 0.75000 | 0.80707 | -2.455 | +0.000 | 1342.89 | 2265.36 |

Dense is an independent baseline without an incremental-chain delta. The incremental chain is lexical → hybrid → RRF → rerank → rewrite; hybrid is compared with lexical, never with dense. Hybrid and RRF are alternative fusion strategies. Rerank and rewrite add components to the RRF path. In this small controlled corpus, dense increased recall while normalized fusion and the English reranker reduced ranking quality. The rules-based rewrite did not change final ranking metrics. Added components are therefore not assumed to produce a positive gain.

Every stage had zero errors and zero degraded queries. All four filter no-answer controls returned empty results. This confirms filter/empty-result behavior only; no semantic abstention classifier is evaluated.

There was one measured pass and no warmup, and other local experiments ran concurrently. Latency includes first-use inference cost and CPU contention; these figures are function diagnostics, not a defensible latency speedup comparison. This fixture does not select the live retrieval profile.

| Stage | English nDCG@10 (8 answerable) | Chinese nDCG@10 (8 answerable) |
|---|---:|---:|
| lexical | 1.00000 | 0.66324 |
| dense | 1.00000 | 0.67806 |
| hybrid | 1.00000 | 0.64687 |
| rrf | 1.00000 | 0.66324 |
| rerank | 1.00000 | 0.61414 |
| rewrite | 1.00000 | 0.61414 |

The machine-readable aggregate and per-query rankings are in [POSTGRES_SYNTHETIC_REPORT.json](POSTGRES_SYNTHETIC_REPORT.json). Reproduce the corpus with [the synthetic fixture instructions](../../backend/samples/postgres_synthetic/README.md).
