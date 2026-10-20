"""Pinned public retrieval datasets, full corpora and a locked evaluation query set.

Only explicit Parquet/TSV files are loaded; no dataset repository code is executed.
Parquet preparation needs pyarrow. Reading prepared JSON files is stdlib-only.
Raw and prepared data belong under ignored backend/data/public-rag, never in Git.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "backend/samples/rag_ablation/public_datasets.json"
DEFAULT_OUTPUT = ROOT / "backend/data/public-rag"


def file_sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def source_url(source):
    if not re.fullmatch(r"[0-9a-f]{40}", source["revision"]):
        raise ValueError("source_requires_immutable_commit_revision")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repo"]):
        raise ValueError("invalid_dataset_repository")
    path = source["path"]
    if not path or path.startswith("/") or ".." in Path(path).parts or "\\" in path:
        raise ValueError("unsafe_source_path")
    return (f"https://huggingface.co/datasets/{source['repo']}/resolve/"
            f"{source['revision']}/{urllib.parse.quote(path, safe='/')}?download=true")


def verify_source_file(path, source):
    path = Path(path)
    if path.stat().st_size != source["bytes"]:
        raise ValueError(f"source_size_mismatch:{path.name}")
    digest = file_sha256(path)
    if source.get("sha256"):
        if digest != source["sha256"]:
            raise ValueError(f"source_sha256_mismatch:{path.name}")
    elif source.get("git_blob_sha1"):
        # Git's blob OID hashes the length-prefixed content, not the raw file.
        value = hashlib.sha1(f"blob {path.stat().st_size}\0".encode("ascii"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                value.update(chunk)
        if value.hexdigest() != source["git_blob_sha1"]:
            raise ValueError(f"source_git_blob_mismatch:{path.name}")
    else:
        raise ValueError("source_requires_expected_content_hash")
    return digest


def download_source(source, destination, *, workers=8, progress=None):
    """Reuse verified files; large Parquet artifacts use parallel resumable ranges."""
    destination = Path(destination)
    url = source_url(source)
    if destination.exists():
        verify_source_file(destination, source)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.get("sha256"):
        specification = importlib.util.spec_from_file_location(
            "public_download", Path(__file__).with_name("public_download.py"))
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        module.download_verified(url, destination, source["sha256"], source["bytes"],
                                 workers=workers, progress=progress)
    else:
        # TSV qrels are <= 210 kB; retry these atomically, without remote code.
        partial = destination.with_name(destination.name + ".partial")
        for attempt in range(5):
            try:
                request = urllib.request.Request(url, headers={
                    "User-Agent": "chenxi-public-rag-benchmark/1.0", "Accept-Encoding": "identity"})
                with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                    while chunk := response.read(65536):
                        output.write(chunk)
                        if output.tell() > source["bytes"]:
                            raise ValueError("download_exceeds_pinned_size")
                verify_source_file(partial, source)
                partial.replace(destination)
                break
            except (OSError, TimeoutError):
                if attempt == 4:
                    raise
                time.sleep(min(8, 2 ** attempt))
    verify_source_file(destination, source)
    return destination


def read_rows(path, file_format):
    if file_format == "parquet":
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise RuntimeError("Preparing Parquet datasets requires pyarrow; reading prepared data does not.") from error
        for batch in parquet.ParquetFile(path).iter_batches(batch_size=4096):
            yield from batch.to_pylist()
    elif file_format == "tsv":
        with Path(path).open(encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle, delimiter="\t")
    else:
        raise ValueError("unsupported_data_format")


def stable_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("id_must_be_nonempty_string_or_integer")
    value = str(value)
    if not value or value != value.strip():
        raise ValueError("id_must_be_nonempty_without_outer_whitespace")
    return value


def relevance_grade(value):
    if isinstance(value, bool):
        raise ValueError("relevance_must_be_nonnegative_integer")
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValueError("relevance_must_be_nonnegative_integer")
    return value


def normalize_dataset(corpus_rows, query_rows, qrel_rows_by_split, specification, *,
                      dataset_name, max_queries, seed):
    """Validate IDs, keep every corpus row, then select queries without scoring."""
    if max_queries < 0:
        raise ValueError("max_queries_must_be_nonnegative_zero_means_all")
    corpus_map, query_map = {}, {}
    columns = specification["corpus_columns"]
    for row in corpus_rows:
        key = stable_id(row[columns["id"]])
        if key in corpus_map:
            raise ValueError(f"duplicate_corpus_id:{key}")
        title = row.get(columns.get("title"), "")
        text = row[columns["text"]]
        if not isinstance(title, str) or not isinstance(text, str):
            raise ValueError(f"invalid_document_text_type:{key}")
        corpus_map[key] = {"id": key, "title": title, "text": text}
    empty_ids = sorted(key for key, row in corpus_map.items()
                       if not (row["title"] + row["text"]).strip())
    empty_id_set = set(empty_ids)
    columns = specification["query_columns"]
    for row in query_rows:
        key = stable_id(row[columns["id"]])
        if key in query_map:
            raise ValueError(f"duplicate_query_id:{key}")
        text = row[columns["query"]]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"empty_or_invalid_query:{key}")
        query_map[key] = text
    for field, actual in (("expected_corpus_count", len(corpus_map)),
                          ("expected_all_query_count", len(query_map))):
        if field in specification and actual != specification[field]:
            raise ValueError(f"{field}_mismatch:{actual}")
    columns = specification["qrel_columns"]
    qrels, split_details = {}, {}
    for split, rows in sorted(qrel_rows_by_split.items()):
        mapping, row_count, duplicates = {}, 0, 0
        for row in rows:
            query_id = stable_id(row[columns["query_id"]])
            document_id = stable_id(row[columns["document_id"]])
            score = relevance_grade(row[columns["score"]])
            if query_id not in query_map:
                raise ValueError(f"qrel_unknown_query:{split}:{query_id}")
            if document_id not in corpus_map:
                raise ValueError(f"qrel_unknown_document:{split}:{document_id}")
            current = mapping.setdefault(query_id, {})
            if document_id in current:
                if current[document_id] != score:
                    raise ValueError(f"conflicting_qrel:{split}:{query_id}:{document_id}")
                duplicates += 1
            current[document_id] = score
            row_count += 1
        qrels[split] = mapping
        split_details[split] = {"query_count": len(mapping), "qrel_rows": row_count,
                                "duplicate_identical_qrel_rows": duplicates,
                                "qrels_sha256": hashlib.sha256(canonical_bytes(mapping)).hexdigest()}
        empty_positive_pairs = [(qid, pid) for qid, labels in mapping.items()
                                for pid, score in labels.items() if score > 0 and pid in empty_id_set]
        split_details[split]["positive_qrel_pairs_to_empty_documents"] = len(empty_positive_pairs)
        split_details[split]["queries_with_positive_empty_documents"] = len(
            {qid for qid, _ in empty_positive_pairs})
    for left, right in combinations(sorted(qrels), 2):
        overlap = sorted(set(qrels[left]) & set(qrels[right]))
        if overlap:
            raise ValueError(f"query_split_overlap:{left}:{right}:{len(overlap)}")
    split = specification["evaluation_split"]
    if split not in qrels or not qrels[split]:
        raise ValueError("missing_evaluation_qrels")
    candidates = sorted(qrels[split])
    if ("expected_evaluation_query_count" in specification and
            len(candidates) != specification["expected_evaluation_query_count"]):
        raise ValueError("expected_evaluation_query_count_mismatch")
    without_positive = [key for key in candidates if not any(v > 0 for v in qrels[split][key].values())]
    if without_positive:
        raise ValueError(f"evaluation_queries_without_positive_qrels:{len(without_positive)}")
    # Hash order is portable, independent of row order or RNG implementation.
    ordered = sorted(candidates, key=lambda key: (
        hashlib.sha256(f"{seed}\0{dataset_name}\0{split}\0{key}".encode()).hexdigest(), key))
    chosen = sorted(ordered[:max_queries] if max_queries else ordered)
    queries = [{"id": key, "query": query_map[key], "language": specification["language"],
                "qrels": dict(sorted(qrels[split][key].items()))} for key in chosen]
    corpus = [corpus_map[key] for key in sorted(corpus_map)]
    details = {
        "corpus_count": len(corpus), "all_query_text_count": len(query_map),
        "evaluation_split": split, "available_evaluation_queries": len(candidates),
        "selected_query_count": len(queries), "source_splits": split_details,
        "split_query_ids_disjoint": True,
        "corpus_diagnostics": {
            "empty_document_definition": "title_and_text_combined_have_no_nonwhitespace_characters",
            "empty_document_count": len(empty_ids), "empty_document_ids": empty_ids,
            "empty_documents_preserved": True,
            "selected_positive_qrel_pairs_to_empty_documents": sum(
                score > 0 and pid in empty_id_set for query in queries
                for pid, score in query["qrels"].items()),
            "selected_queries_with_positive_empty_documents": sum(
                any(score > 0 and pid in empty_id_set for pid, score in query["qrels"].items())
                for query in queries),
        },
        "selection": {"method": "smallest_sha256(seed NUL dataset NUL split NUL query_id)",
                      "seed": seed, "max_queries": max_queries,
                      "selected_query_ids": chosen,
                      "selected_query_ids_sha256": hashlib.sha256(canonical_bytes(chosen)).hexdigest(),
                      "selected_before_retrieval": True,
                      "corpus_selection": "all_source_documents_no_qrel_based_filter"},
    }
    return corpus, queries, details


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(value) + b"\n")
    temporary.replace(path)


@contextmanager
def preparation_lock(directory):
    path = directory / "prepare.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("dataset_preparation_locked; verify the owning process before removing prepare.lock") from error
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def load_prepared_dataset(dataset_dir):
    """Load full corpus, locked query list, manifest; verify both canonical files."""
    directory = Path(dataset_dir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name in ("corpus.jsonl", "queries.json"):
        path = directory / name
        expected = manifest["prepared_files"][name]
        if path.stat().st_size != expected["bytes"] or file_sha256(path) != expected["sha256"]:
            raise ValueError(f"prepared_file_integrity_mismatch:{name}")
    with (directory / "corpus.jsonl").open(encoding="utf-8") as handle:
        corpus = [json.loads(line) for line in handle if line.strip()]
    queries = json.loads((directory / "queries.json").read_text(encoding="utf-8"))
    if len(corpus) != manifest["corpus_count"] or len(queries) != manifest["selected_query_count"]:
        raise ValueError("prepared_dataset_count_mismatch")
    return corpus, queries, manifest


def prepare_dataset(dataset_name, output_root=DEFAULT_OUTPUT, *, max_queries=None,
                    seed=20261015, config_path=DEFAULT_CONFIG, workers=8, progress=None,
                    download_only=False):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    specification = config["datasets"][dataset_name]
    max_queries = specification["default_max_queries"] if max_queries is None else max_queries
    if max_queries < 0:
        raise ValueError("max_queries_must_be_nonnegative_zero_means_all")
    identity = {"dataset_name": dataset_name, "specification": specification,
                "max_queries": max_queries, "seed": seed, "preparation_schema_version": 2}
    fingerprint = hashlib.sha256(canonical_bytes(identity)).hexdigest()
    directory = Path(output_root) / dataset_name
    directory.mkdir(parents=True, exist_ok=True)
    paths = {"corpus_path": directory / "corpus.jsonl", "queries_path": directory / "queries.json",
             "manifest_path": directory / "manifest.json"}
    with preparation_lock(directory):
        if paths["manifest_path"].exists():
            existing = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
            if existing.get("preparation_fingerprint") != fingerprint:
                raise ValueError("prepared_dataset_configuration_mismatch_use_new_output_root")
            load_prepared_dataset(directory)
            return paths
        source_records, source_files = [], {}
        for source in specification["files"]:
            local_name = source["local_name"]
            if Path(local_name).name != local_name or "\\" in local_name or "/" in local_name:
                raise ValueError("unsafe_local_filename")
            path = download_source(source, directory / "raw" / local_name,
                                   workers=workers, progress=progress)
            source_records.append({**source, "url": source_url(source),
                                   "downloaded_sha256": verify_source_file(path, source)})
            role = source["role"]
            key = (role, source.get("split"))
            if key in source_files:
                raise ValueError("duplicate_source_role_split")
            source_files[key] = (path, source["format"])
        if download_only:
            return paths
        corpus, queries, details = normalize_dataset(
            read_rows(*source_files[("corpus", None)]),
            read_rows(*source_files[("queries", None)]),
            {split: read_rows(*file) for (role, split), file in source_files.items() if role == "qrels"},
            specification, dataset_name=dataset_name, max_queries=max_queries, seed=seed)
        temporary = paths["corpus_path"].with_suffix(".jsonl.tmp")
        with temporary.open("wb") as handle:
            for document in corpus:
                handle.write(canonical_bytes(document) + b"\n")
        temporary.replace(paths["corpus_path"])
        atomic_json(paths["queries_path"], queries)
        manifest = {
            "schema_version": 2, "dataset_name": dataset_name,
            "preparation_script_sha256": file_sha256(Path(__file__)),
            "preparation_fingerprint": fingerprint,
            "language": specification["language"],
            "evaluation_note": specification["evaluation_note"],
            "homepage": specification["homepage"], "citation": specification["citation"],
            "license_metadata": specification["license_metadata"],
            "public_data_only": True, "remote_dataset_code_executed": False,
            "sources": source_records, **details,
            "prepared_files": {path.name: {"sha256": file_sha256(path), "bytes": path.stat().st_size}
                               for path in (paths["corpus_path"], paths["queries_path"])},
        }
        atomic_json(paths["manifest_path"], manifest)  # Completion marker is written last.
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=("fiqa", "t2retrieval", "scifact", "all"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-queries", type=int, default=None, help="0 selects all official evaluation questions")
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--download-workers", type=int, default=8)
    parser.add_argument("--download-only", action="store_true", help="Verify raw downloads without importing pyarrow")
    args = parser.parse_args()
    datasets = ("scifact", "fiqa", "t2retrieval") if args.dataset == "all" else (args.dataset,)
    for name in datasets:
        paths = prepare_dataset(name, args.output_root, max_queries=args.max_queries,
                                seed=args.seed, config_path=args.config, workers=args.download_workers,
                                download_only=args.download_only,
                                progress=lambda status: print(json.dumps(status), flush=True))
        if args.download_only:
            print(json.dumps({"dataset": name, "raw_downloads": "verified"}), flush=True)
            continue
        manifest = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
        print(json.dumps({"dataset": name, "corpus_count": manifest["corpus_count"],
                          "selected_query_count": manifest["selected_query_count"],
                          "evaluation_split": manifest["evaluation_split"],
                          "manifest": str(paths["manifest_path"])}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
