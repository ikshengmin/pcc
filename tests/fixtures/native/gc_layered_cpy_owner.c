/* Acquire a genuine independent C owner from a layered finalizer. The
 * foreign payload is opaque and stable, not a traced managed edge. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern void Py_INCREF(PyObject *);
extern void Py_DECREF(PyObject *);
extern int64_t pcc_gc_foreign_lease_active;
extern void *pcc_gc_object_index_find(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_tracing_candidates_reset(void);
extern void pcc_gc_tracing_candidates_note(void *);
extern void pcc_gc_tracing_candidates_seal(int64_t);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_tracing_finish_commits, pcc_gc_tracing_finish_stop_epoch;
extern void pcc_gc_cms_stop_worker(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

static int token, other_token, error, finalizers, releases, keep, transfer;
static void *nodes[2];
static PyObject *class_root, *other_handle;
static PyObject *held_by_c; /* Deliberately never a registered managed root. */

static void release_foreign(void *payload) {
    if (payload != &token) error = 5;
    ++releases;
}
static PyObject *finalize(PyObject *self) {
    if (pcc_thread_world_stop_epoch_held() >= 0) error = 6;
    ++finalizers;
    PyObject *handle = py_instance_get_field((PyInstanceObject *)self, 0);
    if (!handle || py_cpy_handle_get(handle) != &token) { error = 7; return py_None; }
    int64_t before = __atomic_load_n(&pcc_gc_foreign_lease_active, __ATOMIC_ACQUIRE);
    if (transfer) Py_DECREF(other_handle);
    Py_INCREF(handle);
    if (transfer && __atomic_load_n(&pcc_gc_foreign_lease_active, __ATOMIC_ACQUIRE) != before) error = 17;
    if (keep) held_by_c = handle;
    else Py_DECREF(handle);
    py_decref(handle);
    return py_int_from_i64(0);
}

static void *prepare(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) { error = 8; return NULL; }
    const char *fields[] = {"handle"};
    PyClassObject *cls = py_class_new("LayeredForeignHandleOwner", NULL, 0, fields, 1);
    if (!cls) { error = 9; return NULL; }
    pcc_gc_store_root(&class_root, (PyObject *)cls);
    py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)finalize);
    if (transfer) {
        PyObject *other = py_cpy_handle_new(&other_token);
        if (!other) { error = 18; return NULL; }
        pcc_gc_store_root(&other_handle, other);
        Py_INCREF(other);
    }
    PyObject *a = py_instance_new(cls);
    PyObject *handle = py_cpy_handle_new(&token);
    if (!a || !handle) { error = 10; return NULL; }
    py_instance_set_field((PyInstanceObject *)a, 0, handle);
    pcc_gc_unpin(a);
    pcc_gc_unpin(handle);
    PyObject *dead[] = {handle, a};
    pcc_py_gc_minor_graph_lock();
    for (int i = 0; i < 2; ++i) {
        PyObject *obj = dead[i];
        nodes[i] = pcc_gc_object_index_find(obj);
        if (!nodes[i]) error = 11;
        pcc_gc_set_mark_color(obj, PY_FLAG_GC_WHITE);
        __atomic_fetch_and(&py_header(obj)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
        __atomic_fetch_or(&py_header(obj)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
    }
    pcc_py_gc_minor_graph_unlock();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2) return 12;
    transfer = strcmp(argv[1], "transfer") == 0;
    keep = transfer || strcmp(argv[1], "keep") == 0;
    if (pcc_current_thread_id() <= 0) return 13;
    pcc_gc_cms_stop_worker();
    py_cpy_handle_set_release_fn(release_foreign);
    pcc_gc_scheduler_root_register(&class_root);
    pcc_gc_scheduler_root_register(&other_handle);
    void *thread = NULL;
    if (pcc_thread_start(&thread, prepare, NULL) || pcc_thread_join(thread, NULL)) return 14;
    if (error) return error;
    if (pcc_stop_the_world() != 0) return 15;
    pcc_gc_begin_explicit_tracing_collect();
    pcc_py_gc_minor_graph_lock();
    pcc_gc_tracing_candidates_reset();
    pcc_gc_tracing_candidates_note(nodes[0]);
    pcc_gc_tracing_candidates_note(nodes[1]);
    pcc_gc_tracing_finish_commits = 1;
    pcc_gc_tracing_candidates_seal(1);
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    pcc_py_gc_minor_graph_unlock();
    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    int64_t reclaimed = pcc_gc_tracing_sweep_unreachable(2);
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - before;
    pcc_gc_end_explicit_tracing_collect();
    if (pcc_resume_world() != 0) return 16;
    int usable = 0;
    if (keep && releases == 0 && held_by_c)
        usable = py_cpy_handle_get(held_by_c) == &token;
    printf("del=%d released=%d reclaimed=%lld usable=%d full=%lld\n",
           finalizers, releases, (long long)reclaimed, usable, (long long)full);
    if (keep && usable) {
        Py_DECREF(held_by_c);
        held_by_c = NULL;
    }
    return error;
}
