"""Ablation, fusion and failure behavior; all passages below are synthetic."""
import unittest

from research_agent.pg_lab import lexical_query_tokens
from research_agent.retrieval_lab import Candidate, fuse, metrics, run_pipeline
from research_agent.retrieval_providers import RuleQueryRewriter


def candidate(chunk, doc, score=1):
    return Candidate(chunk, doc, 'Synthetic ' + doc, 'Synthetic passage', score)


class Store:
    def __init__(self):
        self.calls = []
        self.fail_dense = False

    def lexical(self, q, filters, limit):
        self.calls.append(('lexical', q, filters))
        return [candidate(1, 'a', 100), candidate(2, 'b', 50)]

    def dense(self, q, filters, limit):
        self.calls.append(('dense', q, filters))
        if self.fail_dense:
            raise RuntimeError('provider unavailable')
        return [candidate(3, 'c', .9), candidate(2, 'b', .8)]


class RetrievalLabTests(unittest.TestCase):
    def test_rrf_promotes_agreement_and_retains_union(self):
        rows = fuse([Store().lexical('', {}, 10), Store().dense('', {}, 10)], method='rrf')
        self.assertEqual([r.document_id for r in rows], ['b', 'a', 'c'])

    def test_rrf_is_invariant_to_score_scale_and_duplicate_rows(self):
        a = [candidate(1, 'a', .1), candidate(2, 'b', .01)]
        b = [candidate(2, 'b', 100), candidate(3, 'c', 90)]
        rows = fuse([a, b], method='rrf')
        duplicated = fuse([a + a, b], method='rrf')
        self.assertEqual(rows, duplicated)
        self.assertEqual(rows[0].document_id, 'b')

    def test_baselines_are_single_channel(self):
        for stage in ['lexical', 'dense']:
            store = Store()
            run_pipeline(store, 'question', stage=stage)
            self.assertEqual([r[0] for r in store.calls], [stage])

    def test_filters_reach_both_branches(self):
        store = Store()
        filters = {'source_ids': ['authorized'], 'metadata': {'company': 'Synthetic'}}
        run_pipeline(store, 'question', stage='rrf', filters=filters)
        self.assertTrue(all(call[2] == filters for call in store.calls))

    def test_dense_failure_degrades_with_warning(self):
        store = Store()
        store.fail_dense = True
        result = run_pipeline(store, 'question', stage='rrf')
        self.assertEqual(result['warnings'], ['dense_failed_using_lexical'])
        self.assertEqual(result['results'][0].document_id, 'a')
        with self.assertRaises(RuntimeError):
            run_pipeline(store, 'question', stage='dense')

    def test_reranker_failure_preserves_rrf_order(self):
        result = run_pipeline(Store(), 'question', stage='rerank',
                              rerank=lambda q, rows: [float('nan')] * len(rows))
        self.assertEqual(result['results'][0].document_id, 'b')
        self.assertIn('rerank_failed_using_rrf', result['warnings'])

    def test_reranker_can_change_order(self):
        result = run_pipeline(Store(), 'question', stage='rerank',
                              rerank=lambda q, rows: [0, 1, 2])
        self.assertEqual(result['results'][0].document_id, 'c')

    def test_rewrite_preserves_original_and_bounds_queries(self):
        result = run_pipeline(Store(), 'original', stage='rewrite',
                              rewrite=lambda q: ['original', 'rewrite1', 'rewrite2', 'rewrite3'],
                              rerank=lambda q, rows: [1] * len(rows))
        self.assertEqual(result['queries'], ['original', 'rewrite1', 'rewrite2'])

    def test_rewrite_failure_runs_original(self):
        result = run_pipeline(Store(), 'original', stage='rewrite',
                              rewrite=lambda q: None,
                              rerank=lambda q, rows: [1] * len(rows))
        self.assertEqual(result['queries'], ['original'])
        self.assertIn('rewrite_failed_using_original_query', result['warnings'])

    def test_optional_stages_do_not_silently_fake_providers(self):
        with self.assertRaises(ValueError):
            run_pipeline(Store(), 'question', stage='rerank')
        with self.assertRaises(ValueError):
            run_pipeline(Store(), 'question', stage='rewrite')

    def test_metrics_known_rankings(self):
        self.assertEqual(metrics(['a', 'b'], {'a': 2, 'b': 1}, 2)['ndcg_at_k'], 1)
        actual = metrics(['x', 'b'], {'a': 1, 'b': 1}, 2)
        self.assertEqual(actual['recall_at_k'], .5)
        self.assertEqual(actual['mrr_at_k'], .5)
        with self.assertRaises(ValueError):
            metrics(['a'], {'a': 0})

    def test_rule_rewrite_is_label_independent_and_keeps_names_dates(self):
        variants = RuleQueryRewriter()('Micron 2025 高带宽内存需求和毛利率')
        self.assertEqual(len(variants), 1)
        self.assertIn('Micron', variants[0])
        self.assertIn('2025', variants[0])
        self.assertIn('high bandwidth memory', variants[0])
        self.assertIn('gross margin', variants[0])

    def test_natural_language_lexical_query_keeps_entities_and_terms(self):
        self.assertEqual(lexical_query_tokens('What is the HBM demand for Micron in 2025?'),
                         ['hbm', 'demand', 'micron', '2025'])
        tokens = lexical_query_tokens('Micron 2025 高带宽内存需求')
        self.assertIn('micron', tokens)
        self.assertIn('2025', tokens)
        self.assertIn('内存', tokens)
        self.assertNotIn('内', tokens)
        self.assertEqual(lexical_query_tokens('芯'), ['芯'])


if __name__ == '__main__':
    unittest.main()
