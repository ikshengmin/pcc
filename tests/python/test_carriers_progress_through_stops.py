"""Carriers keep making progress through stops on every collector.

A scheduler-lock holder and an idle carrier are no-park: the holder may not
park while carriers need the lock, and the idle carrier waits on the
scheduler condition under that lock. A stop therefore waits for both to
leave the lock. This program keeps carriers idle on timers, wakes them from
channels and timers inside scheduler critical sections, starts and stops
the pool (each wakes every idle carrier), and stops the world from the main
thread and from collections the carriers' own allocations start.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import gc
import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64

pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)


def sleeper(index):
    total = 0
    step = 0
    while step < 12:
        vt.sleep_current(1)
        total += len([index, step, {"k": (step,)}])
        step += 1
    return total


def churner(index):
    total = 0
    step = 0
    while step < 300:
        box = {"n": [index, step], "t": (str(step), step)}
        box["self"] = [box]
        total += len(box["n"])
        if step % 32 == 0:
            vt.yield_now()
        step += 1
    return total


def producer(sender, count):
    value = 0
    while value < count:
        assert vt.send(sender, [value])
        if value % 8 == 0:
            vt.sleep_current(1)
        value += 1
    vt.close_sender(sender)
    return count


def consumer(receiver):
    total = 0
    received = vt.recv(receiver)
    while received[0] == vt.RECV_VALUE:
        total += received[1][0]
        received = vt.recv(receiver)
    assert received[0] == vt.RECV_SENDER_CLOSED
    return total


def run_round():
    tasks = []
    index = 0
    while index < 8:
        tasks.append(vt.spawn(sleeper, index))
        tasks.append(vt.spawn(churner, index))
        index += 1
    channel = vt.mpsc(4)
    tasks.append(vt.spawn(consumer, channel[1]))
    tasks.append(vt.spawn(producer, channel[0], 64))
    assert vt.carrier_pool_start(4) == 4
    results = []
    try:
        for task in tasks:
            while vt.outcome(task) == vt.OUTCOME_PENDING:
                gc.collect()
                pause(200000)
            assert vt.exception(task) is None
            results.append(vt.result(task))
    finally:
        assert vt.carrier_pool_stop() == 4
    return results


def main():
    # Each round starts and stops the pool: both wake every idle carrier.
    round_index = 0
    while round_index < 4:
        print(run_round())
        round_index += 1


main()
'''

# sleeper(i) = 12 * 3, churner(i) = 300 * 2, consumer = sum(range(64)).
EXPECTED = (str([36, 600] * 8 + [2016, 64]) + "\n") * 4


@pytest.mark.integration
def test_carriers_progress_through_stops_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "carrier_stops.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "carrier_stops"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for run in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, run)
