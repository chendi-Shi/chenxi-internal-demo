"""Resume integrity and interruption behavior with synthetic results, no models or DB."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research_agent.pg_lab import evaluate_cases, validate_completed_report
from research_agent.retrieval_lab import Candidate


class BenchmarkResumeTests(unittest.TestCase):
    def setUp(self):
        self.store = SimpleNamespace(snapshot={'id': 1, 'document_count': 1})
        self.cases = [
            {'id': 'synthetic-positive', 'query': 'invented question', 'language': 'en',
             'qrels': {'fiction-document': 2}},
            {'id': 'synthetic-empty', 'query': 'invented empty', 'language': 'zh', 'qrels': {}},
        ]
        self.fingerprint = {'sha256': 'synthetic-fingerprint'}

    def result(self, store, query, *, stage, **kwargs):
        rows = [] if query == 'invented empty' else [
            Candidate(1, 'fiction-document', 'Fiction', 'Invented passage', 1, 1)]
        return {'results': rows, 'stage': stage, 'queries': [query], 'warnings': [],
                'elapsed_ms': 11, 'matched_chunks': len(rows), 'total_candidates': len(rows)}

    def run_evaluation(self, output, **kwargs):
        return evaluate_cases(self.store, self.cases, stage='lexical', output=output,
                              fingerprint=self.fingerprint, repeats=2, warmup=1, **kwargs)

    def test_interrupted_measurement_resumes_without_repeating_completed_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'lexical.local.json'
            calls = []

            def interrupted(*args, **kwargs):
                calls.append(args[1])
                if len(calls) == 4:  # Two warmups, one saved measurement, then interruption.
                    raise KeyboardInterrupt
                return self.result(*args, **kwargs)

            with patch('research_agent.pg_lab.run_pipeline', side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_evaluation(output)
            checkpoint = output.with_name(output.stem + '.checkpoint.local.json')
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            self.assertEqual(len(saved['cases']), 1)
            self.assertFalse(output.exists())
            with patch('research_agent.pg_lab.run_pipeline', side_effect=self.result) as runner:
                report = self.run_evaluation(output, resume=True)
            self.assertEqual(runner.call_count, 5)  # Fresh warmup plus only three missing cases.
            self.assertEqual([(row['repeat'], row['query_id']) for row in report['cases']],
                             [(0, 'synthetic-positive'), (0, 'synthetic-empty'),
                              (1, 'synthetic-positive'), (1, 'synthetic-empty')])
            self.assertEqual(report['resume'], {'process_sessions': 2, 'actual_warmup_runs': 4,
                                                'additional_warmup_runs': 2})
            self.assertEqual(report['aggregate']['warmup_runs'], 4)
            self.assertEqual(report['aggregate']['cold_first_query_ms'], 11)
            self.assertEqual(report['aggregate']['measured_runs'], 4)
            self.assertEqual(report['aggregate']['ndcg_at_k'], 1)
            self.assertFalse(validate_completed_report(
                report, self.cases, self.store.snapshot, self.fingerprint, 'lexical', 2, 1))

    def test_checkpoint_rejects_changed_fingerprint_before_model_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'lexical.local.json'
            with patch('research_agent.pg_lab.run_pipeline', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_evaluation(output)
            self.fingerprint = {'sha256': 'changed-query-or-source-or-model'}
            with patch('research_agent.pg_lab.run_pipeline') as runner:
                with self.assertRaisesRegex(ValueError, 'fingerprint_mismatch'):
                    self.run_evaluation(output, resume=True)
                runner.assert_not_called()

    def test_completed_validation_recalculates_labels_and_requires_legacy_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('research_agent.pg_lab.run_pipeline', side_effect=self.result):
                report = self.run_evaluation(Path(directory) / 'lexical.local.json')
            legacy = copy.deepcopy(report)
            del legacy['evaluation_fingerprint']
            with self.assertRaisesRegex(ValueError, 'fingerprint_mismatch'):
                validate_completed_report(legacy, self.cases, self.store.snapshot,
                                          self.fingerprint, 'lexical', 2, 1)
            self.assertTrue(validate_completed_report(
                legacy, self.cases, self.store.snapshot, self.fingerprint, 'lexical', 2, 1,
                accept_legacy=True))
            changed = copy.deepcopy(self.cases)
            changed[0]['qrels'] = {'different-fiction-document': 2}
            with self.assertRaisesRegex(ValueError, 'metrics_mismatch'):
                validate_completed_report(report, changed, self.store.snapshot,
                                          self.fingerprint, 'lexical', 2, 1)


if __name__ == '__main__':
    unittest.main()
