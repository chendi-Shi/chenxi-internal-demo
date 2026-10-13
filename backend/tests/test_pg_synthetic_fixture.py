"""The published PostgreSQL diagnostic fixture must remain self-contained and invented."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / 'samples' / 'postgres_synthetic'


class SyntheticFixtureTests(unittest.TestCase):
    def test_fixed_qrels_only_reference_evidence_in_enabled_sources(self):
        suite = json.loads((DIRECTORY / 'suite.json').read_text(encoding='utf-8'))
        documents = {d['id']: d for d in suite['documents']}
        self.assertEqual(suite['dataset_kind'], 'synthetic')
        self.assertEqual(len({q['id'] for q in suite['queries']}), len(suite['queries']))
        for query in suite['queries']:
            for doc_id, relevance in query['qrels'].items():
                self.assertIn(doc_id, documents)
                self.assertNotEqual(documents[doc_id]['source_id'], 'synthetic_disabled')
                self.assertIn(relevance, (1, 2))
        for document in documents.values():
            self.assertTrue(document['body'].startswith(('This is synthetic test data.', '这是虚构测试资料。')))

    def test_snapshot_contains_citations_and_locked_queries_without_fake_vectors(self):
        spec = importlib.util.spec_from_file_location('synthetic_snapshot', DIRECTORY / 'build_snapshot.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            counts = builder.build(output)
            with closing(sqlite3.connect(output / 'hub.sqlite3')) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM hub_documents').fetchone()[0], counts['document_count'])
                self.assertEqual(conn.execute('SELECT count(*) FROM hub_vectors').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT count(*) FROM hub_chunks WHERE page=1 AND start=0 AND end=length(text)').fetchone()[0], counts['chunk_count'])
            queries = json.loads((output / 'queries.json').read_text(encoding='utf-8'))
            self.assertEqual(len(queries), counts['query_count'])
            self.assertGreater(counts['filter_noanswer_queries'], 0)
            with self.assertRaises(FileExistsError):
                builder.build(output)


if __name__ == '__main__':
    unittest.main()
