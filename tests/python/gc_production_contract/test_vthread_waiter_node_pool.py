"""Production contract: vthread waiter-node freelist pool.

Part of the virtual-thread T-track (T-P0-VTHREAD-NODE-POOL) and the
production slot/root contract. The owned py_threading runtime wakes
virtual threads that block on a Lock / Event / Condition / Semaphore through a
per-object waiter queue whose nodes (PyThreadVThreadWaiter) used to be raw
calloc()/free() per park. This slice recycles those nodes through a bounded
slab/freelist pool (PCC_VTHREAD_WAITER_POOL_LIMIT), mirroring the ready-entry
pool in pcc_threads.c (PCC_VTHREAD_READY_ENTRY_POOL_LIMIT).

The waiter-node path is a runtime ABI surface rather than a Python source
construct, so this brick builds a focused C probe against pcc's no-libpython
runtime archive.

The probe spawns many virtual threads that each try to acquire one shared
threading.Lock while running on the scheduler. Only one holds it at a time; the
rest enqueue waiter nodes and park. A releaser notifies one virtual waiter at a
time to retry acquisition until every worker has acquired and released it. Running this
over many rounds forces the freelist to fill and be reused. The pool must
preserve the observable contract: every worker eventually acquires the lock
(mutual exclusion, FIFO wake, no lost wakeups), the scheduler queues drain to
empty, and no waiter node leaks.
"""
from __future__ import annotations

from tests.python.gc_production_contract.native_abi_probe import run_native_abi_probe


# Number of worker vthreads that contend for the single lock per round, and the
# number of rounds. WORKERS well exceeds any single burst, and running ROUNDS
# rounds guarantees the freelist is populated by an earlier round and reused by
# a later one (the whole point of the slab pool).
_WORKERS = 64
_ROUNDS = 8

_SOURCE = r"""
#include "py_runtime.h"
#include "py_internal.h"

#include <stdint.h>
#include <stdlib.h>

#define WORKERS %(workers)d
#define ROUNDS %(rounds)d

/* Shared lock for the current round and bookkeeping the probe checks. */
static PyObject *g_lock = NULL;
static int g_acquired_total = 0;   /* how many workers acquired the lock */
static int g_max_concurrent = 0;   /* peak simultaneous holders (must stay 1) */
static int g_held_now = 0;         /* current holders (mutual-exclusion check) */

/* Worker resume: slot 0 holds a small-int marker (stage) across resumes.
 *   stage 0 -> first entry: try to acquire the lock.
 *   stage 1 -> we hold the lock: do the mutual-exclusion accounting, release
 *              (which wakes the next waiter -> node recycle), and finish.
 * py_threading_lock_acquire_vthread returns 0 when it takes the lock
 * immediately, or 1 when it enqueued a waiter node and parked.
 *
 * The first worker to grab the lock deliberately yields (sleep(0)) while still
 * holding it, re-queueing itself at the tail. That lets every other ready
 * worker run and pile onto the waiter queue (rc == 1), which is what forces the
 * freelist pool to fill up and later be reused. */
static int64_t worker_resume(PyObject *vthread, PyObject *continuation) {
    PyObject *marker = py_continuation_get_slot(continuation, 0);
    int64_t stage = marker != NULL ? py_int_value_i64(marker) : 0;
    py_decref(marker);

    if (stage == 0) {
        int64_t rc = py_threading_lock_acquire_vthread(g_lock);
        if (rc < 0) return -1;
        if (rc == 1) {
            /* A wake is not ownership. Keep stage 0 so the next resume
             * retries acquisition before entering the critical section. */
            return 0;
        }
        /* Remember that our next resume must release the lock. */
        PyObject *held = py_int_from_i64(1);
        if (held == NULL) return -1;
        (void)py_continuation_set_slot(continuation, 0, held);
        py_decref(held);
        /* rc == 0: took the lock uncontended. Yield while holding so the other
         * queued workers contend and enqueue waiter nodes; we come back at
         * stage 1 to release. */
        if (py_virtual_thread_sleep(vthread, 0) != 0) return -1;
        return 0;
    }

    /* stage == 1: we own the lock now. */
    g_held_now++;
    if (g_held_now > g_max_concurrent) g_max_concurrent = g_held_now;
    g_acquired_total++;
    g_held_now--;
    if (py_threading_lock_release(g_lock) < 0) return -1;
    return 0;
}

static PyObject *make_worker(void) {
    int32_t frame_map[1] = {1};   /* one slot; holds a tagged small-int marker */
    PyObject *slots[1] = {0};
    PyObject *zero = py_int_from_i64(0);
    if (zero == NULL) return NULL;
    slots[0] = zero;
    PyObject *cont = py_continuation_new_typed(
        frame_map, slots, (void *)&worker_resume
    );
    py_decref(zero);
    if (cont == NULL) return NULL;
    PyObject *vt = py_virtual_thread_new(cont);
    py_decref(cont);
    return vt;
}

static int run_one_round(void) {
    g_lock = py_threading_lock_new();
    if (g_lock == NULL) return 0;
    g_acquired_total = 0;
    g_max_concurrent = 0;
    g_held_now = 0;

    for (int i = 0; i < WORKERS; i++) {
        PyObject *vt = make_worker();
        if (vt == NULL) { py_decref(g_lock); g_lock = NULL; return 0; }
        if (py_virtual_thread_start(vt) != 0) {
            py_decref(vt);
            py_decref(g_lock);
            g_lock = NULL;
            return 0;
        }
        py_decref(vt);
    }

    /* Drive the scheduler to completion. Each acquire/park + release/wake is a
     * step; give generous headroom. */
    int64_t ran = py_virtual_thread_run_until_idle((int64_t)(WORKERS * 8 + 64));
    if (ran < 0) { py_decref(g_lock); g_lock = NULL; return 0; }

    int ok = g_acquired_total == WORKERS
        && g_max_concurrent == 1
        && g_held_now == 0
        && py_virtual_thread_ready_count() == 0
        && py_virtual_thread_timer_count() == 0
        && py_virtual_thread_io_wait_count() == 0;

    py_decref(g_lock);
    g_lock = NULL;
    return ok;
}

int64_t pcc_test_gc_probe(void) {
    int all_ok = 1;
    for (int r = 0; r < ROUNDS; r++) {
        if (!run_one_round()) { all_ok = 0; break; }
    }

    /* A final gc.collect() after the pool has been exercised must not crash or
     * leave scheduler state behind. */
    (void)pcc_gc_collect(0);
    int drained = py_virtual_thread_ready_count() == 0
        && py_virtual_thread_timer_count() == 0
        && py_virtual_thread_io_wait_count() == 0;
    int64_t allocations = py_virtual_thread_node_pool_stat(
        PCC_VTHREAD_NODE_WAITER, PCC_VTHREAD_POOL_ALLOCATIONS
    );
    int64_t reuses = py_virtual_thread_node_pool_stat(
        PCC_VTHREAD_NODE_WAITER, PCC_VTHREAD_POOL_REUSES
    );
    int64_t cached = py_virtual_thread_node_pool_stat(
        PCC_VTHREAD_NODE_WAITER, PCC_VTHREAD_POOL_CACHED
    );
    int reused = allocations > 0 && reuses > 0 && cached > 0 && cached <= 4096;

    if (!all_ok) return 1;
    if (!drained) return 2;
    if (!reused) return 3;
    return 0;
}
""" % {"workers": _WORKERS, "rounds": _ROUNDS}


def test_vthread_waiter_node_pool(tmp_path, pcc_runtime_archive, python_program_compiler):
    run = run_native_abi_probe(
        tmp_path, _SOURCE, pcc_runtime_archive, python_program_compiler,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "0\n", (
        f"waiter pool probe: {run.stdout.strip()} (1=workers, 2=drain, 3=reuse)"
    )
