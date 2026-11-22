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
extern int64_t pcc_gc_tracing_candidate_count;
extern int64_t pcc_gc_metric_layer_weak_registry_scans;
extern int64_t pcc_platform_monotonic_us(void);
extern void pcc_gc_cms_stop_worker(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { MAX_CANDIDATES = 256, CLASS_ROOT = 0, OLD_ROOT, CANDIDATE_WEAKS, ROOTS };
static void *nodes[MAX_CANDIDATES];
/* Identities for comparisons only, not registered roots or C locals across
 * collection. The departing setup thread owns the last strong variables. */
static PyObject *expected_targets[MAX_CANDIDATES];
static PyObject *expected_refs[MAX_CANDIDATES];
static char events[MAX_CANDIDATES * 2];
static int event_count, weak_callbacks, public_collect;
static int64_t cohort_at_first_finalizer;
static PyObject *roots[ROOTS];
static int candidates, old_count, weak_count, observe_candidates, finalized, setup_error;
static int peer_ready, peer_done;

static int parse_int(const char *arg) {
    int result = 0;
    while (*arg) {
        if (*arg < '0' || *arg > '9' || result > 65536) return -1;
        result = result * 10 + *arg++ - '0';
    }
    return result;
}

static PyObject *finalize_item(PyObject *self) {
    if (pcc_thread_world_stop_epoch_held() >= 0) setup_error = 4;
    if (observe_candidates == 2) {
        if (finalized == 0)
            cohort_at_first_finalizer = __atomic_load_n(&pcc_gc_tracing_candidate_count, __ATOMIC_ACQUIRE);
        if (event_count < MAX_CANDIDATES * 2) events[event_count++] = 'D';
        if (finalized >= candidates || expected_targets[finalized] != self ||
            weak_callbacks != finalized) setup_error = 27;
    }
    ++finalized;
    return py_int_from_i64(0);
}

static PyObject *weak_event(PyObject *captures, PyObject *args) {
    (void)captures;
    PyObject *ref = py_tuple_get(args, 0);
    if (event_count < MAX_CANDIDATES * 2) events[event_count++] = 'W';
    if (weak_callbacks >= candidates || expected_refs[weak_callbacks] != ref ||
        finalized != weak_callbacks + 1 || pcc_thread_world_stop_epoch_held() >= 0)
        setup_error = 28;
    ++weak_callbacks;
    if (ref) py_decref(ref);
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
    PyObject *unrelated = py_list_new(old_count + weak_count + (weak_count > 0));
    if (!unrelated) { setup_error = 7; return NULL; }
    pcc_gc_store_root(&roots[OLD_ROOT], unrelated);
    for (int i = 0; i < old_count; ++i) {
        PyObject *live = py_list_new(0);
        if (!live) { setup_error = 8; return NULL; }
        py_list_append(unrelated, live);
        pcc_gc_unpin(live);
    }
    if (weak_count > 0) {
        PyObject *target = py_instance_new(cls);
        if (!target) { setup_error = 18; return NULL; }
        py_list_append(unrelated, target);
        for (int i = 0; i < weak_count; ++i) {
            PyObject *ref = py_weakref_new(target, NULL);
            if (!ref) { setup_error = 19; return NULL; }
            py_list_append(unrelated, ref);
        }
    }
    if (observe_candidates) {
        PyObject *observed = py_list_new(candidates);
        if (!observed) { setup_error = 23; return NULL; }
        pcc_gc_store_root(&roots[CANDIDATE_WEAKS], observed);
    }
    PyObject *on_weak = observe_candidates == 2 ? py_func_new((void *)weak_event, NULL) : NULL;
    if (observe_candidates == 2 && !on_weak) { setup_error = 29; return NULL; }
    PyObject *previous = NULL;
    for (int i = 0; i < candidates; ++i) {
        PyObject *item = py_instance_new(cls);
        if (!item) { setup_error = 9; return NULL; }
        if (observe_candidates) {
            PyObject *ref = py_weakref_new(item, on_weak);
            if (!ref) { setup_error = 24; return NULL; }
            py_list_append(roots[CANDIDATE_WEAKS], ref);
            if (observe_candidates == 2) {
                expected_refs[i] = ref;
                expected_targets[i] = item;
            }
        }
        if (previous) py_instance_set_field((PyInstanceObject *)previous, 0, item);
        pcc_gc_unpin(item);
        if (!public_collect) {
            pcc_py_gc_minor_graph_lock();
            nodes[candidates-1-i] = pcc_gc_object_index_find(item);
            pcc_gc_set_mark_color(item, PY_FLAG_GC_WHITE);
            __atomic_fetch_and(&py_header(item)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
            __atomic_fetch_or(&py_header(item)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
            pcc_py_gc_minor_graph_unlock();
            if (!nodes[candidates-1-i]) { setup_error = 10; return NULL; }
        }
        previous = item;
    }
    return NULL;
}

static void *idle_peer(void *unused) {
    (void)unused;
    int status = pcc_current_thread_id() > 0 ? 1 : -1;
    __atomic_store_n(&peer_ready, status, __ATOMIC_RELEASE);
    if (status > 0)
        while (!__atomic_load_n(&peer_done, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 3 || argc > 6) return 11;
    candidates = parse_int(argv[1]);
    old_count = parse_int(argv[2]);
    const int with_peer = argc >= 4;
    if (with_peer) weak_count = parse_int(argv[3]);
    if (argc >= 5) observe_candidates = parse_int(argv[4]);
    if (argc == 6) public_collect = parse_int(argv[5]);
    if (observe_candidates < 0 || observe_candidates > 2 ||
        public_collect < 0 || public_collect > 1 ||
        (public_collect && observe_candidates != 2)) return 25;
    if (candidates < 2 || candidates > MAX_CANDIDATES || old_count < 0 || old_count > 65536
        || weak_count < 0 || weak_count > 65536) return 12;
    if (pcc_current_thread_id() <= 0) return 13;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < ROOTS; ++i) pcc_gc_scheduler_root_register(&roots[i]);
    void *thread = NULL;
    if (pcc_thread_start(&thread, make_graph, NULL) || pcc_thread_join(thread, NULL)) return 14;
    if (setup_error) return setup_error;
    if (with_peer) {
        if (pcc_thread_start(&thread, idle_peer, NULL)) return 20;
        int ready;
        while (!(ready = __atomic_load_n(&peer_ready, __ATOMIC_ACQUIRE))) pcc_thread_safepoint();
        if (ready < 0) return 21;
    }
    int64_t full_before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    int64_t scans_before = pcc_gc_metric_layer_weak_registry_scans;
    int64_t elapsed, reclaimed, stops = -1;
    if (public_collect) {
        int64_t start = pcc_platform_monotonic_us();
        reclaimed = pcc_gc_collect(0);
        elapsed = pcc_platform_monotonic_us() - start;
    } else {
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
        int64_t start = pcc_platform_monotonic_us();
        reclaimed = pcc_gc_tracing_sweep_unreachable(candidates);
        elapsed = pcc_platform_monotonic_us() - start;
        stops = pcc_thread_world_stop_epoch_held() - epoch;
        pcc_gc_end_explicit_tracing_collect();
        if (pcc_resume_world() != 0) return 16;
    }
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - full_before;
    if (with_peer) {
        __atomic_store_n(&peer_done, 1, __ATOMIC_RELEASE);
        if (pcc_thread_join(thread, NULL)) return 22;
        printf("weak=%d scans=%lld ", weak_count,
               (long long)(pcc_gc_metric_layer_weak_registry_scans - scans_before));
    }
    printf("n=%d live=%d del=%d reclaimed=%lld full=%lld windows=%lld us=%lld\n",
           candidates, old_count, finalized, (long long)reclaimed, (long long)full,
           (long long)stops, (long long)elapsed);
    if (observe_candidates) {
        for (int i = 0; i < candidates; ++i) {
            PyObject *ref = py_list_get(roots[CANDIDATE_WEAKS], i);
            if (!ref || py_weakref_call(ref) != py_None) return 26;
        }
    }
    if (observe_candidates == 2) {
        printf("events=%.*s callbacks=%d public=%d cohort=%lld\n", event_count, events,
               weak_callbacks, public_collect, (long long)cohort_at_first_finalizer);
        if (weak_callbacks != candidates) return 30;
    }
    if (setup_error) return setup_error;
    return finalized == candidates && full == 0 &&
           (public_collect ? reclaimed >= 0 : reclaimed == candidates) ? 0 : 17;
}
