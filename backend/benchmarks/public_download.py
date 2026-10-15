"""Resumable, verified downloads of pinned public benchmark/model artifacts.

No tokens or account credentials are sent. Parts are retained after interruption.
The caller supplies the SHA-256 and byte size from the pinned upstream manifest.
"""
from __future__ import annotations

import hashlib
import math
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def file_sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def download_verified(url, path, sha256, size, *, workers=8, progress=None,
                      _retried_corruption=False):
    """Download fixed-size ranges with bounded retry and verify the complete file."""
    if not url.startswith('https://') or len(sha256) != 64 or size <= 0:
        raise ValueError('pinned_https_sha256_and_size_required')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == size and file_sha256(path) == sha256:
        return path
    parts = path.parent / (path.name + '.parts')
    parts.mkdir(exist_ok=True)
    identity = parts / 'identity.txt'
    expected = f'{sha256}\n{size}\n'
    if identity.exists() and identity.read_text() != expected:
        raise ValueError('download_parts_belong_to_different_artifact')
    identity.write_text(expected, encoding='ascii')
    chunk_size = 2 * 1024 * 1024
    count = math.ceil(size / chunk_size)

    def fetch(index):
        part = parts / f'{index:06d}.part'
        start, end = index * chunk_size, min(size, (index + 1) * chunk_size) - 1
        target = end - start + 1
        for attempt in range(12):
            have = part.stat().st_size if part.exists() else 0
            if have == target:
                return index
            if have > target:
                raise ValueError('oversized_download_part')
            request = urllib.request.Request(url, headers={
                'Range': f'bytes={start + have}-{end}',
                'User-Agent': 'chenxi-public-rag-benchmark/1.0',
                'Accept-Encoding': 'identity',
            })
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    if response.status == 200 and start == 0 and have == 0 and count == 1:
                        pass
                    elif (response.status != 206 or response.headers.get('Content-Range') !=
                          f'bytes {start + have}-{end}/{size}'):
                        raise ValueError('server_did_not_honor_exact_range')
                    with part.open('ab') as output:
                        remaining = target - have
                        while remaining:
                            data = response.read(min(65536, remaining))
                            if not data:
                                raise OSError('download_stream_ended_early')
                            output.write(data)
                            remaining -= len(data)
                        output.flush()
                        os.fsync(output.fileno())
                return index
            except (OSError, TimeoutError):
                if attempt == 11:
                    raise
                time.sleep(min(30, 2 ** min(attempt, 5)))
        raise RuntimeError('download_incomplete')

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as executor:
        futures = [executor.submit(fetch, index) for index in range(count)]
        done = 0
        for future in as_completed(futures):
            future.result()
            done += 1
            if progress:
                progress({'file': path.name, 'parts_complete': done, 'parts_total': count,
                          'bytes_total': size})
    temporary = path.with_name(path.name + '.assembling')
    digest = hashlib.sha256()
    with temporary.open('wb') as output:
        for index in range(count):
            with (parts / f'{index:06d}.part').open('rb') as source:
                while data := source.read(1024 * 1024):
                    digest.update(data)
                    output.write(data)
        output.flush()
        os.fsync(output.fileno())
    if temporary.stat().st_size != size or digest.hexdigest() != sha256:
        # These are only the exact range files owned by this artifact. Do not leave
        # corrupt complete ranges that would poison every subsequent resume.
        temporary.unlink()
        for index in range(count):
            (parts / f'{index:06d}.part').unlink()
        if not _retried_corruption:
            return download_verified(url, path, sha256, size, workers=workers,
                                     progress=progress, _retried_corruption=True)
        raise ValueError('download_sha256_mismatch')
    temporary.replace(path)
    for index in range(count):
        (parts / f'{index:06d}.part').unlink()
    identity.unlink()
    parts.rmdir()
    return path
