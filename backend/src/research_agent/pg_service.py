"""Start an already initialized local PostgreSQL cluster from a private config."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def ensure_started(config_path: Path):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    cluster = Path(config['cluster_dir'])
    executable = Path(config['runtime_dir']) / 'Library' / 'bin' / 'pg_ctl.exe'
    if not executable.is_file() and config.get('runtime_source_dir'):
        # A local path alias may be removed between app sessions. The explicit
        # source is only a runtime fallback; never substitute a different cluster.
        executable = Path(config['runtime_source_dir']) / 'Library' / 'bin' / 'pg_ctl.exe'
    if not executable.is_file() or not (cluster / 'PG_VERSION').is_file():
        raise ValueError('postgres_runtime_or_initialized_cluster_missing')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    status = subprocess.run([str(executable), '-D', str(cluster), 'status'],
                            capture_output=True, creationflags=flags, timeout=15)
    if status.returncode == 0:
        return 'already_running'
    if config.get('host') != '127.0.0.1' or not 1024 <= int(config['port']) <= 65535:
        raise ValueError('local_postgres_bind_required')
    # Keep the server log outside PGDATA: Windows crash recovery fsyncs PGDATA
    # and cannot reopen the log while PostgreSQL holds its output handle.
    log_file = Path(config.get('log_file', cluster.parent / f'{cluster.name}.runtime.local.log'))
    if log_file.resolve().is_relative_to(cluster.resolve()):
        raise ValueError('postgres_log_must_be_outside_cluster')
    log_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(executable), '-D', str(cluster), '-l',
                             str(log_file), '-o',
                             f"-p {int(config['port'])} -h 127.0.0.1", '-w', 'start'],
                            # A background postmaster can inherit a captured pipe
                            # and prevent communicate() from ever reaching EOF.
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, timeout=45)
    if result.returncode:
        raise ValueError('postgres_start_failed_see_local_cluster_log')
    return 'started'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    try:
        print('PostgreSQL: ' + ensure_started(args.config), file=sys.stderr)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        print('PostgreSQL start failed; check the local runtime/cluster config.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
