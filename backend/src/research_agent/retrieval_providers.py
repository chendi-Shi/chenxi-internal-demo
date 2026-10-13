"""Local query rewriting and ONNX cross-encoder; inference never uses cloud APIs."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .hub_query import ANCHOR_CONCEPTS, CONCEPTS

RERANK_MODEL = 'Xenova/ms-marco-MiniLM-L-6-v2'
RERANK_REVISION = 'a09144355adeed5f58c8ed011d209bf8ee5a1fec'
RERANK_FILES = {
    'tokenizer.json': ('tokenizer.json', 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66'),
    'model_quantized.onnx': ('onnx/model_quantized.onnx', 'e9d8ebf845c413e981c175bfe49a3bfa9b3dcce2a3ba54875ee5df5a58639fbe'),
}


def prepare_reranker(directory: Path):
    """Download pinned public weights explicitly, never as an inference side effect."""
    import httpx
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for name, (remote, expected) in RERANK_FILES.items():
            path = directory / name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                temporary = directory / (name + '.part')
                with client.stream('GET', f'https://huggingface.co/{RERANK_MODEL}/resolve/{RERANK_REVISION}/{remote}') as response:
                    response.raise_for_status()
                    with temporary.open('wb') as handle:
                        for block in response.iter_bytes():
                            handle.write(block)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                    raise ValueError('reranker_download_checksum_mismatch')
                temporary.replace(path)
            files.append({'path': name, 'upstream_path': remote, 'sha256': expected,
                          'bytes': path.stat().st_size})
    manifest = {'repo': RERANK_MODEL, 'revision': RERANK_REVISION, 'files': files}
    (directory / 'manifest.local.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


class RuleQueryRewriter:
    """Deterministic bilingual concept expansion, independent of evaluation labels."""
    def __call__(self, query: str) -> list[str]:
        terms = []
        for group in (*ANCHOR_CONCEPTS, *CONCEPTS):
            for alias in group:
                pattern = re.escape(alias)
                if alias.isascii():
                    pattern = r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])'
                if re.search(pattern, query, flags=re.I):
                    english = [a for a in group if a.isascii()]
                    terms.extend(english[:2])
                    break
        # Keep original names/dates in either language. No facts or corpus passages involved.
        expansion = ' '.join(dict.fromkeys(terms))
        expanded = (query + ' ' + expansion).strip()[:500]
        return [expanded] if expanded and expanded.casefold() != query.casefold() else []


class OnnxReranker:
    """Pinned local MS MARCO cross-encoder, evaluated separately for Chinese queries."""
    def __init__(self, directory: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        manifest = json.loads((directory / 'manifest.local.json').read_text(encoding='utf-8'))
        hashes = {item['path']: item['sha256'] for item in manifest['files']}
        for name in ('tokenizer.json', 'model_quantized.onnx'):
            with (directory / name).open('rb') as handle:
                actual = hashlib.file_digest(handle, 'sha256').hexdigest()
            if actual != hashes[name]:
                raise ValueError('reranker_model_checksum_mismatch')
        self.identity = manifest['repo'] + '@' + manifest['revision']
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512, strategy='only_second')
        pad = self.tokenizer.token_to_id('[PAD]')
        self.tokenizer.enable_padding(pad_id=pad, pad_token='[PAD]')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / 'model_quantized.onnx'),
                                            sess_options=options,
                                            providers=['CPUExecutionProvider'])
        self.inputs = {v.name for v in self.session.get_inputs()}

    def __call__(self, query, rows):
        import numpy as np
        scores = []
        for offset in range(0, len(rows), 8):
            batch = rows[offset:offset + 8]
            encoded = self.tokenizer.encode_batch([(query, r.title + '\n' + r.text) for r in batch])
            inputs = {
                'input_ids': np.array([e.ids for e in encoded], dtype=np.int64),
                'attention_mask': np.array([e.attention_mask for e in encoded], dtype=np.int64),
                'token_type_ids': np.array([e.type_ids for e in encoded], dtype=np.int64),
            }
            output = self.session.run(None, {k: v for k, v in inputs.items() if k in self.inputs})[0]
            scores.extend(float(v) for v in output.reshape(-1))
        return scores
