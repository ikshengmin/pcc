"""Bounded persistent-carrier polling preserves progress, stop and error handling."""

import ast
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_worker(steps, ready_count, io_waits=0, io_ready=0):
    events = []
    state = {"steps": 0, "stop": 0, "carriers": 0, "failures": 0, "sleepers": 0}
    tls = {"pcc_current_virtual_thread_carrier_py": -1}

    def counter(key):
        if key == "pcc_vthread_persistent_pool_stop_py":
            return state["stop"]
        if key == "pcc_vthread_ready_count_py":
            return 0
        raise AssertionError(key)

    def add(key, value):
        if key == "pcc_vthread_carrier_count_py":
            state["carriers"] += value
        elif key in ("pcc_vthread_persistent_pool_failures_py", "pcc_vthread_carrier_failures_py"):
            state["failures"] += value
        else:
            raise AssertionError(key)

    def step():
        result = steps[state["steps"]]
        state["steps"] += 1
        if state["steps"] == len(steps):
            state["stop"] = 1
        return result

    def timers():
        events.append(("timers", state["steps"]))

    def io(mode):
        assert mode == 0
        events.append(("io", state["steps"]))

    def run_once():
        timers()
        io(0)
        return step()

    def sleeper_count(operation, key, offset, delta, order):
        assert (operation, key, offset, order) == (
            "add", "pcc_vthread_sleepers_py", 0, "seq_cst",
        )
        before = state["sleepers"]
        state["sleepers"] += delta
        assert state["sleepers"] >= 0
        return before

    def load_atomic(key, offset, order):
        assert offset == 0 and order == "acquire"
        value = {
            "pcc_vthread_io_count_py": io_waits,
            "pcc_vthread_io_ready_pending_py": io_ready,
        }[key]
        return value(state["steps"]) if callable(value) else value

    ns = {
        "atomic_rmw_i64": sleeper_count,
        "atomic_fence": lambda order: None,
        "atomic_load_i64": load_atomic,
        "load_i64": lambda arg, offset: 1,
        "load_i32": lambda key, offset: tls[key],
        "store_i32": lambda key, offset, value: tls.__setitem__(key, value),
        "global_addr": lambda key: key,
        "_counter": counter, "_counter_add": add,
        "py_virtual_thread_run_once": run_once, "_run_ready_step": step,
        "py_virtual_thread_poll_timers": timers, "py_virtual_thread_poll_io": io,
        "py_virtual_thread_ready_count": lambda: ready_count,
        "_scheduler_lock": lambda: 0, "_scheduler_unlock": lambda: None,
        "_scheduler_cond": lambda: 1, "_scheduler_mutex": lambda: 2,
        "pcc_cond_timedwait_ms": lambda *args: events.append(("wait", state["steps"])),
        "pcc_thread_safepoint": lambda: events.append(("safepoint", state["steps"])),
        "null": lambda: 0,
    }
    path = ROOT / "pcc/runtime/py/py_virtual_thread_runtime.py"
    names = {"_persistent_carrier_worker", "_carrier_wait_locked"}
    nodes = [n for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {node.name for node in nodes} == names
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    ns["_persistent_carrier_worker"](0)
    assert state["carriers"] == 0
    assert state["sleepers"] == 0
    assert tls["pcc_current_virtual_thread_carrier_py"] == -1
    return events, state


def test_full_ready_queue_bounds_timer_and_io_poll_delay():
    events, state = run_worker([1] * 130, 1000)
    assert [i for event, i in events if event == "timers"] == [0, 64, 128]
    assert [i for event, i in events if event == "io"] == [0, 64, 128]
    assert state["steps"] == 130 and state["failures"] == 0


def test_stop_is_observed_after_each_step():
    events, state = run_worker([1], 1000)
    assert state["steps"] == 1
    assert events == [("timers", 0), ("io", 0)]


@pytest.mark.parametrize("io_waits,io_ready", [(1, 0), (0, 1), (512, 0), (512, 1)])
def test_pending_or_readied_io_is_repolled_within_eight_ready_steps(io_waits, io_ready):
    events, state = run_worker([1] * 130, 1000, io_waits, io_ready)
    polls = [step for event, step in events if event == "io"]
    assert polls[0] == 0
    assert max(after - before for before, after in zip(polls, polls[1:])) <= 8
    assert state["steps"] - polls[-1] <= 8
    assert state["steps"] == 130 and state["failures"] == 0


def test_new_fd_wait_interrupts_an_already_selected_compute_batch():
    events, state = run_worker([1] * 10, 1000, io_waits=lambda steps: steps >= 1)
    assert [step for event, step in events if event == "io"] == list(range(10))
    assert state["steps"] == 10 and state["failures"] == 0


@pytest.mark.parametrize("io_waits", [1, 512])
def test_live_fd_waits_are_observed_between_ready_steps(io_waits):
    events, _state = run_worker([1] * 10, 1000, io_waits=io_waits)
    assert [step for event, step in events if event == "io"] == list(range(10))


def test_already_readied_io_keeps_batching_when_no_fd_waits_remain():
    events, _state = run_worker([1] * 20, 1000, io_ready=1)
    assert [step for event, step in events if event == "io"] == [0, 8, 16]


def test_stolen_ready_batch_repolls_after_idle_wakeup():
    events, state = run_worker([0, 1], 1000)
    assert events == [("timers", 0), ("io", 0), ("wait", 1),
                      ("safepoint", 1), ("timers", 1), ("io", 1)]
    assert state["steps"] == 2


def test_failed_step_stops_and_counts_both_failure_metrics():
    events, state = run_worker([-1, 1], 1000)
    assert state["steps"] == 1 and state["failures"] == 2
    assert events == [("timers", 0), ("io", 0)]


PROGRAM = '''import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64
idle = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
gate = [False]
def ready_work():
    while not gate[0]:
        vt.yield_now()
    return 7
def timed_work():
    vt.sleep_current(2)
    gate[0] = True
    return 9
def main():
    tasks = []
    for i in range(96):
        tasks.append(vt.spawn(ready_work))
    tasks.append(vt.spawn(timed_work))
    assert vt.carrier_pool_start(4) == 4
    total = 0
    try:
        for task in tasks:
            while vt.outcome(task) == vt.OUTCOME_PENDING:
                idle(100000)
            assert vt.exception(task) is None
            total += vt.result(task)
    finally:
        assert vt.carrier_pool_stop() == 4
    assert total == 681
    print("READY_BATCH_OK")
main()
'''


def test_native_persistent_carriers_poll_timer_under_ready_churn(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python
    source = tmp_path / "ready_batch.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "ready_batch"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (0, "READY_BATCH_OK\n", ""), backend
