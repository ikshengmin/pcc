"""Bounded terminal-frame clear primitive, not compiler activation.

Contract: docs/project-intent.md, five-GC roots and Python finalization.
The native fixtures witness runtime admission and real relocation.
"""
import pytest

from tests.python.test_generator_moving_frame_take import _native, _run


NATIVE_SHAPES = r'''
#include "py_internal.h"
#include <stdio.h>
#include <stdint.h>
extern int32_t pcc_gc_backend4_no_refcounts, pcc_gc_backend_frozen;
extern int32_t pcc_log_init_state, pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int64_t pcc_thread_no_park_depth(void);
static PyObject *seed(int index) {
    if (index % 3 == 0) return NULL;
    if (index % 3 == 1) return py_None;
    return py_int_from_i64(index + 41);
}
int main(void) {
    int backend = pcc_gc_backend();
    py_gc_disable();
    PyObject *roots[3] = {0};
    int32_t map[1] = {3};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(18);
    roots[1] = py_list_new(0);
    roots[2] = py_exc_new(19, "preserved");
    if (!roots[0] || !roots[1] || !roots[2]) return 1;
    int admitted = backend >= 3 && pcc_gc_backend_frozen && pcc_gc_backend4_no_refcounts
        && pcc_log_init_state == 2 && !pcc_diagnostics_runtime_log_fast_state
        && !pcc_gc_refcount_provenance_probe;
    if (backend >= 3 && !pcc_gc_refcount_provenance_probe && !pcc_diagnostics_runtime_log_fast_state && !admitted) return 2;
    for (int i = 0; i < 18; i++)
        if (i % 3 == 2 && !py_mem_ptr_is_tagged_int(seed(i))) return 13;
    int counts[] = {0, 1, 2, 15, 16, 17, 18};
    for (int test = 0; test < 7; test++) {
        for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
        int count = counts[test];
        py_raise(roots[2]);
        int result = py_gen_frame_clear_group(roots[0], 0, count);
        if (result != (admitted && count >= 2 && count <= 16)) return 3;
        if (py_current_exception() != roots[2]) return 4;
        py_clear_exception();
        for (int i = 0; i < 18; i++) {
            PyObject *expected = result && i < count ? py_None : seed(i);
            if (((PyListObject *)roots[0])->items[i] != expected) return 5;
        }
        if (!result) {
            /* This is the unchanged caller fallback, with its original order. */
            for (int i = 0; i < count; i++)
                py_gen_frame_set(pcc_gc_load_ptr(0, &roots[0]), i, py_None);
        }
        for (int i = 0; i < count; i++)
            if (((PyListObject *)roots[0])->items[i] != py_None) return 6;
    }
    for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
    if (py_gen_frame_clear_group(roots[0], -1, 2)
            || py_gen_frame_clear_group(roots[0], 17, 2)
            || py_gen_frame_clear_group(roots[0], INT64_MAX, 2)) return 7;
    for (int i = 0; i < 18; i++)
        if (((PyListObject *)roots[0])->items[i] != seed(i)) return 8;
    /* A rejected partially invalid range is left to the serial caller. */
    py_gen_frame_set(roots[0], 17, py_None);
    py_gen_frame_set(roots[0], 18, py_None);
    if (((PyListObject *)roots[0])->items[17] != py_None
            || ((PyListObject *)roots[0])->items[16] != seed(16)) return 9;
    /* A nonfinalizable container can own a finalizable/weakly observed graph.
       The whole range must reject, not only objects with direct callbacks. */
    for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
    py_list_append(roots[1], roots[2]);
    py_gen_frame_set(roots[0], 15, roots[1]);
    if (py_gen_frame_clear_group(roots[0], 0, 16)) return 11;
    for (int i = 0; i < 18; i++)
        if (((PyListObject *)roots[0])->items[i] != (i == 15 ? roots[1] : seed(i))) return 12;
    py_gen_frame_set(roots[0], 15, seed(15));
    if (pcc_py_gc_minor_graph_lock_depth() || pcc_thread_no_park_depth()) return 10;
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("FRAME_CLEAR_GROUP_SHAPES_OK");
    return 0;
}
'''


NATIVE_RELOCATION = r'''
#include "py_internal.h"
#include <stdio.h>
extern PyObject *pcc_gc_generational_oldify_copy(PyObject *);
int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 1;
    py_gc_disable();
    PyObject *roots[2] = {0};
    int32_t map[1] = {2};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(4);
    roots[1] = backend == 3 ? py_bytes_new("moved", 5) : py_list_new(0);
    if (!roots[0] || !roots[1]) return 2;
    for (int i = 0; i < 4; i++) py_gen_frame_set(roots[0], i, roots[1]);
    /* Only nonowning target cells admit; the untouched ends still prove
       GC3 value relocation and GC4 owner/value canonicalization. */
    py_gen_frame_set(roots[0], 1, NULL);
    py_gen_frame_set(roots[0], 2, py_int_from_i64(42));
    pcc_gc_unpin(roots[0]);
    pcc_gc_unpin(roots[1]);
    PyObject *source = roots[0], *value = roots[1], *owner = source, *moved;
    if (backend == 3) {
        pcc_py_gc_minor_graph_lock();
        moved = pcc_gc_generational_oldify_copy(value);
        pcc_py_gc_minor_graph_unlock();
    } else {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(128) <= 0
                || !pcc_gc_relocation_set_contains(source)
                || !pcc_gc_relocation_set_contains(value)) return 3;
        moved = pcc_gc_relocate_copy(value, sizeof(PyListObject));
        owner = pcc_gc_relocate_copy(source, sizeof(PyListObject));
        if (!owner || owner == source) return 4;
    }
    if (!moved || moved == value) return 5;
    if (py_gen_frame_clear_group(source, 1, 2) != 1) return 6;
    if (pcc_gc_load_ptr(0, &roots[0]) != owner) return 7;
    if (((PyListObject *)owner)->items[1] != py_None
            || ((PyListObject *)owner)->items[2] != py_None) return 8;
    if (pcc_gc_load_ptr(owner, &((PyListObject *)owner)->items[0]) != moved
            || pcc_gc_load_ptr(owner, &((PyListObject *)owner)->items[3]) != moved) return 9;
    if (py_header(owner)->flags & PY_FLAG_GC_PINNED) return 10;
    for (int i = 0; i < 2; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(roots);
    puts("FRAME_CLEAR_GROUP_RELOCATION_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_clear_admission_bounds_exception_and_serial_fallback(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_clear_group_shapes", NATIVE_SHAPES)
    for backend in range(5):
        result = _run(executable, backend)
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_REFCOUNT_PROVENANCE_PROBE="1")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        result = _run(executable, backend, PCC_LOG="gc", PCC_LOG_FILE=str(tmp_path / f"clear-gc{backend}.log"))
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend


@pytest.mark.integration
def test_native_clear_after_real_gc3_value_and_gc4_owner_relocation(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_clear_group_relocation", NATIVE_RELOCATION)
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_MINOR_ALLOC_MAX="4096")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_RELOCATION_OK\n", ""), backend
