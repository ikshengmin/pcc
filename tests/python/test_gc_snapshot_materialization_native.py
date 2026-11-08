"""Owned C/self-runtime proof of retained outer tracked-view node lifetimes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.tools.runtime_archive_provenance import manifest_path_for_archive
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern void pcc_gc_default_table_lock(void);
extern void pcc_gc_default_table_unlock(void);
typedef struct PyInstanceObject PyInstanceObject;
extern PyClassObject *py_class_new(const char *, PyClassObject **, int32_t, const char **, int32_t);
extern void py_class_add_method(PyClassObject *, const char *, PyObject *);
extern PyObject *py_instance_new(PyClassObject *);
extern void py_instance_set_field(PyInstanceObject *, int32_t, PyObject *);
extern int64_t pcc_gc_tracked_materialize(void);
extern void pcc_gc_tracked_dematerialize(int64_t);
extern int64_t pcc_gc_backend0_collect_candidates(void *, int64_t);
extern int32_t pcc_gc_tracking_flag_mode;
extern int32_t pcc_gc_backend_frozen;
extern int32_t pcc_gc_backend4_auto_step_pending;
/* A process-lifetime empty LIST image, not a managed allocation. */
static struct {
    PyObjectHeader header;
    int64_t length;
    int64_t capacity;
    PyObject **items;
} cold_image = { { 1, PY_TYPE_LIST, 1 }, 0, 0, 0 };
extern int32_t pcc_gc_tracking_materialized;
extern int64_t pcc_gc_table_lock_depth;
extern void *py_gc_index_find(PyObject *);
typedef struct Node {
    PyObject *object;
    int64_t gc_refs;
    int32_t reachable;
    int32_t shard;
    struct Node *prev;
    struct Node *next;
} Node;
typedef struct Candidate { Node *node; PyObject *object; } Candidate;
extern Node *py_gc_head;
extern Node *pcc_gc_deferred_node_free_head;
static PyObject *roots[3];
static PyObject *saved;
static int finalized;
static int callback_failure;

static int fail(int code) {
    fprintf(stderr, "SNAPSHOT_OWNER_FAIL %d\n", code);
    return code;
}
static int contains(PyObject *list, PyObject *target) {
    int found = 0;
    int64_t count = py_list_len(list);
    for (int64_t i = 0; i < count; i++) {
        PyObject *obj = py_list_get(list, i);
        if (!obj) return -1;
        if (obj == target) found++;
        py_decref(obj);
    }
    return found;
}
static PyObject *finalize(PyObject *self) {
    finalized++;
    Node *outer = (Node *)py_gc_index_find(self);
    if (!outer || !pcc_gc_tracking_materialized) callback_failure = 1;
    PyObject *objects = py_gc_get_objects();
    if (!objects || contains(objects, self) != 1) callback_failure = 2;
    if (py_gc_index_find(self) != outer || !pcc_gc_tracking_materialized)
        callback_failure = 3;
    if (outer && outer->object != self) callback_failure = 4;
    py_decref(objects);
    /* Keep this isolate alive for the collector's post-finalizer recheck. */
    py_incref(self);
    saved = self;
    if (py_gc_collect() != 0) callback_failure = 5;
    return py_None;
}
int main(void) {
    int backend = (int)pcc_gc_backend();
    py_gc_disable();
    /* No managed allocation precedes this snapshot. Its output allocation
     * used to freeze GC0 and recycle the cursor captured from the old list. */
    if (pcc_gc_backend_frozen) return fail(30);
    if (pcc_gc_pointer_register_static_literal((PyObject *)&cold_image) < 0)
        return fail(31);
    py_gc_track((PyObject *)&cold_image);
    if (pcc_gc_backend_frozen) return fail(32);
    PyObject *cold = py_gc_get_objects();
    if (!cold || contains(cold, (PyObject *)&cold_image) != (backend != 4)
            || contains(cold, cold) != 0) return fail(33);
    if (!pcc_gc_backend_frozen || pcc_gc_tracking_materialized) return fail(34);
    py_decref(cold);
    py_gc_untrack((PyObject *)&cold_image);
    int32_t map = 3;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(0);
    roots[1] = py_list_new(0);
    if (!roots[0] || !roots[1]) return fail(1);
    while (pcc_stop_the_world() != 0) pcc_thread_safepoint();
    pcc_gc_default_table_lock();
    int64_t owned = pcc_gc_tracking_flag_mode && !pcc_gc_tracking_materialized;
    int64_t count = pcc_gc_tracked_materialize();
    if (count < 0 || owned != (backend == 0)) return fail(2);
    Node *first = (Node *)py_gc_index_find(roots[0]);
    Node *dead = (Node *)py_gc_index_find(roots[1]);
    if (backend != 4 && (!first || !dead)) return fail(3);
    if (backend == 0 && count != py_gc_get_count(0)) return fail(4);
    if (backend == 0) {
        py_gc_enable();
        py_gc_set_threshold(1, 10, 10);
        pcc_gc_backend4_auto_step_pending = 1;
    }
    for (int round = 0; round < 3; round++) {
        roots[2] = py_gc_get_objects();
        if (!roots[2] || contains(roots[2], roots[2]) != 0) return fail(5);
        if (contains(roots[2], roots[0]) != (backend != 4)) return fail(6);
        if (py_gc_index_find(roots[0]) != first) return fail(7);
        if (backend != 4 && first->object != roots[0]) return fail(8);
        if (!pcc_thread_owns_stopped_world() || pcc_gc_table_lock_depth != 1)
            return fail(9);
        pcc_gc_store_root(&roots[2], 0);
        if (backend == 0 && !pcc_gc_backend4_auto_step_pending) return fail(35);
    }
    py_gc_disable();
    if (backend != 4) {
        py_gc_untrack(roots[1]);
        if (dead->object || py_gc_index_find(roots[1])) return fail(10);
        if (pcc_gc_deferred_node_free_head != dead) return fail(11);
        if (backend == 0) {
            Candidate rejected = { dead, roots[1] };
            if (pcc_gc_backend0_collect_candidates(&rejected, 1) != 0)
                return fail(12);
            if (pcc_gc_deferred_node_free_head != dead || dead->object)
                return fail(13);
            if (py_gc_index_find(roots[0]) != first || first->object != roots[0])
                return fail(14);
            if (!pcc_gc_tracking_materialized || pcc_gc_table_lock_depth != 1
                    || !pcc_thread_owns_stopped_world()) return fail(15);
        }
    }
    pcc_gc_tracked_dematerialize(owned);
    if (backend == 0 && (pcc_gc_tracking_materialized || py_gc_head
            || py_gc_index_find(roots[0]))) return fail(16);
    if (backend != 4 && pcc_gc_deferred_node_free_head != dead) return fail(17);
    pcc_gc_default_table_unlock();
    if (pcc_gc_deferred_node_free_head || pcc_gc_table_lock_depth) return fail(18);
    pcc_resume_world();
    pcc_gc_store_root(&roots[1], 0);
    pcc_gc_store_root(&roots[0], 0);
    pcc_gc_frame_leave(roots);

    if (backend == 0) {
        const char *fields[1] = { "self" };
        PyClassObject *cls = py_class_new("SnapshotFinalizer", 0, 0, fields, 1);
        if (!cls) return fail(19);
        py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)finalize);
        PyObject *obj = py_instance_new(cls);
        py_instance_set_field((PyInstanceObject *)obj, 0, obj);
        PyObject *weak = py_weakref_new(obj, 0);
        if (!weak) return fail(20);
        py_decref(obj);
        py_gc_collect();
        if (finalized != 1 || callback_failure || saved != obj) return fail(21);
        if (pcc_gc_tracking_materialized || pcc_gc_table_lock_depth
                || pcc_thread_owns_stopped_world()) return fail(22);
        PyObject *seen = py_weakref_call(weak);
        if (seen != py_None) return fail(23);
        py_decref(seen);
        py_decref(saved);
        saved = 0;
        if (py_gc_collect() < 1 || finalized != 1) return fail(24);
        py_decref(weak);
        py_decref((PyObject *)cls);
    }
    if (py_err_occurred()) return fail(25);
    printf("SNAPSHOT_OWNER_OK gc=%d finalizers=%d\n", backend, finalized);
    return 0;
}
'''


@pytest.mark.integration
def test_nested_snapshots_keep_outer_nodes_and_finalizer_cursors(
    tmp_path: Path, threaded_pcc_runtime_archive,
):
    source = tmp_path / 'snapshot_owner.c'
    source.write_text(SOURCE)
    binary = tmp_path / 'snapshot_owner'
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive)
    archive = Path(threaded_pcc_runtime_archive)
    manifest = manifest_path_for_archive(archive)
    receipt = {
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'archive': str(archive),
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'provenance_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'runs': [],
    }
    receipt_path = tmp_path / 'snapshot-owner.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS='1',
                     PCC_REFCOUNT_KIND='atomic', PATH='/nonexistent'),
        )
        (tmp_path / f'gc{backend}.stdout').write_text(result.stdout)
        (tmp_path / f'gc{backend}.stderr').write_text(result.stderr)
        receipt['runs'].append({'backend': backend, 'returncode': result.returncode,
                                'stdout': result.stdout, 'stderr': result.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        expected = f'SNAPSHOT_OWNER_OK gc={backend} finalizers={int(backend == 0)}\n'
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ''), backend
