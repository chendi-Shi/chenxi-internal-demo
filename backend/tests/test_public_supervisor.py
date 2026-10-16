import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarks'))

from supervise_public_campaign import alive, classify_exit


def test_partial_budget_is_not_reported_complete_or_failed():
    assert classify_exit('gpu_experiments', 2, {'status': 'budget_exhausted'}) == 'budget_exhausted'
    assert classify_exit('gpu_experiments', 2, {'status': 'incomplete_stopped'}) == 'incomplete_stopped'
    assert classify_exit('gpu_experiments', 2, {'status': 'completed'}) == 'failed'
    assert classify_exit('gpu_experiments', 2) == 'failed'
    assert classify_exit('gpu_pilot', 2, {'status': 'budget_exhausted'}) == 'failed'
    assert classify_exit('gpu_experiments', 0) == 'completed'


def test_current_process_is_alive_for_single_writer_lock():
    assert alive(os.getpid())
