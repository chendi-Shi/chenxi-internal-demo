"""Historical analysis integrity: query units, missing evidence and reproducibility."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "retrieval_history", ROOT / "backend" / "benchmarks" / "analyze_retrieval_history.py")
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


def case(query_id, value, repeat=0, language="en", control=False):
    return {
        "query_id": query_id, "repeat": repeat, "language": language,
        "no_answer_expected": control,
        "metrics": None if control else {metric: value for metric in history.METRICS},
        "elapsed_ms": 10 + repeat, "failed": False, "warnings": [],
        "returned_count": 0 if control else 1,
    }


class HistoricalAnalysisTests(unittest.TestCase):
    def test_repeats_are_averaged_before_pairing_without_inflating_query_count(self):
        candidate = history.collapse_query_repeats([
            case("a", 0, 0), case("a", 1, 1), case("a", 1, 2), case("b", 0.5)])
        reference = history.collapse_query_repeats([case("a", 0), case("b", 0.5)])
        result = history.paired_comparison(candidate, reference, seed=9, resamples=100)
        self.assertEqual(result["metrics"]["ndcg_at_k"]["paired_query_count"], 2)
        self.assertAlmostEqual(result["metrics"]["ndcg_at_k"]["mean_delta"], 1 / 3)
        self.assertEqual(candidate["a"]["mean_elapsed_ms"], 11)
        self.assertEqual(result["metrics"]["ndcg_at_k"]["wins"], 1)
        self.assertEqual(result["metrics"]["ndcg_at_k"]["ties"], 1)

    def test_controls_are_excluded_and_unmatched_query_ids_are_visible(self):
        candidate = history.collapse_query_repeats([
            case("a", 1), case("candidate_only", 0.5), case("empty", 0, control=True)])
        reference = history.collapse_query_repeats([
            case("a", 0.5), case("reference_only", 1), case("empty", 0, control=True)])
        result = history.paired_comparison(candidate, reference, seed=1, resamples=50)
        self.assertEqual(result["query_ids"], ["a"])
        self.assertEqual(result["excluded_candidate_only_ids"], ["candidate_only"])
        self.assertEqual(result["excluded_reference_only_ids"], ["reference_only"])
        self.assertEqual(result["metrics"]["ndcg_at_k"]["bootstrap_percentile_95_ci"], [0.5, 0.5])

    def test_pairing_is_independent_of_row_order_and_language_stratifies(self):
        left = [case("b", 0.2, language="zh"), case("a", 1)]
        right = [case("a", 0.5), case("b", 0.2, language="zh")]
        result = history.paired_comparison(history.collapse_query_repeats(left),
                                          history.collapse_query_repeats(right),
                                          seed=42, resamples=100)
        reordered = history.paired_comparison(history.collapse_query_repeats(left[::-1]),
                                             history.collapse_query_repeats(right[::-1]),
                                             seed=42, resamples=100)
        self.assertEqual(result, reordered)
        chinese = history.paired_comparison(history.collapse_query_repeats(left),
                                           history.collapse_query_repeats(right),
                                           seed=42, resamples=100, language="zh")
        self.assertEqual(chinese["query_ids"], ["b"])
        self.assertEqual(chinese["metrics"]["ndcg_at_k"]["ties"], 1)

    def test_bootstrap_is_deterministic_and_ties_do_not_create_uncertainty(self):
        values = [-0.25, 0, 0.25, 0.5]
        first = history.summarize_differences(values, seed=101, resamples=1000)
        second = history.summarize_differences(values, seed=101, resamples=1000)
        self.assertEqual(first, second)
        self.assertEqual((first["wins"], first["ties"], first["losses"]), (2, 1, 1))
        tied = history.summarize_differences([0] * 8, resamples=50)
        self.assertEqual(tied["bootstrap_percentile_95_ci"], [0, 0])
        self.assertEqual(history.summarize_differences([], resamples=5)["status"], "unavailable")

    def test_malformed_observations_fail_instead_of_inventing_values(self):
        with self.assertRaisesRegex(ValueError, "duplicate_query_repeat"):
            history.collapse_query_repeats([case("a", 0.5), case("a", 0.5)])
        missing = case("a", 0.5)
        missing["metrics"] = None
        with self.assertRaisesRegex(ValueError, "missing_answerable_metrics"):
            history.collapse_query_repeats([missing])
        with self.assertRaisesRegex(ValueError, "metric_out_of_range"):
            history.collapse_query_repeats([case("a", 1.1)])
        with self.assertRaisesRegex(ValueError, "non_finite"):
            history.collapse_query_repeats([case("a", float("nan"))])
        with self.assertRaisesRegex(ValueError, "bootstrap_resamples_must_be_positive"):
            history.summarize_differences([0], resamples=0)

    def test_query_metadata_must_match_across_repeats_and_stages(self):
        with self.assertRaisesRegex(ValueError, "inconsistent_query_metadata"):
            history.collapse_query_repeats([case("a", 1), case("a", 1, repeat=1, language="zh")])
        with self.assertRaisesRegex(ValueError, "cross_stage_query_metadata_mismatch"):
            history.paired_comparison(history.collapse_query_repeats([case("a", 1)]),
                                      history.collapse_query_repeats([case("a", 1, language="zh")]),
                                      seed=1, resamples=10)

    def test_missing_latency_is_not_zero_or_a_partial_average(self):
        missing = case("a", 1, repeat=1)
        del missing["elapsed_ms"]
        rows = history.collapse_query_repeats([case("a", 1), missing])
        self.assertIsNone(rows["a"]["mean_elapsed_ms"])

    def test_failure_and_warning_observations_remain_in_denominator(self):
        failed = case("a", 0)
        failed["failed"] = True
        failed["warnings"] = ["synthetic-warning"]
        rows = history.collapse_query_repeats([failed, case("a", 1, repeat=1)])
        self.assertEqual(rows["a"]["metrics"]["ndcg_at_k"], 0.5)
        self.assertEqual(rows["a"]["failed_runs"], 1)
        self.assertEqual(rows["a"]["warning_runs"], 1)

    def test_committed_real_report_cannot_support_query_level_inference(self):
        source = json.loads((ROOT / "docs/evaluation/POSTGRES_REAL_REPORT.json").read_text(encoding="utf-8"))
        original = copy.deepcopy(source)
        result = history.analyse_report(source, resamples=50)
        self.assertEqual(source, original)
        self.assertEqual(result["availability"]["query_level_stages"], [])
        for comparison in result["comparisons"]:
            self.assertEqual(comparison["paired"]["status"], "unavailable")
            self.assertIsNone(comparison["paired"]["ci"])
            self.assertIsNone(comparison["paired_latency_diagnostic"])
        rrf = next(row for row in result["comparisons"]
                   if row["candidate"] == "rrf" and row["reference"] == "lexical")
        self.assertAlmostEqual(rrf["aggregate_deltas"]["recall_at_k"]["percentage_points"], -12.5)

    def test_committed_synthetic_query_means_reconcile_and_no_datasets_are_pooled(self):
        result = history.build_analysis(ROOT, analysis_date="2026-10-15", seed=7, resamples=30)
        self.assertFalse(result["bootstrap"]["datasets_pooled"])
        self.assertEqual(result["datasets"]["real"]["measured_date"], "2026-10-13")
        self.assertEqual(result["datasets"]["synthetic"]["measured_date"], "2026-10-09")
        synthetic = result["datasets"]["synthetic"]
        for stage in synthetic["stages"]:
            summary = stage["query_level_summary"]
            self.assertEqual(summary["unique_query_count"], 20)
            self.assertEqual(summary["answerable_count"], 16)
            self.assertEqual(summary["control_successful_empty_runs"], 4)
            for difference in summary["difference_from_reported_aggregate"].values():
                self.assertAlmostEqual(difference, 0)
        rewrite = next(row for row in synthetic["comparisons"]
                       if row["candidate"] == "rewrite" and row["reference"] == "rerank")
        for metric in history.METRICS:
            self.assertEqual(rewrite["paired"]["metrics"][metric]["ties"], 16)
        for source in synthetic["sources"]:
            self.assertEqual(len(source["sha256"]), 64)
            self.assertFalse(Path(source["path"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
