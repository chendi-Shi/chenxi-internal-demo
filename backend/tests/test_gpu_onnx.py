"""Offline tests; fake profiles/providers are fixtures, never GPU measurement evidence."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarks'))
from gpu_onnx import (
    OnnxCrossEncoder,
    OnnxTextEncoder,
    _DirectMLModel,
    inspect_profile,
    mean_pool_normalize,
    verify_model_files,
)


def event(provider='DmlExecutionProvider', op='DmlFusedNode', name='fused_kernel_time'):
    return {'cat': 'Node', 'name': name, 'args': {'provider': provider, 'op_name': op}, 'dur': 1}


def token_fixture(model_class):
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    model = model_class.__new__(model_class)
    model.tokenizer = Tokenizer(models.WordLevel(
        {'[PAD]': 0, '[UNK]': 1, '[CLS]': 2, '[SEP]': 3, 'word': 4, 'query': 5}, unk_token='[UNK]'))
    model.tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    model.tokenizer.post_processor = processors.TemplateProcessing(
        single='[CLS] $A [SEP]', pair='[CLS] $A [SEP] $B:1 [SEP]:1',
        special_tokens=[('[CLS]', 2), ('[SEP]', 3)])
    model.pad_id, model.pad_token, model.progress, model.output_index = 0, '[PAD]', None, 0
    model.inputs = {'input_ids': 'tensor(int64)', 'attention_mask': 'tensor(int64)'}
    return model


class GpuOnnxTests(unittest.TestCase):
    def test_registration_or_cpu_events_are_not_gpu_evidence(self):
        for profile in ([], [{'providers': ['DmlExecutionProvider']}],
                        [event('CPUExecutionProvider', 'MatMul')],
                        [event(name='fused_fence_before')], [event(op='Shape')]):
            with self.assertRaises(RuntimeError):
                inspect_profile(profile)

    def test_profile_reports_actual_compute_and_cpu_fallback(self):
        result = inspect_profile([event(), event('CPUExecutionProvider', 'Shape')])
        self.assertEqual(result['dml_compute_events'], 1)
        self.assertEqual(result['cpu_ops'], {'Shape': 1})
        self.assertEqual(result['cpu_kernel_events'], 1)

    def test_file_verification_binds_model_and_tokenizer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'onnx').mkdir()
            files = []
            for name, data in [('onnx/model.onnx', b'test graph'), ('tokenizer.json', b'test tokenizer')]:
                (root / name).write_bytes(data)
                files.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            self.assertEqual(len(verify_model_files(root, {'files': files})), 2)
            (root / 'tokenizer.json').write_bytes(b'bad! tokenizer')
            with self.assertRaises(ValueError):
                verify_model_files(root, {'files': files})

    def test_file_verification_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                verify_model_files(temporary, {'files': [{'path': '../outside', 'size': 0, 'sha256': 'x'}]})

    def test_model_loader_rejects_cpu_only_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'onnx').mkdir()
            files = []
            for name in ('onnx/model.onnx', 'tokenizer.json'):
                (root / name).write_bytes(b'test')
                files.append({'local_path': name, 'size': 4, 'sha256': hashlib.sha256(b'test').hexdigest()})
            modules = {'onnxruntime': SimpleNamespace(get_available_providers=lambda: ['CPUExecutionProvider']),
                       'tokenizers': SimpleNamespace(Tokenizer=None)}
            with patch.dict(sys.modules, modules), self.assertRaisesRegex(RuntimeError, 'directml_provider_unavailable'):
                _DirectMLModel(root, {'files': files})

    def test_profile_proof_cached_and_session_stays_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'profile.json'
            path.write_text(json.dumps([event()]), encoding='utf-8')
            calls = []
            model = _DirectMLModel.__new__(_DirectMLModel)
            model.inference_runs, model._proof = 0, None
            model.spec, model.fingerprint = {'id': 'fake-test-model'}, {'device_id': 0}
            def finish():
                calls.append('end')
                return str(path)
            model.session = SimpleNamespace(end_profiling=finish,
                                             get_providers=lambda: ['DmlExecutionProvider'],
                                             run=lambda names, feed: ['next output'])
            with self.assertRaises(RuntimeError):
                model.prove_gpu_execution()
            model.inference_runs = 1
            self.assertEqual(model.prove_gpu_execution(), model.prove_gpu_execution())
            self.assertEqual(calls, ['end'])
            self.assertEqual(model._run({}), ['next output'])

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy runtime not installed')
    def test_masked_mean_pooling_and_l2_normalization(self):
        import numpy as np
        hidden = np.array([[[3, 0], [0, 4], [999, 999]]], dtype=np.float32)
        output = mean_pool_normalize(hidden, [[1, 1, 0]])
        np.testing.assert_allclose(output, [[.6, .8]], atol=1e-7)
        self.assertEqual(output.dtype, np.float32)

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy runtime not installed')
    def test_pooling_rejects_nonfinite_and_empty_rows(self):
        for hidden, mask in [([[[float('nan')]]], [[1]]), ([[[1]]], [[0]]), ([[[0]]], [[1]])]:
            with self.assertRaises(ValueError):
                mean_pool_normalize(hidden, mask)

    @unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('tokenizers'),
                         'NumPy/tokenizers runtime not installed')
    def test_fixed_padding_and_truncation_counts_across_embedding_batches(self):
        import numpy as np
        model = token_fixture(OnnxTextEncoder)
        model.spec = {'id': 'test', 'dimension': 2, 'query_prefix': '', 'passage_prefix': ''}
        seen = []
        def fake_run(feed):
            seen.append(feed['input_ids'].shape)
            return [np.ones((*feed['input_ids'].shape, 2), dtype=np.float32)]
        model._run = fake_run
        vectors = model.encode(['word', ' '.join(['word'] * 12), 'word word'], max_length=8, batch_size=2)
        self.assertEqual(vectors.shape, (3, 2))
        self.assertEqual(seen, [(2, 8), (1, 8)])
        self.assertEqual(model.last_call_telemetry['items'], 3)
        self.assertEqual(model.last_call_telemetry['truncated_items'], 1)
        self.assertEqual(model.last_call_telemetry['retained_tokens'], 15)
        self.assertEqual(model.last_call_telemetry['padded_tokens'], 9)
        model.encode([], max_length=8)
        self.assertEqual(model.last_call_telemetry['items'], 0)

    @unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('tokenizers'),
                         'NumPy/tokenizers runtime not installed')
    def test_pair_truncation_keeps_query_and_reports_overflow_without_retokenizing(self):
        import numpy as np
        model = token_fixture(OnnxCrossEncoder)
        model.spec = {'id': 'test'}
        seen = []
        def fake_run(feed):
            seen.append(feed['input_ids'].tolist())
            return [np.zeros((len(feed['input_ids']), 1), dtype=np.float32)]
        model._run = fake_run
        result = model.score('query query', ['word', ' '.join(['word'] * 12)], max_length=8, batch_size=1)
        self.assertEqual(result, [0.0, 0.0])
        self.assertTrue(all(row[0][:4] == [2, 5, 5, 3] for row in seen))
        self.assertEqual(model.last_call_telemetry['items'], 2)
        self.assertEqual(model.last_call_telemetry['truncated_items'], 1)
        self.assertEqual(model.last_call_telemetry['retained_tokens'], 14)
        self.assertEqual(model.last_call_telemetry['padded_tokens'], 2)
        self.assertEqual(model.last_call_telemetry['truncation_strategy'], 'only_second')


if __name__ == '__main__':
    unittest.main()
