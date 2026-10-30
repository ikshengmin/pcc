"""Terminal GC0 slab objects need no empty moving/identity metadata transaction."""

import ast
import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_gc_free_watch import WatchRuntime

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case,expected", [
    ("terminal", 1), ("other-mode", 0), ("transition", 0), ("identity", 0),
    ("foreign-origin", 0), ("live", 0), ("poisoned", 0), ("not-deallocating", 0),
])
def test_empty_metadata_admission_requires_terminal_provenance(case, expected):
    globals_ = {"pcc_gc_refcount_atomic": int(case != "other-mode"),
                "pcc_gc_foreign_lease_active": -1 if case == "transition" else 0}
    seen = []
    def load_i64(base, offset, order):
        assert order == "acquire"
        if isinstance(base, str):
            return globals_[base]
        assert case != "foreign-origin", "header read without exact provenance"
        seen.append("header")
        return 1 if case == "live" else (-1 if case == "poisoned" else 0)
    def load_i32(base, offset, *order):
        if isinstance(base, str):
            return globals_[base]
        assert case != "foreign-origin"
        return 0 if case == "not-deallocating" else 524288
    ns = {"global_addr": lambda key: key, "load_i32": load_i32,
          "atomic_load_i64": load_i64, "atomic_load_i32": load_i32,
          "_identity_head_acquire": lambda: 0 if case != "identity" else 1,
          "ptr_is_null": lambda pointer: pointer == 0,
          "pcc_gc_granule_is_object_start": lambda obj: int(case != "foreign-origin")}
    path = ROOT / "pcc/runtime/py/py_gc_backend.py"
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "_gc0_terminal_free_has_no_metadata")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    assert ns[node.name](1000) == expected
    if case in ("other-mode", "transition", "identity", "foreign-origin"):
        assert not seen


def test_fast_note_keeps_free_watch_and_last_note_evidence():
    model = WatchRuntime()
    obj = model.allocate()
    record = model.begin(obj)
    model.ns["_gc0_terminal_free_has_no_metadata"] = lambda pointer: 1
    model.note(obj)
    assert model.end(record) == 1
    assert model.tls["pcc_gc_last_noted_free"] == obj
    assert model.notes == [] and model.locks == 0


SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int sched_yield(void);
static int64_t ready, go, attempted, done;
static void *worker(void *object) {
    if (pcc_current_thread_id() <= 0) return (void *)1;
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&go, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint(); sched_yield();
    }
    __atomic_store_n(&attempted, 1, __ATOMIC_RELEASE);
    py_decref((PyObject *)object);
    __atomic_store_n(&done, 1, __ATOMIC_RELEASE);
    return 0;
}
int main(int argc, char **argv) {
    int with_identity = argc > 1 ? atoi(argv[1]) : 0;
    if (pcc_gc_set_backend(0) != 0) return 10;
    PyObject *object = py_str_new("terminal", 8);
    if (!object) return 11;
    if (pcc_gc_backend4_stable_id_entries() != 0) return 12;
    if (with_identity && pcc_gc_object_id(object) <= 0) return 13;
    void *thread = 0;
    if (pcc_thread_start(&thread, worker, object) != 0) return 14;
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint(); sched_yield();
    }
    pcc_py_gc_minor_graph_lock();
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&attempted, __ATOMIC_ACQUIRE)) sched_yield();
    int64_t deadline = pcc_runtime_monotonic_us() + 200000;
    while (!__atomic_load_n(&done, __ATOMIC_ACQUIRE) && pcc_runtime_monotonic_us() < deadline) sched_yield();
    int progressed = __atomic_load_n(&done, __ATOMIC_ACQUIRE) != 0;
    pcc_py_gc_minor_graph_unlock();
    void *status = 0;
    if (pcc_thread_join(thread, &status) != 0 || status != 0) return 15;
    if (progressed != !with_identity) return 20;
    if (pcc_gc_backend4_stable_id_entries() != 0) return 21;
    puts("GC0_METADATA_FREE_OK");
    return 0;
}
'''


def test_native_terminal_free_skips_only_empty_identity_metadata(tmp_path, pcc_runtime_archive):
    source = tmp_path / "terminal_free.c"
    source.write_text(SOURCE)
    binary = tmp_path / "terminal_free"
    compile_owned_c_with_runtime(source, binary, runtime_archive=Path(pcc_runtime_archive))
    for with_identity in (0, 1):
        result = subprocess.run([str(binary), str(with_identity)], capture_output=True,
                                text=True, timeout=15, env=dict(os.environ, PATH="/nonexistent"))
        assert (result.returncode, result.stdout, result.stderr) == (0, "GC0_METADATA_FREE_OK\n", ""), with_identity
