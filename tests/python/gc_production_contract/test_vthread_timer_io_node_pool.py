"""Timer/IO vthread nodes are bounded, reusable roots."""

from __future__ import annotations

from tests.python.gc_production_contract.native_abi_probe import run_native_abi_probe


_SOURCE = r"""
#include "py_runtime.h"
#include "native_scheduler_roots.h"

#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>

static PyObject *new_vthread(void) {
    return py_virtual_thread_new(py_None);
}

static int timer_round(void) {
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_sleep(vt, 1) != 0) return 0;
    pcc_gc_release(vt);
    if (active_vthread_roots() != 1) return 0;
    (void)pcc_gc_collect(0);
    if (active_vthread_roots() != 1) return 0;
    if (pcc_runtime_sleep_ns(2000000) != 0) return 0;
    if (py_virtual_thread_poll_timers() != 1) return 0;
    if (active_vthread_roots() != 1) return 0;
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return 0;
    if (active_vthread_roots() != 0) return 0;
    if (py_virtual_thread_complete(ready, py_None) != 0) return 0;
    pcc_gc_release(ready);
    return 1;
}

static int io_round(int read_fd) {
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_block_on_fd(vt, read_fd, POLLIN, 1) != 0) return 0;
    pcc_gc_release(vt);
    if (active_vthread_roots() != 1) return 0;
    (void)pcc_gc_collect(0);
    if (active_vthread_roots() != 1) return 0;
    if (pcc_runtime_sleep_ns(2000000) != 0) return 0;
    if (py_virtual_thread_poll_io(0) != 1) return 0;
    if (active_vthread_roots() != 1) return 0;
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return 0;
    if (active_vthread_roots() != 0) return 0;
    if (py_virtual_thread_complete(ready, py_None) != 0) return 0;
    pcc_gc_release(ready);
    return 1;
}

static int pool_reused(int64_t family) {
    int64_t allocations = py_virtual_thread_node_pool_stat(
        family, PCC_VTHREAD_POOL_ALLOCATIONS
    );
    int64_t reuses = py_virtual_thread_node_pool_stat(
        family, PCC_VTHREAD_POOL_REUSES
    );
    int64_t cached = py_virtual_thread_node_pool_stat(
        family, PCC_VTHREAD_POOL_CACHED
    );
    return allocations > 0 && reuses > 0 && cached > 0 && cached <= 4096;
}

int64_t pcc_test_gc_probe(void) {
    if (py_virtual_thread_effect_reset() != 0) return 1;
    int fds[2];
    if (pipe(fds) != 0) return 4;
    for (int i = 0; i < 16; i++) {
        if (!timer_round()) return 10;
        if (!io_round(fds[0])) return 11;
    }
    close(fds[0]);
    close(fds[1]);
    if (!pool_reused(PCC_VTHREAD_NODE_READY)) return 20;
    if (!pool_reused(PCC_VTHREAD_NODE_TIMER)) return 21;
    if (!pool_reused(PCC_VTHREAD_NODE_IO)) return 22;
    if (active_vthread_roots() != 0) return 23;
    if (py_virtual_thread_ready_count() != 0) return 24;
    if (py_virtual_thread_timer_count() != 0) return 25;
    if (py_virtual_thread_io_wait_count() != 0) return 26;
    return 0;
}
"""


def test_vthread_timer_io_nodes_reuse_and_preserve_roots(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    run = run_native_abi_probe(
        tmp_path, _SOURCE, pcc_runtime_archive, python_program_compiler,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "0\n", f"native timer/I/O pool probe: {run.stdout.strip()}"
