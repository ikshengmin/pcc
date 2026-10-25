"""Native safety of GC0 candidate revalidation; discovery is not concurrent yet."""
import os
from pathlib import Path
import subprocess

import pytest


PROBE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_gc_backend0_collect_candidates(void *, int64_t);
extern int32_t pcc_gc_backend0_subset_active;
extern int64_t pcc_gc_backend0_subset_count;
extern void *pcc_gc_backend0_subset_pairs;
typedef struct Candidate { PyGcNode *node; PyObject *object; } Candidate;
static int fail(int code) { fprintf(stderr, "candidate-check=%d\n", code); return code; }
static Candidate record(PyObject *obj) {
    Candidate entry;
    entry.node = py_gc_index_find(obj);
    entry.object = obj;
    return entry;
}
static int clean_context(void) {
    return pcc_gc_backend0_subset_active == 0 && pcc_gc_backend0_subset_count == 0
           && pcc_gc_backend0_subset_pairs == 0;
}
static PyObject *saved;
static int finalized;
static PyObject *finalize(PyObject *self) {
    finalized++;
    if (finalized == 1) { py_incref(self); saved = self; }
    if (py_gc_collect() != 0) return 0;
    return py_None;
}

int main(void) {
    if (pcc_gc_backend() != 0 || pcc_threads_enabled() != 1) return fail(2);
    py_gc_disable();
    if (pcc_gc_backend0_collect_candidates(0, 0) != 0 || !clean_context()) return fail(3);
    /* A reference from an excluded owner is external to the selected graph. */
    PyObject *child = py_list_new(0);
    PyObject *parent = py_list_new(0);
    if (child == 0 || parent == 0) return fail(4);
    py_list_append(child, child);
    py_list_append(parent, child);
    Candidate single = record(child);
    py_decref(child);
    if (pcc_gc_backend0_collect_candidates(&single, 1) != 0) return fail(5);
    if (py_list_len(child) != 1 || !clean_context()) return fail(6);
    py_decref(parent);
    if (pcc_gc_backend0_collect_candidates(&single, 1) != 1 || !clean_context()) return fail(7);
    /* A stale record cannot dereference a freed node or collect another object. */
    if (pcc_gc_backend0_collect_candidates(&single, 1) != 0 || !clean_context()) return fail(8);

    PyObject *left = py_list_new(0);
    PyObject *right = py_list_new(0);
    py_list_append(left, right);
    py_list_append(right, left);
    Candidate both[3] = { record(left), record(right), record(left) };
    py_decref(left); py_decref(right);
    if (pcc_gc_backend0_collect_candidates(both, 1) != 0) return fail(9);
    if (py_list_len(left) != 1 || py_list_len(right) != 1) return fail(10);
    if (pcc_gc_backend0_collect_candidates(both, 3) != 2 || !clean_context()) return fail(11);

    /* A registered borrowed root protects a cycle without adding a count. */
    PyObject *rooted = py_list_new(0);
    py_list_append(rooted, rooted);
    Candidate root_record = record(rooted);
    void *handle = pcc_gc_scheduler_root_register_handle(&rooted);
    if (handle == 0) return fail(12);
    py_decref(rooted);
    if (pcc_gc_backend0_collect_candidates(&root_record, 1) != 0) return fail(13);
    pcc_gc_scheduler_root_unregister_handle(handle);
    if (pcc_gc_backend0_collect_candidates(&root_record, 1) != 1) return fail(14);

    /* A zero-count parent parked before untrack still owns its children. */
    parent = py_list_new(0); child = py_list_new(0);
    py_list_append(child, child); py_list_append(parent, child);
    Candidate pending[2] = { record(parent), record(child) };
    py_decref(child);
    __atomic_store_n(&py_header(parent)->refcount, 0, __ATOMIC_RELEASE);
    __atomic_fetch_or(&py_header(parent)->flags, 524288u, __ATOMIC_RELEASE);
    if (pcc_gc_backend0_collect_candidates(pending, 2) != 0) return fail(15);
    if (py_list_len(child) != 1) return fail(16);
    __atomic_store_n(&py_header(parent)->refcount, 1, __ATOMIC_RELEASE);
    __atomic_fetch_and(&py_header(parent)->flags, ~524288u, __ATOMIC_RELEASE);
    py_decref(parent);
    if (pcc_gc_backend0_collect_candidates(pending + 1, 1) != 1) return fail(17);

    /* Finalizers may resurrect: weakrefs survive the first collection. */
    const char *fields[1] = { "self" };
    PyClassObject *cls = py_class_new("CandidateResurrection", 0, 0, fields, 1);
    if (cls == 0) return fail(18);
    py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)finalize);
    PyObject *instance = py_instance_new(cls);
    if (instance == 0) return fail(19);
    py_instance_set_field((PyInstanceObject *)instance, 0, instance);
    PyObject *weak = py_weakref_new(instance, 0);
    if (weak == 0) return fail(20);
    Candidate resurrection = record(instance);
    py_decref(instance);
    if (pcc_gc_backend0_collect_candidates(&resurrection, 1) != 0) return fail(21);
    if (finalized != 1 || saved != instance || !clean_context()) return fail(22);
    PyObject *seen = py_weakref_call(weak);
    if (seen != instance) return fail(23);
    py_decref(seen);
    py_decref(saved); saved = 0;
    if (pcc_gc_backend0_collect_candidates(&resurrection, 1) != 1) return fail(24);
    seen = py_weakref_call(weak);
    if (seen != py_None || finalized != 1 || !clean_context()) return fail(25);
    py_decref(seen); py_decref(weak); py_decref((PyObject *)cls);
    if (py_err_occurred() != 0) return fail(26);
    /* The ordinary full-heap entry still collects objects outside any subset. */
    child = py_list_new(0); py_list_append(child, child); py_decref(child);
    if (py_gc_collect() < 1 || !clean_context()) return fail(27);
    puts("GC0_CANDIDATE_REVALIDATION_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_candidate_revalidation_preserves_current_roots_and_resurrection(tmp_path, pcc_runtime_archive):
    from tests.owned_runtime_c_fixture import link_c_harness

    source = tmp_path / 'candidate_revalidation.c'
    source.write_text(PROBE)
    binary = tmp_path / 'candidate_revalidation'
    runtime = Path(__file__).resolve().parents[2] / 'pcc/runtime'
    link_c_harness(source, binary, extra_include_dirs=(str(runtime / 'src'),))
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, PCC_GC_BACKEND='0', PATH='/nonexistent'))
    (tmp_path / 'native.stdout').write_text(run.stdout)
    (tmp_path / 'native.stderr').write_text(run.stderr)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'GC0_CANDIDATE_REVALIDATION_OK\n', '')
