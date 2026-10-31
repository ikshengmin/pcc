"""Enumerate live GC objects by header flag, without tracked lists.

pcc_allocator_object_cells_enumerate walks the allocator's object slabs and
pcc_gc_managed_pointer_index_enumerate the per-object registrations of large
objects. Together they are the collection-time source of tracked candidates
that replaces per-object list maintenance (design: GC0-3 flag-only tracking).
The probe creates small and large containers, tracks, untracks and frees
some, and checks that the walk reports exactly the objects whose header
carries the tracked flag, each once, on every collector.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


C_PROGRAM = r"""
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void py_gc_track(PyObject *o);
extern void py_gc_untrack(PyObject *o);
extern int64_t pcc_allocator_object_cells_enumerate(PyObject **out, int64_t capacity, int64_t required_flags);
extern int64_t pcc_gc_managed_pointer_index_enumerate(PyObject **out, int64_t capacity, int64_t required_flags);

#define TRACKED 2

static int32_t flags_of(PyObject *o) { return *(int32_t *)((char *)o + 12); }

/* Occurrences of each probe object among the tracked objects walked now. */
static int walk(PyObject **objects, int n, int *seen) {
    int64_t small = pcc_allocator_object_cells_enumerate(NULL, 0, TRACKED);
    int64_t large = pcc_gc_managed_pointer_index_enumerate(NULL, 0, TRACKED);
    if (small < 0 || large < 0) return -1;
    PyObject **found = malloc(sizeof(PyObject *) * (size_t)(small + large + 1));
    if (!found) return -1;
    if (pcc_allocator_object_cells_enumerate(found, small, TRACKED) != small) return -1;
    if (pcc_gc_managed_pointer_index_enumerate(found + small, large, TRACKED) != large) return -1;
    for (int i = 0; i < n; i++) seen[i] = 0;
    for (int64_t k = 0; k < small + large; k++) {
        if ((flags_of(found[k]) & TRACKED) == 0) return -2;
        for (int i = 0; i < n; i++) if (found[k] == objects[i]) seen[i]++;
    }
    free(found);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int backend = atoi(argv[1]);
    if (pcc_gc_set_backend(backend) != 0) return 2;
    PyObject *list = py_list_new(0);
    PyObject *dict = py_dict_new();
    PyObject *small = py_tuple_new(3);
    PyObject *big = py_tuple_new(4096);          /* 32 KiB of items: no slab class */
    PyObject *text = py_str_new("leaf", 4);
    if (!list || !dict || !small || !big || !text) return 3;
    for (int i = 0; i < 3; i++) py_tuple_set_item(small, i, py_int_from_i64(i));
    for (int i = 0; i < 4096; i++) py_tuple_set_item(big, i, py_int_from_i64(i));
    py_gc_track(list);
    py_gc_track(dict);
    py_gc_track(big);
    py_gc_track(small);
    py_gc_untrack(small);
    PyObject *objects[5] = {list, dict, small, big, text};
    int seen[5];
    int status = walk(objects, 5, seen);
    if (status != 0) return 10 - status;
    /* Exactly the flagged objects, each once. Large objects are walked from
     * GC0's managed pointer set; tracing collectors index every object in
     * their own object index instead, outside this walk. */
    for (int i = 0; i < 5; i++) {
        if (i == 3 && backend != 0) continue;
        int flagged = (flags_of(objects[i]) & TRACKED) != 0;
        if (seen[i] != flagged) return 20 + i;
    }
    int big_seen = backend == 0 ? seen[3] : -1;
    printf("%d %d %d %d", seen[0], seen[1], seen[2], big_seen);
    /* A dead object leaves the walk with its cell. */
    py_decref(dict);
    status = walk(objects, 2, seen);
    if (status != 0) return 30 - status;
    if (seen[1] != 0) return 33;
    printf(" %d\n", seen[0]);
    py_decref(list);
    py_decref(small);
    py_decref(big);
    py_decref(text);
    return 0;
}
"""


@pytest.mark.integration
def test_tracked_object_walk_reports_exactly_flagged_live_objects(tmp_path, pcc_runtime_archive):
    source = tmp_path / "gc_object_walk.c"
    source.write_text(C_PROGRAM, encoding="utf-8")
    binary = tmp_path / "gc_object_walk"
    compile_owned_c_with_runtime(source, binary, runtime_archive=Path(pcc_runtime_archive))
    outputs = {}
    for backend in range(5):
        ran = subprocess.run([str(binary), str(backend)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent"))
        assert (ran.returncode, ran.stderr) == (0, ""), backend
        outputs[backend] = ran.stdout
    # list and dict are tracked wherever tracking is maintained, the small
    # tuple was untracked, the string never was; after the dict dies only
    # the list remains. The large tuple is checked where GC0 registers it.
    assert outputs[0] == "1 1 0 1 1\n", outputs[0]
    for backend in (1, 2, 3):
        assert outputs[backend] == "1 1 0 -1 1\n", (backend, outputs[backend])
    # The threaded runtime's GC4 keeps no tracked flags.
    assert outputs[4] == "0 0 0 -1 0\n", outputs[4]
