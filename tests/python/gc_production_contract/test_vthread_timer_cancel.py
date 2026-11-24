"""Production vthread timer cancellation is immediate and preserves roots."""

from __future__ import annotations

from tests.python.gc_production_contract.native_abi_probe import RUNTIME_DIR, run_native_abi_probe


_SOURCE = r"""
#include "py_runtime.h"
#include "py_internal.h"
#include "native_scheduler_roots.h"

#include <stdint.h>
#include <stdlib.h>

static PyObject *new_vthread(void) {
    return py_virtual_thread_new(py_None);
}

static int check_cancel_reuse_and_root_transfer(void) {
    PyObject *first = new_vthread();
    if (first == NULL) return __LINE__;
    if (py_virtual_thread_sleep(first, 2) != 0) return __LINE__;
    void *first_entry = ((PyVirtualThreadObject *)first)->timer_entry;
    if (first_entry == NULL) return __LINE__;
    if (py_virtual_thread_timer_count() != 1) return __LINE__;
    if (active_vthread_roots() != 1) return __LINE__;

    if (py_virtual_thread_cancel_timer(first) != 1) return __LINE__;
    if (py_virtual_thread_cancel_timer(first) != 0) return __LINE__;
    if (((PyVirtualThreadObject *)first)->timer_entry != NULL) return __LINE__;
    if (py_virtual_thread_timer_count() != 0) return __LINE__;
    if (active_vthread_roots() != 0) return __LINE__;
    if (py_virtual_thread_complete(first, py_None) != 0) return __LINE__;
    pcc_gc_release(first);

    /* Force immediate reuse of the cancelled timer node. */
    PyObject *second = new_vthread();
    if (second == NULL) return __LINE__;
    if (py_virtual_thread_sleep(second, 5) != 0) return __LINE__;
    if (((PyVirtualThreadObject *)second)->timer_entry != first_entry) return __LINE__;
    pcc_gc_release(second);
    (void)pcc_gc_collect(0);
    if (active_vthread_roots() != 1) return __LINE__;
    if (pcc_runtime_sleep_ns(10000000) != 0) return __LINE__;
    if (py_virtual_thread_poll_timers() != 1) return __LINE__;
    if (py_virtual_thread_timer_count() != 0) return __LINE__;
    if (active_vthread_roots() != 1) return __LINE__;
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return __LINE__;
    if (active_vthread_roots() != 0) return __LINE__;
    if (((PyVirtualThreadObject *)ready)->timer_entry != NULL) return __LINE__;
    if (py_virtual_thread_complete(ready, py_None) != 0) return __LINE__;
    pcc_gc_release(ready);
    return 1;
}

static int check_complete_cancels_sleep(void) {
    PyObject *vthread = new_vthread();
    if (vthread == NULL) return __LINE__;
    if (py_virtual_thread_sleep(vthread, 5) != 0) return __LINE__;
    if (active_vthread_roots() != 1) return __LINE__;
    if (py_virtual_thread_complete(vthread, py_None) != 0) return __LINE__;
    if (((PyVirtualThreadObject *)vthread)->timer_entry != NULL) return __LINE__;
    if (py_virtual_thread_timer_count() != 0) return __LINE__;
    if (active_vthread_roots() != 0) return __LINE__;
    pcc_gc_release(vthread);
    if (pcc_runtime_sleep_ns(10000000) != 0) return __LINE__;
    if (py_virtual_thread_poll_timers() != 0) return __LINE__;
    return py_virtual_thread_poll_ready() == NULL;
}

static int check_unpark_cancels_sleep(void) {
    PyObject *vthread = new_vthread();
    if (vthread == NULL) return __LINE__;
    if (py_virtual_thread_sleep(vthread, 5) != 0) return __LINE__;
    if (py_virtual_thread_unpark(vthread) != 0) return __LINE__;
    if (((PyVirtualThreadObject *)vthread)->timer_entry != NULL) return __LINE__;
    if (py_virtual_thread_timer_count() != 0) return __LINE__;
    /* The timer root was released and exactly one ready-queue root replaced it. */
    if (active_vthread_roots() != 1) return __LINE__;
    pcc_gc_release(vthread);
    (void)pcc_gc_collect(0);
    if (pcc_runtime_sleep_ns(10000000) != 0) return __LINE__;
    if (py_virtual_thread_poll_timers() != 0) return __LINE__;
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return __LINE__;
    if (py_virtual_thread_poll_ready() != NULL) return __LINE__;
    if (active_vthread_roots() != 0) return __LINE__;
    if (py_virtual_thread_complete(ready, py_None) != 0) return __LINE__;
    pcc_gc_release(ready);
    return 1;
}

int64_t pcc_test_gc_probe(void) {
    if (py_virtual_thread_effect_reset() != 0) return 1;
    int stage = check_cancel_reuse_and_root_transfer();
    if (stage != 1) return stage;
    stage = check_complete_cancels_sleep();
    if (stage != 1) return stage ? stage : __LINE__;
    stage = check_unpark_cancels_sleep();
    if (stage != 1) return stage;
    if (py_virtual_thread_timer_count() != 0) return __LINE__;
    if (py_virtual_thread_ready_count() != 0) return __LINE__;
    if (active_vthread_roots() != 0) return __LINE__;
    if (py_virtual_thread_node_pool_stat(
        PCC_VTHREAD_NODE_TIMER, PCC_VTHREAD_POOL_REUSES
    ) <= 0) return __LINE__;
    return 0;
}
"""


def test_vthread_timer_cancel_is_immediate_and_root_safe(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    run = run_native_abi_probe(
        tmp_path, _SOURCE, pcc_runtime_archive, python_program_compiler,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "0\n", f"native timer probe failed at C line {run.stdout.strip()}"


def test_production_timer_cancel_is_declared() -> None:
    header = (RUNTIME_DIR / "include" / "py_runtime.h").read_text(
        encoding="utf-8"
    )
    assert "py_virtual_thread_cancel_timer" in header
