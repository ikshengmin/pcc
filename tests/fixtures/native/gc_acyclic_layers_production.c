/* Drives the real sweep from a sealed mark, not a planner-only entry. */
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

enum { CLASS_A, CLASS_B, WEAK_A, WEAK_B, HOLD, ROOTS };
static PyObject *roots[ROOTS];
static void *nodes[2];
static int rescue, error;
static PyObject *volatile *caller_hold;
static int events[6], event_count;

static void note(int event) {
    if (event_count >= 6 || pcc_thread_world_stop_epoch_held() >= 0) error = 5;
    else events[event_count++] = event;
}
static PyObject *finish_a(PyObject *self) {
    (void)self;
    note(1);
    if (rescue) {
        PyObject *target = py_weakref_call(roots[WEAK_B]);
        if (!target || target == py_None) error = 6;
        else if (rescue == 2) *caller_hold = target;
        else pcc_gc_store_root(&roots[HOLD], target);
        if (target) py_decref(target);
    }
    return py_int_from_i64(0);
}
static PyObject *finish_b(PyObject *self) {
    (void)self;
    note(3);
    return py_int_from_i64(0);
}
static PyObject *weak_event(PyObject *captures, PyObject *args) {
    (void)captures;
    PyObject *ref = py_tuple_get(args, 0);
    if (ref == roots[WEAK_A]) note(2);
    else if (ref == roots[WEAK_B]) note(4);
    else error = 7;
    if (ref) py_decref(ref);
    return py_int_from_i64(0);
}

static void *seal_on_departing_thread(void *unused) {
    (void)unused;
    const char *fields[] = {"child"};
    if (pcc_current_thread_id() <= 0) { error = 8; return NULL; }
    PyClassObject *a_cls = py_class_new("LayerA", NULL, 0, fields, 1);
    PyClassObject *b_cls = py_class_new("LayerB", NULL, 0, NULL, 0);
    if (!a_cls || !b_cls) { error = 9; return NULL; }
    pcc_gc_store_root(&roots[CLASS_A], (PyObject *)a_cls);
    pcc_gc_store_root(&roots[CLASS_B], (PyObject *)b_cls);
    py_class_add_method(a_cls, "__del__", (PyObject *)(uintptr_t)finish_a);
    py_class_add_method(b_cls, "__del__", (PyObject *)(uintptr_t)finish_b);
    PyObject *a = py_instance_new(a_cls);
    PyObject *b = py_instance_new(b_cls);
    PyObject *callback = py_func_new((void *)weak_event, NULL);
    if (!a || !b || !callback) { error = 10; return NULL; }
    if (!rescue) py_instance_set_field((PyInstanceObject *)a, 0, b);
    PyObject *wa = py_weakref_new(a, callback);
    PyObject *wb = py_weakref_new(b, callback);
    if (!wa || !wb) { error = 11; return NULL; }
    pcc_gc_store_root(&roots[WEAK_A], wa);
    pcc_gc_store_root(&roots[WEAK_B], wb);
    pcc_gc_unpin(a);
    pcc_gc_unpin(b);
    pcc_py_gc_minor_graph_lock();
    PyObject *items[] = {a, b};
    for (int i = 0; i < 2; ++i) {
        PyObject *obj = items[i];
        nodes[rescue ? i : 1-i] = pcc_gc_object_index_find(obj);
        if (!nodes[rescue ? i : 1-i]) error = 12;
        pcc_gc_set_mark_color(obj, PY_FLAG_GC_WHITE);
        __atomic_fetch_and(&py_header(obj)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
        __atomic_fetch_or(&py_header(obj)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
    }
    pcc_py_gc_minor_graph_unlock();
    return NULL;
}

int main(int argc, char **argv) {
    rescue = argc > 1 && strcmp(argv[1], "rescue") == 0;
    if (argc > 1 && strcmp(argv[1], "caller") == 0) rescue = 2;
    // Unregistered storage in the suspended owner’s caller frame. This slot
    // starts empty; the callback creates a new root through a saved address.
    PyObject *volatile raw = NULL;
    caller_hold = &raw;
    if (pcc_current_thread_id() <= 0) return 19;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < ROOTS; ++i) pcc_gc_scheduler_root_register(&roots[i]);
    void *thread = NULL;
    if (pcc_thread_start(&thread, seal_on_departing_thread, NULL) ||
        pcc_thread_join(thread, NULL)) return 20;
    if (error) return error;
    if (pcc_stop_the_world() != 0) return 21;
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
    pcc_gc_tracing_sweep_unreachable(2);
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - before;
    pcc_gc_end_explicit_tracing_collect();
    if (pcc_resume_world() != 0) return 22;
    PyObject *b = py_weakref_call(roots[WEAK_B]);
    if (rescue == 2 && (raw != b || raw == py_None || raw == NULL)) error = 13;
    note(b == py_None ? 5 : 6);
    const char *names[] = {"", "a-del", "a-weak", "b-del", "b-weak", "b-dead", "b-live"};
    for (int i = 0; i < event_count; ++i)
        printf("%s%s", i ? " " : "", names[events[i]]);
    puts("");
    fprintf(stderr, "full=%lld\n", (long long)full);
    return error;
}
