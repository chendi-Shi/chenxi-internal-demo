"""Evaluation/cache behavior only; fixture providers do not claim GPU/model quality."""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).parents[1] / "benchmarks/run_public_gpu_ablation.py"
SPEC = importlib.util.spec_from_file_location("run_public_gpu_ablation", BENCHMARK)
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class UnlimitedBudget:
    def check(self):
        return None


class StopAfter:
    def __init__(self, checks):
        self.checks = checks

    def check(self):
        self.checks -= 1
        if self.checks < 0:
            raise benchmark.StopRequested("test_interrupt")


def corpus():
    return [{"id": "a", "title": "Aurora", "text": "liquid cooling accelerators"},
            {"id": "b", "title": "月湾", "text": "云计算服务器资本支出"},
            {"id": "c", "title": "Bank", "text": "capital budget cash flow"},
            {"id": "d", "title": "Memory", "text": "stacked memory packaging"}]


def silent_progress(*args, **kwargs):
    return None


@pytest.fixture
def checkpoint(tmp_path):
    checkpoint = benchmark.Checkpoint(tmp_path / "output")
    yield checkpoint
    checkpoint.close()


def test_checkpoint_only_commits_complete_non_degraded_records(checkpoint):
    record = {"experiment": "rrf", "query_id": "q", "failed": False, "degraded": False}
    checkpoint.save_record("job", record)
    assert checkpoint.completed("job") == {("rrf", "q")}
    assert checkpoint.records("job") == [record]
    with pytest.raises(ValueError, match="cannot_be_completed"):
        checkpoint.save_record("other", record | {"failed": True})
    assert checkpoint.completed("other") == set()
    error = benchmark.DegradedResult(["dense_failed_using_lexical"])
    checkpoint.failure("job", "rrf", "q", error)
    assert checkpoint.reliability("job")["failed_attempts_before_completion"] == 1
    assert checkpoint.reliability("job")["degradation_warnings"] == ["dense_failed_using_lexical"]


def test_stop_signal_escapes_pipeline_fallback_and_does_not_commit():
    class Store:
        def lexical(self, *args):
            return []
        def dense(self, *args):
            raise benchmark.StopRequested("budget")
    with pytest.raises(benchmark.StopRequested):
        benchmark.measure_query(Store(), {"id": "q", "query": "test", "language": "en", "qrels": {"a": 1}},
                                benchmark.Experiment("rrf"), None)


def test_public_qrels_preserve_grade_three_and_reject_empty():
    cases = [{"id": "q", "query": "公开数据", "language": "zh", "qrels": {"a": 3}}]
    benchmark.validate_public_cases(cases)
    assert cases[0]["qrels"]["a"] == 3
    with pytest.raises(ValueError, match="positive_qrels"):
        benchmark.validate_public_cases([cases[0] | {"qrels": {}}])
    for bad_id in ([], None, " "):
        with pytest.raises(ValueError, match="query_id"):
            benchmark.validate_public_cases([cases[0] | {"id": bad_id}])
    with pytest.raises(ValueError, match="query_cluster"):
        benchmark.validate_public_cases([cases[0] | {"cluster_id": ""}])


def test_actual_fts_index_bilingual_and_resumable(tmp_path, checkpoint):
    docs = corpus()
    hashed = benchmark.corpus_fingerprint(docs)
    connection = benchmark.make_lexical_index(docs, hashed, tmp_path, UnlimitedBudget(), silent_progress)
    store = benchmark.CachedStore(docs, hashed, connection, None, "unused", None, 256,
                                  checkpoint, UnlimitedBudget())
    assert store.lexical("Aurora liquid cooling", {}, 10)[0].document_id == "a"
    assert store.lexical("资本支出", {}, 10)[0].document_id == "b"
    store.lexical("资本支出", {}, 10)
    assert store.hits["lexical"] == 1
    connection.close()
    resumed = benchmark.make_lexical_index(docs, hashed, tmp_path, UnlimitedBudget(), silent_progress)
    assert resumed.execute("SELECT count(*) FROM terms").fetchone()[0] == 4
    resumed.close()


class FixtureEncoder:
    """Deterministic fixture vectors for checkpoint mechanics; never used in reports."""
    fingerprint = {"provider": "fixture_test_only"}

    def __init__(self):
        self.calls = []
        self.proofs = 0

    def encode(self, texts, **kwargs):
        np = pytest.importorskip("numpy")
        self.calls.extend(texts)
        return np.array([[1, 0] for _ in texts], dtype=np.float32)

    def prove_gpu_execution(self):
        self.proofs += 1
        return {"fixture_test_only": True}


def test_vector_checkpoint_resumes_after_flush_and_validates_content(tmp_path, checkpoint):
    pytest.importorskip("numpy")
    docs = corpus()
    encoder = FixtureEncoder()
    hashed = benchmark.corpus_fingerprint(docs)
    with pytest.raises(benchmark.StopRequested):
        benchmark.encode_corpus(docs, hashed, encoder, 256, 2, tmp_path, checkpoint,
                                StopAfter(1), silent_progress)
    state_path = next((tmp_path / "vectors").glob("*/state.json"))
    assert json.loads(state_path.read_text())["rows_done"] == 2
    resumed = FixtureEncoder()
    vectors, key, proof = benchmark.encode_corpus(docs, hashed, resumed, 256, 2, tmp_path,
                                                checkpoint, UnlimitedBudget(), silent_progress)
    assert vectors.shape == (4, 2)
    assert len(resumed.calls) == 2
    assert resumed.proofs == 1
    assert proof["fixture_test_only"]
    del vectors
    # A completed cache must actually be byte-identical before it can be trusted.
    vector_path = tmp_path / "vectors" / key / "vectors.npy"
    with vector_path.open("r+b") as handle:
        handle.seek(-1, 2)
        byte = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([byte[0] ^ 1]))
    with pytest.raises(ValueError, match="content_hash"):
        benchmark.encode_corpus(docs, hashed, resumed, 256, 2, tmp_path,
                                checkpoint, UnlimitedBudget(), silent_progress)


def test_exact_dense_top_k_tie_order_and_nonfinite_rejection():
    np = pytest.importorskip("numpy")
    assert benchmark.exact_top_k(np.array([0.5, 1, 1, 0.5]), 2).tolist() == [1, 2]
    assert benchmark.exact_top_k(np.array([0.5, 1, 1, 0.5]), 3).tolist() == [1, 2, 0]
    with pytest.raises(ValueError, match="invalid_dense"):
        benchmark.exact_top_k(np.array([float("nan")]), 1)


class FixtureReranker:
    fingerprint = {"provider": "fixture_reranker_test_only"}

    def __init__(self):
        self.calls = 0
        self.proofs = 0

    def score(self, query, texts, **kwargs):
        self.calls += len(texts)
        return [len(text) / 100 for text in texts]

    def prove_gpu_execution(self):
        self.proofs += 1
        return {"fixture_test_only": True, "session_proofs": self.proofs}


def test_pair_cache_reuses_real_scores_and_proves_new_session(checkpoint):
    rows = [benchmark.Candidate(i, str(i), "title", "body" + str(i), 1) for i in range(3)]
    first_provider = FixtureReranker()
    saved = []
    rerank = benchmark.CachedReranker(first_provider, checkpoint, UnlimitedBudget(), 512, 2, saved.append)
    first = rerank("query", rows[:2])
    assert first_provider.calls == 2
    assert saved and first_provider.proofs == 1
    second_provider = FixtureReranker()
    second = benchmark.CachedReranker(second_provider, checkpoint, UnlimitedBudget(), 512, 2)
    assert second("query", rows[:2]) == first
    assert second_provider.calls == 0
    second("query", rows)
    assert second_provider.calls == 1
    assert second_provider.proofs == 1  # Prior-session evidence must not replace a new-session proof.
    different_length = benchmark.CachedReranker(second_provider, checkpoint, UnlimitedBudget(), 256, 2)
    different_length("query", rows[:2])
    assert second_provider.calls == 3


def test_incomplete_job_cannot_be_published(tmp_path, checkpoint):
    with pytest.raises(ValueError, match="incomplete_job"):
        benchmark.write_job_report(tmp_path, "job", {}, [], [benchmark.Experiment("rrf")],
                                   [{"id": "q"}], {}, checkpoint, {})


def test_budget_stops_by_deadline_and_explicit_stop_file(tmp_path):
    with pytest.raises(benchmark.StopRequested, match="wall_clock"):
        benchmark.Budget(tmp_path, 1, time.time() - 3601).check()
    (tmp_path / "STOP").write_text("stop", encoding="utf-8")
    with pytest.raises(benchmark.StopRequested, match="stop_file"):
        benchmark.Budget(tmp_path, 1, time.time()).check()


def test_per_query_diagnostics_report_candidate_oracle():
    row = benchmark.Candidate(1, "relevant", "title", "text", 1)
    class Store:
        def lexical(self, *args):
            return [row]
    case = {"id": "q", "query": "query", "language": "en", "qrels": {"relevant": 3, "missing": 1}}
    record = benchmark.measure_query(Store(), case, benchmark.Experiment("lexical", dense=False), None)
    assert record["candidate_recall"] == 0.5
    assert record["trace"]["branches"][0]["qrel_recall"] == 0.5
    assert 0 < record["candidate_oracle_ndcg@10"] < 1
    assert record["rerank_pool_oracle_ndcg@10"] is None


def test_completed_export_requires_gpu_evidence_and_preserves_prior_attempts(tmp_path, checkpoint):
    row = benchmark.Candidate(1, "relevant", "title", "text", 1)
    class Store:
        def lexical(self, *args):
            return [row]
    case = {"id": "q", "query": "query", "language": "en", "qrels": {"relevant": 1}}
    experiment = benchmark.Experiment("lexical", dense=False)
    record = benchmark.measure_query(Store(), case, experiment, None)
    matrix = {"contrasts": [], "bootstrap_samples": 20, "seed": 42}
    description = {"embedding_model": "fixture"}
    with pytest.raises(ValueError, match="all_gpu_proofs"):
        benchmark.write_job_report(tmp_path, "job", description, [record], [experiment],
                                   [case], matrix, checkpoint, {})
    checkpoint.failure("job", "lexical", "q", ValueError("fixture"))
    summary = benchmark.write_job_report(tmp_path, "job", description, [record], [experiment],
                                         [case], matrix, checkpoint, {"fixture": {"test_only": True}})
    assert summary["status"] == "completed"
    assert summary["reliability"]["failed_attempts_before_completion"] == 1
    assert summary["rewrite_diagnostics"]["lexical"]["additional_variant_fraction"] == 0
    assert "SQLite FTS5" in summary["retrieval_backends"]["lexical"]
    assert (tmp_path / "jobs/job/query_results.jsonl").exists()


def test_token_counts_combine_without_inventing_original_length():
    batch = {"items": 4, "truncated_items": 1, "retained_tokens": 300, "padded_tokens": 724,
             "max_length": 256, "padding_mode": "fixed_max_length", "truncation_strategy": "longest_first"}
    first = benchmark.accumulate_token_telemetry(None, batch)
    combined = benchmark.accumulate_token_telemetry(first, batch)
    assert combined["items"] == 8
    assert combined["truncated_fraction"] == 0.25
    assert combined["retained_tokens"] == 600
    assert "original_tokens" not in combined


@pytest.mark.parametrize("invalid_rerank", [False, True])
def test_joint_memory_smoke_keeps_preallocated_encoder_alive(tmp_path, invalid_rerank):
    from types import SimpleNamespace

    np = pytest.importorskip("numpy")
    events = []
    class Encoder:
        fingerprint = {"fixture": "encoder"}
        def __init__(self, *args):
            events.append("encoder_loaded")
        def encode(self, texts, **kwargs):
            assert "encoder_deleted" not in events
            assert kwargs["max_length"] == 512 and kwargs["batch_size"] == 8
            events.append("encode")
            return np.ones((len(texts), 2), dtype=np.float32)
        def prove_gpu_execution(self):
            return {"fixture": "encoder_proof"}
        def __del__(self):
            events.append("encoder_deleted")
    class Reranker:
        fingerprint = {"fixture": "reranker"}
        def __init__(self, *args):
            assert events == ["encoder_loaded", "encode"]
            events.append("reranker_loaded")
        def score(self, query, texts, **kwargs):
            assert events == ["encoder_loaded", "encode", "reranker_loaded", "encode"]
            assert kwargs["max_length"] == 512 and kwargs["batch_size"] == 8
            events.append("rerank")
            return [float("nan") if invalid_rerank else 1.0] * len(texts)
        def prove_gpu_execution(self):
            return {"fixture": "reranker_proof"}
        def __del__(self):
            events.append("reranker_deleted")
    args = SimpleNamespace(models_dir=tmp_path)
    matrix = {"reranker_model": "reranker_multilingual"}
    specs = {"e5_small": {}, "reranker_multilingual": {}}
    if invalid_rerank:
        with pytest.raises(ValueError, match="joint_pilot_invalid_rerank"):
            benchmark.joint_memory_smoke(Encoder, Reranker, args, matrix, specs, ["doc"] * 8, "query")
    else:
        result = benchmark.joint_memory_smoke(Encoder, Reranker, args, matrix, specs, ["doc"] * 8, "query")
        assert result["items"] == 8
        assert result["encoder_preallocated_before_reranker_load"]
        assert set(result["gpu_proof"]) == {"e5_small", "reranker_multilingual"}
    assert "encoder_deleted" in events and "reranker_deleted" in events


def test_formal_run_bootstrap_and_completed_resume_with_fixture_providers(tmp_path, monkeypatch):
    """Exercise the real run entry point and persistent metadata, without requiring a GPU."""
    import sqlite3
    from types import SimpleNamespace

    pytest.importorskip("numpy")
    docs = corpus()
    cases = [{"id": "q", "query": "Aurora cooling", "language": "en", "qrels": {"a": 1}}]
    dataset_dir = tmp_path / "data/fixture"
    dataset_dir.mkdir(parents=True)
    manifest = {"dataset_kind": "fixture_test_only", "corpus_count": len(docs)}
    (dataset_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    created = []
    class Encoder(FixtureEncoder):
        def __init__(self, *args):
            super().__init__()
            created.append(self)
    monkeypatch.setitem(sys.modules, "gpu_onnx", SimpleNamespace(
        OnnxTextEncoder=Encoder, OnnxCrossEncoder=FixtureReranker))
    monkeypatch.setitem(sys.modules, "public_benchmark_data", SimpleNamespace(
        load_prepared_dataset=lambda path: (docs, cases, manifest)))
    args = SimpleNamespace(output_dir=tmp_path / "output", data_dir=tmp_path / "data",
                           models_dir=tmp_path / "models", budget_hours=1)
    matrix = {"datasets": ["fixture"], "embedding_models": ["fixture_encoder"],
              "max_lengths": [16], "embedding_batch_size": 2, "rerank_batch_size": 2,
              "reranker_model": "fixture_reranker", "alternative_reranker_model": "fixture_reranker",
              "experiments": [{"id": "lexical", "dense": False}, {"id": "dense", "lexical": False}],
              "contrasts": [{"experiment": "dense", "reference": "lexical"}],
              "candidate_cache_limit": 500, "bootstrap_samples": 20, "seed": 42, "budget_hours": 1}
    specs = {"fixture_encoder": {}}
    assert benchmark.run(args, matrix, specs) == 0
    state = json.loads((args.output_dir / "run_state.json").read_text(encoding="utf-8"))
    assert state["runtime"]["sqlite"] == sqlite3.sqlite_version
    assert state["status"] == "completed" and len(state["completed_jobs"]) == 1
    summaries = list((args.output_dir / "jobs").glob("*/summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["aggregates"]["lexical"]["metrics"]["10"]["recall_at_k"] == 1
    # A fully completed resume must avoid loading another provider or duplicating quality records.
    assert benchmark.run(args, matrix, specs) == 0
    assert len(created) == 1
    with sqlite3.connect(args.output_dir / "checkpoint.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 2


def test_atomic_json_retries_transient_windows_sharing_failure(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    path.write_text('{"old":true}', encoding="utf-8")
    original_replace = Path.replace
    attempts, sleeps = [], []
    def replace(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            raise PermissionError("fixture Windows sharing violation")
        return original_replace(source, destination)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(benchmark.time, "sleep", sleeps.append)
    benchmark.atomic_json(path, {"status": "completed"})
    assert json.loads(path.read_text()) == {"status": "completed"}
    assert len(attempts) == 3
    assert sleeps == [0.01, 0.02]
    assert not path.with_suffix(".json.tmp").exists()


def test_atomic_json_persistent_denial_is_bounded_and_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    path.write_text('{"status":"running"}', encoding="utf-8")
    clock = [0.0]
    sleeps = []
    def deny(*args):
        raise PermissionError("fixture persistent denial")
    def advance(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(Path, "replace", deny)
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(benchmark.time, "sleep", advance)
    with pytest.raises(PermissionError, match="persistent denial"):
        benchmark.atomic_json(path, {"status": "completed"})
    assert clock[0] == pytest.approx(5.0)
    assert max(sleeps) <= 0.2
    assert json.loads(path.read_text()) == {"status": "running"}
    assert json.loads(path.with_suffix(".json.tmp").read_text()) == {"status": "completed"}
