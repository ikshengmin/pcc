/* Isolate actual sweep cost: setup is complete before the timer starts. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern void *pcc_gc_object_index_find(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_tracing_candidates_reset(void);
extern void pcc_gc_tracing_candidates_note(void *);
extern void pcc_gc_tracing_candidates_seal(int64_t);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_tracing_finish_commits, pcc_gc_tracing_finish_stop_epoch;
extern int64_t pcc_platform_monotonic_us(void);
extern void pcc_gc_cms_stop_worker(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { MAX_CANDIDATES = 64, CLASS_ROOT = 0, OLD_ROOT = 1, ROOTS = 2 };
static void *nodes[MAX_CANDIDATES];
static PyObject *roots[ROOTS];
static int candidates, old_count, finalized, setup_error;

static int parse_int(const char *arg) {
    int result = 0;
    while (*arg) {
        if (*arg < '0' || *arg > '9' || result > 65536) return -1;
        result = result * 10 + *arg++ - '0';
    }
    return result;
}

static PyObject *finalize_item(PyObject *self) {
    (void)self;
    if (pcc_thread_world_stop_epoch_held() >= 0) setup_error = 4;
    ++finalized;
    return py_int_from_i64(0);
}

static void *make_graph(void *argument) {
    (void)argument;
    if (pcc_current_thread_id() <= 0) { setup_error = 5; return NULL; }
    const char *fields[] = {"child"};
    PyClassObject *cls = py_class_new("SweepScale", NULL, 0, fields, 1);
    if (!cls) { setup_error = 6; return NULL; }
    pcc_gc_store_root(&roots[CLASS_ROOT], (PyObject *)cls);
    py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)finalize_item);
    PyObject *unrelated = py_list_new(old_count);
    if (!unrelated) { setup_error = 7; return NULL; }
    pcc_gc_store_root(&roots[OLD_ROOT], unrelated);
    for (int i = 0; i < old_count; ++i) {
        PyObject *live = py_list_new(0);
        if (!live) { setup_error = 8; return NULL; }
        py_list_append(unrelated, live);
        pcc_gc_unpin(live);
    }
    PyObject *previous = NULL;
    for (int i = 0; i < candidates; ++i) {
        PyObject *item = py_instance_new(cls);
        if (!item) { setup_error = 9; return NULL; }
        if (previous) py_instance_set_field((PyInstanceObject *)previous, 0, item);
        pcc_gc_unpin(item);
        pcc_py_gc_minor_graph_lock();
        nodes[candidates-1-i] = pcc_gc_object_index_find(item);
        pcc_gc_set_mark_color(item, PY_FLAG_GC_WHITE);
        __atomic_fetch_and(&py_header(item)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
        __atomic_fetch_or(&py_header(item)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
        pcc_py_gc_minor_graph_unlock();
        if (!nodes[candidates-1-i]) { setup_error = 10; return NULL; }
        previous = item;
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) return 11;
    candidates = parse_int(argv[1]);
    old_count = parse_int(argv[2]);
    if (candidates < 2 || candidates > MAX_CANDIDATES || old_count < 0 || old_count > 65536) return 12;
    if (pcc_current_thread_id() <= 0) return 13;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < ROOTS; ++i) pcc_gc_scheduler_root_register(&roots[i]);
    void *thread = NULL;
    if (pcc_thread_start(&thread, make_graph, NULL) || pcc_thread_join(thread, NULL)) return 14;
    if (setup_error) return setup_error;
    if (pcc_stop_the_world() != 0) return 15;
    pcc_gc_begin_explicit_tracing_collect();
    pcc_py_gc_minor_graph_lock();
    pcc_gc_tracing_candidates_reset();
    for (int i = 0; i < candidates; ++i) pcc_gc_tracing_candidates_note(nodes[i]);
    pcc_gc_tracing_finish_commits = 1;
    pcc_gc_tracing_candidates_seal(1);
    int64_t epoch = pcc_thread_world_stop_epoch_held();
    pcc_gc_tracing_finish_stop_epoch = epoch;
    pcc_py_gc_minor_graph_unlock();
    int64_t full_before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    int64_t start = pcc_platform_monotonic_us();
    int64_t reclaimed = pcc_gc_tracing_sweep_unreachable(candidates);
    int64_t elapsed = pcc_platform_monotonic_us() - start;
    int64_t stops = pcc_thread_world_stop_epoch_held() - epoch;
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - full_before;
    pcc_gc_end_explicit_tracing_collect();
    if (pcc_resume_world() != 0) return 16;
    printf("n=%d live=%d del=%d reclaimed=%lld full=%lld windows=%lld us=%lld\n",
           candidates, old_count, finalized, (long long)reclaimed, (long long)full,
           (long long)stops, (long long)elapsed);
    if (setup_error) return setup_error;
    return finalized == candidates && reclaimed == candidates && full == 0 ? 0 : 17;
}
