"""Behavioral checks for actual SQLite evaluation, qrels and repeated measurements."""

import importlib.util
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from research_agent.hub import Hub
from research_agent.hub_models import SearchRequest

BENCHMARK = Path(__file__).parents[1] / "benchmarks/run_sqlite_ablation.py"
SPEC = importlib.util.spec_from_file_location("run_sqlite_ablation", BENCHMARK)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.fixture
def inputs():
    suite = benchmark.read_json(BENCHMARK.parents[1] / "samples/postgres_synthetic/suite.json")
    matrix = benchmark.read_json(BENCHMARK.parents[1] / "samples/rag_ablation/sqlite_matrix.json")
    return suite, matrix


def test_ingest_generated_ids_map_to_qrels_and_disabled_sources_stay_hidden(tmp_path, inputs):
    suite, matrix = inputs
    hub = Hub(tmp_path / "hub", retrieval_backend="sqlite")
    reverse = benchmark.ingest_suite(hub, suite, matrix["disabled_source_ids"])
    assert set(reverse.values()) == {doc["id"] for doc in suite["documents"]}
    assert set(reverse).isdisjoint(reverse.values())
    response = hub.search(SearchRequest(query="Aurora liquid cooling"))
    ranked = [reverse[hit["id"]] for hit in response["results"]]
    assert ranked[0] == "syn-gpu-liquid"
    assert benchmark.rank_metrics(ranked, {"syn-gpu-liquid": 2})["recall@1"] == 1
    for planner in benchmark.PLANNERS:
        with benchmark.planner_scope(planner):
            result = hub.search(SearchRequest(query="Grove cash flow", source_ids=["synthetic_disabled"]))
        assert result["results"] == []


def test_graded_metrics_and_no_answer_are_not_conflated():
    result = benchmark.rank_metrics(["irrelevant", "secondary", "primary"], {"primary": 2, "secondary": 1})
    assert result["recall@1"] == 0
    assert result["recall@3"] == 1
    assert result["mrr@3"] == 0.5
    assert 0 < result["ndcg@3"] < 1
    assert benchmark.rank_metrics([], {}) == {}
    with pytest.raises(ValueError, match="duplicate_ranked"):
        benchmark.rank_metrics(["primary", "primary"], {"primary": 2})


def sample(metric, ids):
    return {"metrics": {"ndcg@5": metric}, "ranked_ids": ids, "wall_ms": 3,
            "payload_utf8_bytes": 100, "failure": None, "warnings": [],
            "no_answer_correct": None, "policy_version": 2}


def test_repeats_aggregate_within_query_and_bootstrap_uses_queries():
    first = benchmark.aggregate_query([sample(1, ["a"]), sample(0, [])])
    assert first["metrics"]["ndcg@5"] == 0.5
    assert not first["ranking_stable"]
    comparison = benchmark.paired_comparison({"q1": 0.5, "q2": 1}, {"q1": 1, "q2": 0}, 500, 42)
    assert comparison["query_count"] == 2
    assert comparison["delta"] == 0.25
    assert (comparison["wins"], comparison["ties"], comparison["losses"]) == (1, 0, 1)
    assert comparison == benchmark.paired_comparison({"q1": 0.5, "q2": 1}, {"q1": 1, "q2": 0}, 500, 42)
    with pytest.raises(ValueError, match="paired_query_ids"):
        benchmark.paired_comparison({"q1": 1}, {"q2": 1}, 10, 42)


def test_component_scope_restores_production_planner_and_keeps_expression():
    import research_agent.hub as production

    original = production.plan_query
    plan = original("Aurora liquid cooling")
    with benchmark.planner_scope("no_title_guards"):
        experimental = production.plan_query("Aurora liquid cooling")
        assert experimental.expression == plan.expression
        assert experimental.entities == ()
    assert production.plan_query is original
    with benchmark.planner_scope("no_concept_expansion"):
        assert production.plan_query("资本开支").expression != original("资本开支").expression
    assert production.plan_query is original


def test_failures_are_not_successful_empty_answers(tmp_path, inputs, monkeypatch):
    suite, matrix = inputs
    hub = Hub(tmp_path / "hub", retrieval_backend="sqlite")
    reverse = benchmark.ingest_suite(hub, suite, matrix["disabled_source_ids"])
    def unavailable(*args, **kwargs):
        raise RuntimeError("private details must not be copied")
    monkeypatch.setattr(hub, "search", unavailable)
    result = benchmark.run_sample(hub, suite["queries"][-1], matrix["variants"][0], reverse,
                                  {doc["id"]: doc for doc in suite["documents"]},
                                  datetime.fromisoformat(matrix["fixed_now"]), 1)
    assert result["failure"] == "RuntimeError"
    assert result["no_answer_correct"] is False


def test_matrix_rejects_changed_filters_and_multivariable_confound(inputs):
    suite, matrix = inputs
    benchmark.validate_inputs(suite, matrix)
    modified = deepcopy(matrix)
    modified["variants"][1]["policy"]["recency_boost"] = 1
    with pytest.raises(ValueError, match="one_configuration_component"):
        benchmark.validate_inputs(suite, modified)
    suite["queries"][0]["qrels"]["missing-id"] = 2
    with pytest.raises(ValueError, match="qrel_document_missing"):
        benchmark.validate_inputs(suite, matrix)


def test_small_real_run_counts_warmup_separately_and_policy_is_saved(inputs):
    suite, matrix = inputs
    suite["queries"] = [suite["queries"][0], suite["queries"][-1]]
    matrix["variants"] = matrix["variants"][:3]
    matrix.update(repeats=2, warmup_rounds=1, bootstrap_resamples=50)
    report = benchmark.execute(suite, matrix)
    assert not report["warmup_failures"]
    for result in report["variants"].values():
        assert result["aggregate"]["measured_calls"] == 4
        assert result["aggregate"]["failures"] == 0
        assert result["aggregate"]["no_answer_correct_rate"] == 1
        assert all(row["aggregate"]["policy_versions"] == [2] for row in result["queries"])
        assert result["comparisons"]["baseline"]["ndcg@5"]["query_count"] == 1
    assert len(report["execution_order"]) == 3
