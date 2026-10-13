"""PostgreSQL snapshot import and repeatable retrieval ablation CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import struct
import time
from contextlib import nullcontext
from pathlib import Path

from .hub_semantic import query_vector
from .retrieval_lab import STAGES, Candidate, metrics, run_pipeline
from .store import search_tokens

_METRIC_KEYS = ('recall_at_k', 'mrr_at_k', 'ndcg_at_k')


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def evaluation_fingerprint(queries_path, snapshot, stage, repeats, warmup, *,
                           reranker_dir=None, rewrite_map=None, rewrite_rules=False):
    """Bind private checkpoints to data, providers and retrieval implementation."""
    directory = Path(__file__).parent
    sources = ('pg_lab.py', 'retrieval_lab.py', 'retrieval_providers.py',
               'hub_semantic.py', 'hub_e5.py', 'hub_query.py', 'store.py')
    parts = {
        'queries_sha256': hashlib.sha256(Path(queries_path).read_bytes()).hexdigest(),
        'snapshot_sha256': _json_digest(snapshot),
        'sources_sha256': _json_digest({name: hashlib.sha256(
            (directory / name).read_bytes()).hexdigest() for name in sources}),
        'stage': stage, 'repeats': repeats, 'warmup': warmup,
        'candidate_limit': 100, 'result_limit': 10, 'rrf_k': 60,
        'rerank_documents': 50, 'rewrite_rules': bool(rewrite_rules),
    }
    if reranker_dir:
        manifest = Path(reranker_dir) / 'manifest.local.json'
        parts['reranker_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if rewrite_map:
        parts['rewrite_map_sha256'] = hashlib.sha256(Path(rewrite_map).read_bytes()).hexdigest()
    return {'sha256': _json_digest(parts), 'inputs': parts}


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validate_cases(reports, cases, stage, repeats, *, complete):
    """Verify every saved result against the current labels without rerunning it."""
    expected = [(repeat, case) for repeat in range(repeats) for case in cases]
    if len(reports) > len(expected) or (complete and len(reports) != len(expected)):
        raise ValueError('resume_case_count_mismatch')
    for row, (repeat, case) in zip(reports, expected, strict=False):
        if (row['query_id'] != case['id'] or row['repeat'] != repeat or
                row['language'] != case.get('language', 'unknown') or
                row['stage'] != stage or not row['queries'] or
                row['queries'][0] != case['query']):
            raise ValueError('resume_case_identity_mismatch')
        judged = any(value > 0 for value in case['qrels'].values())
        if row['no_answer_expected'] != (not judged):
            raise ValueError('resume_labels_mismatch')
        if row['ids'] != [item['document_id'] for item in row['ranking']]:
            raise ValueError('resume_ranking_mismatch')
        if row['returned_count'] != len(row['ids']) or row['abstained'] != (not row['ids']):
            raise ValueError('resume_ranking_mismatch')
        if judged:
            actual = metrics(row['ids'], case['qrels'])
            if any(not math.isclose(actual[key], row['metrics'][key], abs_tol=1e-12)
                   for key in _METRIC_KEYS):
                raise ValueError('resume_metrics_mismatch')
        elif row['metrics'] is not None:
            raise ValueError('resume_labels_mismatch')
        if not math.isfinite(row['elapsed_ms']) or row['elapsed_ms'] < 0:
            raise ValueError('resume_latency_invalid')


def _summarize(reports, cases, repeats, warmup, cold_first_ms, warmup_failures,
               actual_warmup_runs=None):
    judged = [row for row in reports if row['metrics'] is not None]
    aggregate = {key: statistics.mean(row['metrics'][key] for row in judged)
                 for key in _METRIC_KEYS} if judged else {}
    latencies = sorted(row['elapsed_ms'] for row in reports)
    controls = [row for row in reports if row['no_answer_expected']]
    aggregate.update(query_count=len(cases), measured_runs=len(reports), repeats=repeats,
                     warmup_runs=warmup * len(cases) if actual_warmup_runs is None
                         else actual_warmup_runs, warmup_failures=warmup_failures,
                     no_answer_return_rate=statistics.mean(bool(row['returned_count'])
                         for row in controls) if controls else None,
                     degraded_queries=sum(bool(row['warnings']) for row in reports),
                     failure_rate=statistics.mean(row['failed'] for row in reports)
                         if reports else 0, cold_first_query_ms=cold_first_ms,
                     p50_ms=statistics.median(latencies) if latencies else None,
                     p95_ms=latencies[max(0, math.ceil(.95 * len(latencies)) - 1)]
                         if latencies else None)
    languages = {}
    for language in sorted({row['language'] for row in judged}):
        group = [row for row in judged if row['language'] == language]
        languages[language] = {key: statistics.mean(row['metrics'][key] for row in group)
                               for key in _METRIC_KEYS}
    clean = [row for row in judged if not row['warnings']]
    nondegraded = {key: statistics.mean(row['metrics'][key] for row in clean)
                  for key in _METRIC_KEYS} if clean else {}
    return aggregate, languages, nondegraded


def validate_completed_report(report, cases, snapshot, fingerprint, stage, repeats,
                              warmup, *, accept_legacy=False):
    """Old completed files require explicit opt-in; never invent their provenance."""
    if _json_digest(report['snapshot']) != _json_digest(snapshot):
        raise ValueError('resume_snapshot_mismatch')
    saved = report.get('evaluation_fingerprint')
    if (saved is None and not accept_legacy) or (saved is not None and saved != fingerprint):
        raise ValueError('resume_fingerprint_mismatch')
    _validate_cases(report['cases'], cases, stage, repeats, complete=True)
    actual_warmup = report['aggregate']['warmup_runs']
    if actual_warmup < warmup * len(cases) or (saved is not None and
            report['resume']['actual_warmup_runs'] != actual_warmup):
        raise ValueError('resume_warmup_count_mismatch')
    aggregate, languages, nondegraded = _summarize(
        report['cases'], cases, repeats, warmup,
        report['aggregate']['cold_first_query_ms'], report['aggregate']['warmup_failures'],
        actual_warmup)
    if (aggregate != report['aggregate'] or languages != report['by_language'] or
            nondegraded != report['nondegraded']):
        raise ValueError('resume_aggregate_mismatch')
    if aggregate['failure_rate'] or aggregate['degraded_queries'] or aggregate['warmup_failures']:
        raise ValueError('resume_completed_report_not_healthy')
    if aggregate['no_answer_return_rate'] not in (0, None):
        raise ValueError('resume_completed_report_controls_failed')
    return saved is None


def evaluate_cases(store, cases, *, stage, output, fingerprint, repeats=3, warmup=1,
                   rerank=None, rewrite=None, resume=False, transaction=nullcontext):
    """Persist each completed measured case; restart with an additional full warmup."""
    output = Path(output)
    checkpoint = output.with_name(output.stem + '.checkpoint.local.json')
    state = {'version': 1, 'evaluation_fingerprint': fingerprint,
             'snapshot': store.snapshot, 'cases': [], 'cold_first_query_ms': None,
             'warmup_failures': 0, 'warmup_runs_completed': 0, 'process_sessions': 0}
    if resume and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if state.get('version') != 1 or state['evaluation_fingerprint'] != fingerprint:
            raise ValueError('resume_fingerprint_mismatch')
        if _json_digest(state['snapshot']) != _json_digest(store.snapshot):
            raise ValueError('resume_snapshot_mismatch')
        _validate_cases(state['cases'], cases, stage, repeats, complete=False)
    state['process_sessions'] += 1
    _write_json_atomic(checkpoint, state)
    for _ in range(warmup):
        for case in cases:
            try:
                with transaction():
                    warmed = run_pipeline(store, case['query'], stage=stage,
                                          filters=case.get('filters'), rerank=rerank,
                                          rewrite=rewrite)
            except Exception:
                state['warmup_failures'] += 1
            else:
                if state['cold_first_query_ms'] is None:
                    state['cold_first_query_ms'] = warmed['elapsed_ms']
            state['warmup_runs_completed'] += 1
            _write_json_atomic(checkpoint, state)
        print(f'WARMUP {stage}: {len(cases)} queries complete', flush=True)
    reports = state['cases']
    total = len(cases) * repeats
    for index in range(len(reports), total):
        repeat, position = divmod(index, len(cases))
        case = cases[position]
        started = time.perf_counter()
        try:
            with transaction():
                result = run_pipeline(store, case['query'], stage=stage,
                                      filters=case.get('filters'), rerank=rerank,
                                      rewrite=rewrite)
            failed = False
        except Exception:
            result = {'results': [], 'stage': stage, 'warnings': ['query_failed'],
                      'queries': [case['query']],
                      'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}
            failed = True
        rows = result.pop('results')
        ids = [row.document_id for row in rows]
        judged = any(value > 0 for value in case['qrels'].values())
        result.update(query_id=case['id'], repeat=repeat, ids=ids,
                      language=case.get('language', 'unknown'), failed=failed,
                      ranking=[{'document_id': row.document_id, 'chunk_id': row.chunk_id,
                                'page': row.page, 'score': row.score} for row in rows],
                      metrics=metrics(ids, case['qrels']) if judged else None,
                      no_answer_expected=not judged, returned_count=len(rows),
                      abstained=not rows)
        reports.append(result)
        _write_json_atomic(checkpoint, state)
        if len(reports) % 5 == 0 or len(reports) == total:
            print(f'PROGRESS {stage}: {len(reports)}/{total} measured cases complete', flush=True)
        if position == len(cases) - 1:
            print(f'MEASURED {stage}: round {repeat + 1}/{repeats} complete', flush=True)
    aggregate, languages, nondegraded = _summarize(
        reports, cases, repeats, warmup, state['cold_first_query_ms'], state['warmup_failures'],
        state['warmup_runs_completed'])
    report = {'snapshot': {key: str(value) if key == 'imported_at' else value
                           for key, value in store.snapshot.items()},
              'evaluation_fingerprint': fingerprint,
              'resume': {'process_sessions': state['process_sessions'],
                         'actual_warmup_runs': state['warmup_runs_completed'],
                         'additional_warmup_runs': max(0, state['warmup_runs_completed'] -
                                                       warmup * len(cases))},
              'aggregate': aggregate, 'by_language': languages,
              'nondegraded': nondegraded, 'cases': reports}
    _write_json_atomic(output, report)
    return report

_QUERY_STOP_WORDS = frozenset(
    'a an the and or of to in on at for from by with as is are was were be been being '
    'do does did have has had can could would should will may might this that these those '
    'it its i we you they he she our your their what which when where why how '
    'please tell me about'.split()
)


def lexical_query_tokens(query: str):
    """Drop function words; use Chinese bigrams instead of a broad single-char OR."""
    tokens = list(dict.fromkeys(search_tokens(query).split()))
    has_bigrams = any(re.fullmatch(r'[\u4e00-\u9fff]{2,}', token) for token in tokens)
    return [token for token in tokens if token not in _QUERY_STOP_WORDS and not (
        has_bigrams and re.fullmatch(r'[\u4e00-\u9fff]', token))][:96]


def connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(os.environ["CHENXI_DATABASE_URL"], row_factory=dict_row,
                           connect_timeout=5)


def vector_literal(values):
    values = [float(v) for v in values]
    if not values or any(not math.isfinite(v) for v in values):
        raise ValueError("invalid_vector")
    return "[" + ",".join(str(v) for v in values) + "]"


def import_snapshot(database: Path):
    # Read-only source snapshot; no deletion or modification of the live SQLite library.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        saved = source.execute("SELECT config_json FROM hub_semantic WHERE id=1").fetchone()
        config = json.loads(saved[0]) if saved else None
        with connect() as conn:
            # Serialized, transactional replacement of the dedicated experiment schema.
            conn.execute("SELECT pg_advisory_xact_lock(817041)")
            conn.execute(Path(__file__).with_name("pg_schema.sql").read_text(encoding="utf-8"))
            conn.execute("TRUNCATE retrieval_lab.embeddings,retrieval_lab.chunks,"
                         "retrieval_lab.documents,retrieval_lab.snapshot")
            # Bulk-load before GIN construction; transactional DDL keeps the previous
            # snapshot and its index recoverable if any part of import fails.
            conn.execute("DROP INDEX IF EXISTS retrieval_lab.chunks_fts")
            with conn.cursor() as cursor:
                with cursor.copy("COPY retrieval_lab.documents FROM STDIN") as copy:
                    for row in source.execute("SELECT d.*,s.enabled FROM hub_documents d "
                                              "JOIN hub_sources s ON s.id=d.source_id"):
                        copy.write_row((row['id'], row['source_id'], row['title'], row['body'],
                                        row['published_at'] or None, row['url'], row['metadata_json'],
                                        bool(row['enabled']), row['fingerprint']))
                cursor.execute("CREATE TEMP TABLE imported_chunks (id bigint,document_id text,"
                               "start_offset integer,end_offset integer,page integer,text text,"
                               "token_text text) ON COMMIT DROP")
                with cursor.copy("COPY imported_chunks FROM STDIN") as copy:
                    for row in source.execute("SELECT c.*,d.title FROM hub_chunks c "
                                              "JOIN hub_documents d ON d.id=c.document_id"):
                        copy.write_row((row['id'], row['document_id'], row['start'], row['end'],
                                        row['page'], row['text'],
                                        search_tokens(row['title'] + '\n' + row['text'])))
                cursor.execute("INSERT INTO retrieval_lab.chunks SELECT id,document_id,start_offset,"
                               "end_offset,page,text,to_tsvector('simple',token_text) FROM imported_chunks")
                if config:
                    with cursor.copy("COPY retrieval_lab.embeddings FROM STDIN") as copy:
                        for row in source.execute("SELECT v.* FROM hub_vectors v "
                                                  "JOIN hub_chunks c ON c.id=v.chunk_id "
                                                  "WHERE v.model_digest=?", (config['model_digest'],)):
                            vector = struct.unpack(f"<{len(row['vector']) // 4}f", row['vector'])
                            copy.write_row((row['chunk_id'], row['model_digest'], vector_literal(vector)))
            conn.execute("CREATE INDEX chunks_fts ON retrieval_lab.chunks USING gin(tokens)")
            counts = {name: conn.execute(f"SELECT count(*) AS n FROM retrieval_lab.{name}").fetchone()['n']
                      for name in ('documents', 'chunks', 'embeddings')}
            conn.execute("INSERT INTO retrieval_lab.snapshot VALUES(1,now(),%s,%s,%s,%s::jsonb)",
                         (*counts.values(), json.dumps(config)))
            conn.execute("ANALYZE retrieval_lab.chunks")
            conn.execute("ANALYZE retrieval_lab.embeddings")
    return counts


class PostgresStore:
    def __init__(self, conn):
        self.conn = conn
        row = conn.execute("SELECT * FROM retrieval_lab.snapshot WHERE id=1").fetchone()
        if not row:
            raise ValueError("import_snapshot_first")
        self.snapshot = row

    def _filters(self, filters):
        unknown = set(filters) - {'source_ids', 'since', 'until', 'metadata'}
        if unknown:
            raise ValueError("unknown_filters")
        clauses, args = ['d.enabled'], []
        if filters.get('source_ids'):
            clauses.append('d.source_id=ANY(%s)')
            args.append(filters['source_ids'])
        for name, operator in [('since', '>='), ('until', '<')]:
            if filters.get(name):
                clauses.append(f'd.published_at{operator}%s::timestamptz')
                args.append(filters[name])
        if filters.get('metadata'):
            clauses.append('d.metadata @> %s::jsonb')
            args.append(json.dumps(filters['metadata']))
        return ' AND '.join(clauses), args

    def lexical(self, query, filters, limit):
        # Explicit OR token query for recall, with identical document filters in both branches.
        tokens = lexical_query_tokens(query)
        if not tokens:
            return []
        expression = ' | '.join(tokens)
        where, args = self._filters(filters)
        rows = self.conn.execute(
            # Sort only IDs and scores. Materialize passage text after the top-k cut
            # instead of carrying large PDF passages through a broad full-text sort.
            "WITH ranked AS MATERIALIZED (SELECT c.id AS chunk_id,d.id AS document_id,"
            "ts_rank(c.tokens,q) AS score FROM retrieval_lab.chunks c "
            "JOIN retrieval_lab.documents d ON d.id=c.document_id "
            "CROSS JOIN to_tsquery('simple',%s) q WHERE c.tokens @@ q AND " + where +
            " ORDER BY score DESC,c.id LIMIT %s) "
            "SELECT r.chunk_id,r.document_id,d.title,c.text,c.page,r.score FROM ranked r "
            "JOIN retrieval_lab.chunks c ON c.id=r.chunk_id "
            "JOIN retrieval_lab.documents d ON d.id=r.document_id "
            "ORDER BY r.score DESC,r.chunk_id", [expression, *args, limit]).fetchall()
        return [Candidate(**r) for r in rows]

    def dense(self, query, filters, limit):
        config = self.snapshot['semantic_config']
        if not config or not self.snapshot['vector_count']:
            raise ValueError("semantic_index_missing")
        vector = vector_literal(query_vector(config, query))
        where, args = self._filters(filters)
        # Exact cosine search is the baseline. ANN is a separate recall/latency experiment.
        # Roll back a failed dense statement before the pipeline catches it and
        # continues with lexical results on the same connection.
        with self.conn.transaction():
            rows = self.conn.execute(
                "SELECT c.id AS chunk_id,d.id AS document_id,d.title,c.text,c.page,"
                "1-(v.embedding <=> %s::vector) AS score FROM retrieval_lab.embeddings v "
                "JOIN retrieval_lab.chunks c ON c.id=v.chunk_id "
                "JOIN retrieval_lab.documents d ON d.id=c.document_id "
                "WHERE v.model_digest=%s AND " + where +
                " ORDER BY v.embedding <=> %s::vector,c.id LIMIT %s",
                [vector, config['model_digest'], *args, vector, limit]).fetchall()
        return [Candidate(**r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import-snapshot')
    imp.add_argument('database', type=Path)
    prepare = sub.add_parser('prepare-reranker')
    prepare.add_argument('directory', type=Path)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('queries', type=Path)
    evaluate.add_argument('--stage', choices=STAGES, default='lexical')
    evaluate.add_argument('--output', type=Path, required=True)
    evaluate.add_argument('--reranker-dir', type=Path)
    evaluate.add_argument('--rewrite-map', type=Path)
    evaluate.add_argument('--rewrite-rules', action='store_true')
    evaluate.add_argument('--repeats', type=int, default=3)
    evaluate.add_argument('--warmup', type=int, default=1)
    evaluate.add_argument('--resume', action='store_true')
    evaluate.add_argument('--accept-legacy-complete', action='store_true',
                          help='Explicitly reuse healthy completed files without old fingerprints')
    args = parser.parse_args()
    if args.command == 'prepare-reranker':
        from .retrieval_providers import prepare_reranker
        print(json.dumps(prepare_reranker(args.directory)))
        return
    if args.command == 'import-snapshot':
        print(json.dumps(import_snapshot(args.database)))
        return
    if not 1 <= args.repeats <= 20 or not 0 <= args.warmup <= 5:
        raise ValueError('invalid_repetitions')
    with connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        store = PostgresStore(conn)
        cases = json.loads(args.queries.read_text(encoding='utf-8'))
        if not cases or len({case['id'] for case in cases}) != len(cases):
            raise ValueError('invalid_query_cases')
        fingerprint = evaluation_fingerprint(
            args.queries, store.snapshot, args.stage, args.repeats, args.warmup,
            reranker_dir=args.reranker_dir, rewrite_map=args.rewrite_map,
            rewrite_rules=args.rewrite_rules)
        if args.resume and args.output.exists():
            report = json.loads(args.output.read_text(encoding='utf-8'))
            legacy = validate_completed_report(
                report, cases, store.snapshot, fingerprint, args.stage, args.repeats,
                args.warmup, accept_legacy=args.accept_legacy_complete)
            print(f'REUSED {args.stage}: {len(report["cases"])} validated measured cases'
                  + (' (legacy provenance explicitly accepted)' if legacy else ''), flush=True)
            return
        rerank, rewrite = load_evaluation_providers(args)
        evaluate_cases(store, cases, stage=args.stage, output=args.output,
                       fingerprint=fingerprint, repeats=args.repeats, warmup=args.warmup,
                       rerank=rerank, rewrite=rewrite, resume=args.resume,
                       transaction=conn.transaction)


def load_evaluation_providers(args):
    rerank, rewrite = None, None
    if args.reranker_dir:
        from .retrieval_providers import OnnxReranker
        rerank = OnnxReranker(args.reranker_dir.resolve())
    if args.rewrite_map:
        mapping = json.loads(args.rewrite_map.read_text(encoding='utf-8'))
        def rewrite(q):
            return mapping.get(q, [])
    elif args.rewrite_rules:
        from .retrieval_providers import RuleQueryRewriter
        rewrite = RuleQueryRewriter()
    return rerank, rewrite


if __name__ == '__main__':
    main()
