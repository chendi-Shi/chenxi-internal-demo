"""Behavioral tests use deterministic fake providers, never benchmark model scores."""
import json
import tempfile
import unittest
from pathlib import Path

from research_agent.rag_ablation import (
    Experiment,
    evaluate_matrix,
    load_matrix,
    make_fingerprint,
    public_summary,
    run_ablation,
    validate_cases,
)
from research_agent.retrieval_lab import Candidate, run_pipeline


def candidate(chunk, doc, score):
    return Candidate(chunk, doc, 'Synthetic ' + doc, 'Invented passage', score)


class Store:
    def __init__(self):
        self.calls = []
        self.fail_dense = False

    def lexical(self, query, filters, limit):
        self.calls.append(('lexical', query, filters, limit))
        return [candidate(1, 'a', 100), candidate(2, 'b', 50)]

    def dense(self, query, filters, limit):
        self.calls.append(('dense', query, filters, limit))
        if self.fail_dense:
            raise RuntimeError('private message must not be published')
        return [candidate(3, 'c', .9), candidate(2, 'b', .8)]


CASES = [{'id': 'private-q1', 'query': 'private text', 'language': 'en', 'qrels': {'a': 2}},
         {'id': 'private-q2', 'query': 'another private text', 'language': 'zh', 'qrels': {'c': 2}}]


class RagAblationTests(unittest.TestCase):
    def test_defaults_match_each_existing_stage(self):
        configurations = [
            ('lexical', Experiment('lexical', dense=False)),
            ('dense', Experiment('dense', lexical=False)),
            ('hybrid', Experiment('normalized', fusion='normalized')),
            ('rrf', Experiment('rrf')),
            ('rerank', Experiment('rerank', rerank=True)),
            ('rewrite', Experiment('rewrite', rewrite=True, rerank=True)),
        ]
        for stage, config in configurations:
            with self.subTest(stage=stage):
                old_store, new_store = Store(), Store()
                providers = {'rewrite': lambda q: [q + ' expansion'],
                             'rerank': lambda q, rows: list(range(len(rows)))}
                old = run_pipeline(old_store, 'question', stage=stage, **providers)
                new = run_ablation(new_store, 'question', config, **providers)
                self.assertEqual(old['results'], new['results'])
                self.assertEqual(old['queries'], new['queries'])
                self.assertEqual(old['warnings'], new['warnings'])
                self.assertEqual(old_store.calls, new_store.calls)

    def test_rewrite_can_run_without_reranker_and_preserves_filters(self):
        store = Store()
        result = run_ablation(store, 'original', Experiment('rewrite', rewrite=True),
                              filters={'source_ids': ['synthetic']}, rewrite=lambda q: [q + ' x'])
        self.assertEqual(len(store.calls), 4)
        self.assertEqual(result['trace']['rerank_document_ids'], [])
        self.assertTrue(all(call[2] == {'source_ids': ['synthetic']} for call in store.calls))

    def test_candidate_and_rerank_budgets_are_separate(self):
        class ManyStore(Store):
            def lexical(self, query, filters, limit):
                return [candidate(i, str(i // 2), 100 - i) for i in range(limit)]
        pools = []
        def rerank(query, rows):
            pools.append(rows)
            return list(range(len(rows)))
        result = run_ablation(ManyStore(), 'query',
                              Experiment('budget', dense=False, candidate_limit=50,
                                         rerank=True, rerank_limit=10), rerank=rerank)
        self.assertEqual(len(result['trace']['candidate_document_ids']), 25)
        self.assertEqual(len(pools[0]), 10)
        self.assertEqual(len({row.document_id for row in pools[0]}), 10)

    def test_dense_failure_is_visible_and_dense_only_fails(self):
        store = Store()
        store.fail_dense = True
        result = run_ablation(store, 'q', Experiment('rrf'))
        self.assertEqual(result['warnings'], ['dense_failed_using_lexical'])
        self.assertTrue(result['trace']['branches'][1]['failed'])
        with self.assertRaises(RuntimeError):
            run_ablation(store, 'q', Experiment('dense', lexical=False))

    def test_invalid_rerank_falls_back_to_actual_fusion(self):
        baseline = run_ablation(Store(), 'q', Experiment('rrf'))
        degraded = run_ablation(Store(), 'q', Experiment('rerank', rerank=True),
                                rerank=lambda q, rows: [float('nan')] * len(rows))
        self.assertEqual(baseline['results'], degraded['results'])
        self.assertIn('rerank_failed_using_rrf', degraded['warnings'])

    def test_missing_provider_is_not_silently_benchmarked(self):
        for config in (Experiment('r', rerank=True), Experiment('w', rewrite=True)):
            with self.assertRaises(ValueError):
                evaluate_matrix(Store(), CASES, [config])

    def test_controls_are_separate_and_zero_labels_are_valid(self):
        cases = CASES + [{'id': 'control', 'query': 'unanswerable', 'qrels': {},
                          'control_type': 'semantic_unanswerable'}]
        report = evaluate_matrix(Store(), cases, [Experiment('l', dense=False)], repeats=1, warmup=0)
        summary = public_summary(report, bootstrap_samples=20)
        group = summary['aggregates']['l']
        self.assertEqual(group['independent_answerable_questions'], 2)
        self.assertEqual(group['control_nonempty_runs'], 1)
        self.assertEqual(group['controls_by_type']['semantic_unanswerable']['runs'], 1)
        self.assertEqual(set(group['metrics']), {'1', '3', '5', '10'})

    def test_bootstrap_pairs_questions_not_repeats(self):
        experiments = [Experiment('l', dense=False), Experiment('d', lexical=False)]
        once = evaluate_matrix(Store(), CASES, experiments, repeats=1, warmup=0)
        repeated = evaluate_matrix(Store(), CASES, experiments, repeats=3, warmup=1)
        contrast = [{'experiment': 'd', 'reference': 'l'}]
        first = public_summary(once, contrasts=contrast, bootstrap_samples=50)
        second = public_summary(repeated, contrasts=contrast, bootstrap_samples=50)
        self.assertEqual(first['comparisons'], second['comparisons'])
        self.assertEqual(second['comparisons'][0]['paired_questions'], 2)

    def test_public_summary_does_not_publish_private_trace(self):
        report = evaluate_matrix(Store(), CASES, [Experiment('r')], repeats=1, warmup=0)
        output = json.dumps(public_summary(report, bootstrap_samples=20))
        self.assertNotIn('private text', output)
        self.assertNotIn('private-q1', output)
        self.assertNotIn('document_ids', output)
        self.assertNotIn('Invented passage', output)

    def test_warmup_provider_degradation_is_counted(self):
        store = Store()
        store.fail_dense = True
        report = evaluate_matrix(store, CASES, [Experiment('r')], repeats=1, warmup=1)
        summary = public_summary(report, bootstrap_samples=20)['aggregates']['r']
        self.assertEqual(summary['warmup_degraded'], 2)
        self.assertEqual(summary['degraded_count'], 2)
        self.assertEqual(summary['failure_count'], 0)

    def test_failures_remain_in_quality_denominator(self):
        store = Store()
        store.fail_dense = True
        report = evaluate_matrix(store, CASES, [Experiment('d', lexical=False)], repeats=1, warmup=0)
        summary = public_summary(report, bootstrap_samples=20)['aggregates']['d']
        self.assertEqual(summary['failure_count'], 2)
        self.assertEqual(summary['metrics']['10']['ndcg_at_k'], 0)
        self.assertEqual(summary['candidate_recall'], 0)

    def test_failed_empty_controls_are_not_successful_abstentions(self):
        store = Store()
        store.fail_dense = True
        cases = [{'id': 'none', 'query': 'q', 'qrels': {}, 'control_type': 'impossible_filter'}]
        report = evaluate_matrix(store, cases, [Experiment('d', lexical=False)], repeats=1, warmup=0)
        group = public_summary(report, bootstrap_samples=20)['aggregates']['d']
        self.assertEqual(group['control_failed_runs'], 1)
        self.assertEqual(group['control_successful_empty_runs'], 0)
        self.assertEqual(group['controls_by_type']['impossible_filter']['successful_empty_runs'], 0)

    def test_interaction_and_cluster_bootstrap_use_all_four_cells(self):
        configs = [Experiment('rrf'), Experiment('rrf_rerank', rerank=True),
                   Experiment('rrf_rewrite', rewrite=True),
                   Experiment('rrf_rewrite_rerank', rewrite=True, rerank=True)]
        cases = [dict(row, cluster_id='same_topic') for row in CASES]
        report = evaluate_matrix(Store(), cases, configs, repeats=2, warmup=0,
                                 rewrite=lambda q: [], rerank=lambda q, rows: list(range(len(rows))))
        summary = public_summary(report, bootstrap_samples=20)
        interaction = summary['interactions'][0]
        self.assertAlmostEqual(interaction['mean_delta'], 0)
        self.assertEqual(interaction['paired_questions'], 2)
        self.assertEqual(interaction['independent_clusters'], 1)
        self.assertEqual(interaction['bootstrap_95_percentile_interval'], [0, 0])

    def test_fingerprint_binds_protocol_and_driver(self):
        args = {'cases': CASES, 'experiments': [Experiment('r')], 'snapshot': {},
                'data_fingerprint': {'sha256': 'synthetic-data'}}
        with tempfile.TemporaryDirectory() as directory:
            driver = Path(directory) / 'driver.py'
            driver.write_text('first')
            first = make_fingerprint(**args, protocol={'repeats': 1}, driver_path=driver)
            second = make_fingerprint(**args, protocol={'repeats': 2}, driver_path=driver)
            self.assertNotEqual(first['sha256'], second['sha256'])
            driver.write_text('changed')
            third = make_fingerprint(**args, protocol={'repeats': 1}, driver_path=driver)
            self.assertNotEqual(first['sha256'], third['sha256'])

    def test_same_seed_reproduces_schedule(self):
        configs = [Experiment('l', dense=False), Experiment('d', lexical=False)]
        runs = [evaluate_matrix(Store(), CASES, configs, repeats=2, warmup=0) for _ in range(2)]
        def keys(run):
            return [(r['repeat'], r['experiment'], r['query_id']) for r in run['records']]
        self.assertEqual(keys(runs[0]), keys(runs[1]))

    def test_input_validation_rejects_unknown_configuration_and_bad_labels(self):
        for config in ({'id': 'x', 'lexical': False, 'dense': False},
                       {'id': 'x', 'candidate_limit': 0}, {'id': 'x', 'rerank': 'yes'}):
            with self.assertRaises(ValueError):
                Experiment(**config)
        with self.assertRaises(ValueError):
            validate_cases([{'id': 'a', 'query': 'x', 'qrels': {'doc': -1}}])
        for key in ('cluster_id', 'language'):
            with self.assertRaises(ValueError):
                validate_cases([dict(CASES[0], **{key: []})])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'matrix.json'
            path.write_text(json.dumps({'experiments': [{'id': 'a', 'typo': True}]}))
            with self.assertRaises(ValueError):
                load_matrix(path)


if __name__ == '__main__':
    unittest.main()
