"""Offline, configurable ablations; production HTTP/MCP rankings are unchanged.

Raw reports contain private query/ID traces. Only ``public_summary`` is suitable
for publication. Repeated measurements are collapsed before paired resampling.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .retrieval_lab import Candidate, fuse, metrics

METRIC_KS = (1, 3, 5, 10)


@dataclass(frozen=True)
class Experiment:
    id: str
    lexical: bool = True
    dense: bool = True
    fusion: str = 'rrf'
    candidate_limit: int = 100
    rrf_k: int = 60
    rerank: bool = False
    rerank_limit: int = 50
    rewrite: bool = False
    max_variants: int = 3
    result_limit: int = 10

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or any(
                c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in self.id):
            raise ValueError('invalid_experiment_id')
        if any(type(getattr(self, name)) is not bool for name in ('lexical', 'dense', 'rerank', 'rewrite')):
            raise ValueError('invalid_experiment_boolean')
        if any(type(getattr(self, name)) is not int for name in
               ('candidate_limit', 'rrf_k', 'rerank_limit', 'max_variants', 'result_limit')):
            raise ValueError('invalid_experiment_integer')
        if not (self.lexical or self.dense) or self.fusion not in {'rrf', 'normalized'}:
            raise ValueError('invalid_retrieval_branches')
        if not 10 <= self.result_limit <= self.candidate_limit <= 1000:
            raise ValueError('invalid_candidate_budget')
        if not 1 <= self.rrf_k <= 1000 or not 1 <= self.max_variants <= 3:
            raise ValueError('invalid_fusion_or_rewrite_budget')
        if not self.result_limit <= self.rerank_limit <= 1000:
            raise ValueError('invalid_rerank_budget')


def load_matrix(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    allowed = {item.name for item in fields(Experiment)}
    experiments = []
    for row in value['experiments']:
        if set(row) - allowed:
            raise ValueError('unknown_experiment_field')
        experiments.append(Experiment(**row))
    ids = {item.id for item in experiments}
    if not experiments or len(ids) != len(experiments):
        raise ValueError('duplicate_or_empty_experiments')
    for item in value.get('contrasts', []):
        if item['experiment'] not in ids or item['reference'] not in ids:
            raise ValueError('unknown_contrast_experiment')
    return value, experiments


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError('empty_query_set')
    seen = set()
    for case in cases:
        if not isinstance(case.get('id'), str) or not case['id'] or case['id'] in seen:
            raise ValueError('invalid_query_id')
        seen.add(case['id'])
        if not isinstance(case.get('query'), str) or not case['query'].strip():
            raise ValueError('invalid_query_text')
        if not isinstance(case.get('qrels'), dict) or any(
                not isinstance(key, str) or type(value) is not int or value not in (0, 1, 2)
                for key, value in case['qrels'].items()):
            raise ValueError('invalid_graded_qrels')
        if not isinstance(case.get('filters', {}), dict):
            raise ValueError('invalid_query_filters')
        if not isinstance(case.get('language', 'unknown'), str):
            raise ValueError('invalid_query_language')
        if not isinstance(case.get('cluster_id', case['id']), str) or not case.get('cluster_id', case['id']):
            raise ValueError('invalid_query_cluster')
        if case.get('control_type', 'unspecified') not in (
                'unspecified', 'impossible_filter', 'semantic_unanswerable'):
            raise ValueError('invalid_control_type')


def _unique_documents(rows):
    seen, output = set(), []
    for row in rows:
        if row.document_id not in seen:
            seen.add(row.document_id)
            output.append(row)
    return output


def _ms(start):
    return round((time.perf_counter() - start) * 1000, 3)


def run_ablation(store, query, experiment, *, filters=None, rewrite=None, rerank=None):
    """Run real providers. Missing providers fail; runtime fallbacks are explicit."""
    if not query.strip():
        raise ValueError('empty_query')
    if experiment.rewrite and rewrite is None:
        raise ValueError('rewrite_provider_required')
    if experiment.rerank and rerank is None:
        raise ValueError('rerank_provider_required')
    started = time.perf_counter()
    queries, warnings, branches, lists = [query], [], [], []
    rewrite_ms = rerank_ms = 0.0
    if experiment.rewrite:
        step = time.perf_counter()
        try:
            variants = rewrite(query)
            if not isinstance(variants, list) or any(not isinstance(v, str) for v in variants):
                raise ValueError('invalid_rewrites')
            queries.extend(v.strip() for v in variants if v.strip() and len(v) <= 500)
            queries = list(dict.fromkeys(queries))[:experiment.max_variants]
        except Exception:
            warnings.append('rewrite_failed_using_original_query')
        rewrite_ms = _ms(step)
    for variant_index, variant in enumerate(queries):
        for branch in ('lexical', 'dense'):
            if not getattr(experiment, branch):
                continue
            step = time.perf_counter()
            failed = False
            try:
                rows = getattr(store, branch)(variant, filters or {}, experiment.candidate_limit)
            except Exception:
                if branch != 'dense' or not experiment.lexical:
                    raise
                rows, failed = [], True
                warnings.append('dense_failed_using_lexical')
            branches.append({'variant_index': variant_index, 'branch': branch,
                             'elapsed_ms': _ms(step), 'failed': failed,
                             'chunk_ids': [r.chunk_id for r in rows],
                             'document_ids': [r.document_id for r in _unique_documents(rows)],
                             'scores': [r.score for r in rows]})
            if not failed:
                lists.append(rows)
    step = time.perf_counter()
    rows = (lists[0] if len(lists) == 1 and not (experiment.lexical and experiment.dense)
            and not experiment.rewrite else fuse(lists, method=experiment.fusion, k=experiment.rrf_k))
    fusion_ms = _ms(step)
    candidate_documents = _unique_documents(rows)
    pool = candidate_documents[:experiment.rerank_limit] if experiment.rerank else []
    if experiment.rerank:
        step = time.perf_counter()
        try:
            values = list(rerank(query, pool))
            if len(values) != len(pool) or any(not math.isfinite(float(v)) for v in values):
                raise ValueError('invalid_rerank_scores')
            rows = sorted([Candidate(**(vars(row) | {'score': float(score)}))
                           for row, score in zip(pool, values, strict=True)],
                          key=lambda row: (-row.score, row.chunk_id))
        except Exception:
            warnings.append('rerank_failed_using_rrf' if experiment.fusion == 'rrf'
                            else 'rerank_failed_using_normalized')
        rerank_ms = _ms(step)
    final = _unique_documents(rows)[:experiment.result_limit]
    return {'results': final, 'queries': queries, 'warnings': list(dict.fromkeys(warnings)),
            'elapsed_ms': _ms(started),
            'trace': {'branches': branches, 'rewrite_ms': rewrite_ms, 'fusion_ms': fusion_ms,
                      'rerank_ms': rerank_ms,
                      'candidate_document_ids': [r.document_id for r in candidate_documents],
                      'candidate_chunk_count': len(rows) if not experiment.rerank else
                          len({i for branch in branches for i in branch['chunk_ids']}),
                      'rerank_document_ids': [r.document_id for r in pool],
                      'returned_document_ids': [r.document_id for r in final]}}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(',', ':')).encode()).hexdigest()


def database_fingerprint(conn):
    """Hash ordered server-side row digests; no source text leaves PostgreSQL."""
    result = {}
    for table, order in (('documents', 'id'), ('chunks', 'id'),
                         ('embeddings', 'chunk_id,model_digest')):
        digest = hashlib.sha256()
        count = 0
        with conn.cursor(name='ablation_fingerprint_' + table) as cursor:
            cursor.execute("SELECT encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex') "
                           'AS row_digest FROM retrieval_lab.' + table + ' t ORDER BY ' + order)
            for row in cursor:
                digest.update(row['row_digest'].encode('ascii') + b'\n')
                count += 1
        result[table] = {'rows': count, 'sha256_of_ordered_row_sha256': digest.hexdigest()}
    return {'method': 'ordered_server_row_sha256_v1', 'tables': result, 'sha256': _digest(result)}


def make_fingerprint(*, cases, experiments, snapshot, data_fingerprint, provider_manifest=None,
                     protocol=None, driver_path=None):
    directory = Path(__file__).parent
    names = ('rag_ablation.py', 'retrieval_lab.py', 'pg_lab.py', 'retrieval_providers.py',
             'hub_semantic.py', 'hub_e5.py', 'hub_query.py', 'store.py')
    parts = {'queries_sha256': _digest(cases),
             'experiments_sha256': _digest([asdict(item) for item in experiments]),
             'snapshot_sha256': _digest(snapshot), 'data_sha256': data_fingerprint['sha256'],
             'sources': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                         for name in names},
             'reranker_manifest_sha256': _digest(provider_manifest) if provider_manifest else None,
             'semantic_config_sha256': _digest(snapshot.get('semantic_config')),
             'protocol_sha256': _digest(protocol),
             'driver_sha256': hashlib.sha256(Path(driver_path).read_bytes()).hexdigest()
                 if driver_path else None}
    return {'sha256': _digest(parts), 'inputs': parts}


def _measure(store, case, experiment, repeat, rewrite, rerank, transaction):
    start = time.perf_counter()
    try:
        with transaction():
            result = run_ablation(store, case['query'], experiment, filters=case.get('filters'),
                                  rewrite=rewrite, rerank=rerank)
        failed = False
    except Exception:
        result = {'results': [], 'queries': [case['query']], 'warnings': ['query_failed'],
                  'elapsed_ms': _ms(start), 'trace': {}}
        failed = True
    rows = result.pop('results')
    ids = [row.document_id for row in rows]
    positives = {key for key, value in case['qrels'].items() if value > 0}
    result.update(experiment=experiment.id, query_id=case['id'], repeat=repeat,
                  language=case.get('language', 'unknown'),
                  cluster_id=case.get('cluster_id', case['id']),
                  control_type=case.get('control_type', 'unspecified'),
                  failed=failed, degraded=bool(result['warnings']) and not failed,
                  ids=ids, returned_count=len(ids), answerable=bool(positives),
                  ranking=[{'document_id': row.document_id, 'chunk_id': row.chunk_id,
                            'score': row.score, 'page': row.page} for row in rows],
                  metrics={str(k): metrics(ids, case['qrels'], k) for k in METRIC_KS}
                          if positives else None)
    if positives:
        candidate_ids = set(result['trace'].get('candidate_document_ids', []))
        pool_ids = set(result['trace'].get('rerank_document_ids', []))
        result['candidate_recall'] = len(positives & candidate_ids) / len(positives)
        result['rerank_pool_recall'] = (len(positives & pool_ids) / len(positives)
                                       if experiment.rerank else None)
        for branch in result['trace'].get('branches', []):
            branch['qrel_recall'] = len(positives & set(branch['document_ids'])) / len(positives)
    return result


def evaluate_matrix(store, cases, experiments, *, repeats=3, warmup=1, seed=20261015,
                    rewrite=None, rerank=None, transaction=nullcontext, on_record=None):
    """Warm every configuration; seeded interleaving avoids fixed-stage timing order."""
    validate_cases(cases)
    if not 1 <= repeats <= 20 or not 0 <= warmup <= 5:
        raise ValueError('invalid_repetitions')
    if any(item.rewrite for item in experiments) and rewrite is None:
        raise ValueError('rewrite_provider_required')
    if any(item.rerank for item in experiments) and rerank is None:
        raise ValueError('rerank_provider_required')
    rng, records, warmups = random.Random(seed), [], []
    work = [(experiment, case) for experiment in experiments for case in cases]
    for repeat in range(warmup):
        order = list(work)
        rng.shuffle(order)
        for experiment, case in order:
            result = _measure(store, case, experiment, repeat, rewrite, rerank, transaction)
            warmups.append(result)
            if on_record:
                on_record('warmup', result)
    for repeat in range(repeats):
        order = list(work)
        rng.shuffle(order)
        for experiment, case in order:
            result = _measure(store, case, experiment, repeat, rewrite, rerank, transaction)
            records.append(result)
            if on_record:
                on_record('measured', result)
    return {'protocol': {'repeats': repeats, 'warmup_passes': warmup, 'seed': seed,
                         'schedule': 'seeded_shuffled_configuration_query_pairs_each_pass',
                         'top_ks': list(METRIC_KS), 'independent_questions': len(cases),
                         'answerable_questions': sum(any(v > 0 for v in case['qrels'].values())
                                                     for case in cases)},
            'experiments': [asdict(item) for item in experiments],
            'warmups': warmups, 'records': records}


def _percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def _collapse(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row['answerable']:
            grouped[row['query_id']].append(row)
    return {key: {'language': group[0]['language'], 'cluster_id': group[0]['cluster_id'],
                  'metrics': {str(k): {name: statistics.mean(row['metrics'][str(k)][name]
                                                           for row in group)
                                      for name in ('recall_at_k', 'mrr_at_k', 'ndcg_at_k')}
                              for k in METRIC_KS}}
            for key, group in grouped.items()}


def _metric_average(collapsed):
    values = list(collapsed.values())
    return {str(k): {name: statistics.mean(row['metrics'][str(k)][name] for row in values)
                    for name in ('recall_at_k', 'mrr_at_k', 'ndcg_at_k')}
            for k in METRIC_KS} if values else {}


def _paired_interval(deltas, clusters, bootstrap_samples, seed):
    grouped = defaultdict(list)
    for delta, cluster in zip(deltas, clusters, strict=True):
        grouped[cluster].append(delta)
    rng, groups, samples = random.Random(seed), list(grouped.values()), []
    for _ in range(bootstrap_samples):
        values = [delta for group in rng.choices(groups, k=len(groups)) for delta in group]
        samples.append(statistics.mean(values))
    return {'paired_questions': len(deltas), 'independent_clusters': len(grouped),
            'metric': 'ndcg_at_10', 'mean_delta': statistics.mean(deltas),
            'wins': sum(v > 1e-12 for v in deltas), 'ties': sum(abs(v) <= 1e-12 for v in deltas),
            'losses': sum(v < -1e-12 for v in deltas),
            'bootstrap_95_percentile_interval': [_percentile(samples, .025), _percentile(samples, .975)]}


def public_summary(report, *, contrasts=(), bootstrap_samples=2000, seed=20261015):
    """Whitelist aggregate fields: no query, document ID, text, DSN or local path."""
    if bootstrap_samples < 1:
        raise ValueError('invalid_bootstrap_samples')
    aggregates, collapsed = {}, {}
    for config in report['experiments']:
        name = config['id']
        rows = [row for row in report['records'] if row['experiment'] == name]
        warmups = [row for row in report['warmups'] if row['experiment'] == name]
        collapsed[name] = _collapse(rows)
        latencies = [row['elapsed_ms'] for row in rows]
        answerable = [row for row in rows if row['answerable']]
        controls = [row for row in rows if not row['answerable']]
        groups = {language: _metric_average({key: value for key, value in collapsed[name].items()
                                            if value['language'] == language})
                  for language in sorted({row['language'] for row in rows})}
        stable = defaultdict(set)
        for row in rows:
            stable[row['query_id']].add(tuple(row['ids']))
        aggregates[name] = {'configuration': config, 'measured_runs': len(rows),
                            'independent_answerable_questions': len(collapsed[name]),
                            'metrics': _metric_average(collapsed[name]), 'by_language': groups,
                            'failure_count': sum(row['failed'] for row in rows),
                            'degraded_count': sum(row['degraded'] for row in rows),
                            'warmup_runs': len(warmups),
                            'warmup_failures': sum(row['failed'] for row in warmups),
                            'warmup_degraded': sum(row['degraded'] for row in warmups),
                            'p50_ms': statistics.median(latencies) if latencies else None,
                            'p95_ms': _percentile(latencies, .95),
                            'answerable_p50_ms': statistics.median([r['elapsed_ms'] for r in answerable])
                                if answerable else None,
                            'answerable_p95_ms': _percentile([r['elapsed_ms'] for r in answerable], .95),
                            'candidate_recall': statistics.mean(r['candidate_recall'] for r in answerable)
                                if answerable else None,
                            'rerank_pool_recall': statistics.mean(r['rerank_pool_recall'] for r in answerable)
                                if answerable and config['rerank'] else None,
                            'unstable_query_count': sum(len(value) > 1 for value in stable.values()),
                            'control_runs': len(controls),
                            'control_nonempty_runs': sum(bool(r['ids']) for r in controls),
                            'control_failed_runs': sum(r['failed'] for r in controls),
                            'control_successful_empty_runs': sum(not r['failed'] and not r['ids'] for r in controls),
                            'controls_by_type': {kind: {'runs': sum(r['control_type'] == kind for r in controls),
                                'nonempty_runs': sum(r['control_type'] == kind and bool(r['ids']) for r in controls),
                                'failed_runs': sum(r['control_type'] == kind and r['failed'] for r in controls),
                                'successful_empty_runs': sum(r['control_type'] == kind and not r['failed']
                                                             and not r['ids'] for r in controls)}
                                for kind in sorted({r['control_type'] for r in controls})}}
    comparisons = []
    for contrast in contrasts:
        first, second = contrast['experiment'], contrast['reference']
        if first not in collapsed or second not in collapsed:
            continue
        common = sorted(set(collapsed[first]) & set(collapsed[second]))
        if not common:
            continue
        deltas, clusters = [], []
        for key in common:
            delta = (collapsed[first][key]['metrics']['10']['ndcg_at_k'] -
                     collapsed[second][key]['metrics']['10']['ndcg_at_k'])
            deltas.append(delta)
            clusters.append(collapsed[first][key]['cluster_id'])
        comparisons.append({'experiment': first, 'reference': second} |
                           _paired_interval(deltas, clusters, bootstrap_samples, seed))
    interactions = []
    cells = ('rrf_rewrite_rerank', 'rrf_rewrite', 'rrf_rerank', 'rrf')
    if all(cell in collapsed for cell in cells):
        common = sorted(set.intersection(*(set(collapsed[cell]) for cell in cells)))
        if common:
            deltas = [sum(sign * collapsed[cell][key]['metrics']['10']['ndcg_at_k']
                          for sign, cell in zip((1, -1, -1, 1), cells, strict=True))
                      for key in common]
            estimate = _paired_interval(deltas, [collapsed['rrf'][key]['cluster_id'] for key in common],
                                        bootstrap_samples, seed)
            interactions.append({'factors': ['rule_rewrite', 'rerank'],
                                 'formula': 'rrf_rewrite_rerank - rrf_rewrite - rrf_rerank + rrf',
                                 'interpretation': 'positive means super-additive on the nDCG scale; '
                                                   'not necessarily positive total gain', **estimate})
    return {'protocol': report['protocol'], 'aggregates': aggregates, 'comparisons': comparisons,
            'interactions': interactions,
            'uncertainty': {'method': 'paired_cluster_percentile_bootstrap',
                            'samples': bootstrap_samples, 'seed': seed,
                            'repeats_collapsed_before_resampling': True,
                            'claim': 'exploratory; no multiple-comparison correction; not held-out evidence'},
            'limitations': ['Retrieval only; no answer generation or citation-faithfulness evaluation.',
                            'Empty qrels are controls, not an automatic semantic-abstention claim.',
                            'Synthetic/provider smoke tests do not estimate real-library quality.',
                            'Latency includes retrieval and inference but excludes provider construction, '
                            'fingerprinting and artifact writes; shared-host timings are observational.',
                            'Unjudged documents count as zero; incomplete qrels can penalize valid evidence.']}
