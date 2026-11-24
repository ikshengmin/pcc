"""Native virtual-thread scheduler root contract.

The probe drops the last ordinary reference to a virtual thread after placing it
in the ready queue, timer queue, and IO-wait queue. A gc.collect() happens while
the virtual thread is only reachable from scheduler state; polling the scheduler
must return a virtual thread whose suspended continuation still contains its
pointer-bearing frame slot.
"""
from __future__ import annotations

from tests.python.gc_production_contract.native_abi_probe import run_native_abi_probe


_SOURCE = r"""
#include "py_runtime.h"
#include "py_internal.h"

#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void resume_a(void) {}

static PyObject *make_vthread(const char *label) {
    int32_t frame_map[1] = {1};
    PyObject *slots[1] = {0};
    PyObject *local = py_str_new(label, (int64_t)strlen(label));
    if (local == 0) return 0;
    pcc_gc_store_root(&slots[0], local);
    PyObject *cont = py_continuation_new(frame_map, slots, (void *)&resume_a);
    pcc_gc_store_root(&slots[0], 0);
    pcc_gc_release(local);
    if (cont == 0) return 0;
    PyObject *vt = py_virtual_thread_new(cont);
    pcc_gc_release(cont);
    return vt;
}

static int continuation_slot_matches(PyObject *vthread, const char *label) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)vthread;
    PyObject *cont = pcc_gc_load_ptr(vthread, &vt->continuation);
    if (cont == 0) return 0;
    PyObject *got = py_continuation_get_slot(cont, 0);
    PyObject *expected = py_str_new(label, (int64_t)strlen(label));
    int ok = got != 0 && expected != 0 && py_obj_eq(got, expected);
    py_decref(got);
    py_decref(expected);
    return ok;
}

static int check_ready_queue(void) {
    PyObject *vt = make_vthread("ready-root");
    if (vt == 0) return 0;
    if (py_virtual_thread_start(vt) != 0) return 0;
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == 0) return 0;
    int ok = continuation_slot_matches(ready, "ready-root");
    py_virtual_thread_complete(ready, py_None);
    py_decref(ready);
    return ok;
}

static int check_timer_queue(void) {
    PyObject *vt = make_vthread("timer-root");
    if (vt == 0) return 0;
    if (py_virtual_thread_sleep(vt, 20) != 0) return 0;
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    if (pcc_runtime_sleep_ns(30000000) != 0) return 0;
    if (py_virtual_thread_poll_timers() != 1) return 0;
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == 0) return 0;
    int ok = continuation_slot_matches(ready, "timer-root");
    py_virtual_thread_complete(ready, py_None);
    py_decref(ready);
    return ok;
}

static int check_io_queue(void) {
    int fds[2];
    if (pipe(fds) != 0) return 0;
    PyObject *vt = make_vthread("io-root");
    if (vt == 0) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, 100) != 0) {
        close(fds[0]);
        close(fds[1]);
        return 0;
    }
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    if (write(fds[1], "x", 1) != 1) {
        close(fds[0]);
        close(fds[1]);
        return 0;
    }
    if (py_virtual_thread_poll_io(0) != 1) {
        close(fds[0]);
        close(fds[1]);
        return 0;
    }
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == 0) {
        close(fds[0]);
        close(fds[1]);
        return 0;
    }
    int ok = continuation_slot_matches(ready, "io-root");
    py_virtual_thread_complete(ready, py_None);
    py_decref(ready);
    close(fds[0]);
    close(fds[1]);
    return ok;
}

int64_t pcc_test_gc_probe(void) {
    if (!check_ready_queue()) return 1;
    if (!check_timer_queue()) return 2;
    if (!check_io_queue()) return 3;
    if (py_virtual_thread_ready_count() != 0
        || py_virtual_thread_timer_count() != 0
        || py_virtual_thread_io_wait_count() != 0) return 4;
    return 0;
}
"""


def test_virtual_thread_scheduler_roots(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    run = run_native_abi_probe(
        tmp_path, _SOURCE, pcc_runtime_archive, python_program_compiler,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "0\n", (
        f"root probe: {run.stdout.strip()} (1=ready, 2=timer, 3=I/O, 4=drain)"
    )
