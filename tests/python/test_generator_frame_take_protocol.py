"""Frame take's controlled NULL-clear obligations.

The native cases control mark colors under an outer graph lock: they compare
fused/split state, not autonomous collection, throughput or SATB correctness.
"""

import pytest

from tests.python.test_generator_moving_frame_take import _native


NULL_CLEAR = r'''
#include "py_internal.h"
#include <stdio.h>
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_log_init_state;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern void pcc_gc_generational_promote_young_if_known(void *);
extern void pcc_gc_set_mark_color(void *, int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int64_t pcc_gc_metric_store_count(void);
extern int64_t pcc_gc_store_buffer_owner_entries(void *);
extern int64_t pcc_gc_store_buffer_slot_pending(void *);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
int main(int argc, char **argv) {
    int backend = pcc_gc_backend();
    if (argc != 2 || (backend != 3 && backend != 4)) return 1;
    if (!pcc_gc_backend4_no_refcounts) return 2;
    py_gc_disable();
    PyObject *roots[3] = {NULL, NULL, NULL};
    int32_t map[1] = {3};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(1);
    roots[1] = py_list_new(0);
    if (!roots[0] || !roots[1]) return 3;
    pcc_py_gc_minor_graph_lock();
    pcc_gc_generational_promote_young_if_known(roots[0]);
    if (!(py_header(roots[0])->flags & PY_FLAG_GC_OLD)) return 4;
    if (!(py_header(roots[1])->flags & PY_FLAG_GC_YOUNG)) return 5;
    py_gen_frame_set(roots[0], 0, roots[1]);
    PyListObject *frame = (PyListObject *)roots[0];
    if (frame->items[0] != roots[1]) return 6;
    if (!(py_header(roots[0])->flags & PY_FLAG_GC_REMEMBERED)) return 7;
    if (backend == 4 && (pcc_gc_store_buffer_owner_entries(frame) != 1
            || pcc_gc_store_buffer_slot_pending(&frame->items[0]) != 1)) return 8;
    int marking = argv[1][0] == 'a';
    int split = argv[1][1] == 's';
    pcc_gc_set_mark_color(roots[0], PY_FLAG_GC_BLACK);
    pcc_gc_set_mark_color(roots[1], PY_FLAG_GC_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, marking, __ATOMIC_RELEASE);
    if (pcc_log_init_state != 2 || pcc_diagnostics_runtime_log_fast_state != 0) return 9;
    int64_t gray = pcc_gc_gray_count_load_acquire();
    int64_t stores = pcc_gc_metric_store_count();
    int64_t refs = py_header(roots[1])->refcount;
    int64_t depth = pcc_py_gc_minor_graph_lock_depth();
    if (split) {
        roots[2] = py_list_get(roots[0], 0);
        py_list_set(roots[0], 0, NULL);
    } else {
        roots[2] = py_gen_frame_take(roots[0], 0);
    }
    if (roots[2] != roots[1] || frame->items[0] != NULL) return 10;
    if (pcc_py_gc_minor_graph_lock_depth() != depth) return 11;
    if (pcc_gc_metric_store_count() != stores + 1) return 12;
    if (py_header(roots[2])->refcount != refs) return 13;
    if (pcc_gc_gray_count_load_acquire() != gray || pcc_gc_mark_active != marking) return 14;
    if (!(py_header(roots[2])->flags & PY_FLAG_GC_WHITE)) return 15;
    if (py_header(roots[2])->flags & PY_FLAG_GC_PINNED) return 16;
    if (!(py_header(roots[0])->flags & PY_FLAG_GC_REMEMBERED)) return 17;
    if (backend == 4 && (pcc_gc_store_buffer_owner_entries(frame) != 1
            || pcc_gc_store_buffer_slot_pending(&frame->items[0]) != 1)) return 18;
    /* Drop the synthetic phase before releasing the outer graph lock.
     * No collection occurs here: a stale remembered slot remains queued
     * for its ordinary consumer to re-read, and NULL adds no gray edge. */
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    puts("FRAME_TAKE_NULL_CLEAR_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_take_null_clear_matches_split_mark_and_remembered_state(
    tmp_path, pcc_runtime_archive,
):
    import os
    from tests.python.process_timeout import run_process_group_timeout

    executable = _native(tmp_path, pcc_runtime_archive, "frame_null_clear", NULL_CLEAR)
    for backend in (3, 4):
        for mode in ("if", "is", "af", "as"):
            env = dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="",
                       PCC_GC_REFCOUNT_PROVENANCE_PROBE="0")
            ran = run_process_group_timeout([str(executable), mode], timeout=20, env=env)
            assert (ran.returncode, ran.stdout, ran.stderr) == (
                0, "FRAME_TAKE_NULL_CLEAR_OK\n", "",
            ), (backend, mode)
