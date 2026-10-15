"""Public benchmark provenance and leakage guards, using invented tiny inputs only."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "public_data", ROOT / "backend/benchmarks/public_benchmark_data.py")
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)


class PublicBenchmarkDataTests(unittest.TestCase):
    def setUp(self):
        self.specification = {
            "language": "en", "evaluation_split": "test",
            "corpus_columns": {"id": "_id", "title": "title", "text": "text"},
            "query_columns": {"id": "_id", "query": "text"},
            "qrel_columns": {"query_id": "qid", "document_id": "pid", "score": "score"},
        }
        self.corpus = [{"_id": "d1", "title": "", "text": "Invented first document"},
                       {"_id": "d2", "title": "Title", "text": "Invented second document"},
                       {"_id": "unjudged", "title": "", "text": "Keep unjudged documents too"}]
        self.queries = [{"_id": "q1", "text": "Fictional first question"},
                        {"_id": "q2", "text": "Fictional second question"},
                        {"_id": "train-q", "text": "Training only"}]
        self.qrels = {"test": [{"qid": "q1", "pid": "d1", "score": 2},
                                {"qid": "q2", "pid": "d2", "score": 1}],
                      "train": [{"qid": "train-q", "pid": "d1", "score": 1}]}

    def normalize(self, max_queries=0, seed=20261015):
        return data.normalize_dataset(self.corpus, self.queries, self.qrels, self.specification,
                                      dataset_name="invented", max_queries=max_queries, seed=seed)

    def test_full_corpus_and_official_test_split_are_preserved(self):
        corpus, queries, details = self.normalize()
        self.assertEqual({row["id"] for row in corpus}, {"d1", "d2", "unjudged"})
        self.assertEqual({row["id"] for row in queries}, {"q1", "q2"})
        self.assertEqual(queries[0]["qrels"], {"d1": 2})
        self.assertEqual(details["source_splits"]["train"]["query_count"], 1)
        self.assertEqual(details["selected_query_count"], 2)
        self.assertTrue(details["split_query_ids_disjoint"])

    def test_sampling_is_deterministic_before_scoring_and_independent_of_row_order(self):
        first = self.normalize(max_queries=1)
        self.corpus.reverse()
        self.queries.reverse()
        self.qrels["test"].reverse()
        second = self.normalize(max_queries=1)
        self.assertEqual(first, second)
        self.assertEqual(first[2]["available_evaluation_queries"], 2)
        self.assertEqual(len(first[0]), 3)
        self.assertTrue(first[2]["selection"]["selected_before_retrieval"])

    def test_dev_split_remains_dev_and_is_not_relabeled_as_test(self):
        self.qrels["dev"] = self.qrels.pop("test")
        self.specification["evaluation_split"] = "dev"
        _, _, details = self.normalize()
        self.assertEqual(details["evaluation_split"], "dev")
        self.assertNotIn("test", details["source_splits"])

    def test_query_split_overlap_is_rejected(self):
        self.qrels["train"].append({"qid": "q1", "pid": "d1", "score": 1})
        with self.assertRaisesRegex(ValueError, "query_split_overlap"):
            self.normalize()

    def test_missing_document_and_missing_query_are_rejected(self):
        self.qrels["test"][0]["pid"] = "missing"
        with self.assertRaisesRegex(ValueError, "qrel_unknown_document"):
            self.normalize()
        self.qrels["test"][0]["pid"] = "d1"
        self.qrels["test"][0]["qid"] = "missing"
        with self.assertRaisesRegex(ValueError, "qrel_unknown_query"):
            self.normalize()

    def test_duplicates_are_explicit_and_conflicting_judgments_fail(self):
        self.qrels["test"].append(copy.deepcopy(self.qrels["test"][0]))
        _, _, details = self.normalize()
        self.assertEqual(details["source_splits"]["test"]["duplicate_identical_qrel_rows"], 1)
        self.qrels["test"][-1]["score"] = 0
        with self.assertRaisesRegex(ValueError, "conflicting_qrel"):
            self.normalize()

    def test_empty_positive_set_is_not_silently_removed(self):
        self.qrels["test"][0]["score"] = 0
        with self.assertRaisesRegex(ValueError, "evaluation_queries_without_positive_qrels"):
            self.normalize()

    def test_official_empty_documents_and_qrels_are_preserved_and_counted(self):
        self.corpus[0]["text"] = ""
        self.corpus[-1]["text"] = " \t\n"
        corpus, queries, details = self.normalize()
        self.assertEqual(len(corpus), 3)
        self.assertEqual(corpus[0]["text"], "")
        self.assertEqual(queries[0]["qrels"], {"d1": 2})
        audit = details["corpus_diagnostics"]
        self.assertEqual(audit["empty_document_ids"], ["d1", "unjudged"])
        self.assertEqual(audit["selected_positive_qrel_pairs_to_empty_documents"], 1)
        self.assertEqual(audit["selected_queries_with_positive_empty_documents"], 1)
        self.assertEqual(details["source_splits"]["train"]["positive_qrel_pairs_to_empty_documents"], 1)
        self.assertEqual(details["source_splits"]["test"]["queries_with_positive_empty_documents"], 1)

    def test_nonstrings_remain_invalid_including_falsey_values(self):
        for field in ("text", "title"):
            original = self.corpus[0][field]
            for value in (None, False, 0, []):
                with self.subTest(field=field, value=value):
                    self.corpus[0][field] = value
                    with self.assertRaisesRegex(ValueError, "invalid_document_text_type"):
                        self.normalize()
            self.corpus[0][field] = original

    def test_source_requires_immutable_revision_and_content_hash(self):
        source = {"repo": "BeIR/fiqa", "revision": "a" * 40, "path": "test.tsv"}
        self.assertIn("/" + "a" * 40 + "/test.tsv", data.source_url(source))
        source["revision"] = "main"
        with self.assertRaisesRegex(ValueError, "immutable_commit"):
            data.source_url(source)
        source["revision"] = "a" * 40
        source["path"] = "../bad"
        with self.assertRaisesRegex(ValueError, "unsafe_source_path"):
            data.source_url(source)

    def test_git_blob_integrity_uses_the_git_header_and_checks_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qrels.tsv"
            contents = b"qid\tpid\tscore\nq1\td1\t1\n"
            path.write_bytes(contents)
            expected = {"bytes": len(contents), "git_blob_sha1": hashlib.sha1(
                f"blob {len(contents)}\0".encode("ascii") + contents).hexdigest()}
            self.assertEqual(data.verify_source_file(path, expected), hashlib.sha256(contents).hexdigest())
            expected["git_blob_sha1"] = "0" * 40
            with self.assertRaisesRegex(ValueError, "source_git_blob_mismatch"):
                data.verify_source_file(path, expected)

    def test_prepare_resume_and_output_integrity_without_network_or_pyarrow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "output/invented/raw"
            raw.mkdir(parents=True)
            contents = {
                "corpus.tsv": "_id\ttitle\ttext\nd1\t\tInvented document\nunjudged\t\tKeep this document\n",
                "queries.tsv": "_id\ttext\nq1\tInvented question\n",
                "qrels-test.tsv": "qid\tpid\tscore\nq1\td1\t2\n",
            }
            specification = {**self.specification, "default_max_queries": 1000,
                             "evaluation_note": "Invented test fixture", "homepage": "https://example.invalid",
                             "citation": "Invented fixture", "license_metadata": "Invented fixture",
                             "files": []}
            for name, text in contents.items():
                path = raw / name
                path.write_bytes(text.encode("utf-8"))
                role = "qrels" if name.startswith("qrels") else name.split(".")[0]
                source = {"role": role, "format": "tsv", "local_name": name,
                          "repo": "fiction/fixture", "revision": "a" * 40, "path": name,
                          "bytes": path.stat().st_size, "sha256": data.file_sha256(path)}
                if role == "qrels":
                    source["split"] = "test"
                specification["files"].append(source)
            config = root / "config.json"
            config.write_text(json.dumps({"datasets": {"invented": specification}}), encoding="utf-8")
            paths = data.prepare_dataset("invented", root / "output", config_path=config)
            corpus, queries, manifest = data.load_prepared_dataset(root / "output/invented")
            self.assertEqual(len(corpus), 2)
            self.assertEqual(queries[0]["qrels"], {"d1": 2})
            self.assertEqual(manifest["selected_query_count"], 1)
            self.assertEqual(paths, data.prepare_dataset("invented", root / "output", config_path=config))
            with self.assertRaisesRegex(ValueError, "configuration_mismatch"):
                data.prepare_dataset("invented", root / "output", config_path=config, seed=99)
            paths["queries_path"].write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "prepared_file_integrity_mismatch"):
                data.load_prepared_dataset(root / "output/invented")


if __name__ == "__main__":
    unittest.main()
