"""Coallocated private frames retain owners and survive collector switches."""

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_generator_frame_default_bulk import PROGRAM


@pytest.mark.parametrize("proven,flags,refs,expected", (
    (False, 0, 1, 0),
    (True, 0, 1, 1),
    (True, 2048, 1, 0),
    (True, 524288, 1, 0),
    (True, 0, 0, 0),
    (True, 0, -1, 0),
))
def test_switched_container_release_requires_live_proven_header(proven, flags, refs, expected):
    from tests.python.test_foreign_address_leases import RUNTIME, _functions

    def load_header(value, offset):
        assert proven, "an unknown address must not be dereferenced"
        return {0: refs, 8: 5, 12: flags}[offset]

    namespace = dict(
        pcc_gc_object_is_known_no_lock=lambda value: 0,
        pcc_gc_pointer_is_managed=lambda value: int(proven),
        load_i32=load_header,
        load_i64=load_header,
        PYOBJECTHEADER_FLAGS_OFFSET=12,
        PYOBJECTHEADER_REFCOUNT_OFFSET=0,
        PYOBJECTHEADER_TYPE_TAG_OFFSET=8,
        _gc_graph_leaf_tag=lambda tag: 0,
    )
    _functions(RUNTIME / "py_obj.py", {"_gc4_old_slot_is_releasable"}, namespace)
    assert namespace["_gc4_old_slot_is_releasable"](4096) == expected


@pytest.mark.integration
def test_inline_factory_preserves_python_lifetimes(tmp_path, monkeypatch, pcc_runtime_archive):
    monkeypatch.delenv("PCC_INLINE_GENERATOR_FRAMES", raising=False)
    source = tmp_path / "lifetimes.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "lifetimes"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH="/nonexistent"))
        assert (run.returncode, run.stdout, run.stderr) == (0, "BULK_FRAME_DEFAULT_OK\n", ""), backend


def test_inline_factory_is_default_and_legacy_bulk_control_wins(tmp_path, monkeypatch):
    source = tmp_path / "factory.py"
    source.write_text("def values(value):\n    yield value\n")
    emitted = {}
    for enabled, legacy, expected in ((None, "0", True), ("0", "0", False), ("1", "0", True), ("1", "1", False)):
        if enabled is None:
            monkeypatch.delenv("PCC_INLINE_GENERATOR_FRAMES", raising=False)
        else:
            monkeypatch.setenv("PCC_INLINE_GENERATOR_FRAMES", enabled)
        monkeypatch.setenv("PCC_DISABLE_BULK_GENERATOR_FRAME_INIT", legacy)
        output = tmp_path / (str(enabled) + legacy + ".ll")
        compile_python(str(source), str(output), backend="self", libpython_mode="off",
                       ir_scaffold_mode="on", emit_llvm_only=True)
        calls = [line for line in output.read_text().splitlines() if "call " in line]
        assert any("@py_gen_inline_frame_new(" in line for line in calls) == expected
        emitted[enabled, legacy] = output.read_text()
    assert emitted[None, "0"] == emitted["1", "0"]


@pytest.mark.integration
def test_inline_storage_ownership_slots_and_same_backend_resets(tmp_path, pcc_runtime_archive):
    source = tmp_path / "frame.c"
    source.write_text(r'''#include "py_runtime.h"
#include <stdio.h>
#include <stdlib.h>
typedef void (*PccPyObjSlotVisitorI64)(PyObject **, int64_t, void *);
extern int64_t pcc_gc_visit_object_slots(PyObject *, PccPyObjSlotVisitorI64, void *);
extern int64_t pcc_gc_visit_object_slots_slice(PyObject *, int64_t, int64_t, PccPyObjSlotVisitorI64, void *, int64_t *);
static void visit(PyObject **slot, int64_t role, void *ctx) {
    if (role == 1 && *slot) (*(int64_t *)ctx)++;
}
static PyObject *resume(PyObject *gen, PyObject *frame) {
    if (py_gen_frame_len(frame) != 33) return NULL;
    return py_gen_frame_get(frame, 32);
}
int main(int argc, char **argv) {
    if (argc != 3 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 1;
    PyObject *roots[5] = {NULL, NULL, NULL, NULL, NULL};
    int32_t map[1] = {5};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_inline_frame_new(33);
    if (!roots[0] || py_gen_frame_len(roots[0]) != 33) return 2;
    int is_inline = atoi(argv[1]) == 0;
    if ((((PyObjectHeader *)roots[0])->type_tag == PY_TYPE_GEN) != is_inline) return 3;
    roots[1] = py_gen_from_inline_frame((void *)resume, roots[0]);
    if (!roots[1] || (is_inline && roots[1] != roots[0])) return 4;
    roots[2] = py_gen_completed(roots[1]);
    if (!roots[2] || roots[2] == roots[1]) return 12;
    if (py_gen_take_completed(roots[2]) != roots[1]) return 13;
    pcc_gc_store_root(&roots[2], NULL);
    roots[2] = py_str_new("alive", 5);
    py_gen_frame_set(roots[0], 32, roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    roots[3] = py_list_new(0);
    py_gen_frame_set(roots[0], 31, roots[3]);
    int64_t held_refs = ((PyObjectHeader *)roots[3])->refcount;
    roots[4] = py_tuple_new(3000);
    for (int item = 0; item < 3000; item++) py_tuple_set_item(roots[4], item, py_None);
    py_gen_frame_set(roots[0], 30, roots[4]);
    int64_t large_refs = ((PyObjectHeader *)roots[4])->refcount;
    if (pcc_gc_set_backend(atoi(argv[2])) != 0) return 5;
    py_gc_collect();
    py_gen_frame_set(roots[0], 31, NULL);
    if (((PyObjectHeader *)roots[3])->refcount != held_refs - 1) return 14;
    pcc_gc_store_root(&roots[3], NULL);
    roots[2] = py_gen_frame_get(roots[0], 30);
    if (roots[2] != roots[4] || ((PyObjectHeader *)roots[4])->refcount != large_refs + 1) return 16;
    pcc_gc_store_root(&roots[2], NULL);
    py_gen_frame_set(roots[0], 30, NULL);
    if (((PyObjectHeader *)roots[4])->refcount != large_refs - 1) return 15;
    pcc_gc_store_root(&roots[4], NULL);
    roots[2] = py_str_new("changed", 7);
    py_gen_frame_set(roots[0], 32, roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    py_gc_collect();
    roots[2] = py_gen_next(roots[1]);
    if (!roots[2] || py_str_len(roots[2]) != 7) return 6;
    pcc_gc_store_root(&roots[2], NULL);
    roots[2] = py_gen_frame_take(roots[0], 32);
    if (!roots[2] || py_str_len(roots[2]) != 7) return 7;
    pcc_gc_store_root(&roots[2], NULL);
    if (py_gen_frame_get(roots[0], 32) != NULL) return 8;
    if (is_inline) {
        int64_t count = 0;
        if (!pcc_gc_visit_object_slots(roots[0], visit, &count) || count != 31) return 9;
        int64_t cursor = 0, state[2], sliced = 0;
        do {
            if (!pcc_gc_visit_object_slots_slice(roots[0], cursor, 3, visit, &sliced, state)) return 10;
            cursor = state[0];
        } while (cursor >= 0);
        if (sliced != count) return 11;
    }
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("INLINE_FRAME_OK");
    return 0;
}
''')
    binary = tmp_path / "frame"
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive)
    # The collector is fixed once an object exists; a reset to the same
    # collector between the frame's stores stays legal.
    for initial, final in [(n, n) for n in range(5)]:
        run = subprocess.run([str(binary), str(initial), str(final)], capture_output=True,
                             text=True, timeout=20)
        assert (run.returncode, run.stdout, run.stderr) == (0, "INLINE_FRAME_OK\n", ""), (initial, final)


@pytest.mark.integration
def test_inline_generator_protocol_and_cycle_collection(tmp_path, monkeypatch, pcc_runtime_archive):
    from tests.python.test_async_suspension_roots import _assert_native_async_program_matches_cpython
    from tests.python.test_native_coroutine_protocol import _PROGRAM

    monkeypatch.setenv("PCC_INLINE_GENERATOR_FRAMES", "1")
    monkeypatch.setenv("PCC_DISABLE_BULK_GENERATOR_FRAME_INIT", "0")
    _assert_native_async_program_matches_cpython(tmp_path, monkeypatch, pcc_runtime_archive, _PROGRAM + r'''
import weakref
events = []
class Marker:
    def __del__(self):
        events.append("deleted")
def generator():
    try:
        value = yield "started"
        assert value == 41
        yield value + 1
    except ValueError:
        yield "caught"
    finally:
        events.append("closed")
g = generator()
assert next(g) == "started"
assert g.send(41) == 42
assert g.throw(ValueError("stop")) == "caught"
g.close()
assert events == ["closed"]
def cyclic(holder, marker):
    yield marker
def make_cycle():
    marker = Marker()
    watch = weakref.ref(marker)
    holder = []
    iterator = cyclic(holder, marker)
    holder.append(iterator)
    assert next(iterator) is marker
    return watch
watch = make_cycle()
gc.collect()
assert watch() is None
assert events == ["closed", "deleted"]
print("INLINE_PROTOCOL_CYCLE_OK")
''')
