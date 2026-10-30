"""A virtual thread's cancel request and state are read without the scheduler lock.

Every resume asked twice whether a cancel was requested and once for the
state, each under the scheduler mutex: at four workers waiting for that
mutex (spinning, trylock, yields and the safepoint polls in its spin) was
about 10% of carrier time. Each is one word written under the lock; a
racing request is seen at the next check either way, and acting on it
(closing the generator, completing the cancel, unparking) takes the lock
again. One acquire load answers instead.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import types

import pytest

from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py/py_virtual_thread_runtime.py"
NAMES = ("py_virtual_thread_cancel_requested", "py_virtual_thread_state")


def _readers(words, locked):
    tree = ast.parse(RUNTIME.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    assert sorted(node.name for node in nodes) == sorted(NAMES)
    for node in nodes:
        node.decorator_list = []

    def atomic_load_i64(base, offset, order):
        assert order == "acquire"
        return words[(base, offset)]

    namespace = {
        "_checked": lambda vthread: vthread,
        "ptr_is_null": lambda pointer: pointer is None,
        "atomic_load_i64": atomic_load_i64,
        "load_i64": lambda base, offset: words[(base, offset)],
        "_scheduler_lock": lambda: locked.append("lock") or 0,
        "_scheduler_unlock": lambda: locked.append("unlock"),
    }
    code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace


@pytest.mark.parametrize("requested,state", [(0, 3), (1, 2), (2, 1), (0, 4)])
def test_reads_take_one_acquire_load_and_no_scheduler_lock(requested, state):
    locked = []
    vthread = object()
    ns = _readers({(vthread, 128): requested, (vthread, 32): state}, locked)
    assert ns["py_virtual_thread_cancel_requested"](vthread) == requested
    assert ns["py_virtual_thread_state"](vthread) == state
    assert locked == []
    assert ns["py_virtual_thread_cancel_requested"](None) == -1
    assert ns["py_virtual_thread_state"](None) == -1


PROGRAM = r'''
import pcc.virtual_thread as vt


def worker(seed):
    total = 0
    step = 0
    while step < 400:
        total += seed + step
        if step % 7 == 0:
            vt.yield_now()
        if step % 50 == 25:
            vt.sleep_current(0)
        step += 1
    return total


def canceller(targets):
    accepted = 0
    index = 0
    while index < len(targets):
        if vt.cancel(targets[index]):
            accepted += 1
        vt.yield_now()
        index += 2
    return accepted


def main():
    rounds = 0
    summary = []
    while rounds < 6:
        workers = [vt.spawn(worker, seed) for seed in range(24)]
        stopper = vt.spawn(canceller, workers)
        vt.run(4, 10000000)
        outcomes = [vt.outcome(thread) for thread in workers]
        states = set(vt.state(thread) for thread in workers)
        returned = [vt.result(thread) for thread, outcome in zip(workers, outcomes)
                    if outcome == vt.OUTCOME_RETURNED]
        expected = [seed * 400 + 79800 for seed, outcome in zip(range(24), outcomes)
                    if outcome == vt.OUTCOME_RETURNED]
        cancelled = sum(1 for outcome in outcomes if outcome == vt.OUTCOME_CANCELLED)
        odd_untouched = all(outcomes[index] == vt.OUTCOME_RETURNED for index in range(1, 24, 2))
        summary.append((returned == expected, odd_untouched,
                        cancelled + len(returned) == 24,
                        vt.result(stopper) >= cancelled, states == {4}))
        rounds += 1
    print(summary)


main()
'''


@pytest.mark.integration
def test_cancellation_and_state_on_four_carriers_and_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "cancel_state.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "cancel_state"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    expected = str([(True, True, True, True, True)] * 6) + "\n"
    for backend in range(5):
        for attempt in range(2):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, ""), (backend, attempt)
