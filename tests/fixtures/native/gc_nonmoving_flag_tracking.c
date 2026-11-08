/* Owned runtime tracking origins, defensive raw-thread admission, and real
 * pending GEN death. This exercises internal py_gc_track/untrack, NOT the
 * exported PyObject_GC_Track/UnTrack stubs. The ObjectNode graph is distinct
 * from the optional tracked GCNode representation and must remain intact.
 *
 * Arguments: scenario (0 origins, 1 raw admission, 2 pending GEN), workers
 * (1 or 4), required flag mode (0 nodes or 1 flags). No mode is forced here.
 * GC4's existing empty tracked view is a labeled limitation, not API parity.
 */
#include <Python.h>
#include <pthread.h>

extern int32_t pcc_gc_tracking_flag_mode, pcc_gc_tracking_materialized;
extern int32_t pcc_gc_backend4_no_refcounts, pcc_gc_nonmoving_count_free;
extern void *py_gc_index_find(void *);
extern void *pcc_gc_object_index_find(void *);
extern int64_t pcc_gc_managed_pointer_index_contains(void *);
extern int64_t pcc_gc_static_literal_contains(PyObject *);
extern int64_t pcc_thread_registered(void);
extern void pcc_gc_default_table_lock(void);
extern void pcc_gc_default_table_unlock(void);
extern int64_t pcc_gc_tracked_materialize(void);
extern void pcc_gc_tracked_dematerialize(int64_t);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_gc_deferred_free_close(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_gc_sweep_owed(void);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern void pcc_gc_scalar_sweep_seal(void);
extern int64_t pcc_gc_scalar_sweep_owns(void *);
extern int64_t pcc_gc_scalar_sweep_counter(int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);

/* Values supplied by the test from the runtime's generated ABI constants. */
#ifndef TEST_TRACKED
#error "Pass the generated header flag constants through the owned compiler"
#endif

enum { SMALL = 0, LARGE = 1, RAW = 2, STATIC = 3, PARTIAL = 4,
       SNAPSHOT = 5, REFERRERS = 6, THREAD_BASE = 7, ROOT_COUNT = 11,
       TUPLE_ITEMS = 4096, GARBAGE_COUNT = 32 };
static PyObject *roots[ROOT_COUNT];
static PyObject *garbage[GARBAGE_COUNT]; /* identities, deliberately NOT roots */
static int backend, workers, flag_mode;
static int creator_status;
static int64_t pending_seen, raw_calls;

struct StaticFloat { PyObjectHeader header; double value; };
/* A complete process-lifetime scalar image, not a truncated container header.
 * Registration permits scalar images; explicit internal tracking exercises
 * exact-managed enumeration without inventing a static-container API. */
static struct StaticFloat static_float = {{1, PY_TYPE_FLOAT, TEST_IMMORTAL}, 2.5};

struct RawObject { PyObject_HEAD PyObject *edge; };
static int raw_traverse(PyObject *self, visitproc visit, void *arg) {
    struct RawObject *raw = (struct RawObject *)self;
    /* The native Python.h Py_VISIT preserves the shared slot contract. */
    Py_VISIT(raw->edge);
    return 0;
}
static PyTypeObject raw_type = {
    PyVarObject_HEAD_INIT(0, 0)
    .tp_name = "tracking.RawObject",
    .tp_basicsize = sizeof(struct RawObject),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .tp_traverse = raw_traverse,
};

static int fail(int code, const char *phase) {
    fprintf(stderr, "NONMOVING_TRACKING_FAIL code=%d phase=%s gc=%d flags=%d\n",
            code, phase, backend, flag_mode);
    return code;
}
#define CHECK(condition, code, phase) do { \
    if (!(condition)) return fail(code, phase); \
} while (0)

static int64_t now(void) { return pcc_runtime_monotonic_us(); }
static int stop_world(void) {
    int64_t deadline = now() + 5000000;
    while (pcc_stop_the_world() != 0) {
        if (now() >= deadline) return -1;
        pcc_thread_safepoint();
    }
    return 0;
}
static void *object_node(PyObject *obj) {
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_index_find(obj);
    pcc_py_gc_minor_graph_unlock();
    return node;
}
static int64_t exact_member(PyObject *obj) {
    pcc_py_gc_minor_graph_lock();
    int64_t member = pcc_gc_managed_pointer_index_contains(obj);
    pcc_py_gc_minor_graph_unlock();
    return member;
}
static int occurrences(PyObject *values, PyObject *target) {
    int count = 0;
    int64_t length = py_list_len(values);
    for (int64_t i = 0; i < length; i++) {
        PyObject *item = py_list_get(values, i);
        if (!item) return -1;
        if (item == target) count++;
        py_decref(item);
    }
    return count;
}
static int unique_snapshot(PyObject *values) {
    int64_t length = py_list_len(values);
    for (int64_t i = 0; i < length; i++) {
        PyObject *item = py_list_get(values, i);
        if (!item) return 0;
        int valid = item != values && occurrences(values, item) == 1;
        py_decref(item);
        if (!valid) return 0;
    }
    return 1;
}
static void drop_result(int slot) {
    /* Count-free release is not immediate death. Explicitly untrack our
     * disposable observation list before dropping its owner. */
    py_gc_untrack(roots[slot]);
    pcc_gc_store_root(&roots[slot], 0);
}
static int snapshot_members(int partial_visible) {
    roots[SNAPSHOT] = py_gc_get_objects();
    CHECK(roots[SNAPSHOT] && !py_err_occurred(), 20, "snapshot-result");
    CHECK(unique_snapshot(roots[SNAPSHOT]), 21, "snapshot-unique-not-self");
    for (int i = SMALL; i <= STATIC; i++)
        CHECK(occurrences(roots[SNAPSHOT], roots[i]) == (backend != 4),
              22 + i, "snapshot-origin-once");
    CHECK(occurrences(roots[SNAPSHOT], roots[PARTIAL]) ==
          (partial_visible && backend != 4), 26, "partial-membership");
    if (backend == 4) CHECK(py_list_len(roots[SNAPSHOT]) == 0, 27, "gc4-existing-limit");
    drop_result(SNAPSHOT);
    return 0;
}
static int representation(PyObject *obj) {
    /* Never infer removal of ObjectNodes from absence of tracked GCNodes. */
    CHECK(stop_world() == 0, 30, "representation-stop");
    pcc_gc_default_table_lock();
    int has_tracking_node = py_gc_index_find(obj) != 0;
    CHECK(has_tracking_node == (py_gc_is_tracked(obj) && !flag_mode),
          31, "idle-gcnode-mode");
    CHECK(pcc_gc_tracking_materialized == 0, 32, "idle-view-released");
    pcc_gc_default_table_unlock();
    CHECK(pcc_resume_world() == 0, 33, "representation-resume");
    return 0;
}
static int origins(void) {
    roots[SMALL] = py_list_new(0);
    roots[LARGE] = py_tuple_new(TUPLE_ITEMS);
    roots[PARTIAL] = py_tuple_new(TUPLE_ITEMS);
    CHECK(roots[SMALL] && roots[LARGE] && roots[PARTIAL], 40, "native-objects");
    for (int i = 0; i < TUPLE_ITEMS; i++)
        py_tuple_set_item(roots[LARGE], i, i == 0 ? roots[SMALL] : py_None);
    CHECK(PyType_Ready(&raw_type) == 0, 41, "raw-type-ready");
    struct RawObject *raw = (struct RawObject *)calloc(1, sizeof(struct RawObject));
    CHECK(raw && PyObject_Init((PyObject *)raw, &raw_type) == (PyObject *)raw,
          42, "raw-object-init");
    roots[RAW] = (PyObject *)raw;
    pcc_gc_store_ptr(roots[RAW], &raw->edge, roots[SMALL]);
    pcc_gc_publish_initialized(roots[RAW]);
    roots[STATIC] = (PyObject *)&static_float;
    CHECK(pcc_gc_pointer_register_static_literal(roots[STATIC]) >= 0,
          43, "static-register");
    for (int i = SMALL; i <= PARTIAL; i++) pcc_gc_pin(roots[i]);

    if (backend <= 2)
        CHECK(pcc_gc_granule_is_object_start(roots[SMALL]) == 1, 44, "small-live-slab");
    else
        CHECK(pcc_gc_pointer_is_managed(roots[SMALL]) == 1, 44, "moving-managed-origin");
    CHECK(pcc_gc_granule_is_object_start(roots[LARGE]) != 1 &&
          pcc_gc_granule_is_object_start(roots[PARTIAL]) != 1, 45, "large-not-slab");
    CHECK(pcc_gc_granule_is_object_start(roots[RAW]) != 1, 46, "raw-not-object-slab");
    CHECK(pcc_gc_static_literal_contains(roots[STATIC]) == 1, 47, "static-image");
    CHECK(object_node(roots[STATIC]) == 0 && exact_member(roots[STATIC]) == 1,
          48, "static-exact-only");
    CHECK(py_gc_is_tracked(roots[LARGE]) == (backend != 4), 49, "tuple-constructor-tracks");
    CHECK(py_gc_is_tracked(roots[PARTIAL]) == 0, 50, "partial-untracked");
    if (backend != 0) {
        CHECK(object_node(roots[SMALL]) && object_node(roots[LARGE]) &&
              object_node(roots[RAW]) && object_node(roots[PARTIAL]), 51, "objectnodes-retained");
        CHECK(exact_member(roots[LARGE]) == 0 && exact_member(roots[RAW]) == 0,
              52, "non-slab-objectnode-only");
    } else {
        CHECK(exact_member(roots[LARGE]) == 1 && exact_member(roots[RAW]) == 1,
              53, "gc0-exact-origin");
    }
    /* Pin is established by its real API, then left intact across membership
     * changes. The raw object's MALLOC and static object's IMMORTAL bits also
     * have real producers; no synthetic terminal or malformed headers. */
    for (int i = SMALL; i <= STATIC; i++) py_gc_untrack(roots[i]);
    int64_t base = py_gc_get_count(0);
    for (int i = SMALL; i <= STATIC; i++) {
        int32_t other = ((PyObjectHeader *)roots[i])->flags & ~TEST_TRACKED;
        py_gc_track(roots[i]);
        py_gc_track(roots[i]);
        CHECK(py_gc_is_tracked(roots[i]) == (backend != 4), 54, "track-idempotent");
        CHECK((((PyObjectHeader *)roots[i])->flags & ~TEST_TRACKED) == other,
              55, "track-preserves-flags");
        CHECK(py_gc_get_count(0) == base + (backend != 4), 56, "track-count-once");
        py_gc_untrack(roots[i]);
        py_gc_untrack(roots[i]);
        CHECK(py_gc_is_tracked(roots[i]) == 0 && py_gc_get_count(0) == base,
              57, "untrack-count-once");
        CHECK((((PyObjectHeader *)roots[i])->flags & ~TEST_TRACKED) == other,
              58, "untrack-preserves-flags");
    }
    for (int i = SMALL; i <= STATIC; i++) py_gc_track(roots[i]);
    CHECK(py_gc_get_count(0) == base + (backend != 4 ? 4 : 0), 59, "all-origin-count");
    for (int i = SMALL; i <= STATIC; i++) CHECK(representation(roots[i]) == 0, 60, "origin-mode");
    CHECK(snapshot_members(0) == 0, 61, "origin-snapshot");
    roots[REFERRERS] = py_gc_get_referrers(roots[SMALL]);
    CHECK(roots[REFERRERS] && !py_err_occurred(), 62, "referrers-result");
    CHECK(unique_snapshot(roots[REFERRERS]), 63, "referrers-unique-not-self");
    CHECK(occurrences(roots[REFERRERS], roots[LARGE]) == (backend != 4) &&
          occurrences(roots[REFERRERS], roots[RAW]) == (backend != 4),
          64, "tuple-and-real-c-traversal-referrers");
    if (backend == 4) CHECK(py_list_len(roots[REFERRERS]) == 0, 65, "gc4-referrers-limit");
    drop_result(REFERRERS);
    for (int i = 0; i < TUPLE_ITEMS; i++)
        py_tuple_set_item(roots[PARTIAL], i, i == 0 ? roots[SMALL] : py_None);
    CHECK(snapshot_members(1) == 0, 66, "completed-tuple-snapshot");
    for (int i = SMALL; i <= STATIC; i++) CHECK(representation(roots[i]) == 0, 67, "post-snapshot-mode");
    return 0;
}

struct RawCall {
    PyObject *identity; /* parent keeps a registered root and pin until join */
    int untrack;
    int64_t entered, finished;
    int before_registered, after_registered, depth;
};
static struct RawCall raw_calls_context[4];
static void *raw_entry(void *opaque) {
    struct RawCall *call = (struct RawCall *)opaque;
    call->before_registered = (int)pcc_thread_registered();
    __atomic_store_n(&call->entered, 1, __ATOMIC_RELEASE);
    /* No explicit ID lookup, safepoint, allocation, owner acquisition or
     * object dereference before this first operation. This tests tracking's
     * existing defensive admission, not arbitrary raw-pointer C-API safety. */
    if (call->untrack) py_gc_untrack(call->identity);
    else py_gc_track(call->identity);
    call->after_registered = (int)pcc_thread_registered();
    call->depth = (int)pcc_thread_no_park_depth();
    if (call->untrack) py_gc_untrack(call->identity);
    else py_gc_track(call->identity);
    pcc_thread_unregister_current();
    __atomic_store_n(&call->finished, 1, __ATOMIC_RELEASE);
    return 0;
}
static int admission_round(int untrack, int during_view) {
    pthread_t threads[4];
    for (int i = 0; i < workers; i++) {
        if (untrack) py_gc_track(roots[THREAD_BASE + i]);
        else py_gc_untrack(roots[THREAD_BASE + i]);
        raw_calls_context[i].identity = roots[THREAD_BASE + i];
        raw_calls_context[i].untrack = untrack;
        raw_calls_context[i].entered = raw_calls_context[i].finished = 0;
        raw_calls_context[i].before_registered = raw_calls_context[i].after_registered = -1;
    }
    int64_t before = py_gc_get_count(0);
    int64_t acquired = 0;
    if (during_view) {
        CHECK(stop_world() == 0, 70, "raw-stop");
        pcc_gc_default_table_lock();
        acquired = pcc_gc_tracking_flag_mode && !pcc_gc_tracking_materialized;
        CHECK(pcc_gc_tracked_materialize() >= 0 && acquired == flag_mode,
              71, "raw-view-acquire");
    }
    for (int i = 0; i < workers; i++)
        CHECK(pthread_create(&threads[i], 0, raw_entry, &raw_calls_context[i]) == 0,
              72, "fresh-pthread-create");
    if (during_view) {
        int64_t deadline = now() + 5000000;
        while (pcc_thread_registration_waiter_count() < workers) {
            for (int i = 0; i < workers; i++)
                CHECK(__atomic_load_n(&raw_calls_context[i].finished, __ATOMIC_ACQUIRE) == 0,
                      73, "raw-escaped-stopped-world");
            CHECK(now() < deadline, 74, "raw-admission-wait-timeout");
        }
        CHECK(py_gc_get_count(0) == before, 75, "raw-no-stopped-mutation");
        roots[SNAPSHOT] = py_gc_get_objects();
        CHECK(roots[SNAPSHOT] && unique_snapshot(roots[SNAPSHOT]), 76, "raw-borrowed-snapshot");
        for (int i = 0; i < workers; i++)
            CHECK(occurrences(roots[SNAPSHOT], roots[THREAD_BASE + i]) == untrack,
                  77, "raw-snapshot-before-admission");
        drop_result(SNAPSHOT);
        pcc_gc_tracked_dematerialize(acquired);
        pcc_gc_default_table_unlock();
        CHECK(pcc_resume_world() == 0, 78, "raw-resume-before-join");
    }
    /* Never block in pthread_join while owning STW. Publish the parent's
     * blocking state so teardown/collector work cannot wait on this join. */
    for (int i = 0; i < workers; i++) {
        int64_t blocking = pcc_thread_blocking_enter(__builtin_frame_address(0));
        int joined = pthread_join(threads[i], 0);
        pcc_thread_blocking_leave(blocking);
        CHECK(joined == 0, 79, "raw-join");
        CHECK(raw_calls_context[i].before_registered == 0 &&
              raw_calls_context[i].after_registered == 1 && raw_calls_context[i].depth == 0,
              80, "implicit-admission-balanced");
        CHECK(py_gc_is_tracked(roots[THREAD_BASE + i]) == !untrack, 81, "raw-final-membership");
        CHECK(representation(roots[THREAD_BASE + i]) == 0, 82, "raw-final-mode");
    }
    CHECK(py_gc_get_count(0) == before + (untrack ? -workers : workers), 83, "raw-exact-count");
    raw_calls += workers;
    return 0;
}
static int raw_admission(void) {
    CHECK(backend == 1 || backend == 2, 84, "raw-nonmoving-backend");
    for (int i = 0; i < workers; i++) {
        roots[THREAD_BASE + i] = py_list_new(0);
        CHECK(roots[THREAD_BASE + i], 85, "raw-parent-object");
        pcc_gc_pin(roots[THREAD_BASE + i]);
    }
    for (int view = 0; view < 2; view++)
        for (int untrack = 0; untrack < 2; untrack++)
            CHECK(admission_round(untrack, view) == 0, 86, "raw-round");
    return 0;
}

static PyObject *completed_resume(PyObject *gen, PyObject *frame) {
    (void)frame;
    return py_gen_finish(gen, py_None);
}
static void *make_garbage(void *unused) {
    (void)unused;
    for (int i = 0; i < GARBAGE_COUNT; i++) {
        PyObject *frame = py_gen_frame_new(1);
        PyObject *child = py_list_new(0);
        if (!frame || !child) { creator_status = 1; return 0; }
        py_gen_frame_set(frame, 0, child);
        PyObject *gen = py_gen_new((void *)completed_resume, frame);
        if (!gen) { creator_status = 2; return 0; }
        py_gen_set_done(gen);
        garbage[i] = gen;
        py_decref(gen);
        py_decref(frame);
        py_decref(child);
    }
    return 0;
}
static int pending_retirement(void) {
    CHECK(backend == 1 || backend == 2, 90, "pending-nonmoving-backend");
    void *creator = 0;
    CHECK(pcc_thread_start(&creator, make_garbage, 0) == 0, 91, "pending-creator");
    CHECK(pcc_thread_join(creator, 0) == 0 && creator_status == 0, 92, "pending-creator-joined");
    /* Do not read target identities on the observing thread before the real
     * completed mark and sweep: a conservative stack read would root them.
     * Unlike controlled candidate tests, no mark/header/verdict is injected. */
    CHECK(stop_world() == 0, 93, "pending-stop");
    CHECK(pcc_gc_deferred_free_begin(0) == 1, 94, "pending-reservation");
    CHECK(pcc_gc_scalar_sweep_counter(0) == 1, 95, "pending-building");
    pcc_gc_begin_explicit_tracing_collect();
    for (int step = 0; step < 128 && !pcc_gc_sweep_owed(); step++) pcc_gc_step(1024);
    CHECK(pcc_gc_sweep_owed() != 0, 96, "real-mark-completed");
    CHECK(pcc_gc_tracing_sweep_unreachable(4096) > 0, 97, "real-sweep");
    CHECK(pcc_gc_scalar_sweep_counter(0) == 1 &&
          pcc_gc_scalar_sweep_counter(4) == 0 && pcc_gc_scalar_sweep_counter(5) == 0,
          98, "pending-not-retired-or-released");
    roots[SNAPSHOT] = py_gc_get_objects();
    CHECK(roots[SNAPSHOT] && unique_snapshot(roots[SNAPSHOT]), 99, "pending-snapshot");
    for (int i = 0; i < GARBAGE_COUNT; i++) {
        if (!pcc_gc_scalar_sweep_owns(garbage[i])) continue;
        /* Queue ownership proves bytes remain inspectable until sealing.
         * A genuine pending ObjectNode still exists, but no tracked GCNode
         * or visible snapshot owner may be reconstructed from it. */
        CHECK(object_node(garbage[i]) != 0, 100, "pending-objectnode-retained");
        CHECK((((PyObjectHeader *)garbage[i])->flags & TEST_DEALLOCATING) != 0 &&
              py_gc_is_tracked(garbage[i]) == 0, 101, "pending-real-terminal-state");
        CHECK(py_gc_index_find(garbage[i]) == 0 &&
              occurrences(roots[SNAPSHOT], garbage[i]) == 0, 102, "pending-not-visible");
        int64_t count = py_gc_get_count(0);
        py_gc_untrack(garbage[i]);
        CHECK(py_gc_get_count(0) == count, 103, "pending-untrack-idempotent");
        pending_seen++;
    }
    CHECK(pending_seen > 0, 104, "observed-real-pending-gen");
    drop_result(SNAPSHOT);
    pcc_gc_end_explicit_tracing_collect();
    pcc_gc_scalar_sweep_seal();
    CHECK(pcc_resume_world() == 0, 105, "pending-resume");
    pcc_gc_deferred_free_close();
    int64_t deadline = now() + 5000000;
    while (pcc_gc_scalar_sweep_counter(0) != 0) {
        CHECK(now() < deadline, 106, "pending-drain-timeout");
        pcc_thread_safepoint();
    }
    CHECK(pcc_gc_scalar_sweep_counter(3) == pcc_gc_scalar_sweep_counter(4) &&
          pcc_gc_scalar_sweep_counter(4) == pcc_gc_scalar_sweep_counter(5),
          107, "pending-retirement-completed");
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 4) return 1;
    int scenario = atoi(argv[1]);
    workers = atoi(argv[2]);
    int required_flag_mode = atoi(argv[3]);
    if ((workers != 1 && workers != 4) || scenario < 0 || scenario > 2) return 2;
    backend = (int)pcc_gc_backend();
    CHECK(backend >= 0 && backend <= 4 && pcc_threads_enabled() == 1, 3, "configuration");
    py_gc_disable();
    py_gc_set_threshold(0, 10, 10);
    int32_t map = ROOT_COUNT;
    pcc_gc_frame_enter(&map, roots);
    /* First real allocation freezes selection before checking its mode. */
    roots[THREAD_BASE] = py_list_new(0);
    CHECK(roots[THREAD_BASE], 4, "freeze-allocation");
    pcc_gc_pin(roots[THREAD_BASE]);
    flag_mode = pcc_gc_tracking_flag_mode;
    CHECK(flag_mode == required_flag_mode, 5, "required-tracking-mode");
    CHECK(pcc_gc_backend4_no_refcounts == (backend != 0), 6, "countfree-mode");
    CHECK(pcc_gc_nonmoving_count_free == (backend == 1 || backend == 2), 7, "nonmoving-mode");
    int result = scenario == 0 ? origins() : scenario == 1 ? raw_admission() : pending_retirement();
    if (result) return result;
    if (backend != 0) CHECK(pcc_gc_cms_stop_worker() == 0, 8, "worker-stop");
    CHECK(!py_err_occurred(), 9, "no-pending-exception");
    printf("NONMOVING_TRACKING_OK gc=%d scenario=%d workers=%d flag_mode=%d "
           "origins=%d raw_calls=%lld pending=%lld\n", backend, scenario, workers,
           flag_mode, scenario == 0 ? 4 : 0, (long long)raw_calls, (long long)pending_seen);
    /* Process-isolated fixture: all pinned origin owners remain valid until
     * exit, including the C object's tp_traverse edge. No C-API free claim. */
    return 0;
}
