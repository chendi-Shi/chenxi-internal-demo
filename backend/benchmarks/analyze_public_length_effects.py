"""CPU-only, read-only diagnostics of already completed public embedding caches.

No model sessions or new quality observations are created. The live SQLite WAL
is read through short, autocommit SELECTs; only the requested report is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "original_model_card": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "original_sentence_config": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/blob/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/sentence_bert_config.json",
    "original_pooling_config": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/raw/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/1_Pooling/config.json",
    "original_model_config": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/raw/main/config.json",
    "converted_model_config": "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/2c4055b12046f11709e9df2c122e59ffbdc2f900/config.json",
    "converted_tokenizer_config": "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/2c4055b12046f11709e9df2c122e59ffbdc2f900/tokenizer_config.json",
    "normalized_dot_equals_cosine": "https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html",
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare_vectors(left, right):
    import numpy as np
    left, right = np.asarray(left, dtype=np.float64), np.asarray(right, dtype=np.float64)
    require(left.ndim == 2 and left.shape == right.shape and len(left) > 0, "invalid_vector_pair_shape")
    require(np.isfinite(left).all() and np.isfinite(right).all(), "nonfinite_cached_vectors")
    a, b = np.linalg.norm(left, axis=1), np.linalg.norm(right, axis=1)
    require(np.allclose(a, 1, atol=1e-3) and np.allclose(b, 1, atol=1e-3), "cached_vectors_not_normalized")
    cosine = np.clip(np.sum(left * right, axis=1) / (a * b), -1, 1)
    return {"pairs": len(left), "cosine_min": float(cosine.min()),
            "cosine_mean": float(cosine.mean()), "cosine_p05": float(np.quantile(cosine, .05)),
            "max_abs_coordinate_difference": float(np.max(np.abs(left - right))),
            "cosine_below_0_9999": int(np.sum(cosine < .9999))}


def trace_queries(path, cases, expected_experiments):
    """Use the actual saved rewrite variants, not newly generated expansions."""
    queries, seen = set(), set()
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = (row["experiment"], row["query_id"])
            require(key not in seen and row["repeat"] == 0, "unexpected_or_duplicate_observation")
            require(not row["failed"] and not row["degraded"], "incomplete_query_observation")
            require(row["queries"] and row["queries"][0] == cases[row["query_id"]]["query"], "query_text_mismatch")
            require(all(isinstance(text, str) for text in row["queries"]), "invalid_saved_query")
            seen.add(key)
            queries.update(row["queries"])
    require(seen == {(config, query) for config in expected_experiments for query in cases}, "incomplete_query_matrix")
    return queries


def analyze(run_dir, data_dir, models_dir, *, dataset="scifact"):
    import numpy as np
    import tokenizers
    from tokenizers import Tokenizer

    run_dir, data_dir, models_dir = map(Path, (run_dir, data_dir, models_dir))
    run = read_json(run_dir / "run_state.json")
    require(all(file_hash(ROOT / "backend" / name) == value for name, value in run["source_sha256"].items()),
            "frozen_source_hash_mismatch")
    manifest = read_json(data_dir / dataset / "manifest.json")
    require(manifest.get("public_data_only") is True, "public_dataset_required")
    for name in ("queries.json", "corpus.jsonl"):
        require(file_hash(data_dir / dataset / name) == manifest["prepared_files"][name]["sha256"], "prepared_data_hash_mismatch")
    cases_list = read_json(data_dir / dataset / "queries.json")
    cases = {case["id"]: case for case in cases_list}
    require(len(cases) == len(cases_list), "duplicate_query_ids")
    with (data_dir / dataset / "corpus.jsonl").open(encoding="utf-8") as handle:
        corpus = [json.loads(line) for line in handle]
    corpus_hasher = hashlib.sha256()
    for doc in corpus:
        corpus_hasher.update(json.dumps([doc["id"], doc["title"], doc["text"]], ensure_ascii=False,
                                        separators=(",", ":")).encode() + b"\n")
    corpus_hash, query_hash = corpus_hasher.hexdigest(), digest(cases_list)
    require(len({doc["id"] for doc in corpus}) == len(corpus), "duplicate_corpus_ids")
    selected = {}
    for job_id in run["completed_jobs"]:
        require(len(job_id) == 64 and all(c in "0123456789abcdef" for c in job_id), "invalid_job_id")
        path = run_dir / "jobs" / job_id / "summary.json"
        summary = read_json(path)
        description = summary["job"]
        if description["dataset"] != dataset:
            continue
        key = (description["embedding_model"], description["max_length"])
        require(key not in selected, "duplicate_completed_job")
        selected[key] = (path, summary)
    results = []
    connection = sqlite3.connect((run_dir / "checkpoint.sqlite3").resolve().as_uri() + "?mode=ro",
                                 uri=True, isolation_level=None, timeout=5)
    try:
        for model in ("e5_small", "minilm_multilingual"):
            spec = run["models"][model]
            for item in spec["files"]:
                require(file_hash(models_dir / model / item["local_path"]) == item["sha256"], "model_file_hash_mismatch")
            arrays, states, sources, query_sets = {}, {}, {}, {}
            for length in (256, 512):
                path, summary = selected[(model, length)]
                description = summary["job"]
                require(summary["status"] == "completed" and summary["job_fingerprint"] == path.parent.name,
                        "completed_job_required")
                require(description["run_fingerprint"] == run["fingerprint"] and
                        description["corpus_sha256"] == corpus_hash and description["query_sha256"] == query_hash,
                        "job_data_or_run_fingerprint_mismatch")
                cache_key = summary["gpu_proofs"][model]["vector_cache_fingerprint"]
                require(len(cache_key) == 64 and all(c in "0123456789abcdef" for c in cache_key), "invalid_cache_key")
                cache_dir = run_dir / "cache/vectors" / cache_key
                state = read_json(cache_dir / "state.json")
                identity = state["identity"]
                require(digest(identity) == cache_key and identity["corpus_sha256"] == corpus_hash and
                        identity["max_length"] == length and state["completed"] is True and
                        state["rows_done"] == len(corpus), "incomplete_or_mismatched_vector_cache")
                provider = identity["provider"]
                spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                expected_files = {item["local_path"]: {"sha256": item["sha256"], "size": item["size"]} for item in spec["files"]}
                require(provider["model_id"] == model and provider["repo"] == spec["repo"] and
                        provider["revision"] == spec["revision"] and provider["spec_sha256"] == spec_hash and
                        provider["files"] == expected_files, "cached_provider_model_mismatch")
                require(file_hash(cache_dir / "vectors.npy") == state["vectors_sha256"], "vector_content_hash_mismatch")
                arrays[length] = np.load(cache_dir / "vectors.npy", mmap_mode="r", allow_pickle=False)
                require(arrays[length].shape == (len(corpus), spec["dimension"]) and
                        arrays[length].dtype == np.float32, "vector_shape_or_dtype_mismatch")
                query_sets[length] = trace_queries(path.parent / "query_results.jsonl", cases, set(summary["aggregates"]))
                states[length] = state
                sources[str(length)] = {"job_fingerprint": summary["job_fingerprint"], "summary_sha256": file_hash(path),
                                        "query_results_sha256": file_hash(path.parent / "query_results.jsonl"),
                                        "vector_cache_fingerprint": cache_key, "vectors_sha256": state["vectors_sha256"]}
            require(query_sets[256] == query_sets[512], "saved_query_variants_differ")
            require(states[256]["identity"]["provider"] == states[512]["identity"]["provider"], "length_provider_mismatch")
            queries = sorted(query_sets[256])
            query_vectors = {256: [], 512: []}
            for query in queries:
                for length in (256, 512):
                    key = "query_vector:" + digest([states[length]["identity"]["provider"], length, query, 1, "query"])
                    cursor = connection.execute("SELECT value FROM cached_json WHERE key=?", (key,))
                    row = cursor.fetchone()
                    cursor.close()
                    require(row is not None, "matching_cached_query_vector_missing")
                    vector = json.loads(row[0])
                    require(len(vector) == spec["dimension"], "query_vector_dimension_mismatch")
                    query_vectors[length].append(vector)
            tokenizer_path = models_dir / model / "tokenizer.json"
            saved_tokenizer = read_json(tokenizer_path)
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.no_padding()
            tokenizer.no_truncation()
            query_lengths = [len(item.ids) for item in tokenizer.encode_batch([spec["query_prefix"] + q for q in queries])]
            require(max(query_lengths) <= 256, "padding_comparison_requires_untruncated_queries")
            lengths = []
            for offset in range(0, len(corpus), 128):
                texts = [spec["passage_prefix"] + (doc["title"] + "\n" + doc["text"]).strip()
                         for doc in corpus[offset:offset + 128]]
                lengths.extend(len(item.ids) for item in tokenizer.encode_batch(texts))
            lengths = np.asarray(lengths)
            groups = {}
            for label, mask in (("untruncated_at_256", lengths <= 256),
                                ("257_to_512_tokens", (lengths > 256) & (lengths <= 512)),
                                ("over_512_tokens", lengths > 512)):
                groups[label] = compare_vectors(arrays[256][mask], arrays[512][mask]) if mask.any() else {"pairs": 0}
            results.append({"model_id": model, "repo": spec["repo"], "revision": spec["revision"],
                            "model_files": {item["local_path"]: item["sha256"] for item in spec["files"]},
                            "provider_fingerprint": digest(states[256]["identity"]["provider"]),
                            "independent_original_queries": len(cases), "cached_query_texts_including_rule_variants": len(queries),
                            "max_query_tokens_including_prefix_and_special_tokens": max(query_lengths),
                            "saved_tokenizer_default_truncation": saved_tokenizer.get("truncation"),
                            "cached_query_padding_invariance": compare_vectors(query_vectors[256], query_vectors[512]),
                            "query_vector_value_fingerprints": {str(k): digest(v) for k, v in query_vectors.items()},
                            "document_vector_comparison": groups, "source_artifacts": sources})
    finally:
        connection.close()
    return {"schema_version": 1, "status": "completed_cache_diagnostic", "generated_at_utc": datetime.now(UTC).isoformat(),
            "dataset": dataset, "run_fingerprint": run["fingerprint"], "corpus_sha256": corpus_hash,
            "query_sha256": query_hash, "corpus_count": len(corpus), "new_quality_observations": 0,
            "diagnostic_script_sha256": file_hash(__file__), "runtime": {"numpy": np.__version__, "tokenizers": tokenizers.__version__},
            "method": "Read only completed cached normalized vectors and actual saved query variants; compare cosine and coordinate differences in float64. Count input tokens using pinned tokenizer JSON, without truncation/padding. No inference or live SQLite writes.",
            "length_definition": "Encoder input tokens include special tokens and model-specific prefixes; reranker length is unchanged at 512.",
            "models": results, "sources": SOURCES,
            "interpretation_boundaries": [
                "MiniLM original SentenceTransformer pipeline default max_seq_length is 128; the pinned tokenizer JSON also stores truncation length 128. This experiment deliberately overrides it with 256/512.",
                "Original and Xenova model/tokenizer configurations permit 512 positions/tokens. Structural support is not a quality guarantee or proof of training sequence length.",
                "The inspected official sources do not establish this checkpoint's training length. Default 128 must not be described as a proven training length or proven cause of degradation.",
                "Near-identical untruncated queries and short documents argue against a simple padding/mask implementation error on these cached outputs; this is not full PyTorch/ONNX/DirectML numerical parity validation.",
                "Larger representation changes when additional document content is included are observed associations. Semantic dilution or a mismatch with the published default remain hypotheses, not established causes.",
                "Query variants and reused corpus embeddings are diagnostics, not additional independent quality samples. No quality metric or confidence interval is recomputed here.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "backend/data/public-rag")
    parser.add_argument("--models-dir", type=Path, default=ROOT / "backend/data/public-rag/models")
    parser.add_argument("--dataset", choices=("scifact", "fiqa", "t2retrieval"), default="scifact")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evaluation/PUBLIC_GPU_LENGTH_DIAGNOSTIC.json")
    args = parser.parse_args()
    report = analyze(args.run_dir, args.data_dir, args.models_dir, dataset=args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "dataset": report["dataset"], "new_quality_observations": 0}))


if __name__ == "__main__":
    main()
