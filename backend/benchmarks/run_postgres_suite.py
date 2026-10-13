"""Run all six stages with private local inputs; print aggregate metrics only."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from research_agent.pg_lab import validate_completed_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--reranker-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--resume', action='store_true',
                        help='Reuse validated completed stages and resume case checkpoints')
    parser.add_argument('--accept-legacy-complete', action='store_true',
                        help='Explicitly accept healthy old reports lacking source fingerprints')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    env = dict(os.environ, CHENXI_DATABASE_URL=config['database_url'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    snapshot = None
    for stage in ('lexical', 'dense', 'hybrid', 'rrf', 'rerank', 'rewrite'):
        output = args.output_dir / (stage + '.local.json')
        command = [sys.executable, '-m', 'research_agent.pg_lab', 'evaluate',
                   str(args.queries.resolve()), '--stage', stage, '--output', str(output.resolve()),
                   '--repeats', str(args.repeats), '--warmup', '1']
        if stage in {'rerank', 'rewrite'}:
            command.extend(['--reranker-dir', str(args.reranker_dir.resolve())])
        if stage == 'rewrite':
            command.append('--rewrite-rules')
        if args.resume:
            command.append('--resume')
        if args.accept_legacy_complete:
            if not args.resume:
                parser.error('--accept-legacy-complete requires --resume')
            # Only legacy baseline/control reports are eligible. New model stages
            # must always carry their complete data/source/provider fingerprint.
            if stage in {'lexical', 'dense', 'hybrid', 'rrf'}:
                command.append('--accept-legacy-complete')
        print('RUNNING ' + stage, flush=True)
        subprocess.run(command, env=env, check=True)
        result = json.loads(output.read_text(encoding='utf-8'))
        cases = json.loads(args.queries.read_text(encoding='utf-8'))
        validate_completed_report(result, cases, result['snapshot'],
                                  result.get('evaluation_fingerprint'), stage,
                                  args.repeats, 1,
                                  accept_legacy=args.accept_legacy_complete and
                                      stage in {'lexical', 'dense', 'hybrid', 'rrf'})
        if summary:
            if result['snapshot'] != snapshot:
                raise ValueError('suite_snapshot_changed_between_stages')
        else:
            snapshot = result['snapshot']
        summary[stage] = {'aggregate': result['aggregate'], 'by_language': result['by_language'],
                          'nondegraded': result['nondegraded']}
        print(stage + ' ' + json.dumps(summary[stage], ensure_ascii=False), flush=True)
    (args.output_dir / 'summary.local.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
