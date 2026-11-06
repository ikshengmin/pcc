"""Ready work must progress while bounded local queues overflow.

This exercises the persistent carrier through the public virtual-thread API.
It does not prescribe an execution order: a homogeneous yielding cohort must
all enter before a few hot tasks consume most of the cohort's work.
"""

import os
import subprocess
import sys

import pytest


pytestmark = pytest.mark.xdist_group(name="vthread_ready_fairness")

PROGRAM = '''import gc
import sys
import threading
import pcc.virtual_thread as vt
from pcc.extern import c_int64, extern
pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
clock = extern("pcc_platform_monotonic_us", (), c_int64)
guard = threading.Lock()
handles = []
counts = [0, 0, 0, 0, 0]
jobs = 400

def worker():
    for turn in range(64):
        guard.acquire()
        counts[0] += 1
        if turn == 0:
            counts[1] += 1
            if counts[1] == jobs:
                counts[2] = counts[0]
        guard.release()
        vt.yield_now()
    guard.acquire()
    counts[3] += 1
    guard.release()

def producer():
    for index in range(jobs):
        handles.append(vt.spawn(worker))
    # Another carrier can run children while this producer is still creating
    # the cohort. Measure waiting only after every task has been submitted.
    guard.acquire()
    counts[4] = counts[0]
    guard.release()
    gc.collect()

def main():
    carriers = int(sys.argv[1])
    assert vt.carrier_pool_start(carriers) == carriers
    try:
        parent = vt.spawn(producer)
        deadline = clock() + 15000000
        while True:
            guard.acquire()
            finished = counts[3]
            guard.release()
            if finished == jobs:
                break
            if clock() >= deadline:
                raise RuntimeError("ready cohort did not finish")
            pause(1000000)
        assert vt.outcome(parent) == vt.OUTCOME_RETURNED
        for handle in handles:
            while vt.outcome(handle) == vt.OUTCOME_PENDING:
                assert clock() < deadline
                pause(1000000)
            assert vt.outcome(handle) == vt.OUTCOME_RETURNED
        print("READY_COHORT", counts[1], counts[2], counts[0], counts[4])
        assert counts[1] == jobs and counts[0] == jobs * 64
    finally:
        assert vt.carrier_pool_stop() == carriers
main()
'''


@pytest.fixture(scope="module")
def fairness_binary(tmp_path_factory):
    root = tmp_path_factory.mktemp("vthread-ready-fairness")
    source = root / "fairness.py"
    source.write_text(PROGRAM)
    binary = root / "fairness"
    result = subprocess.run(
        [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PCC_NO_AUTO_PCC1="1", PCC_WITH_THREADS="1"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


def _run_cohort(fairness_binary, collector, carriers):
    result = subprocess.run(
        [str(fairness_binary), str(carriers)], capture_output=True, text=True,
        timeout=20, env=dict(os.environ, PCC_GC_BACKEND=str(collector)),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("READY_COHORT 400 ")
    assert result.stderr == ""
    return [int(value) for value in result.stdout.split()[1:]]


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
def test_one_carrier_does_not_starve_ready_local_or_overflow_work(fairness_binary, collector):
    started, first_all, steps, published_at = _run_cohort(fairness_binary, collector, 1)
    assert started == 400 and steps == 400 * 64
    assert first_all - published_at <= 400 * 3


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
def test_two_carriers_preserve_every_cohort_owner(fairness_binary, collector):
    # Steps on another CPU do not measure a queued task's scheduler turns:
    # one CPU can keep running while another is descheduled or collecting.
    # Verify bounded completion and exactly-once execution on this topology.
    started, _first_all, steps, _published_at = _run_cohort(fairness_binary, collector, 2)
    assert started == 400 and steps == 400 * 64
