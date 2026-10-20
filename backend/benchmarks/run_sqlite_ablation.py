"""Actual offline Hub/SQLite ablations on the committed public synthetic corpus.

Run from backend: python benchmarks/run_sqlite_ablation.py
The runner uses fresh temporary databases and never opens a user's Hub database.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))

from research_agent import hub_query  # noqa: E402
from research_agent.hub import Hub  # noqa: E402
from research_agent.hub_models import (  # noqa: E402
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SearchResponse,
    SourceDefinition,
)
from research_agent.store import search_tokens  # noqa: E402

PLANNERS = {"full", "no_concept_expansion", "no_title_guards", "raw_lexical_or", "raw_lexical_and"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_inputs(suite, matrix):
    if suite.get("dataset_kind") != "synthetic":
        raise ValueError("only_public_synthetic_suite_supported")
    docs = suite["documents"]
    doc_ids = [doc["id"] for doc in docs]
    query_ids = [query["id"] for query in suite["queries"]]
    if not docs or len(set(doc_ids)) != len(doc_ids):
        raise ValueError("duplicate_or_empty_fixture_documents")
    if not query_ids or len(set(query_ids)) != len(query_ids):
        raise ValueError("duplicate_or_empty_fixture_queries")
    disabled = set(matrix["disabled_source_ids"])
    sources = {doc["source_id"] for doc in docs}
    if not disabled <= sources:
        raise ValueError("unknown_disabled_source")
    for query in suite["queries"]:
        if not set(query["qrels"]) <= set(doc_ids):
            raise ValueError("qrel_document_missing")
        if any(not isinstance(grade, int) or grade < 0 for grade in query["qrels"].values()):
            raise ValueError("invalid_relevance_grade")
        if "limit" in query["filters"]:
            raise ValueError("query_limit_would_override_top_k_experiment")
        SearchRequest(query=query["query"], **query["filters"])
        for doc in docs:
            if query["qrels"].get(doc["id"], 0) > 0 and doc["source_id"] in disabled:
                raise ValueError("positive_qrel_in_disabled_source")
    variants = matrix["variants"]
    by_id = {v["id"]: v for v in variants}
    if len(by_id) != len(variants) or matrix["baseline_id"] not in by_id:
        raise ValueError("invalid_variant_ids")
    for key in ("repeats", "bootstrap_resamples"):
        if not isinstance(matrix[key], int) or matrix[key] < 1:
            raise ValueError("invalid_" + key)
    if matrix["warmup_rounds"] < 0 or matrix["cutoffs"] != [1, 3, 5, 10]:
        raise ValueError("invalid_warmup_or_cutoffs")
    now = datetime.fromisoformat(matrix["fixed_now"])
    if now.tzinfo is None:
        raise ValueError("fixed_now_requires_timezone")
    for variant in variants:
        if variant["planner"] not in PLANNERS:
            raise ValueError("unknown_planner")
        policy = matrix["base_policy"] | variant["policy"]
        RetrievalPolicy.model_validate(policy)
        if not set(policy["source_weights"]) <= sources:
            raise ValueError("unknown_weighted_source")
        parent = variant["parent"]
        if parent is None:
            if variant["id"] != matrix["baseline_id"]:
                raise ValueError("only_baseline_can_omit_parent")
            continue
        if parent not in by_id or parent == variant["id"]:
            raise ValueError("invalid_variant_parent")
        parent_variant = by_id[parent]
        parent_policy = matrix["base_policy"] | parent_variant["policy"]
        changed = [key for key in policy if policy[key] != parent_policy[key]]
        changed += ["planner"] if variant["planner"] != parent_variant["planner"] else []
        if len(changed) != 1:
            raise ValueError("variant_must_change_one_configuration_component:" + variant["id"])


def ingest_suite(hub, suite, disabled_source_ids):
    """Map IDs returned by real ingestion back to qrel IDs, never assume equality."""
    reverse = {}
    disabled = set(disabled_source_ids)
    for source_id in sorted({doc["source_id"] for doc in suite["documents"]}):
        hub.upsert_source(SourceDefinition(
            id=source_id, name=source_id, kind="demo", enabled=source_id not in disabled,
        ))
        documents = [doc for doc in suite["documents"] if doc["source_id"] == source_id]
        response = hub.ingest(IngestRequest(source_id=source_id, documents=[
            DocumentInput(
                external_id=doc["id"], title=doc["title"], body=doc["body"],
                published_at=doc["date"], metadata=doc["metadata"],
            ) for doc in documents
        ]))
        if len(response["document_ids"]) != len(documents):
            raise ValueError("ingest_id_count_mismatch")
        for generated, doc in zip(response["document_ids"], documents, strict=True):
            if generated in reverse:
                raise ValueError("duplicate_generated_document_id")
            reverse[generated] = doc["id"]
    if len(reverse) != len(suite["documents"]):
        raise ValueError("incomplete_qrel_id_mapping")
    return reverse


def rank_metrics(ranked_ids, qrels, cutoffs=(1, 3, 5, 10)):
    """Document-level metrics; no-answer questions are excluded, not scored as 1."""
    if len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("duplicate_ranked_document")
    positive = {key: grade for key, grade in qrels.items() if grade > 0}
    if not positive:
        return {}
    ideal = sorted(positive.values(), reverse=True)
    metrics = {}
    for cutoff in cutoffs:
        grades = [positive.get(doc_id, 0) for doc_id in ranked_ids[:cutoff]]
        metrics[f"recall@{cutoff}"] = sum(grade > 0 for grade in grades) / len(positive)
        metrics[f"mrr@{cutoff}"] = next(
            (1 / rank for rank, grade in enumerate(grades, 1) if grade > 0), 0,
        )
        dcg = sum((2 ** grade - 1) / math.log2(rank + 1)
                  for rank, grade in enumerate(grades, 1))
        idcg = sum((2 ** grade - 1) / math.log2(rank + 1)
                   for rank, grade in enumerate(ideal[:cutoff], 1))
        metrics[f"ndcg@{cutoff}"] = dcg / idcg
    return metrics


@contextmanager
def planner_scope(mode):
    """Patch only the planner seam during one search; all scoring is production code."""
    original = hub_query.plan_query
    if mode == "full":
        yield
        return

    def experimental(query, aliases=()):
        if mode == "no_concept_expansion":
            with patch.object(hub_query, "CONCEPTS", ()):
                return original(query, aliases)
        if mode == "no_title_guards":
            return replace(original(query, aliases), entities=(), dates=())
        if mode in {"raw_lexical_or", "raw_lexical_and"}:
            operator = " OR " if mode == "raw_lexical_or" else " AND "
            tokens = dict.fromkeys(search_tokens(query).split())
            return hub_query.QueryPlan(operator.join('"' + token + '"' for token in tokens), ())
        raise ValueError("unknown_planner")

    with patch("research_agent.hub.plan_query", experimental):
        yield


def run_sample(hub, query, variant, reverse_ids, docs_by_id, now, expected_version):
    request = SearchRequest(query=query["query"], **query["filters"])
    sample = {"failure": None, "warnings": [], "ranked_ids": [], "ranked_hits": []}
    try:
        with planner_scope(variant["planner"]):
            started = time.perf_counter()
            response = hub.search(request, now=now)
            elapsed = (time.perf_counter() - started) * 1000
        SearchResponse.model_validate(response)
        if response["policy_version"] != expected_version:
            raise ValueError("policy_version_mismatch")
        ranked_ids = [reverse_ids[hit["id"]] for hit in response["results"]]
        disabled = {source["id"] for source in hub.sources() if not source["enabled"]}
        for hit, fixture_id in zip(response["results"], ranked_ids, strict=True):
            doc = docs_by_id[fixture_id]
            if hit["source_id"] in disabled:
                raise ValueError("disabled_source_leaked")
            if request.source_ids and hit["source_id"] not in request.source_ids:
                raise ValueError("source_filter_leaked")
            if any(doc["metadata"].get(k) != v for k, v in request.metadata.items()):
                raise ValueError("metadata_filter_leaked")
            published = datetime.fromisoformat(doc["date"].replace("Z", "+00:00"))
            if request.since and published < datetime.fromisoformat(request.since):
                raise ValueError("since_filter_leaked")
            if request.until and published >= datetime.fromisoformat(request.until):
                raise ValueError("until_filter_leaked")
        warning = response["retrieval"].get("warning")
        sample.update({
            "ranked_ids": ranked_ids,
            "ranked_hits": [{"id": fixture_id, "score": hit["score"],
                             "score_details": hit["score_details"]}
                            for hit, fixture_id in zip(response["results"], ranked_ids, strict=True)],
            "wall_ms": elapsed, "hub_elapsed_ms": response["elapsed_ms"],
            "payload_utf8_bytes": len(json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")),
            "policy_version": response["policy_version"],
            "retrieval_mode": response["retrieval"]["mode"],
            "warnings": [warning] if warning else [],
            "matched_chunks": response["matched_chunks"], "total": response["total"],
        })
    except Exception as exc:
        # No exception string/body is persisted; report machine-readable failures.
        sample["failure"] = type(exc).__name__
    sample["metrics"] = rank_metrics(sample["ranked_ids"], query["qrels"])
    sample["no_answer_correct"] = (
        sample["failure"] is None and not sample["ranked_ids"]
    ) if not any(grade > 0 for grade in query["qrels"].values()) else None
    return sample


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def aggregate_query(samples):
    """Average repeats within a question before any cross-question statistics."""
    if not samples:
        raise ValueError("missing_query_samples")
    keys = set(samples[0]["metrics"])
    if any(set(sample["metrics"]) != keys for sample in samples):
        raise ValueError("inconsistent_repeat_metrics")
    return {
        "metrics": {key: statistics.mean(s["metrics"][key] for s in samples)
                    for key in sorted(keys)},
        "wall_ms_mean": statistics.mean(s["wall_ms"] for s in samples if "wall_ms" in s)
        if any("wall_ms" in s for s in samples) else None,
        "payload_utf8_bytes_mean": statistics.mean(
            s["payload_utf8_bytes"] for s in samples if "payload_utf8_bytes" in s
        ) if any("payload_utf8_bytes" in s for s in samples) else None,
        "no_answer_correct_rate": statistics.mean(s["no_answer_correct"] for s in samples)
        if samples[0]["no_answer_correct"] is not None else None,
        "failures": sum(s["failure"] is not None for s in samples),
        "warnings": sum(bool(s["warnings"]) for s in samples),
        "ranking_stable": len({tuple(s["ranked_ids"]) for s in samples}) == 1,
        "policy_versions": sorted({s["policy_version"] for s in samples if "policy_version" in s}),
    }


def paired_comparison(current, reference, resamples, seed):
    """Paired percentile CI; resample questions, never individual repeated searches."""
    if set(current) != set(reference):
        raise ValueError("paired_query_ids_mismatch")
    deltas = [current[key] - reference[key] for key in sorted(current)]
    if not deltas:
        raise ValueError("no_paired_queries")
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(resamples)]
    return {
        "query_count": len(deltas), "delta": statistics.mean(deltas),
        "ci95": [percentile(boot, 0.025), percentile(boot, 0.975)],
        "wins": sum(value > 1e-12 for value in deltas),
        "ties": sum(abs(value) <= 1e-12 for value in deltas),
        "losses": sum(value < -1e-12 for value in deltas),
    }


def quality_aggregate(query_rows):
    answerable = [row for row in query_rows if row["aggregate"]["metrics"]]
    keys = answerable[0]["aggregate"]["metrics"] if answerable else {}
    return {
        "query_count": len(query_rows), "answerable_query_count": len(answerable),
        "metrics": {key: statistics.mean(row["aggregate"]["metrics"][key]
                                         for row in answerable) for key in keys},
    }


def summarize_variant(query_rows):
    samples = [sample for row in query_rows for sample in row["samples"]]
    latency = [s["wall_ms"] for s in samples if "wall_ms" in s]
    no_answer = [row for row in query_rows if row["aggregate"]["no_answer_correct_rate"] is not None]
    return quality_aggregate(query_rows) | {
        "measured_calls": len(samples),
        "failures": sum(s["failure"] is not None for s in samples),
        "warnings": sum(bool(s["warnings"]) for s in samples),
        "warning_counts": dict(Counter(w for s in samples for w in s["warnings"])),
        "failure_counts": dict(Counter(s["failure"] for s in samples if s["failure"])),
        "unstable_ranking_queries": [row["id"] for row in query_rows
                                     if not row["aggregate"]["ranking_stable"]],
        "wall_ms": {"mean": statistics.mean(latency) if latency else None,
                    "p50": percentile(latency, 0.5), "p95": percentile(latency, 0.95)},
        "payload_utf8_bytes_mean": statistics.mean(
            s["payload_utf8_bytes"] for s in samples if "payload_utf8_bytes" in s
        ) if any("payload_utf8_bytes" in s for s in samples) else None,
        "returned_documents_mean": statistics.mean(len(s["ranked_ids"]) for s in samples),
        "no_answer_queries": len(no_answer),
        "no_answer_correct_rate": statistics.mean(
            row["aggregate"]["no_answer_correct_rate"] for row in no_answer
        ) if no_answer else None,
        "by_language": {language: quality_aggregate([row for row in query_rows
                                                    if row["language"] == language])
                        for language in sorted({row["language"] for row in query_rows})},
    }


def execute(suite, matrix):
    validate_inputs(suite, matrix)
    variants = matrix["variants"]
    queries = suite["queries"]
    docs_by_id = {doc["id"]: doc for doc in suite["documents"]}
    now = datetime.fromisoformat(matrix["fixed_now"])
    rng = random.Random(matrix["seed"])
    samples = {(v["id"], q["id"]): [] for v in variants for q in queries}
    warmup_failures, warmup_warnings = [], []
    execution_order = []
    with tempfile.TemporaryDirectory(prefix="chenxi-public-rag-ablation-") as directory:
        contexts = {}
        for variant in variants:
            hub = Hub(Path(directory) / variant["id"], retrieval_backend="sqlite")
            reverse = ingest_suite(hub, suite, matrix["disabled_source_ids"])
            policy = RetrievalPolicy.model_validate(matrix["base_policy"] | variant["policy"])
            saved = hub.update_policy(PolicyUpdate(
                expected_version=hub.policy()["version"], policy=policy,
            ))
            contexts[variant["id"]] = (hub, reverse, saved["version"])
        for phase, rounds in (("warmup", matrix["warmup_rounds"]), ("measure", matrix["repeats"])):
            for round_index in range(rounds):
                pairs = [(v, q) for v in variants for q in queries]
                rng.shuffle(pairs)
                execution_order.append({"phase": phase, "round": round_index + 1,
                                        "pairs": [[v["id"], q["id"]] for v, q in pairs]})
                for variant, query in pairs:
                    hub, reverse, version = contexts[variant["id"]]
                    sample = run_sample(hub, query, variant, reverse, docs_by_id, now, version)
                    if phase == "measure":
                        sample["repeat"] = round_index + 1
                        samples[(variant["id"], query["id"])].append(sample)
                    else:
                        identity = {"variant": variant["id"], "query_id": query["id"],
                                    "round": round_index + 1}
                        if sample["failure"]:
                            warmup_failures.append(identity | {"failure": sample["failure"]})
                        if sample["warnings"]:
                            warmup_warnings.append(identity | {"warnings": sample["warnings"]})
        stats = contexts[matrix["baseline_id"]][0].stats()
        reverse = contexts[matrix["baseline_id"]][1]
    results = {}
    for variant in variants:
        rows = [{"id": q["id"], "query": q["query"], "language": q["language"],
                 "filters": q["filters"], "qrels": q["qrels"],
                 "samples": samples[(variant["id"], q["id"])],
                 "aggregate": aggregate_query(samples[(variant["id"], q["id"])])}
                for q in queries]
        results[variant["id"]] = {
            "configuration": variant | {"effective_policy": matrix["base_policy"] | variant["policy"]},
            "aggregate": summarize_variant(rows), "queries": rows,
        }
    for result in results.values():
        result["comparisons"] = {}
        for reference_id in dict.fromkeys(filter(None, [matrix["baseline_id"],
                                                        result["configuration"]["parent"]])):
            comparisons = {}
            for metric in result["aggregate"]["metrics"]:
                current = {row["id"]: row["aggregate"]["metrics"][metric]
                           for row in result["queries"] if row["aggregate"]["metrics"]}
                reference = {row["id"]: row["aggregate"]["metrics"][metric]
                             for row in results[reference_id]["queries"] if row["aggregate"]["metrics"]}
                comparisons[metric] = paired_comparison(
                    current, reference, matrix["bootstrap_resamples"], matrix["seed"],
                )
            result["comparisons"][reference_id] = comparisons
    return {
        "database_stats": stats,
        "document_id_mapping": reverse,
        "execution_order": execution_order,
        "warmup_failures": warmup_failures, "warmup_warnings": warmup_warnings,
        "variants": results,
    }


def source_fingerprints(suite_path, matrix_path):
    paths = [suite_path, matrix_path, Path(__file__), BACKEND / "pyproject.toml"]
    # Include the entire production Python source tree, even indirect ingest dependencies.
    paths += sorted((BACKEND / "src" / "research_agent").rglob("*.py"))
    return {path.resolve().relative_to(REPOSITORY).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def format_markdown(report):
    variants = report["variants"]
    baseline = variants[report["matrix"]["baseline_id"]]
    totals = report["counts"]
    lines = [
        "# SQLite RAG 检索消融：真实 Hub 执行、公开虚构样例", "",
        f"运行时间：{report['created_at']}。固定新鲜度时钟：{report['matrix']['fixed_now']}。", "",
        f"本报告实跑 `{totals['variants']}` 个配置 × `{totals['queries']}` 个问题 × "
        f"`{report['matrix']['repeats']}` 轮，共 `{totals['measured_calls']}` 次正式 `Hub.search`；"
        f"另有 `{totals['warmup_calls']}` 次预热。全部使用新建临时 SQLite 数据库，"
        f"真实 ingest `{totals['documents']}` 篇虚构文档，形成 `{report['database_stats']['chunks']}` 个 chunk。",
        "没有生成模型、embedding、reranker、PostgreSQL 服务或网络调用；结论只覆盖当前 SQLite 词法检索。", "",
        "## 方法与边界", "",
        "- 复用 `backend/samples/postgres_synthetic/suite.json` 的固定正文与 qrels，未根据本次排序重标。"
        "生产 ingest 返回的哈希文档 ID 经显式映射后再与 fixture qrels 比较；映射写入 JSON。",
        "- 中性基线使用原生产双语 planner、FTS5 BM25、来源权重 1、recency_boost=0、half_life_days=90、top-k=10。"
        "`recency_025` 才对应生产默认新鲜度配置。",
        "- 组件关闭只通过测试作用域内的 planner patch 实现。评分、分块、过滤、去重、排序与 top-k 全部调用现有生产代码。"
        "每个配置独立数据库，策略通过乐观锁保存；每条结果检查 policy_version。",
        "- 所有来源/date/metadata 查询过滤保持原样；disabled source 始终禁用。返回值逐条校验过滤与来源边界。"
        "关闭 title guard 仅移除 planner 的标题实体/日期判断，不移除请求过滤或访问边界。",
        "- raw OR/AND 是替换整个 planner 的复合对照，不能把它们的差值归因于某一个组件。"
        "half-life 的单变量参考是 recency_1，其他配置参考 baseline。",
        "- 每一轮随机打乱配置与问题的笛卡尔积，预热轮不入统计；固定随机种子和完整执行顺序写入 JSON。"
        "计时只包围 Hub.search（包括 planner），不包括 ingest、策略保存、patch 建立、结果校验及序列化。",
        "- Recall/MRR/nDCG 在 k=1/3/5/10 统计，仅包含 16 个有答案问题；4 个无答案过滤问题单列正确空返回率。"
        "MRR 截断于 k，nDCG gain=2^grade−1；未返回或未标注文档按 0 计。top-k 较小也保留相同评估截断。",
        "- 先在问题内平均重复，再对问题宏平均。95% CI 对配对问题差值做 5,000 次 percentile bootstrap；"
        "重复次数不扩大质量样本量。W/T/L 也按问题计数；多个比较未校正，CI 仅作探索性不确定性描述。",
        "- payload 是完整详细 SearchResponse 的紧凑 UTF-8 JSON 字节数，包含动态 elapsed_ms；不是模型 tokenizer、"
        "HTTP 线缆开销或生成 token。只评估检索，不评估答案生成、事实正确性或真实全库质量。", "",
        "## 汇总", "",
        "| 配置 | Recall@5 | MRR@5 | nDCG@5 | ΔnDCG@5 / 95% CI vs baseline | W/T/L | p50 / p95 ms | 平均字节 |", 
        "|---|---:|---:|---:|---|---|---:|---:|",
    ]
    for name, result in variants.items():
        agg = result["aggregate"]
        comp = result["comparisons"]["baseline"]["ndcg@5"]
        metrics = agg["metrics"]
        lines.append(f"| {name} | {metrics['recall@5']:.4f} | {metrics['mrr@5']:.4f} | "
                     f"{metrics['ndcg@5']:.4f} | {comp['delta']:+.4f} "
                     f"[{comp['ci95'][0]:+.4f}, {comp['ci95'][1]:+.4f}] | "
                     f"{comp['wins']}/{comp['ties']}/{comp['losses']} | "
                     f"{agg['wall_ms']['p50']:.3f} / {agg['wall_ms']['p95']:.3f} | "
                     f"{agg['payload_utf8_bytes_mean']:.1f} |")
    lines += ["", "所有截止位的 Recall/MRR/nDCG、语言切片、每次排名与评分、耗时、字节数、策略版本、CI "
              "和完整输入/源代码 SHA-256 见 [机器可读报告](RAG_SQLITE_ABLATION.json)。", "",
              "## 结果解读", ""]
    base_metrics = baseline["aggregate"]["metrics"]
    lines.append(f"基线 Recall@5={base_metrics['recall@5']:.4f}、MRR@5={base_metrics['mrr@5']:.4f}、"
                 f"nDCG@5={base_metrics['ndcg@5']:.4f}。以下解释保留负增益与无变化结果，不挑选获胜配置。")
    for name, result in variants.items():
        parent = result["configuration"]["parent"]
        if parent is None:
            continue
        comp = result["comparisons"][parent]["ndcg@5"]
        changes = []
        prior = {row["id"]: row for row in variants[parent]["queries"]}
        for row in result["queries"]:
            if row["aggregate"]["metrics"]:
                delta = row["aggregate"]["metrics"]["ndcg@5"] - prior[row["id"]]["aggregate"]["metrics"]["ndcg@5"]
                if abs(delta) > 1e-12:
                    changes.append(f"{row['id']} ({delta:+.4f})")
        lines.append(f"- **{name}**：相对 `{parent}` 的 ΔnDCG@5={comp['delta']:+.4f}；"
                     + ("变化问题：" + "、".join(changes) + "。" if changes else "16 个有答案问题的 nDCG@5 均未变化。"))
    lines += ["", "来源权重与新鲜度的无变化只能说明此固定小样本没有暴露排名敏感性：实体标题约束和过滤常使候选很少。"
              "不能由此断言这些组件无用。fixture 的正例大多来自 alpha，来源权重存在标注分布偏置，不能据此选生产最优参数。",
              "", "## 中英文与逐题诊断", "",
              "| 配置 | en 有答案问题 | en nDCG@5 | zh 有答案问题 | zh nDCG@5 | 无答案正确空返回 |",
              "|---|---:|---:|---:|---:|---:|"]
    for name, result in variants.items():
        agg = result["aggregate"]
        en, zh = agg["by_language"]["en"], agg["by_language"]["zh"]
        lines.append(f"| {name} | {en['answerable_query_count']} | {en['metrics']['ndcg@5']:.4f} | "
                     f"{zh['answerable_query_count']} | {zh['metrics']['ndcg@5']:.4f} | "
                     f"{agg['no_answer_correct_rate']:.1%} |")
    lines += ["", "| 基线问题 | 语言 | Recall@5 | nDCG@5 | 前 5 个 fixture ID |", "|---|---|---:|---:|---|"]
    for row in baseline["queries"]:
        metrics = row["aggregate"]["metrics"]
        if not metrics:
            continue
        ranked = ", ".join(row["samples"][0]["ranked_ids"][:5]) or "（空）"
        lines.append(f"| {row['id']} | {row['language']} | {metrics['recall@5']:.4f} | "
                     f"{metrics['ndcg@5']:.4f} | {ranked} |")
    failures = sum(r["aggregate"]["failures"] for r in variants.values())
    warnings = sum(r["aggregate"]["warnings"] for r in variants.values())
    unstable = sum(len(r["aggregate"]["unstable_ranking_queries"]) for r in variants.values())
    lines += ["", "## 健康检查与可复现性", "",
              f"正式调用失败 **{failures}**、带 warning 的调用 **{warnings}**、跨轮排名不稳定的问题/配置 **{unstable}**。"
              f"预热失败 **{len(report['warmup_failures'])}**、预热 warning **{len(report['warmup_warnings'])}**。"
              "失败调用不会被当作正确空返回；有答案失败计零并保留失败标志。",
              "", f"运行环境：Python {report['runtime']['python']}，SQLite {report['runtime']['sqlite']}，"
              f"Pydantic {report['runtime']['pydantic']}，{report['runtime']['system']} / {report['runtime']['machine']}。"
              "这是微型本地热缓存诊断，p95 不代表并发吞吐、冷启动或生产 SLO；其他进程会影响延迟。",
              "", "```powershell", "cd backend", "python -m pip install -e '.[dev]'",
              "python benchmarks/run_sqlite_ablation.py", "python -m pytest tests/test_sqlite_ablation.py", "```", "",
              "默认读取公开 suite 与 sqlite_matrix，写入本目录 JSON/Markdown。可用 `--output` 与 `--markdown` "
              "写到独立位置。矩阵中种子、轮次、bootstrap 次数、固定时钟和全部变体均显式配置。"
              "每次重跑质量指标应稳定，墙钟耗时与 payload 中 elapsed_ms 导致的少量字节变化属正常。", "",
              "## 后续证据缺口", "",
              "- 26 篇短文均为单 chunk，不能测出 chunk 大小/重叠、长文 citation 覆盖或跨段证据拼接的贡献。",
              "- 中文问题/英文正文的词汇鸿沟需要独立标注集与真实模型实验；本次没有伪造 dense/hybrid/rerank 结果。",
              "- 需要更大、盲标、各来源正例均衡的测试集和独立验证集，才能调权重与半衰期。",
              "- PostgreSQL 既有实验、向量覆盖及生成答案质量必须单独评估，不能与此微型 SQLite 表格直接横比。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=BACKEND / "samples/postgres_synthetic/suite.json")
    parser.add_argument("--matrix", type=Path, default=BACKEND / "samples/rag_ablation/sqlite_matrix.json")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "docs/evaluation/RAG_SQLITE_ABLATION.json")
    parser.add_argument("--markdown", type=Path, default=REPOSITORY / "docs/evaluation/RAG_SQLITE_ABLATION.md")
    args = parser.parse_args()
    suite, matrix = read_json(args.suite), read_json(args.matrix)
    report = execute(suite, matrix)
    report = {
        "schema_version": 1, "experiment": "actual_sqlite_public_synthetic_ablation",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset_kind": suite["dataset_kind"], "dataset_version": suite["version"],
        "annotation_method": suite["annotation_method"], "matrix": matrix,
        "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                    "pydantic": importlib.metadata.version("pydantic"),
                    "system": platform.system(), "machine": platform.machine()},
        "source_sha256": source_fingerprints(args.suite, args.matrix),
        "counts": {"documents": len(suite["documents"]), "queries": len(suite["queries"]),
                   "variants": len(matrix["variants"]),
                   "measured_calls": len(matrix["variants"]) * len(suite["queries"]) * matrix["repeats"],
                   "warmup_calls": len(matrix["variants"]) * len(suite["queries"]) * matrix["warmup_rounds"]},
        **report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    args.markdown.write_text(format_markdown(report), encoding="utf-8")
    failures = sum(result["aggregate"]["failures"] for result in report["variants"].values())
    print(json.dumps({"counts": report["counts"], "failures": failures,
                      "warmup_failures": len(report["warmup_failures"]),
                      "baseline": report["variants"][matrix["baseline_id"]]["aggregate"]},
                     ensure_ascii=False, indent=2))
    return 1 if failures or report["warmup_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
