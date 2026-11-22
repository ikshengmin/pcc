/* Seal one definite dead candidate, then call the real production sweep.
 * A setup thread exits first so no caller-stack address can rescue it.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

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

static PyObject *roots[3];
enum { CLASS_ROOT, WATCH, HOLD };
static void *candidate_node;
static int rescue, error;
static int events[3], n;
enum { DEL_LIVE = 1, DEL_DEAD, WEAK_DEAD, WEAK_LIVE, STILL_LIVE, NOW_DEAD };

static void record(int value) {
    if (n < 3) events[n++] = value;
    else error = 30;
}

static PyObject *subject_del(PyObject *self) {
    if (pcc_thread_world_stop_epoch_held() >= 0) error = 31;
    PyObject *target = py_weakref_call(roots[WATCH]);
    record(target == self ? DEL_LIVE : DEL_DEAD);
    if (target != NULL) py_decref(target);
    if (rescue) pcc_gc_store_root(&roots[HOLD], self);
    return py_int_from_i64(0);
}

static PyObject *weak_callback(PyObject *captures, PyObject *args) {
    (void)captures;
    if (pcc_thread_world_stop_epoch_held() >= 0) error = 32;
    PyObject *ref = py_tuple_get(args, 0);
    if (ref != roots[WATCH]) error = 33;
    PyObject *target = py_weakref_call(ref);
    record(target == py_None ? WEAK_DEAD : WEAK_LIVE);
    if (target != NULL) py_decref(target);
    if (ref != NULL) py_decref(ref);
    return py_int_from_i64(0);
}

static void *prepare(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) { error = 10; return NULL; }
    PyClassObject *cls = py_class_new("Subject", NULL, 0, NULL, 0);
    if (!cls) { error = 11; return NULL; }
    pcc_gc_store_root(&roots[CLASS_ROOT], (PyObject *)cls);
    py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)subject_del);
    PyObject *subject = py_instance_new(cls);
    PyObject *callback = py_func_new((void *)weak_callback, NULL);
    if (!subject || !callback) { error = 12; return NULL; }
    PyObject *watch = py_weakref_new(subject, callback);
    if (!watch) { error = 13; return NULL; }
    pcc_gc_store_root(&roots[WATCH], watch);
    pcc_gc_unpin(subject);
    pcc_py_gc_minor_graph_lock();
    candidate_node = pcc_gc_object_index_find(subject);
    pcc_gc_set_mark_color(subject, PY_FLAG_GC_WHITE);
    __atomic_fetch_and(&py_header(subject)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
    __atomic_fetch_or(&py_header(subject)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (!candidate_node) error = 14;
    return NULL;
}

int main(int argc, char **argv) {
    rescue = argc > 1 && strcmp(argv[1], "rescue") == 0;
    if (pcc_current_thread_id() <= 0) return 19;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < 3; i++) pcc_gc_scheduler_root_register(&roots[i]);
    void *thread = NULL;
    if (pcc_thread_start(&thread, prepare, NULL) || pcc_thread_join(thread, NULL)) return 20;
    if (error) return error;
    if (pcc_stop_the_world() != 0) return 21;
    pcc_gc_begin_explicit_tracing_collect();
    pcc_py_gc_minor_graph_lock();
    pcc_gc_tracing_candidates_reset();
    pcc_gc_tracing_candidates_note(candidate_node);
    pcc_gc_tracing_finish_commits = 1;
    pcc_gc_tracing_candidates_seal(1);
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    pcc_py_gc_minor_graph_unlock();
    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    pcc_gc_tracing_sweep_unreachable(1);
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - before;
    pcc_gc_end_explicit_tracing_collect();
    if (pcc_resume_world() != 0) return 22;
    record(py_weakref_call(roots[WATCH]) == py_None ? NOW_DEAD : STILL_LIVE);
    const char *names[] = {"invalid", "del:1", "del:0", "weak:1", "weak:0", "live:1", "live:0"};
    for (int i = 0; i < n; i++) printf("%s%s", names[events[i]], i + 1 == n ? "\n" : " ");
    // The standalone CPython oracle has no implementation counters. Send
    // the exact bounded/fallback route separately from its event stream.
    if (argc > 2) fprintf(stderr, "full=%lld\n", (long long)full);
    return error;
}
