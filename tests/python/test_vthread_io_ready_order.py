"""New readiness and old ready owners must both progress under I/O churn."""

import ast
import os
import subprocess

import pytest

from tests.python.test_virtual_thread_ready_entry_pool import _compile
from tests.python.test_vthread_ready_claims import Cell, Model, SOURCE


def io_model():
    model = Model()
    slots = {
        "pcc_vthread_io_ready_head_py": model.io_head,
        "pcc_vthread_io_ready_tail_py": model.io_tail,
        "pcc_vthread_io_ready_priority_py": Cell(),
        "pcc_vthread_io_ready_pending_py": Cell({0: 0}),
        "pcc_vthread_io_ready_turn_py": Cell({0: 0}),
        "pcc_vthread_ready_count_py": model.count,
    }
    model.namespace.update({
        "global_addr": slots.__getitem__,
        "global_load_ptr": lambda name: model.load(slots[name], 0),
        "global_store_ptr": lambda name, value: model.store(slots[name], 0, value),
        "store_i32": model.store,
        "_counter_set": lambda slot, value: model.store(slot, 0, value),
    })
    selected = []
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_io_ready_push", "_dequeue_io_ready",
        }:
            selected.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {
                    "READY_NODE_SIZE", "READY_NODE_PREVIOUS_OFFSET",
                    "READY_NODE_DEADLINE_OFFSET", "READY_NODE_NO_DEADLINE",
                }):
            selected.append(node)
    exec(compile(ast.Module(selected, type_ignores=[]), str(SOURCE), "exec"), model.namespace)
    return model, slots


def admit(model, deadline=None):
    thread = model.thread(state=3)
    node = model.node(thread)
    node.fields[model.namespace["READY_NODE_DEADLINE_OFFSET"]] = (
        model.namespace["READY_NODE_NO_DEADLINE"] if deadline is None else deadline
    )
    model.namespace["_ready_commit"](thread, node, 1)
    return thread


def take(model):
    thread = model.namespace["_dequeue_io_ready"]()
    if thread is not None:
        assert thread.references == 1
        model.retain(thread, -1)
    return thread


@pytest.mark.parametrize("count", [64, 256, 1024])
def test_new_readiness_and_oldest_owner_progress_without_draining_cohort(count):
    model, slots = io_model()
    old = [admit(model) for _ in range(count)]
    fresh = admit(model)
    first = [take(model), take(model)]
    assert fresh in first and old[0] in first
    all_threads = first + [take(model) for _ in range(count - 1)]
    assert len(set(all_threads)) == count + 1
    assert set(all_threads) == set(old + [fresh])
    assert take(model) is None
    assert model.count.fields[0] == 0
    assert slots["pcc_vthread_io_ready_pending_py"].fields[0] == 0
    assert len(model.released) == count + 1


def test_continuous_new_arrivals_do_not_starve_old_readiness():
    model, _slots = io_model()
    old = [admit(model) for _ in range(64)]
    serviced = []
    for _ in range(2 * len(old)):
        admit(model)
        serviced.append(take(model))
    assert set(old).issubset(serviced)
    assert len(set(serviced)) == len(serviced)


@pytest.mark.parametrize("count", [64, 256, 1024])
def test_short_deadline_owner_is_not_buried_inside_a_mixed_ready_batch(count):
    model, slots = io_model()
    old = [admit(model) for _ in range(count)]
    urgent = admit(model, 1000)
    later = [admit(model, 30000) for _ in range(count)]
    first = [take(model), take(model)]
    assert urgent in first and old[0] in first
    serviced = first + [take(model) for _ in range(2 * count - 1)]
    assert len(set(serviced)) == 2 * count + 1
    assert set(serviced) == set(old + [urgent] + later)
    assert take(model) is None
    assert slots["pcc_vthread_io_ready_priority_py"].fields[0] is None
    assert model.count.fields[0] == 0


def test_expired_priority_claim_does_not_retain_a_stale_node():
    model, slots = io_model()
    live = admit(model, 30000)
    cancelled = admit(model, 1000)
    cancelled.fields[32] = 4
    assert take(model) is live
    assert take(model) is None
    assert slots["pcc_vthread_io_ready_priority_py"].fields[0] is None
    assert model.count.fields[0] == 0


def test_stale_claims_at_both_ends_leave_one_live_owner():
    model, _slots = io_model()
    first, live, last = [admit(model) for _ in range(3)]
    first.fields[32] = last.fields[32] = 4
    assert take(model) is live
    assert take(model) is None
    assert model.io_head.fields[0] is None and model.io_tail.fields[0] is None
    assert model.count.fields[0] == 0 and len(model.released) == 3


@pytest.mark.integration
@pytest.mark.parametrize("mixed", [False, True], ids=["single-arrival", "mixed-batch"])
def test_native_late_io_readiness_overtakes_an_already_ready_cohort(
    tmp_path, threaded_pcc_runtime_archive, mixed,
):
    """External C ABI reference of the owned scheduler, including all GCs."""
    binary = _compile(tmp_path, r'''
        #include "py_runtime.h"
        #include <poll.h>
        #include <stdio.h>
        #include <stdlib.h>
        #include <unistd.h>

        int main(int argc, char **argv) {
            if (argc != 4 || pcc_gc_set_backend(atoi(argv[1]))) return 2;
            int count = atoi(argv[2]);
            int later_count = atoi(argv[3]) ? count : 0;
            if (count < 1 || count > 1024) return 3;
            int old_fds[1024];
            PyObject *oldest = NULL;
            for (int i = 0; i < count; ++i) {
                int pair[2];
                if (pipe(pair)) return 4;
                old_fds[i] = pair[0];
                PyObject *task = py_virtual_thread_new(py_None);
                if (!task || py_virtual_thread_block_on_fd(task, pair[0], POLLIN, -1)) return 5;
                if (i == 0) oldest = task;
                else py_decref(task);
                if (write(pair[1], "x", 1) != 1) return 6;
                /* Readability is queued before these peers disconnect. */
                if (py_virtual_thread_poll_io(0) != 1) return 7;
                close(pair[1]);
            }
            int pair[2];
            if (pipe(pair)) return 8;
            PyObject *fresh = py_virtual_thread_new(py_None);
            if (!fresh || py_virtual_thread_block_on_fd(
                fresh, pair[0], POLLIN, later_count ? 1000 : -1
            )) return 9;
            if (write(pair[1], "y", 1) != 1 || py_virtual_thread_poll_io(0) != 1) return 10;
            int later_fds[1024];
            for (int i = 0; i < later_count; ++i) {
                int later[2];
                if (pipe(later)) return 20;
                later_fds[i] = later[0];
                PyObject *task = py_virtual_thread_new(py_None);
                if (!task || py_virtual_thread_block_on_fd(
                    task, later[0], POLLIN, 30000
                )) return 21;
                py_decref(task);
                if (write(later[1], "z", 1) != 1 || py_virtual_thread_poll_io(0) != 1) return 22;
                close(later[1]);
            }
            int fresh_at = -1, oldest_at = -1;
            for (int i = 0; i <= count + later_count; ++i) {
                PyObject *task = py_virtual_thread_poll_ready();
                if (!task) return 11;
                if (task == fresh) fresh_at = i;
                if (task == oldest) oldest_at = i;
                if (py_virtual_thread_complete(task, py_None)) return 12;
                py_decref(task);
            }
            py_decref(fresh);
            py_decref(oldest);
            close(pair[0]); close(pair[1]);
            for (int i = 0; i < count; ++i) close(old_fds[i]);
            for (int i = 0; i < later_count; ++i) close(later_fds[i]);
            if (py_virtual_thread_ready_count() || py_virtual_thread_io_wait_count()) return 13;
            int64_t cached = cached_pool_roots();
            if (cached < 0 || pcc_gc_scheduler_root_count() != cached) return 14;
            printf("fresh=%d oldest=%d count=%d\n", fresh_at, oldest_at, count);
            return fresh_at >= 0 && fresh_at < 2 && oldest_at >= 0 && oldest_at < 2 ? 0 : 15;
        }
    ''', threaded_pcc_runtime_archive)
    for backend in range(5):
        for count in (64, 256, 1024):
            result = subprocess.run(
                [str(binary), str(backend), str(count), str(int(mixed))],
                env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0 and not result.stderr, (
                backend, count, result.returncode, result.stdout, result.stderr,
            )
