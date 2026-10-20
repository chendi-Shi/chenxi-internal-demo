"""Download only pinned public ONNX/tokenizer files; resumable and SHA-256 verified."""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from public_download import download_verified


def _prepare_unlocked(manifest_path, root, *, include_optional=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    status = {'phase': 'downloading_models', 'completed': [], 'started_at': datetime.now(UTC).isoformat()}

    def record():
        status['updated_at'] = datetime.now(UTC).isoformat()
        temporary = root / 'download-status.tmp'
        temporary.write_text(json.dumps(status, indent=2), encoding='utf-8')
        temporary.replace(root / 'download-status.local.json')

    record()
    try:
        for model in manifest['models']:
            if model.get('optional') and not include_optional:
                continue
            status['model'] = model['id']
            for item in model['files']:
                status['file'] = item['local_path']
                record()

                def progress(value, model_id=model['id']):
                    status['progress'] = value
                    record()
                    if value['parts_complete'] % 10 == 0 or value['parts_complete'] == value['parts_total']:
                        print(json.dumps({'model': model_id, **value}), flush=True)

                download_verified(item['url'], root / model['id'] / item['local_path'],
                                  item['sha256'], item['size'], progress=progress)
            status['completed'].append(model['id'])
            record()
        status['phase'] = 'completed'
        record()
    except Exception as error:
        status.update(phase='failed', error_type=type(error).__name__, error=str(error))
        record()
        raise
    return status


def prepare(manifest_path, root, *, include_optional=False):
    from supervise_public_campaign import alive
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'prepare.lock'
    if lock.exists():
        if alive(int(lock.read_text())):
            raise RuntimeError('model_preparation_already_running')
        lock.unlink()
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    try:
        return _prepare_unlocked(manifest_path, root, include_optional=include_optional)
    finally:
        if lock.exists() and lock.read_text() == str(os.getpid()):
            lock.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] /
                        'samples/rag_ablation/public_models.json')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--include-optional', action='store_true')
    arguments = parser.parse_args()
    prepare(arguments.manifest, arguments.root, include_optional=arguments.include_optional)
