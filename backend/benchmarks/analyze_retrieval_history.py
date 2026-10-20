"""Reanalyse committed retrieval reports; stdlib only, no database or model calls.

This is an analysis of historical observations, never a new retrieval benchmark.
Query repeats are averaged before pairing/bootstrap so they are not independent
relevance observations. Missing private query-level data remains unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

METRICS = ("recall_at_k", "mrr_at_k", "ndcg_at_k")
STAGES = ("lexical", "dense", "hybrid", "rrf", "rerank", "rewrite")
COMPARISONS = (
    ("dense", "lexical", "独立 dense 与 lexical 基线比较；不是逐层加组件"),
    ("hybrid", "lexical", "在 lexical 基础上加入 dense 和分数归一化融合"),
    ("hybrid", "dense", "在 dense 基础上加入 lexical 和分数归一化融合"),
    ("rrf", "lexical", "lexical 与双路 RRF 配置的整体比较"),
    ("rrf", "hybrid", "用 RRF 替代分路归一化加和；两种融合方式互为备选"),
    ("rerank", "rrf", "RRF 后对至多 50 个文档代表 passage 做英语 MS MARCO CrossEncoder 重排；包含候选截断影响"),
    ("rewrite", "rerank", "重排路径加入确定性双语规则扩展；不是 LLM rewrite"),
    ("rewrite", "rrf", "重排与规则扩展的联合变化；不能拆分交互效应"),
    ("rewrite", "lexical", "完整规则扩展路径与 lexical 的整体比较"),
)
DESIGN = {
    "lexical": {"lexical": True, "dense": False, "fusion": None,
                "rerank": False, "rule_rewrite": False},
    "dense": {"lexical": False, "dense": True, "fusion": None,
              "rerank": False, "rule_rewrite": False},
    "hybrid": {"lexical": True, "dense": True, "fusion": "normalized_sum",
               "rerank": False, "rule_rewrite": False},
    "rrf": {"lexical": True, "dense": True, "fusion": "rrf_k60",
            "rerank": False, "rule_rewrite": False},
    "rerank": {"lexical": True, "dense": True, "fusion": "rrf_k60",
               "rerank": True, "rule_rewrite": False},
    "rewrite": {"lexical": True, "dense": True, "fusion": "rrf_k60",
                "rerank": True, "rule_rewrite": True},
}


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"non_numeric:{label}")
    if not math.isfinite(value):
        raise ValueError(f"non_finite:{label}")
    return float(value)


def percentile(values, probability):
    """Linearly interpolated sample quantile, used for bootstrap limits only."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_differences(differences, *, seed=20261015, resamples=10000):
    """Paired query bootstrap: resample whole query differences with replacement."""
    if resamples < 1:
        raise ValueError("bootstrap_resamples_must_be_positive")
    values = [finite_number(value, "difference") for value in differences]
    if not values:
        return {"status": "unavailable", "reason": "no_paired_queries"}
    rng = random.Random(seed)
    n = len(values)
    estimates = [statistics.fmean(values[rng.randrange(n)] for _ in range(n))
                 for _ in range(resamples)]
    return {
        "status": "available", "paired_query_count": n,
        "mean_delta": statistics.fmean(values),
        "wins": sum(value > 1e-12 for value in values),
        "ties": sum(abs(value) <= 1e-12 for value in values),
        "losses": sum(value < -1e-12 for value in values),
        "bootstrap_percentile_95_ci": [percentile(estimates, 0.025),
                                       percentile(estimates, 0.975)],
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
    }


def collapse_query_repeats(cases):
    """Equal-weight distinct query units, preserving failure/control bookkeeping."""
    grouped = defaultdict(list)
    seen = set()
    for case in cases:
        query_id = case.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise ValueError("missing_query_id")
        repeat = case.get("repeat", 0)
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 0:
            raise ValueError(f"invalid_repeat:{query_id}")
        key = (query_id, repeat)
        if key in seen:
            raise ValueError(f"duplicate_query_repeat:{query_id}:{repeat}")
        seen.add(key)
        if not isinstance(case.get("no_answer_expected"), bool):
            raise ValueError(f"missing_control_classification:{query_id}")
        grouped[query_id].append(case)
    collapsed = {}
    for query_id, rows in sorted(grouped.items()):
        languages = {row.get("language", "unknown") for row in rows}
        controls = {row["no_answer_expected"] for row in rows}
        if len(languages) != 1 or len(controls) != 1:
            raise ValueError(f"inconsistent_query_metadata:{query_id}")
        control = next(iter(controls))
        metrics = None
        if not control:
            if any(not isinstance(row.get("metrics"), dict) for row in rows):
                raise ValueError(f"missing_answerable_metrics:{query_id}")
            metrics = {}
            for metric in METRICS:
                values = [finite_number(row["metrics"].get(metric), metric) for row in rows]
                if any(value < 0 or value > 1 for value in values):
                    raise ValueError(f"metric_out_of_range:{query_id}:{metric}")
                metrics[metric] = statistics.fmean(values)
        times = [finite_number(row["elapsed_ms"], "elapsed_ms") for row in rows
                 if row.get("elapsed_ms") is not None]
        if any(value < 0 for value in times):
            raise ValueError(f"negative_latency:{query_id}")
        collapsed[query_id] = {
            "language": next(iter(languages)), "control": control,
            "repeat_count": len(rows), "metrics": metrics,
            # No partial-subset timing averages if one observation is missing.
            "mean_elapsed_ms": statistics.fmean(times) if len(times) == len(rows) else None,
            "failed_runs": sum(bool(row.get("failed")) for row in rows),
            "warning_runs": sum(bool(row.get("warnings")) for row in rows),
            "successful_empty_runs": sum(not row.get("failed") and row.get("returned_count") == 0
                                         for row in rows) if control else None,
        }
    return collapsed


def paired_comparison(candidate, reference, *, seed, resamples, language=None):
    candidate_ids = {key for key, row in candidate.items()
                     if not row["control"] and (language is None or row["language"] == language)}
    reference_ids = {key for key, row in reference.items()
                     if not row["control"] and (language is None or row["language"] == language)}
    paired_ids = sorted(candidate_ids & reference_ids)
    for query_id in sorted(set(candidate) & set(reference)):
        if (candidate[query_id]["language"] != reference[query_id]["language"] or
                candidate[query_id]["control"] != reference[query_id]["control"]):
            raise ValueError(f"cross_stage_query_metadata_mismatch:{query_id}")
    result = {
        "status": "available" if paired_ids else "unavailable",
        "pairing_unit": "unique_query_id_after_mean_over_repeats",
        "excluded_candidate_only_ids": sorted(candidate_ids - reference_ids),
        "excluded_reference_only_ids": sorted(reference_ids - candidate_ids),
        "query_ids": paired_ids,
        "metrics": {},
        "per_query_deltas": [],
    }
    for metric in METRICS:
        deltas = [candidate[key]["metrics"][metric] - reference[key]["metrics"][metric]
                  for key in paired_ids]
        result["metrics"][metric] = summarize_differences(deltas, seed=seed, resamples=resamples)
    for key in paired_ids:
        result["per_query_deltas"].append({
            "query_id": key, "language": candidate[key]["language"],
            **{metric: candidate[key]["metrics"][metric] - reference[key]["metrics"][metric]
               for metric in METRICS},
        })
    return result


def metric_deltas(candidate, reference):
    output = {}
    for metric in METRICS:
        left = finite_number(candidate[metric], metric)
        right = finite_number(reference[metric], metric)
        output[metric] = {
            "delta": left - right, "percentage_points": 100 * (left - right),
            "relative_percent": 100 * (left - right) / right if right else None,
        }
    return output


def provenance(path, root):
    content = path.read_bytes()
    try:
        name = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        name = path.name  # Do not publish the host's absolute path.
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def analyse_report(report, *, seed=20261015, resamples=10000):
    stages = {row["stage"]: row for row in report["stages"]}
    if len(stages) != len(report["stages"]):
        raise ValueError("duplicate_stage")
    if set(stages) != set(STAGES):
        raise ValueError("expected_six_stages")
    query_data = {stage: collapse_query_repeats(row["cases"]) for stage, row in stages.items()
                  if "cases" in row}
    summaries = []
    for name in STAGES:
        source = stages[name]
        summary = {"stage": name, "aggregate": source["aggregate"],
                   "by_language": source.get("by_language", {}),
                   "execution": source.get("execution"),
                   "filter_controls": source.get("filter_controls"),
                   "rewrite_activation": source.get("rewrite_activation"),
                   "query_level_status": "available" if name in query_data else "unavailable"}
        if name in query_data:
            rows = query_data[name]
            answerable = [row for row in rows.values() if not row["control"]]
            controls = [row for row in rows.values() if row["control"]]
            means = {metric: statistics.fmean(row["metrics"][metric] for row in answerable)
                     for metric in METRICS} if answerable else {}
            summary["query_level_summary"] = {
                "unique_query_count": len(rows), "answerable_count": len(answerable),
                "control_count": len(controls), "query_macro_mean": means,
                "difference_from_reported_aggregate": {
                    metric: means[metric] - source["aggregate"][metric] for metric in means},
                "repeats_per_query": {key: row["repeat_count"] for key, row in rows.items()},
                "failed_runs": sum(row["failed_runs"] for row in rows.values()),
                "warning_runs": sum(row["warning_runs"] for row in rows.values()),
                "control_measured_runs": sum(row["repeat_count"] for row in controls),
                "control_successful_empty_runs": sum(row["successful_empty_runs"] for row in controls),
            }
        summaries.append(summary)
    comparisons = []
    for candidate, reference, interpretation in COMPARISONS:
        left, right = stages[candidate], stages[reference]
        comparison = {
            "candidate": candidate, "reference": reference, "interpretation": interpretation,
            "aggregate_deltas": metric_deltas(left["aggregate"], right["aggregate"]),
            "by_language_deltas": {
                language: metric_deltas(left["by_language"][language], right["by_language"][language])
                for language in sorted(set(left.get("by_language", {})) &
                                       set(right.get("by_language", {})))},
            "observed_latency": {
                metric: {"candidate_ms": left["aggregate"].get(metric),
                         "reference_ms": right["aggregate"].get(metric),
                         "ratio": left["aggregate"][metric] / right["aggregate"][metric]
                         if right["aggregate"].get(metric) and
                         left["aggregate"].get(metric) is not None else None}
                for metric in ("p50_ms", "p95_ms")},
        }
        if candidate in query_data and reference in query_data:
            comparison["paired"] = paired_comparison(query_data[candidate], query_data[reference],
                                                      seed=seed, resamples=resamples)
            languages = sorted({row["language"] for row in query_data[candidate].values()
                                if not row["control"]})
            comparison["paired_by_language"] = {
                language: paired_comparison(query_data[candidate], query_data[reference],
                                            seed=seed, resamples=resamples, language=language)
                for language in languages}
            common = sorted(set(query_data[candidate]) & set(query_data[reference]))
            timing_ids = [key for key in common
                          if query_data[candidate][key]["mean_elapsed_ms"] is not None
                          and query_data[reference][key]["mean_elapsed_ms"] is not None]
            comparison["paired_latency_diagnostic"] = {
                "unit": "ms_difference_of_query_mean_elapsed_times",
                "includes_filter_controls": True,
                "query_ids": timing_ids,
                "excluded_missing_timing_ids": sorted(set(common) - set(timing_ids)),
                "summary": summarize_differences(
                    [query_data[candidate][key]["mean_elapsed_ms"] -
                     query_data[reference][key]["mean_elapsed_ms"] for key in timing_ids],
                    seed=seed, resamples=resamples),
                "direction": "Positive delta means candidate was slower, not a win.",
                "causal_claim_allowed": False,
            }
            timing = comparison["paired_latency_diagnostic"]["summary"]
            if timing["status"] == "available":
                timing["slower_queries"] = timing.pop("wins")
                timing["equal_queries"] = timing.pop("ties")
                timing["faster_queries"] = timing.pop("losses")
        else:
            comparison["paired"] = {
                "status": "unavailable",
                "reason": "Committed report has aggregates only; query-level observations are absent.",
                "ci": None, "wins_ties_losses": None,
            }
            comparison["paired_by_language"] = None
            comparison["paired_latency_diagnostic"] = None
        comparisons.append(comparison)
    return {
        "dataset_kind": report["dataset_kind"], "measured_date": report.get("measured_date"),
        "suite_version": report.get("suite_version"), "snapshot": report.get("snapshot"),
        "protocol": report.get("protocol"), "implementation_phase": report.get("implementation_phase"),
        "original_limitations": report.get("limitations", []),
        "availability": {
            "query_level_stages": sorted(query_data),
            "qrels_recomputed": False,
            "ranking_metrics_source": "Existing committed metric values; qrels were not reconstructed.",
            "new_retrieval_execution": False,
        },
        "stages": summaries, "comparisons": comparisons,
    }


def build_analysis(root, *, analysis_date, seed=20261015, resamples=10000):
    datasets = {}
    for key, filename in (("real", "POSTGRES_REAL_REPORT"),
                          ("synthetic", "POSTGRES_SYNTHETIC_REPORT")):
        path = root / "docs" / "evaluation" / f"{filename}.json"
        report = json.loads(path.read_text(encoding="utf-8-sig"))
        analysed = analyse_report(report, seed=seed, resamples=resamples)
        analysed["sources"] = [provenance(path, root)]
        measured_date_source = "report_json" if analysed["measured_date"] else None
        companion = path.with_suffix(".md")
        if companion.exists():
            analysed["sources"].append(provenance(companion, root))
            if not analysed["measured_date"]:
                match = re.search(r"Measured (\d{4}-\d{2}-\d{2})", companion.read_text(encoding="utf-8"))
                if match:
                    analysed["measured_date"] = match.group(1)
                    measured_date_source = "companion_markdown_explicit_measurement_date"
        analysed["measured_date_source"] = measured_date_source
        datasets[key] = analysed
    return {
        "schema_version": 1, "analysis_date": analysis_date,
        "analysis_timezone": "Asia/Shanghai", "analysis_kind": "historical_artifact_reanalysis",
        "analyzer": provenance(Path(__file__), root),
        "bootstrap": {"resamples": resamples, "seed": seed,
                      "unit": "unique_query_id; average repetitions before pairing",
                      "interval": "95% percentile, linear interpolation at 0.025 and 0.975",
                      "scope": "Exploratory descriptive uncertainty conditional on these selected queries.",
                      "sampling_assumption": "Query-level exchangeability is not established by the purposive or topic-related samples.",
                      "multiple_comparison_correction": "none; no hypothesis-test significance claims",
                      "datasets_pooled": False},
        "design": DESIGN,
        "design_limitations": [
            "Six staged configurations are not a complete factorial design.",
            "No RRF+rewrite without rerank; rewrite-by-rerank interaction cannot be estimated.",
            "No lexical+rerank or dense+rerank controls; reranker benefit cannot be separated across recall branches.",
            "No chunk-size, candidate-limit, fusion-weight, RRF-k, rerank-pool or vector-coverage sweep.",
            "No passage-vs-document or deduplicated-qrels comparison; no generation, faithfulness or token-cost measurement.",
            "Chinese and English use different questions/topics; language-group differences are not causal language effects.",
            "Historical latency has fixed stage order, shared CPU and unequal warmup/session conditions; no causal speedup claim.",
            "Intervals do not repair small purposive samples, incomplete judgments, model selection on the same set or implementation drift.",
        ],
        "datasets": datasets,
    }


def number(value, digits=5):
    return "不可得" if value is None else f"{value:.{digits}f}"


def render_markdown(result):
    lines = [
        "# RAG 历史消融结果复核", "",
        f"分析日期：{result['analysis_date']}（Asia/Shanghai）。这是已提交实验工件的再分析，**没有重新运行 PostgreSQL、embedding、reranker 或答案生成**。",
        "原报告保留不变；真实库与虚构库分别分析，不能合并平均或互相验证泛化。完整数值、逐题变化、来源哈希见 [JSON](RAG_HISTORICAL_ANALYSIS.json)。", "",
        "## 方法与可识别边界", "",
        "先按 `query_id` 对重复轮次取均值，再在同一数据集、同一语言组内配对；不把运行次数当独立问题数。",
        f"仅对有逐题指标的工件计算 W/T/L 与 {result['bootstrap']['resamples']:,} 次配对 query bootstrap，固定 seed={result['bootstrap']['seed']}；95% 区间采用百分位法和线性插值。",
        "所有区间都是对已选题集的探索性描述，没有多重比较校正，不做统计显著性或总体泛化声明。W/T/L 分别指候选阶段指标提高、相同、降低，容差 1e-12。",
        "query bootstrap 以问题为重采样单位，但选题并非随机，题目也可能共享主题或文档；区间不保证覆盖独立总体效果。",
        "Recall/MRR/nDCG 复用原工件的逐题值；没有重建 qrels，也没有声称独立核验标注。控制题不参与相关性均值和置信区间。", "",
        "| 阶段 | Lexical | Dense | 融合 | CrossEncoder | 规则扩展 |",
        "|---|---|---|---|---|---|",
    ]
    for name, design in result["design"].items():
        lines.append(f"| {name} | {design['lexical']} | {design['dense']} | {design['fusion'] or '—'} | {design['rerank']} | {design['rule_rewrite']} |")
    lines += ["", "这不是完整析因实验。缺少 RRF+rewrite（无 rerank）、单路+rerank 对照，无法识别规则扩展与重排的交互效应；也没有 chunk size、候选池、RRF k、融合权重、重排池、向量覆盖率扫描。", ""]
    for key, dataset in result["datasets"].items():
        label = "真实库匿名聚合" if key == "real" else "虚构库逐题诊断"
        lines += [f"## {label}", "", f"历史测量日期：**{dataset['measured_date'] or '未记录'}**。", ""]
        if key == "real":
            lines += ["22 个独立问题 = 20 个有答案问题（中英各 10）+ 2 个不可能过滤控制；每阶段 3 轮、66 次，六阶段 396 次。",
                      "该匿名 JSON **没有逐题结果、排名、qrels 或逐题时延**。因此只能复核聚合差值；真实库 CI、W/T/L、逐题失败归因与配对时延均不可得，不能从 3 次重复或聚合均值反推。",
                      "原库 3,143 文档 / 174,070 chunks，11,736 vectors，仅 6.742% 向量覆盖。Dense 与 lexical 的差异混合了检索方法和语料覆盖差异；不能据此说 dense 模型本身失效。", ""]
        else:
            lines += ["26 篇完全虚构文档，向量覆盖 100%；20 个独立问题 = 16 个有答案问题（中英各 8）+ 4 个不可能过滤控制。",
                      "只有 1 轮且无预热，测于后续停用词/中文 bigram 优化之前。可对保存的逐题指标做配对诊断，不能当作当前源码实测或真实库效果。", ""]
        lines += ["| 阶段 | Recall@10 | MRR@10 | nDCG@10 | 英文 nDCG | 中文 nDCG | p50 ms | p95 ms |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for stage in dataset["stages"]:
            values = stage["aggregate"]
            lines.append(f"| {stage['stage']} | {number(values['recall_at_k'])} | {number(values['mrr_at_k'])} | {number(values['ndcg_at_k'])} | {number(stage['by_language'].get('en', {}).get('ndcg_at_k'))} | {number(stage['by_language'].get('zh', {}).get('ndcg_at_k'))} | {number(values.get('p50_ms'), 2)} | {number(values.get('p95_ms'), 2)} |")
        lines += ["", "### 组件与配置差值", "", "所有质量差值为候选减参考，单位百分点（pp）；延迟倍数为候选 p50 / 参考 p50，仅描述原运行。", "",
                  "| 候选 − 参考 | ΔRecall pp | ΔMRR pp | ΔnDCG pp | 英文 ΔnDCG pp | 中文 ΔnDCG pp | p50 倍数 |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for comparison in dataset["comparisons"]:
            deltas = comparison["aggregate_deltas"]
            language = comparison["by_language_deltas"]
            lines.append(f"| {comparison['candidate']} − {comparison['reference']} | {deltas['recall_at_k']['percentage_points']:+.3f} | {deltas['mrr_at_k']['percentage_points']:+.3f} | {deltas['ndcg_at_k']['percentage_points']:+.3f} | {language.get('en', {}).get('ndcg_at_k', {}).get('percentage_points', 0):+.3f} | {language.get('zh', {}).get('ndcg_at_k', {}).get('percentage_points', 0):+.3f} | {number(comparison['observed_latency']['p50_ms']['ratio'], 2)} |")
        if key == "real":
            lines += ["", "Lexical 在该题集的总体 Recall 和 nDCG 最高。RRF 相较 normalized hybrid 提升 nDCG 4.242 pp，但较 lexical 低 13.984 pp；选 RRF 是双路检索的运行折中，并非总体质量胜出。",
                      "Rerank 相较 RRF 的中文 nDCG 从 0.07197 降为 0，英文从 0.53788 升到 0.60184：总体变化 -0.401 pp 掩盖了语言组方向不同。英语训练数据是风险线索，不是已被该实验独立识别的因果解释。",
                      "规则 rewrite 相较 rerank 仅中文组增加 3.155 pp，英文不变；总体 +1.577 pp，仍低于 lexical。原记录 20 道有答案题中 9 道触发扩展（45%），但没有逐题结果，无法比较激活组与未激活组。",
                      "每阶段两道过滤控制 × 3 轮均成功返回空结果；这验证过滤行为，不等于语义无答案检测。测量失败/降级为 0 不代表 warmup 无警告，原 warmup 仅记录抛出的异常。",
                      "前四阶段没有历史源码/数据/provider 指纹；本次 SHA-256 只证明当前提交工件的内容，不补造历史实验指纹。rewrite 跨 2 个进程会话、44 次预热，rerank 为 1 个会话、22 次预热；固定顺序和 CPU 争用使时延比较不能作因果加速结论。", ""]
        else:
            lines += ["", "### 配对相关性诊断", "", "每行都只使用 16 道有答案题；区间是平均差值的 95% bootstrap 区间，单位 pp。", "",
                      "| 候选 − 参考 | 指标 | Δ pp | 95% 区间 pp | W/T/L |",
                      "|---|---|---:|---|---|"]
            for comparison in dataset["comparisons"]:
                for metric, stats in comparison["paired"]["metrics"].items():
                    low, high = stats["bootstrap_percentile_95_ci"]
                    lines.append(f"| {comparison['candidate']} − {comparison['reference']} | {metric} | {100 * stats['mean_delta']:+.3f} | [{100 * low:+.3f}, {100 * high:+.3f}] | {stats['wins']}/{stats['ties']}/{stats['losses']} |")
            lines += ["", "### 分语言配对 nDCG", "", "每个语言组只有 8 道有答案题。两个语言组不是同一批问题的翻译配对，不能把差异解释为纯语言效应。", "",
                      "| 候选 − 参考 | 语言 | Δ pp | 95% 区间 pp | W/T/L |",
                      "|---|---|---:|---|---|"]
            for comparison in dataset["comparisons"]:
                for language, pair in comparison["paired_by_language"].items():
                    stats = pair["metrics"]["ndcg_at_k"]
                    low, high = stats["bootstrap_percentile_95_ci"]
                    lines.append(f"| {comparison['candidate']} − {comparison['reference']} | {language} | {100 * stats['mean_delta']:+.3f} | [{100 * low:+.3f}, {100 * high:+.3f}] | {stats['wins']}/{stats['ties']}/{stats['losses']} |")
            lines += ["", "### 逐题变化位置", "", "以下列出直接组件对照中 nDCG 非零变化；未列出的题在该指标上持平。这里只描述现象，未从分数猜测 qrels 或错误成因。", "",
                      "| 候选 − 参考 | query_id | 语言 | ΔnDCG pp | ΔRecall pp |",
                      "|---|---|---|---:|---:|"]
            for comparison in dataset["comparisons"]:
                if (comparison["candidate"], comparison["reference"]) not in {
                    ("dense", "lexical"), ("hybrid", "lexical"), ("rrf", "hybrid"),
                    ("rerank", "rrf"), ("rewrite", "rerank")
                }:
                    continue
                for row in comparison["paired"]["per_query_deltas"]:
                    if abs(row["ndcg_at_k"]) > 1e-12:
                        lines.append(f"| {comparison['candidate']} − {comparison['reference']} | {row['query_id']} | {row['language']} | {100 * row['ndcg_at_k']:+.3f} | {100 * row['recall_at_k']:+.3f} |")
            lines += ["", "所有英文题的原 nDCG 已为 1，存在天花板效应；总体变化全部来自这 8 道中文题。rerank 与 rewrite 的三项逐题相关性指标完全相同，不能据此推断排名列表完全一致或规则从未触发。",
                      "4 个过滤控制在所有阶段均成功返回空结果；相关性均值复算与原聚合一致。完整逐题差值以及含控制题的配对平均时延差存于 JSON。时延区间仅刻画已观察问题的差异，不能消除无预热、首次加载、顺序和 CPU 争用偏差。", ""]
        lines += ["### 来源", ""]
        for source in dataset["sources"]:
            lines.append(f"- `{source['path']}` — SHA-256 `{source['sha256']}`（{source['bytes']} bytes）")
        lines.append("")
    lines += ["## 复现与后续证据", "",
              "从仓库根目录运行，Python 3.11+ 标准库即可：", "", "```powershell",
              f"python backend/benchmarks/analyze_retrieval_history.py --analysis-date {result['analysis_date']} --bootstrap-resamples {result['bootstrap']['resamples']} --seed {result['bootstrap']['seed']}",
              "python -m unittest discover -s backend/tests -p test_retrieval_history.py", "```", "",
              "下一轮应先锁定独立人工标注集及文档去重规则，再做全量向量覆盖和相同候选条件的对照；加入 rewrite-only、各单路+rerank 及参数扫描。质量试验与时延试验分开，时延预热后随机化/交错配置顺序并隔离负载。",
              "若要为真实库补充配对置信区间，需要在受控环境导出匿名 query_id、language、repeat、逐题三项指标、失败/降级标记和 elapsed_ms；无需把私有 query 文本、文档正文、标题或 qrels 发布入库。",
              "生成质量、忠实度、引用正确率、拒答与 token 成本仍未测量；当前证据只覆盖 retrieval 子链。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--analysis-date", default=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    datetime.strptime(args.analysis_date, "%Y-%m-%d")
    if args.bootstrap_resamples < 1:
        parser.error("--bootstrap-resamples must be positive")
    result = build_analysis(args.repo_root, analysis_date=args.analysis_date,
                            seed=args.seed, resamples=args.bootstrap_resamples)
    output = args.output_dir or args.repo_root / "docs" / "evaluation"
    output.mkdir(parents=True, exist_ok=True)
    (output / "RAG_HISTORICAL_ANALYSIS.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "RAG_HISTORICAL_ANALYSIS.md").write_text(render_markdown(result), encoding="utf-8")
    print("Historical analysis written; no retrieval/database/model execution performed.")


if __name__ == "__main__":
    main()
