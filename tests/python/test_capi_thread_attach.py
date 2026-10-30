"""PyGILState_Ensure/Release attach and detach a raw thread, and
PyEval_SaveThread/RestoreThread detach a thread around a blocking call.

Both pairs were no-ops. A raw thread that called PyGILState_Ensure stayed
outside the live set, and after its last Release it was not removed from it
when the runtime had registered it on its own. A thread blocked in
pthread_join between PyEval_SaveThread and PyEval_RestoreThread still
counted as running, so another thread's collection, which stops the world,
waited for it forever. CPython attaches a thread state in Ensure, deletes the
one Ensure created at the outermost Release, and detaches it in SaveThread.
"""
from pathlib import Path
import os
import subprocess

import pytest

from _gc_substrate_common import _compile_runtime_probe


ATTACH_PROBE = r'''
#include "py_internal.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

typedef int PyGILState_STATE;
extern PyGILState_STATE PyGILState_Ensure(void);
extern void PyGILState_Release(PyGILState_STATE state);
extern void *PyEval_SaveThread(void);
extern void PyEval_RestoreThread(void *state);
extern int64_t pcc_thread_registered(void);

static PyObject *shared;
static atomic_int main_waiting;

static void *worker(void *unused) {
    (void)unused;
    /* Not attached yet: wait until the main thread is in pthread_join. */
    while (atomic_load_explicit(&main_waiting, memory_order_acquire) == 0) {
        sched_yield();
    }
    usleep(20000);
    if (pcc_thread_registered() != 0) return (void *)1;
    PyGILState_STATE outer = PyGILState_Ensure();
    if (pcc_thread_registered() != 1) return (void *)2;
    PyGILState_STATE inner = PyGILState_Ensure();
    for (int64_t i = 0; i < 20000; i++) {
        py_list_append(shared, py_int_from_i64(i));
    }
    /* A collection stops the world. The main thread waits in pthread_join
     * inside PyEval_SaveThread, so it must not hold the stop up. */
    if (py_gc_collect() < 0) return (void *)3;
    PyGILState_Release(inner);
    if (pcc_thread_registered() != 1) return (void *)4;
    PyGILState_Release(outer);
    if (pcc_thread_registered() != 0) return (void *)5;
    return 0;
}

int main(void) {
    if (pcc_current_thread_id() <= 0) return 2;
    shared = py_list_new(0);
    if (shared == 0) return 3;
    pcc_gc_pin(shared);
    /* Ensure on an attached thread nests; its Release keeps it attached. */
    PyGILState_STATE state = PyGILState_Ensure();
    PyGILState_Release(state);
    if (pcc_thread_registered() != 1) return 4;
    pthread_t thread;
    if (pthread_create(&thread, 0, worker, 0) != 0) return 5;
    for (int64_t i = 0; i < 20000; i++) {
        PyObject *item = py_list_new(0);
        if (item == 0) return 6;
        py_list_append(shared, item);
        py_decref(item);
    }
    atomic_store_explicit(&main_waiting, 1, memory_order_release);
    void *saved = PyEval_SaveThread();
    void *result = 0;
    int joined = pthread_join(thread, &result);
    PyEval_RestoreThread(saved);
    if (joined != 0) return 7;
    if (result != 0) return 10 + (int)(intptr_t)result;
    if (py_list_len(shared) != 40000) return 8;
    printf("attach-ok\n");
    return 0;
}
'''


@pytest.mark.parametrize("backend", range(5))
def test_gilstate_attaches_and_save_thread_detaches_on_five_collectors(
    tmp_path: Path, backend: int
):
    executable = _compile_runtime_probe(
        tmp_path, kind="pcc_python", threaded=True,
        stem="capi_thread_attach", source_text=ATTACH_PROBE,
    )
    env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
    for _ in range(3):
        run = subprocess.run([str(executable)], env=env, capture_output=True,
                             text=True, timeout=60)
        assert run.returncode == 0, f"GC{backend}: {run.returncode} {run.stderr[-2000:]}"
        assert run.stdout == "attach-ok\n"
