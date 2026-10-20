"""Actual DirectML hardware smoke check; this is not a retrieval/model benchmark."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from gpu_onnx import inspect_profile


def _varint(value):
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _integer(field, value):
    return _varint(field << 3) + _varint(value)


def _message(field, value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def matmul_relu_model(size=512):
    """Serialize the tiny fixed ONNX schema without downloading the onnx package.

    Field numbers are the public ONNX Model/Graph/Node/ValueInfo/Type definitions:
    https://github.com/onnx/onnx/blob/v1.17.0/onnx/onnx.proto
    There are no trained weights, external data, custom operators or Python code.
    """
    if type(size) is not int or not 1 <= size <= 2048:
        raise ValueError('invalid_test_matrix_size')
    shape = _message(1, _integer(1, size)) * 2
    tensor_type = _integer(1, 1) + _message(2, shape)  # FLOAT + shape
    def value(name):
        return _message(1, name) + _message(2, _message(1, tensor_type))
    matmul = (_message(1, 'A') + _message(1, 'B') + _message(2, 'Z') +
              _message(3, 'SyntheticMatMul') + _message(4, 'MatMul'))
    relu = (_message(1, 'Z') + _message(2, 'Y') + _message(3, 'SyntheticRelu') +
            _message(4, 'Relu'))
    graph = (_message(1, matmul) + _message(1, relu) + _message(2, 'synthetic_gpu_runtime_check') +
             _message(11, value('A')) + _message(11, value('B')) + _message(12, value('Y')))
    return (_integer(1, 8) + _message(2, 'chenxi-runtime-check') + _message(7, graph) +
            _message(8, _integer(2, 13)))  # IR 8, standard ONNX opset 13


def nvidia_inventory():
    binary = shutil.which('nvidia-smi')
    if not binary:
        return {'status': 'unavailable'}
    try:
        process = subprocess.run([binary,
            '--query-gpu=index,name,driver_version,memory.total,memory.used,utilization.gpu',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
        values = []
        for row in csv.reader(process.stdout.splitlines()):
            if len(row) != 6:
                raise ValueError('unexpected_nvidia_smi_columns')
            values.append(dict(zip(('index', 'name', 'driver_version', 'memory_total_mib',
                                    'memory_used_mib', 'utilization_percent'),
                                   (item.strip() for item in row), strict=True)))
        return {'status': 'available', 'adapters': values}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'status': 'unavailable'}


def check_runtime(output_path, *, device_id=0):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'dataset_kind': 'synthetic_runtime_smoke_only', 'measured_at_utc': datetime.now(UTC).isoformat(),
              'graph': 'Y = Relu(MatMul(A, B)); float32 [512,512]', 'dml_device_id': device_id,
              'limitations': ['This validates the runtime on a synthetic graph, not embedding/reranker graph '
                              'compatibility or retrieval quality; each real model requires separate GPU proof.',
                              'NVIDIA inventory is not an explicit DXGI adapter mapping. Confirm device_id '
                              'if the machine has multiple display adapters.',
                              'A brief test may finish between utilization samples; executed DML nodes '
                              'provide the dispatch evidence.']}
    try:
        import numpy as np
        import onnxruntime as ort
        if type(device_id) is not int or device_id < 0:
            raise ValueError('invalid_dml_device_id')
        report.update(nvidia_before=nvidia_inventory(), runtime_version=ort.__version__,
                      available_providers=ort.get_available_providers(), numpy_version=np.__version__)
        if 'DmlExecutionProvider' not in ort.get_available_providers():
            raise RuntimeError('directml_provider_unavailable')
        model = matmul_relu_model()
        report['graph_sha256'] = hashlib.sha256(model).hexdigest()
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_profiling = True
        options.profile_file_prefix = str(output_path.parent / 'synthetic-dml-profile')
        session = ort.InferenceSession(model, sess_options=options,
                                       providers=[('DmlExecutionProvider', {'device_id': device_id}),
                                                  'CPUExecutionProvider'])
        session.disable_fallback()
        if 'DmlExecutionProvider' not in session.get_providers():
            raise RuntimeError('directml_initialization_fell_back_to_cpu')
        random = np.random.default_rng(20261015)
        a = (random.standard_normal((512, 512)) * .1).astype(np.float32)
        b = (random.standard_normal((512, 512)) * .1).astype(np.float32)
        reference = np.maximum(a @ b, 0)
        durations = []
        for _ in range(3):
            start = time.perf_counter()
            actual = session.run(['Y'], {'A': a, 'B': b})[0]
            durations.append((time.perf_counter() - start) * 1000)
        error = np.abs(actual - reference)
        valid = bool(np.isfinite(actual).all() and np.allclose(actual, reference, rtol=2e-3, atol=1e-4))
        profile_path = Path(session.end_profiling())
        proof = inspect_profile(json.loads(profile_path.read_text(encoding='utf-8')))
        proof['profile_sha256'] = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        report.update(status='passed' if valid else 'failed', numerical_match=valid,
                      max_absolute_error=float(error.max()), rms_error=float(np.sqrt(np.mean(error ** 2))),
                      tolerances={'rtol': 2e-3, 'atol': 1e-4},
                      first_call_ms=durations[0], subsequent_call_ms=durations[1:],
                      session_providers=session.get_providers(), gpu_proof=proof,
                      nvidia_after=nvidia_inventory())
    except ImportError as error:
        report.update(status='not_run', reason='missing_runtime_dependency', missing_module=error.name)
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, reason=str(error))
    temporary = output_path.with_name(output_path.name + '.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(output_path)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device-id', type=int, default=0)
    arguments = parser.parse_args()
    result = check_runtime(arguments.output, device_id=arguments.device_id)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
