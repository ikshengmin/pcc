"""pcc_mutex_lock retries on the CPU, polls every failure, then yields.

The kernel source runs under CPython with a scripted trylock, so each policy
step is observable: every EBUSY result services a pending stop before the next
attempt (a waiter must never delay a stop-the-world), the first 1024 failures
retry without leaving the CPU, and every later failure yields as before.
Native contended execution is covered by the threaded runtime programs.

The budget was 64: under four handler workers most scheduler-lock waits
outlasted it, and the yields (a system call each) took about 7% of worker
time. A failed trylock on a held mutex only reads the lock word, so a longer
retry does not take the line from the holder.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
_KERNEL = ast.parse((ROOT / "pcc/runtime/py/freestanding_thread_kernel_pthread.py").read_text())
_KERNEL.body = [node for node in _KERNEL.body
                if isinstance(node, ast.FunctionDef) and node.name == "pcc_mutex_lock"]
_LOCK_CODE = next(
    const for const in compile(_KERNEL, "freestanding_thread_kernel_pthread.py", "exec").co_consts
    if isinstance(const, types.CodeType) and const.co_name == "pcc_mutex_lock"
)
EBUSY = 16


def _lock(namespace):
    return types.FunctionType(_LOCK_CODE, {"ptr_is_null": lambda p: p is None, **namespace})


def _run(results):
    events = []
    script = iter(results)

    def trylock(mutex):
        events.append("try")
        return next(script)

    lock = _lock({
        "pthread_mutex_trylock": trylock,
        "pcc_thread_safepoint": lambda: events.append("poll"),
        "sched_yield": lambda: events.append("yield"),
    })
    return lock(object()), events


BUDGET = 1024


@pytest.mark.parametrize("failures", [0, 1, 63, 64, 65, BUDGET - 1, BUDGET, BUDGET + 1, BUDGET + 6])
def test_contended_acquire_polls_each_failure_and_yields_past_budget(failures):
    status, events = _run([EBUSY] * failures + [0])
    assert status == 0
    assert events.count("try") == failures + 1
    assert events.count("poll") == failures
    assert events.count("yield") == max(0, failures - BUDGET)
    # Each failed attempt is followed by its poll before any yield or retry.
    for index, event in enumerate(events[:-1]):
        if event == "try":
            assert events[index + 1] == "poll"


def test_unexpected_trylock_error_fails_without_waiting():
    status, events = _run([EBUSY, 22])
    assert status == -1
    assert events == ["try", "poll", "try"]


def test_null_mutex_is_rejected():
    assert _lock({})(None) == -1
