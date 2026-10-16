"""Run actual PostgreSQL/model ablations with private inputs and safe aggregate output."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from research_agent.rag_ablation import (
    database_fingerprint,
    evaluate_matrix,
    load_matrix,
    make_fingerprint,
    public_summary,
    validate_cases,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--matrix', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'samples/rag_ablation/postgres_matrix.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--dataset-kind', choices=('synthetic', 'private_agent_labelled', 'private_human_labelled'),
                        required=True)
    parser.add_argument('--experiments', nargs='+', help='Run these matrix IDs only')
    parser.add_argument('--reranker-dir', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--seed', type=int, default=20261015)
    args = parser.parse_args()
    phase, lock, owns_lock = 'backend_dependencies', None, False
    try:
        from research_agent.pg_lab import PostgresStore, _write_json_atomic, connect
        from research_agent.retrieval_providers import OnnxReranker, RuleQueryRewriter
        phase = 'input_validation'
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        matrix, experiments = load_matrix(args.matrix)
        cases = json.loads(args.queries.read_text(encoding='utf-8-sig'))
        validate_cases(cases)
        if args.experiments:
            if set(args.experiments) - {e.id for e in experiments}:
                raise ValueError('unknown_requested_experiment')
            experiments = [e for e in experiments if e.id in args.experiments]
        if not 1 <= args.repeats <= 20 or not 0 <= args.warmup <= 5:
            raise ValueError('invalid_repetitions')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        lock = args.output_dir / 'ablation.lock'
        with lock.open('x', encoding='utf-8') as handle:
            owns_lock = True
            handle.write(str(os.getpid()))
        if any(args.output_dir.glob('*.json')) or any(args.output_dir.glob('*.jsonl')):
            raise ValueError('output_directory_must_be_new')
        phase = 'provider_initialization'
        rerank, manifest = None, None
        if any(e.rerank for e in experiments):
            directory = args.reranker_dir or config.get('reranker_dir')
            if not directory:
                raise ValueError('reranker_directory_required')
            directory = Path(directory)
            if not directory.is_absolute():
                directory = args.config.resolve().parent / directory
            manifest = json.loads((directory / 'manifest.local.json').read_text(encoding='utf-8'))
            rerank = OnnxReranker(directory)
        os.environ['CHENXI_DATABASE_URL'] = config['database_url']
        phase = 'postgres_connection_and_snapshot'
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout = '120s'")
            store = PostgresStore(conn)
            if any(e.dense for e in experiments) and (not store.snapshot.get('semantic_config')
                                                       or not store.snapshot.get('vector_count')):
                raise ValueError('dense_experiments_require_real_indexed_vectors')
            phase = 'data_fingerprint'
            data = database_fingerprint(conn)
            fingerprint = make_fingerprint(cases=cases, experiments=experiments,
                                           snapshot=store.snapshot, data_fingerprint=data,
                                           provider_manifest=manifest,
                                           protocol={'repeats': args.repeats, 'warmup': args.warmup,
                                                     'seed': args.seed, 'dataset_kind': args.dataset_kind},
                                           driver_path=Path(__file__))
            phase = 'measured_experiments'
            completed = 0
            with (args.output_dir / 'trace.local.jsonl').open('x', encoding='utf-8') as trace:
                def checkpoint(kind, record):
                    nonlocal completed
                    trace.write(json.dumps({'kind': kind, 'record': record}, ensure_ascii=False) + '\n')
                    trace.flush()
                    if kind == 'measured':
                        completed += 1
                        if completed % 10 == 0:
                            print(f'MEASURED {completed} cases', flush=True)
                report = evaluate_matrix(store, cases, experiments, repeats=args.repeats,
                                         warmup=args.warmup, seed=args.seed,
                                         rewrite=RuleQueryRewriter(), rerank=rerank,
                                         transaction=conn.transaction, on_record=checkpoint)
            report.update(dataset_kind=args.dataset_kind, measured_at_utc=datetime.now(UTC).isoformat(),
                          fingerprint=fingerprint, data_fingerprint=data,
                          snapshot={k: str(v) if k == 'imported_at' else v for k, v in store.snapshot.items()},
                          environment={'python': platform.python_version(), 'platform': platform.system(),
                                       'processor_architecture': platform.machine()})
            _write_json_atomic(args.output_dir / 'report.local.json', report)
            summary = public_summary(report, contrasts=matrix.get('contrasts', []), seed=args.seed)
            summary.update(dataset_kind=report['dataset_kind'], measured_at_utc=report['measured_at_utc'],
                           fingerprint=fingerprint, data_fingerprint=data, environment=report['environment'],
                           snapshot={key: store.snapshot[key] for key in
                                     ('document_count', 'chunk_count', 'vector_count')})
            summary['snapshot']['vector_coverage_percent'] = (100 * store.snapshot['vector_count'] /
                store.snapshot['chunk_count'] if store.snapshot['chunk_count'] else 0)
            unhealthy = any(v['failure_count'] or v['degraded_count'] or v['warmup_failures'] or
                            v['warmup_degraded'] for v in summary['aggregates'].values())
            summary['status'] = 'completed_with_failures_or_degradation' if unhealthy else 'completed'
            _write_json_atomic(args.output_dir / 'summary.public.json', summary)
            print('COMPLETE: real PostgreSQL results saved; public summary contains aggregate data only.')
            return 2 if unhealthy else 0
    except Exception as error:
        # Never echo driver/model messages: those can include DSNs, paths or private query text.
        print(f'ABLATION FAILED during {phase} ({type(error).__name__}). '
              'Verify local inputs, PostgreSQL/pgvector and required local model files. '
              'No synthetic scores were substituted.', file=sys.stderr)
        return 1
    finally:
        if owns_lock and lock is not None and lock.exists():
            # Delete only a lock we actually created, never another driver's lock.
            try:
                if lock.read_text(encoding='utf-8') == str(os.getpid()):
                    lock.unlink()
            except OSError:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
