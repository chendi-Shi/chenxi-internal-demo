"""Keep the local benchmark progressing after the interactive chat returns.

Run in a hidden/background process on Windows. Child logs and status are local.
This does not change permanent power settings, install software, or publish data.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parent


class CampaignIncomplete(Exception):
    """A worker saved valid checkpoints but intentionally did not finish."""


def classify_exit(name, returncode, worker_state=None):
    if returncode == 0:
        return 'completed'
    if name == 'gpu_experiments' and returncode == 2:
        status = (worker_state or {}).get('status')
        if status in {'budget_exhausted', 'incomplete_stopped'}:
            return status
    return 'failed'


def sample_gpu(output):
    """Best-effort whole-device telemetry, never presented as process-only usage."""
    try:
        result = subprocess.run([
            'nvidia-smi',
            '--query-gpu=index,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw',
            '--format=csv,noheader,nounits',
        ], capture_output=True, text=True, timeout=8, check=True)
        record = {'sampled_at_utc': datetime.now(UTC).isoformat(),
                  'scope': 'whole_device_including_other_processes',
                  'columns': ['index', 'memory_used_mib', 'memory_total_mib',
                              'utilization_percent', 'temperature_c', 'power_w'],
                  'rows': [line.strip().split(', ') for line in result.stdout.splitlines() if line.strip()]}
        with (output / 'gpu-telemetry.local.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record) + '\n')
    except (OSError, subprocess.SubprocessError):
        # Profiling evidence in each model remains the authoritative GPU check.
        return False
    return True


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--wait-pids', type=int, nargs='*', default=[])
    parser.add_argument('--budget-hours', type=float, default=48)
    parser.add_argument('--preparation-timeout-hours', type=float, default=12)
    args = parser.parse_args()
    if not 0 < args.budget_hours <= 48 or not 0 < args.preparation_timeout_hours <= 24:
        parser.error('positive budget <=48 hours and preparation timeout <=24 hours required')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / 'supervisor.lock'
    # A stopped process may leave its lock; only reclaim it after checking its PID.
    if lock_path.exists():
        old = int(lock_path.read_text())
        if alive(old):
            raise RuntimeError('campaign_supervisor_already_running')
        lock_path.unlink()
    with lock_path.open('x') as handle:
        handle.write(str(os.getpid()))
    state = {'pid': os.getpid(), 'phase': 'starting', 'started_at_utc': datetime.now(UTC).isoformat(),
             'gpu_budget_hours': args.budget_hours, 'completed_steps': []}

    def save(phase, **values):
        state.update(phase=phase, updated_at_utc=datetime.now(UTC).isoformat(), **values)
        temporary = output / 'campaign-status.tmp'
        temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
        retry_deadline = time.monotonic() + 5
        while True:
            try:
                temporary.replace(output / 'campaign-status.local.json')
                break
            except PermissionError:
                if time.monotonic() >= retry_deadline:
                    raise
                time.sleep(0.05)
        print(json.dumps(state), flush=True)

    def child(name, arguments):
        save(name)
        gpu_deadline = None
        if name == 'gpu_experiments':
            worker_started = time.time()
            prior_state = output / 'run' / 'run_state.json'
            if prior_state.exists():
                worker_started = json.loads(prior_state.read_text(encoding='utf-8'))['started_epoch']
            remaining = args.budget_hours * 3600 - (time.time() - worker_started)
            # The worker stops at batch boundaries; this catches a hung runtime call.
            gpu_deadline = time.monotonic() + max(0, remaining) + 300
        with (output / f'{name}.stdout.log').open('a', encoding='utf-8') as stdout, \
             (output / f'{name}.stderr.log').open('a', encoding='utf-8') as stderr:
            process = subprocess.Popen([sys.executable, '-u', *map(str, arguments)],
                                       stdout=stdout, stderr=stderr)
            save(name, child_pid=process.pid)
            try:
                while process.poll() is None:
                    if (output / 'STOP').exists():
                        raise CampaignIncomplete('incomplete_stopped')
                    if name in {'prepare_models', 'prepare_datasets'} and time.monotonic() >= deadline:
                        raise TimeoutError('preparation_timeout')
                    if gpu_deadline is not None and time.monotonic() >= gpu_deadline:
                        raise CampaignIncomplete('incomplete_deadline')
                    if name in {'gpu_pilot', 'gpu_experiments'}:
                        sample_gpu(output)
                    time.sleep(15)
                returncode = process.returncode
            except BaseException:
                if process.poll() is None:
                    if name == 'gpu_experiments':
                        (output / 'run' / 'STOP').touch()
                        try:
                            process.wait(timeout=60)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
        worker_state = None
        state_file = output / 'run' / 'run_state.json'
        if name == 'gpu_experiments' and state_file.exists():
            worker_state = json.loads(state_file.read_text(encoding='utf-8'))
        outcome = classify_exit(name, returncode, worker_state)
        if outcome in {'budget_exhausted', 'incomplete_stopped'}:
            save(outcome, incomplete_step=name, returncode=returncode, child_pid=None)
            raise CampaignIncomplete(outcome)
        if outcome == 'failed':
            save('failed', failed_step=name, returncode=returncode, child_pid=None)
            raise RuntimeError(f'campaign_step_failed:{name}:{returncode}')
        state['completed_steps'].append(name)
        save(name + '_complete', child_pid=None)

    try:
        if os.name == 'nt':
            # Temporary execution request: released in finally, never a power-plan edit.
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        save('waiting_for_preparation', waiting_pids=args.wait_pids)
        deadline = time.monotonic() + args.preparation_timeout_hours * 3600
        while any(alive(pid) for pid in args.wait_pids):
            if (output / 'STOP').exists():
                raise CampaignIncomplete('incomplete_stopped')
            if time.monotonic() >= deadline:
                raise TimeoutError('preparation_timeout')
            time.sleep(15)
        child('runtime_check', [BENCHMARKS / 'check_gpu_runtime.py', '--output',
                                output / 'gpu-runtime.local.json'])
        child('prepare_models', [BENCHMARKS / 'prepare_public_models.py', '--root', args.models_dir])
        child('prepare_datasets', [BENCHMARKS / 'public_benchmark_data.py', 'all',
                                  '--output-root', args.data_dir])
        base = [BENCHMARKS / 'run_public_gpu_ablation.py', '--models-dir', args.models_dir,
                '--data-dir', args.data_dir, '--output-dir', output / 'run',
                '--budget-hours', args.budget_hours]
        child('gpu_pilot', [*base, '--pilot'])
        child('gpu_experiments', base)
        save('completed', child_pid=None)
        return 0
    except CampaignIncomplete as reason:
        if state['phase'] not in {'budget_exhausted', 'incomplete_stopped'}:
            save(str(reason), child_pid=None)
        return 2
    except BaseException as error:
        if state['phase'] != 'failed':
            save('failed', error_type=type(error).__name__, reason=str(error))
        raise
    finally:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        if lock_path.exists() and lock_path.read_text() == str(os.getpid()):
            lock_path.unlink()


if __name__ == '__main__':
    raise SystemExit(main())
