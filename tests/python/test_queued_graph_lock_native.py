"""Native queued graph handoff, stop admission, reuse, and nested depth.

One binary uses the real graph word: runtime helpers that run inside a graph
transaction must see that same owner. Cold queue metadata is inspected only
while the test's fast owner prevents any queued owner from dequeuing.
"""
from pathlib import Path
import hashlib
import json
import os

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int32_t g_pcc_py_gc_minor_graph_lock;
extern void *pcc_graph_wait_context_head_py;

/* Named private queue ABI for this invariant witness, matching the kernel.
 * Context fields: actual word +0, immutable next +8, atomic tail +16. */
enum { CONTEXT_WORD = 0, CONTEXT_NEXT = 8, CONTEXT_TAIL = 16, ROUNDS = 300 };
static int64_t entered, stop_completed, late_entered, failure, counter;
static int64_t order[3];
struct Worker { int64_t id; int64_t rounds; };
static struct Worker workers[4];

static int64_t now(void) { return pcc_runtime_monotonic_us(); }
static void fail(int64_t code) { __atomic_store_n(&failure, code, __ATOMIC_RELEASE); }
static void *context_for_graph(void) {
    void *context = (void *)(uintptr_t)__atomic_load_n((uintptr_t *)&pcc_graph_wait_context_head_py, __ATOMIC_ACQUIRE);
    while (context) {
        void *key = *(void **)((char *)context + CONTEXT_WORD);
        if (key == (void *)&g_pcc_py_gc_minor_graph_lock) return context;
        context = *(void **)((char *)context + CONTEXT_NEXT);
    }
    return 0;
}
static uint64_t tail(void) {
    void *context = context_for_graph();
    if (!context) return 0;
    return __atomic_load_n((uint64_t *)((char *)context + CONTEXT_TAIL), __ATOMIC_ACQUIRE);
}
static int wait_tail_changes(uint64_t previous) {
    int64_t deadline = now() + 5000000;
    while (tail() == previous) {
        if (now() >= deadline) return 0;
        pcc_thread_safepoint();
    }
    return 1;
}
static void *queued_once(void *argument) {
    struct Worker *worker = (struct Worker *)argument;
    pcc_py_gc_minor_graph_lock();
    int64_t position = __atomic_fetch_add(&entered, 1, __ATOMIC_ACQ_REL);
    if (position < 0 || position >= 3) fail(20);
    else order[position] = worker->id;
    if (pcc_py_gc_minor_graph_lock_depth() != 1 || pcc_thread_no_park_depth() != 1) fail(21);
    pcc_py_gc_minor_graph_unlock();
    if (pcc_py_gc_minor_graph_lock_depth() != 0 || pcc_thread_no_park_depth() != 0) fail(22);
    return 0;
}
static void *late_newcomer(void *unused) {
    (void)unused;
    __atomic_store_n(&late_entered, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_lock();
    if (pcc_py_gc_minor_graph_lock_depth() != 1 || pcc_thread_no_park_depth() != 1) fail(23);
    pcc_py_gc_minor_graph_unlock();
    return 0;
}
static void *stopper(void *unused) {
    (void)unused;
    if (pcc_stop_the_world() != 0) { fail(30); return 0; }
    /* All linked waiters had to finish graph work/dequeue before parking. */
    if (__atomic_load_n(&entered, __ATOMIC_ACQUIRE) != 3 || tail() != 0) fail(31);
    if (__atomic_load_n(&g_pcc_py_gc_minor_graph_lock, __ATOMIC_ACQUIRE) != 0) fail(32);
    void *late = 0;
    if (pcc_thread_start(&late, late_newcomer, 0) != 0) {
        fail(33);
        pcc_resume_world();
        return 0;
    }
    int64_t deadline = now() + 5000000;
    while (pcc_thread_registration_waiter_count() == 0 && now() < deadline) { }
    if (pcc_thread_registration_waiter_count() != 1
            || __atomic_load_n(&late_entered, __ATOMIC_ACQUIRE) != 0) fail(34);
    __atomic_store_n(&stop_completed, 1, __ATOMIC_RELEASE);
    pcc_resume_world();
    void *result = 0;
    if (pcc_thread_join(late, &result) != 0 || result) fail(35);
    return 0;
}
static void *reuse(void *argument) {
    struct Worker *worker = (struct Worker *)argument;
    for (int64_t round = 0; round < worker->rounds; round++) {
        int outer = round % 17 == 0;
        if (outer) pcc_thread_no_park_enter();
        pcc_py_gc_minor_graph_lock();
        if (pcc_thread_no_park_depth() != 1 + outer) fail(40);
        pcc_py_gc_minor_graph_lock();
        if (pcc_py_gc_minor_graph_lock_depth() != 2 || pcc_thread_no_park_depth() != 1 + outer) fail(41);
        counter++;
        pcc_py_gc_minor_graph_unlock();
        if (pcc_py_gc_minor_graph_lock_depth() != 1) fail(42);
        pcc_py_gc_minor_graph_unlock();
        if (pcc_py_gc_minor_graph_lock_depth() != 0 || pcc_thread_no_park_depth() != outer) fail(43);
        if (outer) pcc_thread_no_park_exit();
    }
    return 0;
}
int main(void) {
    if (pcc_threads_enabled() != 1) return 1;
    int backend = (int)pcc_gc_backend();
    pcc_current_thread_id();
    void *handles[4] = {0, 0, 0, 0};
    pcc_py_gc_minor_graph_lock();
    if (tail() != 0) return 2;
    for (int i = 0; i < 3; i++) {
        workers[i].id = i;
        uint64_t previous = tail();
        if (pcc_thread_start(&handles[i], queued_once, &workers[i]) != 0) return 3;
        if (!wait_tail_changes(previous)) return 4;
    }
    if (__atomic_load_n(&entered, __ATOMIC_ACQUIRE) != 0) return 5;
    if (pcc_thread_start(&handles[3], stopper, 0) != 0) return 6;
    int64_t deadline = now() + 5000000;
    while (!pcc_thread_stop_requested_acquire() && now() < deadline) { }
    if (!pcc_thread_stop_requested_acquire()) return 7;
    /* Outermost release services the stop only after releasing the word.
       Queued waiters remain no-park and drain before stopper can finish. */
    pcc_py_gc_minor_graph_unlock();
    for (int i = 0; i < 4; i++) {
        void *result = 0;
        if (pcc_thread_join(handles[i], &result) != 0 || result) return 8;
    }
    if (__atomic_load_n(&failure, __ATOMIC_ACQUIRE)) return (int)failure;
    if (!stop_completed || late_entered != 1 || entered != 3) return 9;
    if (order[0] != 0 || order[1] != 1 || order[2] != 2 || tail() != 0) return 10;
    for (int i = 0; i < 4; i++) {
        workers[i].rounds = ROUNDS;
        if (pcc_thread_start(&handles[i], reuse, &workers[i]) != 0) return 11;
    }
    for (int i = 0; i < 4; i++) {
        void *result = 0;
        if (pcc_thread_join(handles[i], &result) != 0 || result) return 12;
    }
    if (__atomic_load_n(&failure, __ATOMIC_ACQUIRE)) return (int)failure;
    if (counter != 4 * ROUNDS || tail() != 0
            || pcc_thread_no_park_depth() != 0 || pcc_py_gc_minor_graph_lock_depth() != 0) return 13;
    if (pcc_stop_the_world() != 0) return 14;
    if (tail() != 0) return 15;
    if (pcc_resume_world() != 0) return 16;
    printf("QUEUED_GRAPH_OK gc=%d fifo=012 reuse=%lld\n", backend, (long long)counter);
    return 0;
}
'''


@pytest.mark.integration
def test_queued_graph_native_handoff_stop_registration_and_reuse(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / 'queued_graph.c'
    source.write_text(SOURCE)
    binary = tmp_path / 'queued_graph'
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive)
    receipt = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
               'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'runs': []}
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=45,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS='1', PCC_REFCOUNT_KIND='atomic'),
        )
        (tmp_path / f'gc{backend}.stdout').write_text(result.stdout)
        (tmp_path / f'gc{backend}.stderr').write_text(result.stderr)
        receipt['runs'].append({'backend': backend, 'returncode': result.returncode,
                                'stdout': result.stdout, 'stderr': result.stderr})
        (tmp_path / 'queued-graph.json').write_text(json.dumps(receipt, indent=2) + '\n')
        assert (result.returncode, result.stdout, result.stderr) == (
            0, f'QUEUED_GRAPH_OK gc={backend} fifo=012 reuse=1200\n', '',
        ), backend
