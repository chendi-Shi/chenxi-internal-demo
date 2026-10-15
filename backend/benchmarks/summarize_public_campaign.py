"""Export verified completed public jobs without raw text, models or local paths.

Incomplete jobs are never quality results. Cached pipeline timing is kept apart
from actual inference telemetry, whose cumulative snapshots must not be summed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "backend/samples/rag_ablation/public_gpu_matrix.json"
DATASETS_PATH = ROOT / "backend/samples/rag_ablation/public_datasets.json"
METRICS = ("recall_at_k", "mrr_at_k", "ndcg_at_k")
KS = ("1", "3", "5", "10")
CROSS_CONFIGS = ("dense", "rrf", "rrf_rerank", "rrf_rewrite_rerank")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ValueError("unsafe_public_identifier")
    return value


def numeric(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid_finite_numeric_value")
    return value


def metric_value(value):
    value = numeric(value)
    if not 0 <= value <= 1:
        raise ValueError("ranking_metric_out_of_bounds")
    return value


def percentile(values, quantile):
    ordered = sorted(values)
    # Match research_agent.rag_ablation's percentile convention for direct contrasts.
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def paired_interval(deltas, clusters, *, samples, seed):
    if not deltas or len(deltas) != len(clusters) or samples < 1:
        raise ValueError("invalid_paired_sample")
    groups = defaultdict(list)
    for delta, cluster in zip(deltas, clusters, strict=True):
        groups[cluster].append(numeric(delta))
    # The cluster's total and size reproduce resampling all its query differences.
    totals = [(math.fsum(values), len(values)) for _, values in sorted(groups.items())]
    rng = random.Random(seed)
    boot = []
    for _ in range(samples):
        drawn = rng.choices(totals, k=len(totals))
        boot.append(math.fsum(value for value, _ in drawn) / sum(n for _, n in drawn))
    return {"metric": "ndcg_at_10", "paired_questions": len(deltas),
            "independent_clusters": len(totals), "mean_delta": statistics.fmean(deltas),
            "wins": sum(delta > 1e-12 for delta in deltas),
            "ties": sum(abs(delta) <= 1e-12 for delta in deltas),
            "losses": sum(delta < -1e-12 for delta in deltas),
            "bootstrap_95_percentile_interval": [percentile(boot, .025), percentile(boot, .975)],
            "bootstrap_samples": samples, "seed": seed}


def clean_interval(value):
    if not value:
        return None
    fields = ("paired_questions", "independent_clusters", "mean_delta", "wins", "ties", "losses")
    output = {field: numeric(value[field]) for field in fields}
    interval = value["bootstrap_95_percentile_interval"]
    if len(interval) != 2 or numeric(interval[0]) > numeric(interval[1]):
        raise ValueError("invalid_interval")
    output.update(metric="ndcg_at_10", bootstrap_95_percentile_interval=list(interval))
    if output["wins"] + output["ties"] + output["losses"] != output["paired_questions"]:
        raise ValueError("interval_wtl_count_mismatch")
    return output


def verify_pairing(interval, deltas, clusters):
    expected = {"paired_questions": len(deltas), "independent_clusters": len(set(clusters)),
                "wins": sum(delta > 1e-12 for delta in deltas),
                "ties": sum(abs(delta) <= 1e-12 for delta in deltas),
                "losses": sum(delta < -1e-12 for delta in deltas)}
    if (any(interval[key] != value for key, value in expected.items()) or
            abs(interval["mean_delta"] - statistics.fmean(deltas)) > 1e-9):
        raise ValueError("paired_summary_mismatch")


def clean_token_telemetry(value):
    if not value:
        return None
    result = {key: numeric(value[key]) for key in (
        "items", "truncated_items", "truncated_fraction", "retained_tokens", "padded_tokens", "max_length")
              if value.get(key) is not None}
    for key in ("padding_mode", "truncation_strategy", "status"):
        if value.get(key) is not None:
            result[key] = safe_id(value[key])
    return result


def deltas_by_metric(candidate, reference):
    result = {}
    for k in KS:
        result[k] = {}
        for metric in METRICS:
            left, right = metric_value(candidate[k][metric]), metric_value(reference[k][metric])
            delta = left - right
            result[k][metric] = {"candidate": left, "reference": right, "delta": delta,
                                 "delta_pp": 100 * delta,
                                 "relative_percent": 100 * delta / right if right > 0 else None}
    return result


def record_groups(path):
    groups, seen = defaultdict(lambda: defaultdict(list)), set()
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("failed") is not False or raw.get("degraded") is not False:
                raise ValueError("completed_job_contains_failed_or_degraded_observation")
            if raw.get("answerable") is not True:
                raise ValueError("public_job_requires_answerable_queries")
            experiment, query_id = safe_id(raw["experiment"]), safe_id(raw["query_id"])
            repeat = raw.get("repeat", 0)
            if type(repeat) is not int or repeat < 0:
                raise ValueError("invalid_repeat")
            key = (experiment, query_id, repeat)
            if key in seen:
                raise ValueError("duplicate_query_observation")
            seen.add(key)
            # Deliberate whitelist: raw query text, title, trace and paths never escape.
            groups[experiment][query_id].append({
                "language": safe_id(raw["language"]),
                "cluster_id": safe_id(raw.get("cluster_id", query_id)),
                "metrics": {k: {metric: metric_value(raw["metrics"][k][metric]) for metric in METRICS}
                            for k in KS},
                **{name: numeric(raw.get(name), optional=True) for name in (
                    "candidate_recall", "rerank_pool_recall", "candidate_oracle_ndcg@10",
                    "rerank_pool_oracle_ndcg@10")},
            })
    collapsed = {}
    for experiment, questions in groups.items():
        collapsed[experiment] = {}
        for query_id, rows in questions.items():
            if len({(row["language"], row["cluster_id"]) for row in rows}) != 1:
                raise ValueError("repeat_metadata_mismatch")
            collapsed[experiment][query_id] = {
                "language": rows[0]["language"], "cluster_id": rows[0]["cluster_id"],
                "repeat_count": len(rows),
                "metrics": {k: {name: statistics.fmean(row["metrics"][k][name] for row in rows)
                                for name in METRICS} for k in KS},
                **{name: statistics.fmean(row[name] for row in rows)
                   if all(row[name] is not None for row in rows) else None for name in (
                       "candidate_recall", "rerank_pool_recall", "candidate_oracle_ndcg@10",
                       "rerank_pool_oracle_ndcg@10")},
            }
    return collapsed, len(seen)


def clean_gpu_proof(value):
    execution = value.get("execution", value)
    if execution.get("provider") != "DmlExecutionProvider" or execution.get("dml_compute_events", 0) <= 0:
        raise ValueError("missing_actual_gpu_compute_evidence")
    result = {name: numeric(execution[name]) for name in (
        "dml_kernel_events", "dml_compute_events", "cpu_kernel_events", "other_provider_kernel_events",
        "inference_calls_before_profile_end", "device_id") if name in execution}
    result["provider"] = "DmlExecutionProvider"
    result["profile_sha256"] = safe_id(execution["profile_sha256"]) if execution.get("profile_sha256") else None
    if "coverage_fraction" in value:
        if value["coverage_fraction"] != 1 or value["indexed_documents"] != value["total_documents"]:
            raise ValueError("incomplete_embedding_coverage")
        result.update({key: numeric(value[key]) for key in
                       ("coverage_fraction", "indexed_documents", "total_documents")})
    result["passage_token_telemetry"] = clean_token_telemetry(value.get("passage_token_telemetry"))
    return result


def clean_telemetry(rows):
    allowed = {"passage_gpu_encode", "query_gpu_encode", "pair_gpu_rerank",
               "lexical_uncached", "dense_cpu_exact_scan"}
    return [{"kind": row["kind"], "provider_fingerprint": safe_id(row["provider_fingerprint"]),
             "token_telemetry": clean_token_telemetry(row.get("token_telemetry")),
             **{key: numeric(row[key]) for key in
                ("max_length", "batches", "items", "wall_ms", "items_per_second")
                if row.get(key) is not None}} for row in rows if row.get("kind") in allowed]


def read_positive_ranks(path):
    result = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            query_id = safe_id(row["query_id"])
            for config_field, rank_field in (("reference", "before_positive_ranks"),
                                            ("experiment", "after_positive_ranks")):
                ranks = row.get(rank_field)
                if ranks is None:
                    continue
                cleaned = {safe_id(key): numeric(rank) for key, rank in ranks.items()}
                if any(type(rank) is not int or rank < 1 for rank in cleaned.values()):
                    raise ValueError("invalid_positive_rank")
                key = (safe_id(row[config_field]), query_id)
                if key in result and result[key] != cleaned:
                    raise ValueError("inconsistent_positive_rank_diagnostics")
                result[key] = cleaned
    return result


def safe_configuration(value):
    result = {"id": safe_id(value["id"])}
    for key in ("lexical", "dense", "rerank", "rewrite"):
        if key in value:
            if type(value[key]) is not bool:
                raise ValueError("invalid_configuration_boolean")
            result[key] = value[key]
    for key in ("candidate_limit", "rrf_k", "rerank_limit", "max_variants", "result_limit"):
        if key in value:
            result[key] = numeric(value[key])
    if "fusion" in value:
        result["fusion"] = safe_id(value["fusion"])
    return result


def load_completed_job(path, matrix):
    raw = read_json(path)
    if raw.get("status") != "completed":
        raise ValueError("job_not_completed")
    description = raw["job"]
    dataset, model = safe_id(description["dataset"]), safe_id(description["embedding_model"])
    if dataset not in matrix["datasets"] or model not in matrix["embedding_models"]:
        raise ValueError("job_outside_campaign_matrix")
    if description["max_length"] not in matrix["max_lengths"]:
        raise ValueError("job_length_outside_campaign_matrix")
    manifest = description.get("dataset_manifest", {})
    if manifest.get("public_data_only") is not True:
        raise ValueError("public_dataset_provenance_required")
    fingerprint = safe_id(raw["job_fingerprint"])
    if fingerprint != path.parent.name:
        raise ValueError("job_directory_fingerprint_mismatch")
    record_path = path.parent / "query_results.jsonl"
    records, observation_count = record_groups(record_path)
    expected_configs = {row["id"] for row in matrix["experiments"]}
    if set(records) != expected_configs or set(raw["aggregates"]) != expected_configs:
        raise ValueError("incomplete_configuration_matrix")
    question_sets = [set(rows) for rows in records.values()]
    if not question_sets or any(rows != question_sets[0] for rows in question_sets):
        raise ValueError("incomplete_or_unpaired_query_sets")
    question_count = raw["protocol"]["independent_questions"]
    if len(question_sets[0]) != question_count or manifest.get("selected_query_count") != question_count:
        raise ValueError("query_count_mismatch")
    if "selected_query_ids" in manifest.get("selection", {}):
        if set(manifest["selection"]["selected_query_ids"]) != question_sets[0]:
            raise ValueError("locked_query_ids_mismatch")
    aggregates = {}
    expected_specifications = {row["id"]: row for row in matrix["experiments"]}
    for config, rows in records.items():
        original = raw["aggregates"][config]
        if any(original["configuration"].get(key) != value
               for key, value in expected_specifications[config].items()):
            raise ValueError("configuration_does_not_match_campaign_matrix")
        if original["measured_runs"] != sum(row["repeat_count"] for row in rows.values()):
            raise ValueError("observation_count_mismatch")
        means = {k: {metric: statistics.fmean(row["metrics"][k][metric] for row in rows.values())
                     for metric in METRICS} for k in KS}
        for k in KS:
            for metric in METRICS:
                if abs(means[k][metric] - metric_value(original["metrics"][k][metric])) > 1e-9:
                    raise ValueError("aggregate_does_not_match_query_results")
        aggregates[config] = {"configuration": safe_configuration(original["configuration"]),
                              "metrics": means, "questions": len(rows),
                              "observations": original["measured_runs"],
                              **{key: numeric(original.get(key), optional=True) for key in (
                                  "candidate_recall", "rerank_pool_recall", "p50_ms", "p95_ms")}}
        for metric in ("candidate_recall", "rerank_pool_recall"):
            values = [row[metric] for row in rows.values()]
            aggregates[config][metric] = statistics.fmean(values) if all(value is not None for value in values) else None
    proof = {safe_id(key): clean_gpu_proof(value) for key, value in raw.get("gpu_proofs", {}).items()}
    required = {model}
    if any(row.get("rerank") for row in matrix["experiments"] if row["id"] != "rrf_rerank_english"):
        required.add(matrix["reranker_model"])
    if "rrf_rerank_english" in expected_configs:
        required.add(matrix["alternative_reranker_model"])
    if not required <= set(proof):
        raise ValueError("missing_required_gpu_proofs")
    if proof[model].get("coverage_fraction") != 1 or proof[model].get("total_documents") != manifest["corpus_count"]:
        raise ValueError("embedding_proof_does_not_cover_manifest_corpus")
    comparisons = []
    available_comparisons = {(row["experiment"], row["reference"]): row for row in raw.get("comparisons", [])}
    for contrast in matrix["contrasts"]:
        candidate, reference = contrast["experiment"], contrast["reference"]
        if (candidate, reference) not in available_comparisons:
            raise ValueError("missing_declared_component_contrast")
        interval = clean_interval(available_comparisons[(candidate, reference)])
        query_ids = sorted(records[candidate])
        if any((records[candidate][key]["language"], records[candidate][key]["cluster_id"]) !=
               (records[reference][key]["language"], records[reference][key]["cluster_id"]) for key in query_ids):
            raise ValueError("paired_query_metadata_mismatch")
        verify_pairing(interval, [records[candidate][key]["metrics"]["10"]["ndcg_at_k"] -
                                 records[reference][key]["metrics"]["10"]["ndcg_at_k"] for key in query_ids],
                       [records[candidate][key]["cluster_id"] for key in query_ids])
        comparisons.append({"experiment": candidate, "reference": reference,
                            "metrics": deltas_by_metric(aggregates[candidate]["metrics"], aggregates[reference]["metrics"]),
                            "paired_ndcg10": interval})
    interactions = [{"factors": ["rule_rewrite", "rerank"],
                     "formula": "both - rewrite_only - rerank_only + rrf",
                     "paired_ndcg10": clean_interval(row)} for row in raw.get("interactions", [])
                    if row.get("factors") == ["rule_rewrite", "rerank"]]
    for interaction in interactions:
        configs = ("rrf_rewrite_rerank", "rrf_rewrite", "rrf_rerank", "rrf")
        if not set(configs) <= set(records):
            raise ValueError("interaction_components_unavailable")
        keys = sorted(records["rrf"])
        differences = [sum(sign * records[config][key]["metrics"]["10"]["ndcg_at_k"]
                           for config, sign in zip(configs, (1, -1, -1, 1), strict=True)) for key in keys]
        verify_pairing(interaction["paired_ndcg10"], differences,
                       [records["rrf"][key]["cluster_id"] for key in keys])
    source_hashes = {"summary_sha256": sha256(path), "query_results_sha256": sha256(record_path)}
    diagnostic_path = path.parent / "query_deltas.jsonl"
    positive_ranks = read_positive_ranks(diagnostic_path)
    if diagnostic_path.exists():
        source_hashes["query_deltas_sha256"] = sha256(diagnostic_path)
    completed_at = datetime.fromisoformat(raw["completed_at"]).isoformat() if raw.get("completed_at") else None
    rewrite = {safe_id(config): {"rewrite_enabled": value["rewrite_enabled"],
               **{key: numeric(value[key]) for key in ("queries", "queries_with_additional_variants",
                                                       "additional_variant_fraction", "mean_variants")}}
               for config, value in raw.get("rewrite_diagnostics", {}).items()
               if type(value.get("rewrite_enabled")) is bool}
    empty = manifest.get("corpus_diagnostics", {})
    corpus_diagnostics = {key: numeric(empty[key]) for key in (
        "empty_document_count", "selected_positive_qrel_pairs_to_empty_documents",
        "selected_queries_with_positive_empty_documents") if key in empty}
    corpus_diagnostics["empty_document_ids"] = [safe_id(key) for key in empty.get("empty_document_ids", [])]
    return {
        "job_fingerprint": fingerprint, "dataset": dataset, "embedding_model": model,
        "max_length": description["max_length"], "status": "completed_verified",
        "corpus_sha256": safe_id(description["corpus_sha256"]),
        "query_sha256": safe_id(description["query_sha256"]),
        "run_fingerprint": safe_id(description["run_fingerprint"]),
        "corpus_count": numeric(manifest["corpus_count"]), "query_count": question_count,
        "evaluation_split": safe_id(manifest["evaluation_split"]),
        "observation_count": observation_count, "completed_at": completed_at,
        "corpus_diagnostics": corpus_diagnostics, "rewrite_diagnostics": rewrite,
        "retrieval_backends": {"lexical": "SQLite_FTS5_BM25_with_production_tokens",
                               "dense": "CPU_exact_scan_of_GPU_encoded_vectors",
                               "rerank": "GPU_cross_encoder_cached_raw_logits"},
        "aggregates": aggregates, "comparisons": comparisons, "interactions": interactions,
        "uncertainty": {key: numeric(raw["uncertainty"][key]) for key in ("samples", "seed")
                        if key in raw.get("uncertainty", {})},
        "gpu_proofs": proof, "telemetry_snapshot": clean_telemetry(raw.get("real_inference_telemetry", [])),
        "failed_attempts_before_completion": numeric(raw.get("reliability", {}).get("failed_attempts_before_completion", 0)),
        "source_artifacts": source_hashes,
        "_records": records, "_positive_ranks": positive_ranks,
    }


def observable_case(query_id, candidate, reference, after_ranks=None, before_ranks=None):
    delta = candidate["metrics"]["10"]["ndcg_at_k"] - reference["metrics"]["10"]["ndcg_at_k"]
    left, right = candidate.get("candidate_recall"), reference.get("candidate_recall")
    coverage_delta = left - right if left is not None and right is not None else None
    if coverage_delta is None:
        explanation = "候选覆盖诊断缺失，仅能观察最终相关性变化。"
    elif coverage_delta > 1e-12:
        explanation = "候选正例覆盖增加；最终排序变化仍需结合正例名次，不能据此断定语义原因。"
    elif coverage_delta < -1e-12:
        explanation = "候选正例覆盖下降；这是已观察到的覆盖变化，不单独证明总体退化原因。"
    else:
        explanation = "候选召回率不变；候选组成、相关等级和排序仍可能变化，不能据此认定候选集合相同。"
    return {"query_id": query_id, "language": candidate["language"], "ndcg10_delta_pp": 100 * delta,
            "before_ndcg10": reference["metrics"]["10"]["ndcg_at_k"],
            "after_ndcg10": candidate["metrics"]["10"]["ndcg_at_k"],
            "candidate_recall_delta": coverage_delta,
            "before_positive_ranks": before_ranks, "after_positive_ranks": after_ranks,
            **{f"{side}_{field}": row.get(field) for side, row in
               (("before", reference), ("after", candidate)) for field in
               ("candidate_recall", "rerank_pool_recall", "candidate_oracle_ndcg@10", "rerank_pool_oracle_ndcg@10")},
            "observable_explanation": explanation, "semantic_cause": "not_identified"}


def top_cases(candidate_job, reference_job, candidate_config, reference_config, count):
    left, right = candidate_job["_records"][candidate_config], reference_job["_records"][reference_config]
    rows = [observable_case(key, left[key], right[key],
                           candidate_job["_positive_ranks"].get((candidate_config, key)),
                           reference_job["_positive_ranks"].get((reference_config, key))) for key in sorted(left)]
    wins = sorted((row for row in rows if row["ndcg10_delta_pp"] > 1e-10),
                  key=lambda row: (-row["ndcg10_delta_pp"], row["query_id"]))[:count]
    losses = sorted((row for row in rows if row["ndcg10_delta_pp"] < -1e-10),
                    key=lambda row: (row["ndcg10_delta_pp"], row["query_id"]))[:count]
    return {"selection": "largest_positive_and_negative_ndcg10_deltas; diagnostic_not_prevalence",
            "wins": wins, "losses": losses}


def cross_job_comparisons(jobs, matrix, *, samples, seed, case_count):
    output, skipped = [], []
    model_order = {name: index for index, name in enumerate(matrix["embedding_models"])}
    for first, second in combinations(jobs, 2):
        if first["dataset"] != second["dataset"]:
            continue
        if first["embedding_model"] == second["embedding_model"] and first["max_length"] != second["max_length"]:
            reference, candidate = sorted((first, second), key=lambda job: job["max_length"])
            scope = "context_length"
        elif first["max_length"] == second["max_length"] and first["embedding_model"] != second["embedding_model"]:
            reference, candidate = sorted((first, second), key=lambda job: model_order[job["embedding_model"]])
            scope = "embedding_model"
        else:
            continue
        for config in CROSS_CONFIGS:
            if config not in candidate["aggregates"] or config not in reference["aggregates"]:
                continue
            identity = {"scope": scope, "dataset": candidate["dataset"], "configuration": config,
                        "candidate_job": candidate["job_fingerprint"], "reference_job": reference["job_fingerprint"]}
            if any(candidate[field] != reference[field] for field in ("corpus_sha256", "query_sha256", "evaluation_split", "run_fingerprint")):
                skipped.append(identity | {"reason": "data_or_run_fingerprint_mismatch"})
                continue
            if candidate["aggregates"][config]["configuration"] != reference["aggregates"][config]["configuration"]:
                skipped.append(identity | {"reason": "configuration_mismatch"})
                continue
            left, right = candidate["_records"][config], reference["_records"][config]
            if set(left) != set(right) or any((left[key]["language"], left[key]["cluster_id"]) !=
                                             (right[key]["language"], right[key]["cluster_id"]) for key in left):
                skipped.append(identity | {"reason": "query_pairing_mismatch"})
                continue
            keys = sorted(left)
            deltas = [left[key]["metrics"]["10"]["ndcg_at_k"] - right[key]["metrics"]["10"]["ndcg_at_k"] for key in keys]
            output.append(identity | {
                "candidate_model": candidate["embedding_model"], "reference_model": reference["embedding_model"],
                "candidate_max_length": candidate["max_length"], "reference_max_length": reference["max_length"],
                "metrics": deltas_by_metric(candidate["aggregates"][config]["metrics"], reference["aggregates"][config]["metrics"]),
                "paired_ndcg10": paired_interval(deltas, [left[key]["cluster_id"] for key in keys], samples=samples, seed=seed),
                "cases": top_cases(candidate, reference, config, config, case_count),
            })
    return output, skipped


def planned_counts(matrix, datasets):
    per_dataset = {}
    for name in matrix["datasets"]:
        spec = datasets["datasets"][name]
        available = spec.get("expected_evaluation_query_count", spec.get("expected_all_query_count"))
        maximum = spec["default_max_queries"]
        per_dataset[name] = min(available, maximum) if maximum else available
    multiplier = len(matrix["embedding_models"]) * len(matrix["max_lengths"])
    return {"jobs": len(per_dataset) * multiplier,
            "configuration_cells": len(per_dataset) * multiplier * len(matrix["experiments"]),
            "independent_queries_across_datasets": sum(per_dataset.values()),
            "query_configuration_observations": sum(per_dataset.values()) * multiplier * len(matrix["experiments"]),
            "queries_by_dataset": per_dataset, "basis": "configured_primary_campaign_not_measured_results"}


def reconcile_run_status(state, campaign):
    """A native worker exit can leave its last saved state running indefinitely."""
    worker_status = safe_id(state["status"]) if state.get("status") is not None else None
    supervisor_phase = safe_id(campaign["phase"]) if campaign.get("phase") is not None else None
    returncode = campaign.get("returncode")
    if returncode is not None and type(returncode) is not int:
        raise ValueError("invalid_supervisor_returncode")
    supervisor = {
        "available": bool(campaign), "phase": supervisor_phase, "returncode": returncode,
        "failed_step": safe_id(campaign["failed_step"]) if campaign.get("failed_step") else None,
        "incomplete_step": safe_id(campaign["incomplete_step"]) if campaign.get("incomplete_step") else None,
    }
    terminal_incomplete = {"failed", "budget_exhausted", "incomplete_stopped", "incomplete_deadline"}
    if supervisor_phase in terminal_incomplete:
        effective, basis = supervisor_phase, "supervisor_terminal_status"
    elif worker_status is not None:
        effective, basis = worker_status, "worker_saved_status"
    elif supervisor_phase is not None:
        effective, basis = supervisor_phase, "supervisor_phase"
    else:
        effective, basis = "not_started", "no_status_available"
    return {"source_run_status": effective, "source_run_status_basis": basis,
            "worker_run_status": worker_status, "supervisor_status": supervisor}


def build_report(run_dir, *, matrix=None, datasets=None, analysis_date=None, samples=2000, seed=20261015, case_count=2):
    run_dir = Path(run_dir)
    matrix = read_json(MATRIX_PATH) if matrix is None else matrix
    datasets = read_json(DATASETS_PATH) if datasets is None else datasets
    jobs, rejected = [], []
    for path in sorted((run_dir / "jobs").glob("*/summary.json")):
        try:
            jobs.append(load_completed_job(path, matrix))
        except (ValueError, KeyError, OSError, TypeError) as error:
            # No exception text: paths/raw contents may be included in parser errors.
            reason = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else type(error).__name__
            rejected.append({"job_directory_sha256": hashlib.sha256(path.parent.name.encode()).hexdigest(), "reason": reason})
    identities = [(job["dataset"], job["embedding_model"], job["max_length"]) for job in jobs]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate_completed_job_coordinates")
    plan = planned_counts(matrix, datasets)
    state_path = run_dir / "run_state.json"
    state = read_json(state_path) if state_path.exists() else {}
    campaign_path = run_dir.parent / "campaign-status.local.json"
    campaign = read_json(campaign_path) if campaign_path.exists() else {}
    status = reconcile_run_status(state, campaign)
    source_status = status["source_run_status"]
    state_checks = {
        "run_fingerprints_match": bool(state) and all(job["run_fingerprint"] == state.get("fingerprint") for job in jobs),
        "completed_job_ids_match": bool(state) and set(state.get("completed_jobs", [])) ==
        {job["job_fingerprint"] for job in jobs},
        "planned_query_counts_match": all(job["query_count"] == plan["queries_by_dataset"][job["dataset"]]
                                          for job in jobs),
    }
    complete = (source_status == "completed" and len(jobs) == plan["jobs"] and not rejected
                and all(state_checks.values()))
    publication_status = "completed" if complete else "partial_results" if jobs else "no_completed_results"
    cross, skipped = cross_job_comparisons(jobs, matrix, samples=samples, seed=seed, case_count=case_count)
    for job in jobs:
        for comparison in job["comparisons"]:
            comparison["cases"] = top_cases(job, job, comparison["experiment"], comparison["reference"], case_count)
    # Every job stores a cumulative checkpoint snapshot. Use ONE snapshot, never sum jobs.
    if state.get("real_inference_telemetry"):
        telemetry = clean_telemetry(state["real_inference_telemetry"])
        telemetry_source = "run_state_cumulative_snapshot"
    elif jobs:
        latest = max(jobs, key=lambda job: job.get("completed_at") or "")
        telemetry, telemetry_source = latest["telemetry_snapshot"], "latest_completed_job_cumulative_snapshot"
    else:
        telemetry, telemetry_source = [], "unavailable"
    independent = defaultdict(set)
    for job in jobs:
        independent[job["dataset"]].update(next(iter(job["_records"].values())))
    for job in jobs:
        del job["_records"], job["_positive_ranks"], job["telemetry_snapshot"]
    return {
        "schema_version": 1, "analysis_date": analysis_date or datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "analysis_timezone": "Asia/Shanghai", "publication_status": publication_status,
        **status, "campaign_complete_verified": complete,
        "run_state_reconciliation": state_checks,
        "exporter_sha256": sha256(Path(__file__)),
        "planned": plan,
        "observed": {"completed_verified_jobs": len(jobs), "rejected_job_reports": len(rejected),
                     "configuration_cells": sum(len(job["aggregates"]) for job in jobs),
                     "query_configuration_observations": sum(job["observation_count"] for job in jobs),
                     "unique_queries_by_dataset": {name: len(keys) for name, keys in sorted(independent.items())}},
        "jobs": jobs, "rejected_jobs": rejected,
        "cross_job_comparisons": cross, "cross_job_comparisons_skipped": skipped,
        "cross_job_scope": {"configurations": list(CROSS_CONFIGS),
                            "model_reference": "first_model_in_fixed_matrix", "length_reference": "shorter_input",
                            "status": "available" if cross else "awaiting_compatible_completed_job_pairs"},
        "inference_telemetry": {"source": telemetry_source, "aggregation": "single_cumulative_snapshot_not_sum_of_jobs", "rows": telemetry},
        "uncertainty": {"method": "paired_query_or_cluster_percentile_bootstrap", "cross_job_samples": samples,
                        "percentile_convention": "nearest_rank_matches_direct_contrasts",
                        "seed": seed, "repeats_averaged_before_pairing": True,
                        "multiple_comparison_correction": "none; exploratory_no_significance_claim"},
        "limitations": [
            "Only completed jobs with full matching query observations and actual GPU proof are quality results.",
            "Missing/incomplete results remain absent, never zero-valued model results.",
            "Quality measures retrieval, not generated-answer correctness, faithfulness or semantic abstention.",
            "T2 uses official dev; English and Chinese datasets differ in topics and corpus, so language effects are not controlled.",
            "Embedding model comparison changes training/model specification, not only parameter count.",
            "Input lengths change embedding/query encoder truncation only; reranker stays at 512. This is not passage chunking; dense ranking is exact CPU scan, not GPU ANN.",
            "Pipeline timing mixes uncached calls and cached-score replay; no end-to-end online latency or causal speedup claim.",
            "Real inference telemetry is cumulative and not summed across job snapshots; initialization is excluded.",
            "Largest wins/losses are diagnostic examples, not a representative sample or causal explanation.",
            "Positive rank details come only from saved qrel-derived diagnostics; missing evidence stays null.",
        ],
    }


def render_markdown(report):
    status = report["publication_status"]
    lines = ["# 公开数据 GPU 检索消融：增益与证据", "",
             f"分析日期：{report['analysis_date']}（Asia/Shanghai）。发布状态：**{status}**；有效运行状态：`{report['source_run_status']}`。", "",
             f"Worker 保存状态：`{report.get('worker_run_status') or 'unavailable'}`；"
             f"supervisor 状态：`{report.get('supervisor_status', {}).get('phase') or 'unavailable'}`；"
             f"supervisor 退出码：`{report.get('supervisor_status', {}).get('returncode')}`。"
             "监督器的明确失败或停止优先于 worker 留存状态；这些是状态文件快照，不是存活探测。", "",
             "只有完整 query/配置记录、聚合复核及实际 GPU 执行证据通过验证的 job 进入质量表。原始文本、模型文件和本机路径不在本报告内。", "",
             "| 项目 | 计划 | 已核验完成 |", "|---|---:|---:|",
             f"| dataset × model × length jobs | {report['planned']['jobs']} | {report['observed']['completed_verified_jobs']} |",
             f"| 配置单元 | {report['planned']['configuration_cells']} | {report['observed']['configuration_cells']} |",
             f"| query × 配置观测 | {report['planned']['query_configuration_observations']} | {report['observed']['query_configuration_observations']} |", "",
             f"计划独立问题数：{report['planned']['independent_queries_across_datasets']}（按数据集分开）。已核验问题数：`{json.dumps(report['observed']['unique_queries_by_dataset'], ensure_ascii=False)}`。重复配置不会增加独立问题数。", ""]
    if not report["jobs"]:
        lines += ["**当前没有可发布的完整公开模型质量结果。没有填入零分、模拟分数或预期增益；后续重新导出才会出现实测表格。**", ""]
    for job in report["jobs"]:
        lines += [f"## {job['dataset']} / {job['embedding_model']} / 编码器输入 {job['max_length']}", "",
                  f"split=`{job['evaluation_split']}`；完整语料 {job['corpus_count']} 文档，{job['query_count']} 个独立问题，{job['observation_count']} 条正式配置观测。", "",
                  "| 配置 | Recall@10 | MRR@10 | nDCG@10 | 候选 Recall | 重排池 Recall |",
                  "|---|---:|---:|---:|---:|---:|"]
        for config, aggregate in job["aggregates"].items():
            metric = aggregate["metrics"]["10"]
            pool = "—" if aggregate["rerank_pool_recall"] is None else f"{aggregate['rerank_pool_recall']:.5f}"
            candidate = "—" if aggregate["candidate_recall"] is None else f"{aggregate['candidate_recall']:.5f}"
            lines.append(f"| {config} | {metric['recall_at_k']:.5f} | {metric['mrr_at_k']:.5f} | {metric['ndcg_at_k']:.5f} | {candidate} | {pool} |")
        corpus_tokens = job["gpu_proofs"][job["embedding_model"]].get("passage_token_telemetry")
        if corpus_tokens and corpus_tokens.get("truncated_fraction") is not None:
            lines += ["", f"该语料编码截断率：{100 * corpus_tokens['truncated_fraction']:.2f}%"
                      f"（{corpus_tokens.get('truncated_items')}/{corpus_tokens.get('items')}）；"
                      "这是本 job 完整语料计数，query/pair 累计计数另列在 inference telemetry。"]
        empty = job["corpus_diagnostics"]
        if empty.get("empty_document_count"):
            lines += ["", f"官方空文档保留 {empty['empty_document_count']} 个；当前评估查询中"
                      f" {empty.get('selected_positive_qrel_pairs_to_empty_documents')} 条正例标注指向空文档。没有删除这些语料或标注。"]
        lines += ["", "### 直接组件对照", "", "Δ=候选−参考；pp=差值×100；相对百分比仅在参考值>0时计算。", "",
                  "| 候选 − 参考 | 参考 nDCG@10 | 候选 nDCG@10 | Δ pp | 相对 % | 配对95% CI pp | W/T/L |",
                  "|---|---:|---:|---:|---:|---|---|"]
        for comparison in job["comparisons"]:
            delta = comparison["metrics"]["10"]["ndcg_at_k"]
            interval = comparison["paired_ndcg10"]
            low, high = interval["bootstrap_95_percentile_interval"]
            relative = "—" if delta["relative_percent"] is None else f"{delta['relative_percent']:+.2f}"
            lines.append(f"| {comparison['experiment']} − {comparison['reference']} | {delta['reference']:.5f} | {delta['candidate']:.5f} | {delta['delta_pp']:+.3f} | {relative} | [{100 * low:+.3f}, {100 * high:+.3f}] | {interval['wins']}/{interval['ties']}/{interval['losses']} |")
        for interaction in job["interactions"]:
            stats = interaction["paired_ndcg10"]
            low, high = stats["bootstrap_95_percentile_interval"]
            lines += ["", f"rewrite×rerank 交互项（both−rewrite_only−rerank_only+RRF）：**{100 * stats['mean_delta']:+.3f} pp**，95% CI [{100 * low:+.3f}, {100 * high:+.3f}] pp。正值不等于完整路径总收益为正。"]
        lines += ["", "### 可观察的变化样例", "", "每个直接对照最多展示一个改善和一个退化例；这是极端样例诊断，不代表发生率。正例名次与完整样例在 JSON。", "",
                  "| 对照 | query_id | ΔnDCG pp | 候选 Recall 前→后 | 重排池 Recall 前→后 | 解释边界 |",
                  "|---|---|---:|---|---|---|"]
        for comparison in job["comparisons"]:
            for direction in ("wins", "losses"):
                for case in comparison["cases"][direction][:1]:
                    candidate = f"{case['before_candidate_recall']} → {case['after_candidate_recall']}"
                    pool = f"{case['before_rerank_pool_recall']} → {case['after_rerank_pool_recall']}"
                    lines.append(f"| {comparison['experiment']} − {comparison['reference']} | {case['query_id']} | {case['ndcg10_delta_pp']:+.3f} | {candidate} | {pool} | {case['observable_explanation']} |")
        lines += ["", f"完成前失败尝试：{job['failed_attempts_before_completion']}；最终质量表没有将失败当零分或完整结果。job 指纹：`{job['job_fingerprint']}`。", ""]
    lines += ["## 跨模型与编码器输入长度", "",
              "只比较 dense、RRF、RRF+rerank、RRF+rewrite+rerank 四个固定配置；要求相同数据/问题哈希、完整相同 query IDs、配置及运行指纹。模型参考为矩阵中的首个模型，长度参考为较短编码器输入。256/512 仅改变文档和 query 的 embedding 编码器输入；两组 cross-encoder reranker 都固定 512。", ""]
    if report["cross_job_comparisons"]:
        lines += ["| 数据集 / 配置 | 候选模型 / 长度 | 参考模型 / 长度 | ΔnDCG pp | 95% CI pp | W/T/L |",
                  "|---|---|---|---:|---|---|"]
        for row in report["cross_job_comparisons"]:
            stats = row["paired_ndcg10"]
            low, high = stats["bootstrap_95_percentile_interval"]
            lines.append(f"| {row['dataset']} / {row['configuration']} | {row['candidate_model']} / {row['candidate_max_length']} | {row['reference_model']} / {row['reference_max_length']} | {100 * stats['mean_delta']:+.3f} | [{100 * low:+.3f}, {100 * high:+.3f}] | {stats['wins']}/{stats['ties']}/{stats['losses']} |")
    else:
        lines.append("尚无兼容的已完成 job 配对；跨模型/长度结果保持待测。")
    lines += ["", "## GPU 证据、成本与限制", "",
              "各 job 的 DML compute kernel 事件及覆盖信息保存在 JSON 的 gpu_proofs；请求 GPU provider 本身不算执行证据。CPU 算子仍单独记录。", "",
              f"实际推理 telemetry 来源：`{report['inference_telemetry']['source']}`。只取一个累计快照，绝不把每个 job 的累计值相加。详情见 JSON。",
              "Pipeline p50/p95 混合首次推理和缓存回放，只保留在 JSON 作运行诊断，不能写成在线延迟。密集排序是 CPU exact scan；256/512 是 embedding 编码器输入截断，不是 chunking，reranker 均固定 512。初始化、生成、网络和生产并发不在该推理统计内。", "",
              "T2 使用官方 dev；中英数据集主题和语料不同，不构成受控语言对照。所有 CI 都是探索性配对 query/cluster bootstrap，没有多重比较校正；只评估检索，没有生成正确率、忠实度或语义拒答结论。", "",
              f"未通过导出验证的 job 数：{len(report['rejected_jobs'])}；跳过的不兼容跨 job 对照：{len(report['cross_job_comparisons_skipped'])}。原因代码见 JSON，未通过项不进入质量均值。", "",
              "[完整 JSON](PUBLIC_GPU_ABLATION.json) 包含 k=1/3/5/10、绝对值、百分点/相对变化、案例正例名次及来源哈希；[CSV](PUBLIC_GPU_ABLATION.csv) 便于筛选配置和指标。", ""]
    return "\n".join(lines)


def render_csv(report):
    columns = ("row_type", "dataset", "embedding_model", "max_length", "configuration", "reference",
               "metric", "k", "value", "reference_value", "delta", "delta_pp", "relative_percent",
               "ci_low_pp", "ci_high_pp", "wins", "ties", "losses", "questions")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for job in report["jobs"]:
        context = {key: job[key] for key in ("dataset", "embedding_model", "max_length")}
        for config, aggregate in job["aggregates"].items():
            for k in KS:
                for metric in METRICS:
                    writer.writerow(context | {"row_type": "absolute", "configuration": config,
                                                "metric": metric, "k": k, "value": aggregate["metrics"][k][metric],
                                                "questions": aggregate["questions"]})
        for row in job["comparisons"]:
            write_contrast_csv(writer, context, row, "component", row["experiment"], row["reference"])
    by_id = {job["job_fingerprint"]: job for job in report["jobs"]}
    for row in report["cross_job_comparisons"]:
        candidate, reference = by_id[row["candidate_job"]], by_id[row["reference_job"]]
        context = {key: candidate[key] for key in ("dataset", "embedding_model", "max_length")}
        label = f"{reference['embedding_model']}:{reference['max_length']}:{row['configuration']}"
        write_contrast_csv(writer, context, row, row["scope"], row["configuration"], label)
    return stream.getvalue()


def write_contrast_csv(writer, context, contrast, row_type, config, reference):
    for k in KS:
        for metric in METRICS:
            delta = contrast["metrics"][k][metric]
            row = context | {"row_type": row_type, "configuration": config, "reference": reference,
                             "metric": metric, "k": k, "value": delta["candidate"],
                             "reference_value": delta["reference"],
                             **{key: delta[key] for key in ("delta", "delta_pp", "relative_percent")}}
            if k == "10" and metric == "ndcg_at_k":
                stats = contrast["paired_ndcg10"]
                low, high = stats["bootstrap_95_percentile_interval"]
                row.update(ci_low_pp=100 * low, ci_high_pp=100 * high,
                           **{key: stats[key] for key in ("wins", "ties", "losses")},
                           questions=stats["paired_questions"])
            writer.writerow(row)


def export_report(report, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    contents = {"json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                "md": render_markdown(report), "csv": render_csv(report)}
    for extension, content in contents.items():
        path = output / f"PUBLIC_GPU_ABLATION.{extension}"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content.encode("utf-8"))
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/evaluation")
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--analysis-date")
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--cases-per-direction", type=int, default=2)
    args = parser.parse_args()
    if args.bootstrap_resamples < 1 or args.cases_per_direction < 0:
        parser.error("bootstrap must be positive and case count nonnegative")
    if args.analysis_date:
        datetime.strptime(args.analysis_date, "%Y-%m-%d")
    report = build_report(args.run_dir, matrix=read_json(args.matrix), analysis_date=args.analysis_date,
                          samples=args.bootstrap_resamples, seed=args.seed, case_count=args.cases_per_direction)
    export_report(report, args.output_dir)
    print(json.dumps({"publication_status": report["publication_status"], "observed": report["observed"]}), flush=True)


if __name__ == "__main__":
    main()
