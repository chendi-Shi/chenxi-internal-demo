"""Pinned local ONNX inference on DirectML, with observed provider evidence.

Dependencies are loaded lazily. Tokenization/pooling run on CPU; the transformer
graph must execute DML compute nodes. Provider registration alone is not proof.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


def _sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verify_model_files(directory, spec):
    """Check exact graph/tokenizer bytes before loading any model graph."""
    root = Path(directory).resolve()
    verified = {}
    for item in spec['files']:
        relative = item.get('local_path') or item['path']
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or relative in verified:
            raise ValueError('invalid_model_file_path')
        if not path.is_file() or path.stat().st_size != item['size']:
            raise ValueError('model_file_missing_or_size_mismatch')
        digest = _sha256(path)
        if digest != item['sha256']:
            raise ValueError('model_file_checksum_mismatch')
        verified[relative] = {'sha256': digest, 'size': path.stat().st_size}
    if not {'onnx/model.onnx', 'tokenizer.json'} <= set(verified):
        raise ValueError('model_and_tokenizer_required')
    return verified


def inspect_profile(profile):
    """Inspect executed node events, not requested/available provider metadata."""
    if not isinstance(profile, list):
        raise ValueError('invalid_onnx_profile')
    gpu, cpu, other = [], [], []
    shape_ops = {'Shape', 'Size', 'Gather', 'Unsqueeze', 'Squeeze', 'Concat', 'Reshape', 'Cast'}
    for event in profile:
        if event.get('cat') != 'Node' or 'provider' not in event.get('args', {}):
            continue
        # Fence markers do not represent execution. ONNX kernel events have this suffix.
        if not event.get('name', '').endswith('_kernel_time'):
            continue
        provider = event['args']['provider']
        target = gpu if provider == 'DmlExecutionProvider' else cpu if provider == 'CPUExecutionProvider' else other
        target.append(event)
    compute = [event for event in gpu if event['args'].get('op_name', '') not in shape_ops]
    if not compute:
        raise RuntimeError('no_executed_dml_compute_nodes')
    return {'provider': 'DmlExecutionProvider', 'dml_kernel_events': len(gpu),
            'dml_compute_events': len(compute), 'cpu_kernel_events': len(cpu),
            'other_provider_kernel_events': len(other),
            'cpu_ops': dict(Counter(e['args'].get('op_name', 'unknown') for e in cpu)),
            'dml_ops': dict(Counter(e['args'].get('op_name', 'unknown') for e in gpu)),
            'evidence': 'ONNX Runtime executed Node kernel events with DmlExecutionProvider',
            'limitation': 'Profile events prove DML dispatch; their host durations are not GPU-only '
                          'kernel timings. Verify the adapter identity and GPU telemetry separately.'}


def mean_pool_normalize(hidden, attention_mask):
    import numpy as np
    hidden = np.asarray(hidden, dtype=np.float32)
    mask = np.asarray(attention_mask, dtype=np.float32)
    if hidden.ndim != 3 or mask.shape != hidden.shape[:2] or not np.isfinite(hidden).all():
        raise ValueError('invalid_token_embeddings')
    lengths = mask.sum(axis=1, keepdims=True)
    if np.any(lengths <= 0):
        raise ValueError('empty_attention_mask')
    pooled = (hidden * mask[..., None]).sum(axis=1) / lengths
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or np.any(norms <= 0):
        raise ValueError('invalid_pooled_embedding')
    return np.asarray(pooled / norms, dtype=np.float32)


class _DirectMLModel:
    def __init__(self, directory, spec, *, device_id=0, progress=None):
        self.directory, self.spec = Path(directory).resolve(), spec
        self.progress = progress
        self.verified_files = verify_model_files(self.directory, spec)
        import onnxruntime as ort
        from tokenizers import Tokenizer
        if type(device_id) is not int or device_id < 0:
            raise ValueError('invalid_dml_device_id')
        if 'DmlExecutionProvider' not in ort.get_available_providers():
            raise RuntimeError('directml_provider_unavailable')
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_profiling = True
        profile_dir = self.directory / 'profiles'
        profile_dir.mkdir(parents=True, exist_ok=True)
        options.profile_file_prefix = str(profile_dir / 'dml')
        self.session = ort.InferenceSession(str(self.directory / 'onnx/model.onnx'),
                                           sess_options=options,
                                           providers=[('DmlExecutionProvider', {'device_id': device_id}),
                                                      'CPUExecutionProvider'])
        if 'DmlExecutionProvider' not in self.session.get_providers():
            raise RuntimeError('directml_initialization_fell_back_to_cpu')
        self.session.disable_fallback()
        self.tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
        pad_token = spec['pad_token']
        pad_id = self.tokenizer.token_to_id(pad_token)
        if pad_id is None:
            raise ValueError('model_pad_token_missing')
        self.pad_id, self.pad_token = pad_id, pad_token
        self.inputs = {item.name: item.type for item in self.session.get_inputs()}
        if not {'input_ids', 'attention_mask'} <= set(self.inputs) or set(self.inputs) - {
                'input_ids', 'attention_mask', 'token_type_ids'}:
            raise ValueError('unsupported_model_inputs')
        self.outputs = self.session.get_outputs()
        self.inference_runs, self._proof, self.profile_path = 0, None, None
        self.fingerprint = {'model_id': spec['id'], 'repo': spec['repo'], 'revision': spec['revision'],
                            'files': self.verified_files, 'runtime': 'onnxruntime-directml',
                            'runtime_version': ort.__version__, 'provider': 'DmlExecutionProvider',
                            'device_id': device_id, 'mem_pattern': False, 'execution_mode': 'sequential',
                            'padding_mode': 'fixed_max_length',
                            'spec_sha256': hashlib.sha256(json.dumps(spec, sort_keys=True,
                                                                     separators=(',', ':')).encode()).hexdigest()}

    def _feed(self, texts, *, max_length, pair=False):
        import numpy as np
        if type(max_length) is not int or not 8 <= max_length <= 512:
            raise ValueError('invalid_model_sequence_length')
        self.tokenizer.enable_truncation(max_length=max_length,
                                         strategy='only_second' if pair else 'longest_first')
        # Fixed shapes avoid compiling/caching a different DirectML graph for each
        # batch's longest document. Attention masks still exclude padding from pooling.
        self.tokenizer.enable_padding(length=max_length, pad_id=self.pad_id, pad_token=self.pad_token)
        encoded = self.tokenizer.encode_batch(texts)
        retained = sum(sum(item.attention_mask) for item in encoded)
        self.last_batch_telemetry = {
            'items': len(encoded),
            'truncated_items': sum(bool(item.overflowing) for item in encoded),
            'retained_tokens': retained,
            'padded_tokens': sum(len(item.ids) for item in encoded) - retained,
        }
        rows = {'input_ids': [item.ids for item in encoded],
                'attention_mask': [item.attention_mask for item in encoded],
                'token_type_ids': [item.type_ids for item in encoded]}
        feed = {}
        for name, type_name in self.inputs.items():
            if type_name not in ('tensor(int64)', 'tensor(int32)'):
                raise ValueError('unsupported_model_input_dtype')
            feed[name] = np.asarray(rows[name], dtype=np.int64 if type_name == 'tensor(int64)' else np.int32)
        return feed

    def _reset_token_telemetry(self, max_length, *, pair=False):
        if type(max_length) is not int or not 8 <= max_length <= 512:
            raise ValueError('invalid_model_sequence_length')
        self.last_call_telemetry = {
            'items': 0, 'truncated_items': 0, 'retained_tokens': 0, 'padded_tokens': 0,
            'max_length': max_length, 'padding_mode': 'fixed_max_length',
            'truncation_strategy': 'only_second' if pair else 'longest_first',
            'measurement': 'Tokenizer overflow metadata; retained tokens include special and prefix tokens.',
        }

    def _record_token_batch(self):
        for key in ('items', 'truncated_items', 'retained_tokens', 'padded_tokens'):
            self.last_call_telemetry[key] += self.last_batch_telemetry[key]

    def _run(self, feed):
        if 'DmlExecutionProvider' not in self.session.get_providers():
            raise RuntimeError('directml_provider_lost')
        output = self.session.run(None, feed)
        self.inference_runs += 1
        return output

    def _progress(self, completed, total):
        if self.progress:
            self.progress({'model_id': self.spec['id'], 'completed': completed, 'total': total})

    def prove_gpu_execution(self):
        """Cache proof from first actual calls; further calls retain the same session."""
        if self._proof is not None:
            return dict(self._proof)
        if not self.inference_runs:
            raise RuntimeError('gpu_proof_requires_completed_inference')
        # Ending profiling does not close the session or disable subsequent inference.
        self.profile_path = Path(self.session.end_profiling())
        profile = json.loads(self.profile_path.read_text(encoding='utf-8'))
        evidence = inspect_profile(profile)
        evidence.update(profile_sha256=_sha256(self.profile_path),
                        inference_calls_before_profile_end=self.inference_runs,
                        model_id=self.spec['id'], device_id=self.fingerprint['device_id'])
        self._proof = evidence
        return dict(evidence)


class OnnxTextEncoder(_DirectMLModel):
    def __init__(self, directory, spec, *, device_id=0, progress=None):
        if spec.get('purpose') != 'embed' or spec.get('pooling') != 'attention_mask_mean_l2':
            raise ValueError('embedding_model_spec_required')
        super().__init__(directory, spec, device_id=device_id, progress=progress)
        candidates = [i for i, item in enumerate(self.outputs) if len(item.shape) == 3]
        if not candidates:
            raise ValueError('model_token_embeddings_required')
        self.output_index = next((i for i in candidates if self.outputs[i].name == 'last_hidden_state'), candidates[0])

    def encode(self, texts, *, query=False, max_length=256, batch_size=8):
        import numpy as np
        if type(batch_size) is not int or batch_size < 1 or not isinstance(query, bool):
            raise ValueError('invalid_embedding_batch')
        texts = list(texts)
        if any(not isinstance(text, str) for text in texts):
            raise ValueError('embedding_texts_must_be_strings')
        self._reset_token_telemetry(max_length)
        if not texts:
            return np.empty((0, self.spec['dimension']), dtype=np.float32)
        prefix = self.spec['query_prefix'] if query else self.spec['passage_prefix']
        results = []
        for offset in range(0, len(texts), batch_size):
            batch = [prefix + text for text in texts[offset:offset + batch_size]]
            feed = self._feed(batch, max_length=max_length)
            hidden = self._run(feed)[self.output_index]
            vectors = mean_pool_normalize(hidden, feed['attention_mask'])
            if vectors.shape != (len(batch), self.spec['dimension']):
                raise ValueError('embedding_dimension_mismatch')
            results.append(vectors)
            self._record_token_batch()
            self._progress(offset + len(batch), len(texts))
        return np.concatenate(results, axis=0)


class OnnxCrossEncoder(_DirectMLModel):
    def __init__(self, directory, spec, *, device_id=0, progress=None):
        if spec.get('purpose') != 'rerank' or spec.get('pooling') != 'single_raw_logit':
            raise ValueError('reranker_model_spec_required')
        super().__init__(directory, spec, device_id=device_id, progress=progress)
        self.output_index = next((i for i, item in enumerate(self.outputs) if item.name == 'logits'), 0)

    def score(self, query, document_texts, *, max_length=512, batch_size=8):
        import numpy as np
        if not isinstance(query, str) or type(batch_size) is not int or batch_size < 1:
            raise ValueError('invalid_reranker_batch')
        documents = list(document_texts)
        if any(not isinstance(text, str) for text in documents):
            raise ValueError('reranker_texts_must_be_strings')
        self._reset_token_telemetry(max_length, pair=True)
        results = []
        for offset in range(0, len(documents), batch_size):
            batch = documents[offset:offset + batch_size]
            feed = self._feed([(query, text) for text in batch], max_length=max_length, pair=True)
            logits = np.asarray(self._run(feed)[self.output_index], dtype=np.float32)
            if logits.shape not in ((len(batch),), (len(batch), 1)) or not np.isfinite(logits).all():
                raise ValueError('invalid_cross_encoder_logits')
            results.extend(float(value) for value in logits.reshape(-1))
            self._record_token_batch()
            self._progress(offset + len(batch), len(documents))
        return results
