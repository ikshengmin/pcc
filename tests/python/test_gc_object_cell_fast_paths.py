"""Allocation and terminal-free paths that trust what their caller proved.

Specification: docs/project-intent.md (five-GC lifetime equality, exact
provenance). pcc_gc_alloc publishes a cell the object allocator just handed
out without re-validating it through the granule radix
(pcc_allocator_object_publish_fresh); GC0's terminal free retires a cell its
predicate just found LIVE with one lifecycle CAS
(pcc_allocator_object_retire_live); a GC1-GC3 registration finds the cell's
record, span and number with one span lookup
(pcc_allocator_object_cell_locate) and assigns its mark bit only when the bit
differs (pcc_gc_page_mark_assign_cell). This drives the production runtime
archive under all five collectors and checks the provenance each path
publishes against the validating predicates.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"

HARNESS = r'''
    #define _GNU_SOURCE
    #include "py_internal.h"
    #include <stdint.h>
    #include <stdio.h>
    #include <stdlib.h>

    extern void *pcc_allocator_alloc_object(int64_t size);
    extern int64_t pcc_allocator_object_publish_fresh(void *ptr);
    extern int64_t pcc_allocator_object_retire_live(void *ptr);
    extern void *pcc_allocator_object_cell_locate(void *ptr, void *out);
    extern void *pcc_allocator_object_cell_span(void *ptr);
    extern int64_t pcc_allocator_object_cell_number(void *span, void *ptr);
    extern void *pcc_allocator_object_node_record_ensure(void *ptr);
    extern void *pcc_allocator_object_node_record_find(void *ptr);
    extern int64_t pcc_allocator_exact_object_cache[8192];
    extern int64_t pcc_gc_granule_is_object_start(void *ptr);
    extern int64_t pcc_gc_page_mark_test(void *obj);
    extern void pcc_gc_page_mark_assign_cell(void *span, int64_t cell, int64_t marked);
    extern int64_t pcc_gc_free_watch_end(void *record);

    static int cached(void *obj) {
        uint64_t bits = (uint64_t)(uintptr_t)obj;
        return pcc_allocator_exact_object_cache[((bits >> 4) ^ (bits >> 12)) & 8191]
            == (int64_t)bits;
    }

    int main(void) {
        int64_t backend = atoi(getenv("PCC_GC_BACKEND"));
        if (pcc_gc_set_backend(backend) != 0) return 10;
        py_gc_init();
        py_gc_disable();

        /* A small object: an object cell published LIVE, seeded in the
           exact cache, answered by the validating predicate. */
        PyObject *small = pcc_gc_alloc(48, PY_TYPE_TUPLE, 0);
        if (small == NULL) return 1;
        if (pcc_gc_granule_is_object_start(small) != 1) return 2;
        if (!cached(small)) return 3;
        if (pcc_gc_pointer_is_managed(small) != 1) return 4;
        /* Publishing it again is idempotent, as the granule publish is. */
        if (pcc_allocator_object_publish_fresh(small) != 1) return 5;

        /* A large allocation is a raw mapping, not a cell: the fresh
           publish declines and the exact set keeps its provenance. */
        PyObject *large = pcc_gc_alloc(20000, PY_TYPE_TUPLE, 0);
        if (large == NULL) return 6;
        if (pcc_allocator_object_publish_fresh(large) != 0) return 7;
        if (pcc_gc_granule_is_object_start(large) == 1) return 8;
        if (pcc_gc_pointer_is_managed(large) != 1) return 9;

        /* A RESERVED cell straight from the allocator publishes once. */
        void *cell = pcc_allocator_alloc_object(64);
        if (cell == NULL) return 11;
        if (pcc_gc_granule_is_object_start(cell) == 1) return 12;
        if (pcc_allocator_object_publish_fresh(cell) != 1) return 13;
        if (pcc_gc_granule_is_object_start(cell) != 1) return 14;
        /* retire_live moves exactly a LIVE cell to FREE, once. */
        if (pcc_allocator_object_retire_live(cell) != 1) return 15;
        if (pcc_gc_granule_is_object_start(cell) == 1) return 16;
        if (pcc_allocator_object_retire_live(cell) != 0) return 17;

        if (backend >= 1 && backend <= 3) {
            /* One lookup answers record, span and cell number. */
            void *located[2] = {NULL, NULL};
            void *record = pcc_allocator_object_cell_locate(small, located);
            void *span = pcc_allocator_object_cell_span(small);
            if (record == NULL || record != pcc_allocator_object_node_record_find(small)) return 20;
            if (located[0] != span || span == NULL) return 21;
            if ((int64_t)(intptr_t)located[1] != pcc_allocator_object_cell_number(span, small)) return 22;
            if (pcc_gc_object_index_find(small) != record) return 23;
            /* Interior and foreign addresses are no cell. */
            if (pcc_allocator_object_cell_locate((char *)small + 16, located) != NULL) return 24;
            if (pcc_allocator_object_cell_locate(&backend, located) != NULL) return 25;
            /* Registered outside a mark: white. Assigning the bit it has
               changes nothing; assigning the other one flips it. */
            int64_t number = (int64_t)(intptr_t)located[1];
            if (pcc_gc_page_mark_test(small) != 0) return 26;
            pcc_gc_page_mark_assign_cell(span, number, 0);
            if (pcc_gc_page_mark_test(small) != 0) return 27;
            pcc_gc_page_mark_assign_cell(span, number, 32);
            if (pcc_gc_page_mark_test(small) != 1) return 28;
            pcc_gc_page_mark_assign_cell(span, number, 32);
            if (pcc_gc_page_mark_test(small) != 1) return 29;
            pcc_gc_page_mark_assign_cell(span, number, 0);
            if (pcc_gc_page_mark_test(small) != 0) return 30;
        }

        if (backend == 0) {
            /* GC0's terminal free: the watch hears the death, the cell is
               FREE afterwards and the next allocation of its class reuses
               it (the thread's magazine is LIFO). */
            PyObject *tuple = py_tuple_new(2);
            if (tuple == NULL) return 40;
            if (pcc_gc_granule_is_object_start(tuple) != 1) return 41;
            int64_t watch[3];
            pcc_gc_free_watch_begin(watch, tuple);
            py_decref(tuple);
            if (pcc_gc_free_watch_end(watch) != 1) return 42;
            if (pcc_gc_granule_is_object_start(tuple) == 1) return 43;
            if (pcc_gc_pointer_is_managed(tuple) != 0) return 44;
            PyObject *again = py_tuple_new(2);
            if (again != tuple) return 45;
            if (pcc_gc_granule_is_object_start(again) != 1) return 46;
            py_decref(again);
            /* A death the watch does not name leaves it unnoted. */
            PyObject *other = py_tuple_new(2);
            PyObject *watched = py_tuple_new(2);
            pcc_gc_free_watch_begin(watch, watched);
            py_decref(other);
            if (pcc_gc_free_watch_end(watch) != 0) return 47;
            py_decref(watched);
        }
        printf("fast-paths-ok\n");
        return 0;
    }
'''


def test_object_cell_fast_paths_publish_and_retire_exact_cells(
    tmp_path: Path, pcc_runtime_archive: Path,
) -> None:
    source = tmp_path / "cell_fast_paths.c"
    output = tmp_path / "cell_fast_paths"
    source.write_text(textwrap.dedent(HARNESS), encoding="utf-8")
    built = subprocess.run(
        [os.environ.get("CC", "cc"), "-std=c11", "-pthread",
         f"-I{RUNTIME_DIR / 'include'}", f"-I{RUNTIME_DIR / 'src'}",
         str(source), str(pcc_runtime_archive), "-lm", "-o", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    for backend in range(5):
        ran = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert ran.returncode == 0, f"GC{backend}: {ran.stdout}{ran.stderr}"
        assert ran.stdout == "fast-paths-ok\n"
