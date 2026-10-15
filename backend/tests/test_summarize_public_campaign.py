"""Campaign publication guards using deliberately invented local observations."""
import copy
import csv
import importlib.util
import io
import json
import statistics
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "campaign_export", ROOT / "backend/benchmarks/summarize_public_campaign.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class PublicCampaignExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.matrix = {
            "datasets": ["invented"], "embedding_models": ["encoder_a", "encoder_b"],
            "max_lengths": [256, 512],
            "experiments": [{"id": "dense", "lexical": False}, {"id": "rrf"}],
            "contrasts": [{"experiment": "rrf", "reference": "dense"}],
        }
        self.datasets = {"datasets": {"invented": {
            "expected_evaluation_query_count": 2, "default_max_queries": 1000}}}

    def tearDown(self):
        self.temporary.cleanup()

    def job(self, model="encoder_a", length=256, offset=0, repeats=1, telemetry_items=10):
        identity = f"{model}-{length}"
        path = self.run / "jobs" / identity / "summary.json"
        records, aggregates = [], {}
        defaults = {"lexical": True, "dense": True, "fusion": "rrf", "candidate_limit": 100,
                    "rrf_k": 60, "rerank": False, "rerank_limit": 50, "rewrite": False,
                    "max_variants": 3, "result_limit": 10}
        per_query = {"dense": [0.2 + offset, 0.6 + offset], "rrf": [0.6 + offset, 0.5 + offset]}
        for config in self.matrix["experiments"]:
            for number, value in enumerate(per_query[config["id"]], 1):
                for repeat in range(repeats):
                    records.append({
                        "experiment": config["id"], "query_id": f"q{number}", "language": "en",
                        "cluster_id": f"q{number}", "repeat": repeat,
                        "failed": False, "degraded": False, "answerable": True,
                        "metrics": {k: {name: value for name in exporter.METRICS} for k in exporter.KS},
                        "candidate_recall": value, "rerank_pool_recall": None,
                        "candidate_oracle_ndcg@10": value,
                        "query": "SECRET QUERY TEXT", "trace": {"path": "C:/private/document"},
                    })
            mean = statistics.fmean(per_query[config["id"]])
            aggregates[config["id"]] = {
                "configuration": defaults | config, "measured_runs": 2 * repeats,
                "metrics": {k: {name: mean for name in exporter.METRICS} for k in exporter.KS},
                "candidate_recall": mean, "p50_ms": 1.5, "p95_ms": 2.0,
            }
        token_stats = {"items": 10, "truncated_items": 2, "truncated_fraction": .2,
                       "retained_tokens": 200, "padded_tokens": 2560, "max_length": length,
                       "padding_mode": "fixed_max_length", "truncation_strategy": "longest_first",
                       "status": "measured", "text": "SECRET QUERY TEXT"}
        summary = {
            "status": "completed", "job_fingerprint": identity,
            "job": {"dataset": "invented", "embedding_model": model, "max_length": length,
                    "corpus_sha256": "a" * 64, "query_sha256": "b" * 64, "run_fingerprint": "run1",
                    "dataset_manifest": {"public_data_only": True, "corpus_count": 10,
                                         "selected_query_count": 2, "evaluation_split": "test",
                                         "selection": {"selected_query_ids": ["q1", "q2"]}}},
            "protocol": {"independent_questions": 2}, "aggregates": aggregates,
            "comparisons": [{"experiment": "rrf", "reference": "dense", **exporter.paired_interval(
                [.4, -.1], ["q1", "q2"], samples=100, seed=7)}],
            "gpu_proofs": {model: {"execution": {"provider": "DmlExecutionProvider", "dml_compute_events": 10,
                                                    "cpu_kernel_events": 2, "profile_sha256": "c" * 64,
                                                    "path": "C:/private/profile.json"},
                                    "indexed_documents": 10, "total_documents": 10, "coverage_fraction": 1,
                                    "passage_token_telemetry": token_stats}},
            "real_inference_telemetry": [{"kind": "passage_gpu_encode", "provider_fingerprint": "provider1",
                                          "max_length": length, "items": telemetry_items, "batches": 2,
                                          "wall_ms": 250, "items_per_second": 40,
                                          "token_telemetry": token_stats}],
            "completed_at": f"2026-10-15T{10 + int(length == 512):02d}:00:00+00:00",
            "rewrite_diagnostics": {"rrf": {"rewrite_enabled": False, "queries": 2,
                                             "queries_with_additional_variants": 0,
                                             "additional_variant_fraction": 0, "mean_variants": 1}},
            "reliability": {"failed_attempts_before_completion": 1},
            "private_path": "C:/private/document",
        }
        save_json(path, summary)
        (path.parent / "query_results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
        (path.parent / "query_deltas.jsonl").write_text("\n".join(json.dumps({
            "experiment": "rrf", "reference": "dense", "query_id": f"q{number}",
            "before_positive_ranks": {f"d{number}": 3}, "after_positive_ranks": {f"d{number}": 1},
            "query": "SECRET QUERY TEXT"}) for number in (1, 2)) + "\n", encoding="utf-8")
        return path, summary

    def report(self):
        return exporter.build_report(self.run, matrix=self.matrix, datasets=self.datasets,
                                     analysis_date="2026-10-15", samples=100, seed=7)

    def test_no_results_has_no_zero_quality_placeholder(self):
        report = self.report()
        self.assertEqual(report["publication_status"], "no_completed_results")
        self.assertEqual(report["jobs"], [])
        self.assertEqual(report["planned"]["configuration_cells"], 8)
        self.assertEqual(len(exporter.render_csv(report).splitlines()), 1)
        exporter.export_report(report, self.root / "published")
        self.assertTrue((self.root / "published/PUBLIC_GPU_ABLATION.md").exists())

    def test_completed_job_retains_evidence_without_raw_text_or_private_paths(self):
        self.job()
        report = self.report()
        self.assertEqual(report["publication_status"], "partial_results")
        serialized = json.dumps(report)
        self.assertNotIn("SECRET", serialized)
        self.assertNotIn("C:/private", serialized)
        job = report["jobs"][0]
        self.assertEqual(job["gpu_proofs"]["encoder_a"]["passage_token_telemetry"]["truncated_fraction"], .2)
        self.assertEqual(report["inference_telemetry"]["rows"][0]["token_telemetry"]["truncated_items"], 2)
        cases = job["comparisons"][0]["cases"]
        self.assertEqual(cases["wins"][0]["query_id"], "q1")
        self.assertEqual(cases["losses"][0]["query_id"], "q2")
        self.assertEqual(cases["wins"][0]["after_positive_ranks"], {"d1": 1})
        self.assertEqual(cases["wins"][0]["semantic_cause"], "not_identified")

    def test_repeats_are_collapsed_before_cross_job_bootstrap(self):
        self.job(repeats=3)
        self.job(length=512, offset=.1, repeats=2, telemetry_items=20)
        report = self.report()
        self.assertEqual(report["observed"]["query_configuration_observations"], 20)
        self.assertEqual(report["observed"]["unique_queries_by_dataset"], {"invented": 2})
        for comparison in report["cross_job_comparisons"]:
            stats = comparison["paired_ndcg10"]
            self.assertEqual(stats["paired_questions"], 2)
            self.assertEqual(stats["wins"], 2)
            self.assertAlmostEqual(stats["mean_delta"], .1)
        self.assertEqual(report["inference_telemetry"]["rows"][0]["items"], 20)

    def test_hash_mismatch_blocks_cross_job_comparison(self):
        self.job()
        path, summary = self.job(model="encoder_b", offset=.1)
        summary["job"]["query_sha256"] = "d" * 64
        save_json(path, summary)
        report = self.report()
        self.assertEqual(report["cross_job_comparisons"], [])
        self.assertEqual(len(report["cross_job_comparisons_skipped"]), 2)

    def test_partial_or_falsified_aggregate_is_never_a_quality_result(self):
        for alteration, reason in (("status", "job_not_completed"),
                                   ("aggregate", "aggregate_does_not_match_query_results"),
                                   ("wtl", "paired_summary_mismatch")):
            with self.subTest(alteration=alteration):
                path, summary = self.job()
                if alteration == "status":
                    summary["status"] = "failed"
                elif alteration == "aggregate":
                    summary["aggregates"]["rrf"]["metrics"]["10"]["ndcg_at_k"] = 1
                else:
                    summary["comparisons"][0].update(wins=2, losses=0)
                save_json(path, summary)
                report = self.report()
                self.assertEqual(report["jobs"], [])
                self.assertEqual(report["rejected_jobs"][0]["reason"], reason)

    def test_failed_or_duplicate_observation_is_rejected(self):
        path, _ = self.job()
        records_path = path.parent / "query_results.jsonl"
        text = records_path.read_text()
        records_path.write_text(text + text.splitlines()[0] + "\n")
        self.assertEqual(self.report()["rejected_jobs"][0]["reason"], "duplicate_query_observation")
        row = json.loads(text.splitlines()[0])
        row["degraded"] = True
        records_path.write_text(json.dumps(row) + "\n" + "\n".join(text.splitlines()[1:]))
        self.assertEqual(self.report()["rejected_jobs"][0]["reason"],
                         "completed_job_contains_failed_or_degraded_observation")

    def test_all_jobs_and_state_identity_are_required_for_campaign_completion(self):
        paths = [self.job(model=model, length=length)[0] for model in self.matrix["embedding_models"]
                 for length in self.matrix["max_lengths"]]
        state = {"status": "completed", "fingerprint": "run1",
                 "completed_jobs": [path.parent.name for path in paths]}
        save_json(self.run / "run_state.json", state)
        self.assertTrue(self.report()["campaign_complete_verified"])
        state["fingerprint"] = "stale"
        save_json(self.run / "run_state.json", state)
        self.assertFalse(self.report()["campaign_complete_verified"])
        state["fingerprint"] = "run1"
        state["completed_jobs"].pop()
        save_json(self.run / "run_state.json", state)
        self.assertFalse(self.report()["campaign_complete_verified"])

    def test_budget_stop_preserves_partial_results(self):
        self.job()
        save_json(self.run / "run_state.json", {"status": "budget_exhausted", "fingerprint": "run1"})
        report = self.report()
        self.assertEqual(report["publication_status"], "partial_results")
        self.assertEqual(report["source_run_status"], "budget_exhausted")

    def test_supervisor_native_failure_overrides_stale_worker_running(self):
        self.job()
        save_json(self.run / "run_state.json", {"status": "running", "fingerprint": "run1"})
        save_json(self.root / "campaign-status.local.json", {
            "phase": "failed", "returncode": 3221225477, "failed_step": "gpu_experiments",
            "reason": "SECRET path C:/private/native-error", "pid": 1234,
        })
        report = self.report()
        self.assertEqual(report["publication_status"], "partial_results")
        self.assertEqual(report["source_run_status"], "failed")
        self.assertEqual(report["source_run_status_basis"], "supervisor_terminal_status")
        self.assertEqual(report["worker_run_status"], "running")
        self.assertEqual(report["supervisor_status"]["phase"], "failed")
        self.assertEqual(report["supervisor_status"]["returncode"], 3221225477)
        self.assertEqual(report["supervisor_status"]["failed_step"], "gpu_experiments")
        self.assertFalse(report["campaign_complete_verified"])
        self.assertNotIn("SECRET", json.dumps(report))
        markdown = exporter.render_markdown(report)
        self.assertIn("有效运行状态：`failed`", markdown)
        self.assertIn("Worker 保存状态：`running`", markdown)
        self.assertIn("supervisor 退出码：`3221225477`", markdown)

    def test_worker_status_remains_compatible_without_supervisor(self):
        save_json(self.run / "run_state.json", {"status": "running", "fingerprint": "run1"})
        report = self.report()
        self.assertEqual(report["source_run_status"], "running")
        self.assertEqual(report["source_run_status_basis"], "worker_saved_status")
        self.assertEqual(report["worker_run_status"], "running")
        self.assertFalse(report["supervisor_status"]["available"])
        self.assertIsNone(report["supervisor_status"]["phase"])
        self.assertIsNone(report["supervisor_status"]["returncode"])
        self.assertFalse(report["campaign_complete_verified"])

    def test_supervisor_failure_blocks_campaign_completion_with_all_jobs_saved(self):
        paths = [self.job(model=model, length=length)[0] for model in self.matrix["embedding_models"]
                 for length in self.matrix["max_lengths"]]
        save_json(self.run / "run_state.json", {
            "status": "completed", "fingerprint": "run1", "completed_jobs": [path.parent.name for path in paths]})
        save_json(self.root / "campaign-status.local.json", {"phase": "failed", "returncode": 1,
                                                              "failed_step": "publish_results"})
        report = self.report()
        self.assertEqual(report["source_run_status"], "failed")
        self.assertEqual(report["worker_run_status"], "completed")
        self.assertEqual(report["publication_status"], "partial_results")
        self.assertFalse(report["campaign_complete_verified"])

    def test_gpu_provider_request_without_compute_evidence_or_coverage_is_rejected(self):
        for missing in ("kernel", "coverage"):
            path, summary = self.job()
            proof = summary["gpu_proofs"]["encoder_a"]
            if missing == "kernel":
                proof["execution"]["dml_compute_events"] = 0
            else:
                proof["coverage_fraction"] = .5
            save_json(path, summary)
            self.assertEqual(self.report()["jobs"], [])

    def test_relative_change_zero_baseline_and_csv_pp_are_explicit(self):
        self.job()
        report = self.report()
        rows = list(csv.DictReader(io.StringIO(exporter.render_csv(report))))
        contrast = next(row for row in rows if row["row_type"] == "component"
                        and row["metric"] == "ndcg_at_k" and row["k"] == "10")
        self.assertAlmostEqual(float(contrast["delta_pp"]), 15)
        self.assertAlmostEqual(float(contrast["relative_percent"]), 37.5)
        reference = {k: {name: 0 for name in exporter.METRICS} for k in exporter.KS}
        candidate = copy.deepcopy(reference)
        candidate["10"]["ndcg_at_k"] = .1
        self.assertIsNone(exporter.deltas_by_metric(candidate, reference)["10"]["ndcg_at_k"]["relative_percent"])

    def test_cluster_bootstrap_is_deterministic_and_does_not_count_repeats(self):
        first = exporter.paired_interval([.1, .2, -.1], ["a", "a", "b"], samples=100, seed=5)
        second = exporter.paired_interval([.1, .2, -.1], ["a", "a", "b"], samples=100, seed=5)
        self.assertEqual(first, second)
        self.assertEqual(first["independent_clusters"], 2)
        self.assertEqual(first["paired_questions"], 3)

    def test_real_summary_schema_and_interaction_reconcile_against_query_records(self):
        from research_agent.rag_ablation import public_summary

        path, original = self.job()
        records_path = path.parent / "query_results.jsonl"
        rows = [json.loads(line) for line in records_path.read_text().splitlines()]
        configurations = [aggregate["configuration"] for aggregate in original["aggregates"].values()]
        for name, rewrite, rerank, delta in (("rrf_rewrite", True, False, .05),
                                            ("rrf_rerank", False, True, .1),
                                            ("rrf_rewrite_rerank", True, True, .2)):
            config = configurations[1] | {"id": name, "rewrite": rewrite, "rerank": rerank}
            configurations.append(config)
            self.matrix["experiments"].append(config)
            for base in [row for row in rows if row["experiment"] == "rrf"]:
                row = copy.deepcopy(base)
                row["experiment"] = name
                for k in exporter.KS:
                    row["metrics"][k] = {metric: value + delta for metric, value in row["metrics"][k].items()}
                row["rerank_pool_recall"] = .8 if rerank else None
                rows.append(row)
        for row in rows:
            row.update(elapsed_ms=1, ids=["d1"], control_type="unspecified")
        actual = public_summary({"experiments": configurations, "records": rows, "warmups": [],
                                 "protocol": original["protocol"]},
                                contrasts=self.matrix["contrasts"], bootstrap_samples=100, seed=7)
        original.update(actual)
        self.matrix["reranker_model"] = "reranker"
        original["gpu_proofs"]["reranker"] = original["gpu_proofs"]["encoder_a"]["execution"]
        save_json(path, original)
        records_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        report = self.report()
        self.assertEqual(report["rejected_jobs"], [])
        stats = report["jobs"][0]["interactions"][0]["paired_ndcg10"]
        self.assertAlmostEqual(stats["mean_delta"], .05)
        self.assertEqual(stats["wins"], 2)
        original["interactions"][0]["mean_delta"] = .3
        save_json(path, original)
        self.assertEqual(self.report()["rejected_jobs"][0]["reason"], "paired_summary_mismatch")


if __name__ == "__main__":
    unittest.main()
