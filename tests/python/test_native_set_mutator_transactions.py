"""Concurrent public set mutations: preserve ownership and visible contents."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''import gc
import threading

def drain(shared, ready, start, output):
    saved = shared.pop
    ready.set()
    start.wait()
    while True:
        try:
            value = saved()
        except KeyError:
            break
        output.append(value)
        if len(output) == 1:
            gc.collect()

def edit(shared, ready, start):
    ready.set()
    start.wait()
    for i in range(40):
        shared.clear()
        shared.update([i, i + 1, i + 2])
        if i % 5 == 0:
            gc.collect()

def inspect(shared, ready, start, observed):
    ready.set()
    start.wait()
    for i in range(40):
        if not shared.isdisjoint([-100]):
            observed.append("unexpected")
        # Each snapshot is coherent even if the size changes between calls.
        values = list(shared)
        if len(values) != len(set(values)):
            observed.append("duplicate")
    observed.append("complete")

class NoHash:
    def __hash__(self):
        if self.sealed:
            raise ValueError("unexpected rehash")
        return 9
    def __eq__(self, other):
        if self.sealed:
            raise ValueError("unexpected equality")
        return self is other
    def __init__(self):
        self.sealed = False

def main():
    shared = set(range(64))
    left = []
    right = []
    ready1 = threading.Event()
    ready2 = threading.Event()
    start = threading.Event()
    a = threading.Thread(target=drain, args=(shared, ready1, start, left))
    b = threading.Thread(target=drain, args=(shared, ready2, start, right))
    a.start()
    b.start()
    ready1.wait()
    ready2.wait()
    start.set()
    a.join()
    b.join()
    print(len(shared), sorted(left + right) == list(range(64)))
    ready1 = threading.Event()
    ready2 = threading.Event()
    start = threading.Event()
    observed = []
    a = threading.Thread(target=edit, args=(shared, ready1, start))
    b = threading.Thread(target=inspect, args=(shared, ready2, start, observed))
    a.start()
    b.start()
    ready1.wait()
    ready2.wait()
    start.set()
    a.join()
    b.join()
    print(sorted(shared), observed)
    key = NoHash()
    shared = {key}
    key.sealed = True
    print(shared.pop() is key, len(shared))
    try:
        shared.pop()
    except KeyError:
        print("empty pop")
main()
'''


def test_two_threads_exhaust_and_replace_the_same_public_set(tmp_path: Path, pcc_runtime_archive):
    source = tmp_path / "shared_set.py"
    binary = tmp_path / "shared_set"
    source.write_text(PROGRAM, encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], text=True, capture_output=True, timeout=15)
    assert reference.returncode == 0 and not reference.stderr, reference.stderr
    assert reference.stdout.splitlines() == ["0 True", "[39, 40, 41] ['complete']", "True 0", "empty pop"]
    compile_python(str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=15,
                             env=dict(os.environ, PCC_GC_BACKEND=str(gc)))
        assert run.returncode == 0 and not run.stderr, (gc, run.returncode, run.stderr)
        assert run.stdout == reference.stdout, (gc, run.stdout)


def test_gc4_relocates_set_payload_preserving_private_mutation_version(tmp_path: Path, pcc_runtime_archive):
    # A genuine stopped-world move of a registered-only root. The existing
    # collector selection/refusal contract is unmodified. Never force a live
    # caller's borrowed/address-leased set to move.
    from tests.owned_runtime_c_fixture import link_c_harness_bounded
    from tests.python._gc_substrate_common import GC4_STOP_MOVES_ROOTED_C

    runtime = Path(__file__).resolve().parents[2] / "pcc/runtime"
    source = tmp_path / "set_move.c"
    exe = tmp_path / "set_move"
    source.write_text(r'''
#include "py_runtime.h"
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_gc_relocation_payload_raw_snapshot_locked(
    PyObject *, int64_t, int64_t, void *);
static PyObject *owner_root;
static int64_t original_id, original_version;
/* Standard permitted collector selection and stop/resume helper. */
RELOCATION_HELPER
static int64_t version(PyObject *set) {
    int64_t state[52] = {0};
    pcc_py_gc_minor_graph_lock();
    int64_t ok = pcc_gc_relocation_payload_raw_snapshot_locked(
        set, PY_TYPE_SET, sizeof(PySetObject), state);
    pcc_py_gc_minor_graph_unlock();
    /* Allocate/copy scalar trailer, never register it as a managed field. */
    int64_t bytes = state[96 / 8] * (int64_t)sizeof(SetEntry);
    if (ok != 1 || state[72 / 8] != 1
        || state[(152 + 8) / 8] != bytes + 8
        || state[(152 + 16) / 8] != bytes + 8
        || state[(152 + 32) / 8] != bytes) return -1;
    return state[112 / 8];
}
__attribute__((noinline)) static int build(void) {
    owner_root = py_set_new();
    if (!owner_root) return 1;
    for (int64_t i = 1; i <= 4; i++) py_set_add(owner_root, py_int_from_i64(i));
    if (py_set_remove(owner_root, py_int_from_i64(2)) != 0) return 2;
    original_id = pcc_gc_object_id(owner_root);
    original_version = version(owner_root);
    return original_version > 0 ? 0 : 3;
}
int main(void) {
    if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) return 4;
    void *handle = pcc_gc_scheduler_root_register_handle(&owner_root);
    if (!handle || build()) return 5;
    gc4_scrub_stack();
    int rc = gc4_stop_moves_rooted(&owner_root, sizeof(PySetObject));
    if (rc) return rc;
    PyObject *set = owner_root;
    if (pcc_gc_object_id(set) != original_id || version(set) != original_version) return 6;
    PyObject *key = py_set_pop(set);
    if (!key || py_err_occurred() || py_set_len(set) != 2 || version(set) <= original_version) return 7;
    pcc_gc_release(key);
    for (int64_t i = 10; i < 90; i++) py_set_add(set, py_int_from_i64(i));
    if (py_set_len(set) != 82 || !py_set_contains(set, py_int_from_i64(89))) return 8;
    py_set_clear(set);
    if (py_err_occurred() || py_set_len(set) != 0 || version(set) <= original_version) return 9;
    owner_root = NULL;
    pcc_gc_scheduler_root_unregister_handle(handle);
    pcc_gc_release(set);
    return 0;
}
'''.replace("RELOCATION_HELPER", GC4_STOP_MOVES_ROOTED_C), encoding="utf-8")
    built = link_c_harness_bounded(source, exe, runtime_archive=pcc_runtime_archive,
                                   extra_include_dirs=(str(runtime / "include"), str(runtime / "src")),
                                   timeout=60)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, (run.returncode, run.stderr)


def test_native_set_clear_detaches_before_key_destructor_reentry(tmp_path: Path, pcc_runtime_archive):
    # Ordinary RC runs qualifying finalizers synchronously. Do not require
    # other collectors to mimic its scheduling; moving/retention are covered
    # separately by the real selected GC4 and five-backend public programs.
    source = tmp_path / "clear_callback.py"
    exe = tmp_path / "clear_callback"
    source.write_text('''import gc
active = {17, 19}
events = []
class Released:
    def __init__(self, label):
        self.label = label
    def __hash__(self):
        return self.label
    def __del__(self):
        events.append(17 in active or 19 in active)
        active.add(777)
        gc.collect()
def fill():
    active.add(Released(1))
    active.add(Released(2))
def run():
    fill()
    saved_clear = active.clear
    saved_clear()
    gc.collect()
    print(len(events), any(events), len(active), 777 in active)
run()
''', encoding="utf-8")
    ref = subprocess.run([sys.executable, str(source)], text=True, capture_output=True, timeout=10)
    assert ref.returncode == 0 and ref.stdout.strip() == "2 False 1 True", (ref.stdout, ref.stderr)
    compile_python(str(source), str(exe), backend="self", runtime_archive=str(pcc_runtime_archive))
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=15,
                         env=dict(os.environ, PCC_GC_BACKEND="0"))
    assert run.returncode == 0 and run.stdout == ref.stdout and not run.stderr, (run.stdout, run.stderr)


TYPED_MUTATORS = '''import gc
class Payload:
    def __init__(self, value):
        self.value = value
    def __hash__(self):
        return self.value

def typed_pop(shared: set):
    return shared.pop()

def typed_clear(shared: set):
    return shared.clear()

def dynamic_pop(shared):
    return shared.pop()

def main():
    obj = Payload(31)
    saved = {obj}.pop()
    gc.collect()
    print(saved is obj, saved.value)
    print(typed_pop({Payload(32)}).value)
    print(dynamic_pop({Payload(33)}).value)
    print(typed_clear({Payload(34)}) is None)
    try:
        typed_pop(set())
    except KeyError:
        print("original key error")
    def pop_through_callee(value):
        return value
    s = {Payload(35)}
    print(pop_through_callee(s.pop()).value, len(s))
main()
'''

def test_public_pop_and_clear_keep_original_method_result_and_error(tmp_path: Path, pcc_runtime_archive):
    source = tmp_path / "set_result.py"
    binary = tmp_path / "set_result"
    source.write_text(TYPED_MUTATORS, encoding="utf-8")
    ref = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert ref.returncode == 0 and not ref.stderr, ref.stderr
    assert ref.stdout.splitlines() == ["True 31", "32", "33", "True", "original key error", "35 0"]
    compile_python(str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert run.returncode == 0 and not run.stderr, (backend, run.stdout, run.stderr)
        assert run.stdout == ref.stdout, (backend, run.stdout)
