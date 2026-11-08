/* Contract: docs/project-intent.md, shared five-GC slot/root semantics.
 * Owned-C caller of the production pcc-Python runtime. Forwarding is installed
 * by its public API between actual same-type objects, never synthetic flags.
 * The private queue observation matches test_queued_graph_lock_native.py: a
 * held graph word prevents the enqueued writer from dequeuing or publishing.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct NativeRange { void **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used, capacity, owner;
} NativeFrame;
enum { FROM = 0, TARGET = 1, SHARED = 2, ROOT_COUNT = 3 };
enum { CONTEXT_WORD = 0, CONTEXT_NEXT = 8, CONTEXT_TAIL = 16 };
typedef struct Fixture {
    PyObject *roots[ROOT_COUNT];
    PyObject *immediate;
    int64_t take, writer_ready, writer_go, writer_done, collector_done;
    int64_t queue_witnesses, collections, initial_population;
} Fixture;

/* Signatures traced to c_abi_export owners in freestanding_thread_kernel_pthread,
 * freestanding_runtime_high_substrate and freestanding_gc_root_operations. */
extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_thread_native_root_contains(void *);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern PyObject *pcc_gc_resolve_root_slot_unlocked(void *, int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_metric_store_count(void);
extern int32_t pcc_gc_forwarding_population;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_refcount_provenance_probe;
extern int32_t pcc_thread_conservative_roots_enabled;
extern int32_t g_pcc_py_gc_minor_graph_lock;
extern void *pcc_graph_wait_context_head_py;

static void fail(int code, const char *where) {
    fprintf(stderr, "FORWARDED_ROOT_FAIL code=%d where=%s\n", code, where);
    _Exit(code);
}
static void require(int condition, int code, const char *where) {
    if (!condition) fail(code, where);
}
static int64_t now(void) { return pcc_runtime_monotonic_us(); }
static int64_t acquire(int64_t *word) {
    return __atomic_load_n(word, __ATOMIC_ACQUIRE);
}
static void publish(int64_t *word) {
    __atomic_store_n(word, 1, __ATOMIC_RELEASE);
}
static void wait_flag(int64_t *word, const char *where) {
    int64_t deadline = now() + 5000000;
    while (!acquire(word)) {
        require(now() < deadline, 10, where);
        pcc_thread_safepoint();
    }
}
static void store_root(Fixture *fixture, PyObject **slot, PyObject *value) {
    if (fixture->take) pcc_gc_store_root_take(slot, value);
    else pcc_gc_store_root(slot, value);
}
static PyObject *tagged(int64_t value) {
    PyObject *result = py_int_from_i64(value);
    uint64_t bits = ((uint64_t)value << 1) | 1;
    require((uint64_t)(uintptr_t)result == bits, 11, "int63_constructor");
    int overflow = 0;
    require(py_int_to_i64(result, &overflow) == value && !overflow,
            12, "int63_roundtrip");
    return result;
}
static uint64_t graph_tail(void) {
    void *context = (void *)(uintptr_t)__atomic_load_n(
        (uintptr_t *)&pcc_graph_wait_context_head_py, __ATOMIC_ACQUIRE);
    while (context) {
        void *word = *(void **)((char *)context + CONTEXT_WORD);
        if (word == (void *)&g_pcc_py_gc_minor_graph_lock)
            return __atomic_load_n((uint64_t *)((char *)context + CONTEXT_TAIL),
                                   __ATOMIC_ACQUIRE);
        context = *(void **)((char *)context + CONTEXT_NEXT);
    }
    return 0;
}
static void require_balanced(int64_t graph, int64_t lease) {
    require(pcc_py_gc_minor_graph_lock_depth() == graph, 13, "graph_balance");
    require(pcc_thread_no_park_depth() == lease, 14, "no_park_balance");
}
static void require_forwarded(Fixture *fixture) {
    require(__atomic_load_n(&pcc_gc_forwarding_population, __ATOMIC_ACQUIRE) > 0,
            15, "real_forwarding_population");
    /* Run an actual resolver on a rooted cell and reset only under its lock.
     * The from/to objects stay rooted; no collector runs in the setup phase. */
    fixture->roots[SHARED] = fixture->roots[FROM];
    require(pcc_gc_resolve_root_slot_unlocked(&fixture->roots[SHARED], 0)
                == fixture->roots[TARGET], 16, "real_forwarding_resolution");
    require(fixture->roots[SHARED] == fixture->roots[TARGET], 17, "healed_slot");
    fixture->roots[SHARED] = fixture->roots[FROM];
}
static void checked_store(Fixture *fixture, PyObject *value, PyObject *expected) {
    pcc_py_gc_minor_graph_lock();
    require_forwarded(fixture);
    pcc_py_gc_minor_graph_unlock();
    int64_t graph = pcc_py_gc_minor_graph_lock_depth();
    int64_t lease = pcc_thread_no_park_depth();
    int64_t stores = pcc_gc_metric_store_count();
    int64_t from_refs = py_header(fixture->roots[FROM])->refcount;
    int64_t target_refs = py_header(fixture->roots[TARGET])->refcount;
    store_root(fixture, &fixture->roots[SHARED], value);
    require(fixture->roots[SHARED] == expected, 18, "published_value");
    require_balanced(graph, lease);
    require(pcc_gc_metric_store_count() == stores + 1, 19, "one_store_metric");
    require(py_header(fixture->roots[FROM])->refcount == from_refs &&
            py_header(fixture->roots[TARGET])->refcount == target_refs,
            20, "count_free_owners");
    stores = pcc_gc_metric_store_count();
    store_root(fixture, 0, value);
    require(pcc_gc_metric_store_count() == stores, 21, "null_slot_metric");
    require_balanced(graph, lease);
}
static void semantics(Fixture *fixture) {
    PyObject *values[6] = {
        0, tagged(-4611686018427387904LL), tagged(4611686018427387903LL),
        tagged(-1), tagged(0), tagged(1)
    };
    /* Plain entry, an existing no-park lease, and recursive graph ownership. */
    for (int nesting = 0; nesting < 3; nesting++) {
        if (nesting) pcc_thread_no_park_enter();
        if (nesting == 2) {
            pcc_py_gc_minor_graph_lock();
            pcc_py_gc_minor_graph_lock();
        }
        for (int index = 0; index < 6; index++)
            checked_store(fixture, values[index], values[index]);
        if (nesting == 2) {
            pcc_py_gc_minor_graph_unlock();
            pcc_py_gc_minor_graph_unlock();
        }
        if (nesting) pcc_thread_no_park_exit();
        require_balanced(0, 0);
    }
    /* Heap forwarding and foreign/even words still need the checked plan.
     * These are rooted only with collection disabled and cleared before GC. */
    checked_store(fixture, fixture->roots[FROM], fixture->roots[TARGET]);
    checked_store(fixture, fixture->roots[TARGET], fixture->roots[TARGET]);
    checked_store(fixture, (PyObject *)(uintptr_t)2, (PyObject *)(uintptr_t)2);
    checked_store(fixture, (PyObject *)(uintptr_t)6, (PyObject *)(uintptr_t)6);
    uint64_t foreign[4] = {0, 0, 0, 0};
    checked_store(fixture, (PyObject *)foreign, (PyObject *)foreign);
    store_root(fixture, &fixture->roots[SHARED], 0);
    require_balanced(0, 0);
}
static void *writer(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    require_balanced(0, 0);
    publish(&fixture->writer_ready);
    wait_flag(&fixture->writer_go, "writer_go");
    /* No runtime lock is taken by the harness between go and this call.
     * A queue tail therefore witnesses the public store's real lock entry. */
    store_root(fixture, &fixture->roots[SHARED], fixture->immediate);
    require_balanced(0, 0);
    publish(&fixture->writer_done);
    return 0;
}
static void *collector(void *argument) {
    Fixture *fixture = (Fixture *)argument;
    require(pcc_stop_the_world() == 0, 30, "real_stop");
    require(pcc_thread_owns_stopped_world() == 1, 31, "stop_owner");
    require(graph_tail() == 0 &&
            __atomic_load_n(&g_pcc_py_gc_minor_graph_lock, __ATOMIC_ACQUIRE) == 0,
            32, "queue_drained_before_stop");
    /* A writer may park in its final unlock before returning, so writer_done
     * is deliberately not the publication witness. The actual slot is. */
    require(fixture->roots[SHARED] == fixture->immediate, 33, "store_before_stop");
    int64_t collections = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_COUNT);
    require(pcc_gc_collect(2) >= 0, 34, "actual_collection");
    fixture->collections = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_COUNT) - collections;
    require(fixture->collections > 0, 35, "collection_receipt");
    require(pcc_thread_owns_stopped_world() == 1, 36, "nested_stop_balance");
    require(fixture->roots[SHARED] == fixture->immediate, 37, "collector_kept_immediate");
    require(py_str_len(fixture->roots[TARGET]) == 14, 38, "registered_target_survives");
    require(pcc_resume_world() == 0, 39, "resume_world");
    require_balanced(0, 0);
    publish(&fixture->collector_done);
    return 0;
}
static void handoff(Fixture *fixture) {
    PccThreadHandle *writer_handle = 0, *collector_handle = 0;
    require(pcc_thread_start(&writer_handle, writer, fixture) == 0, 40, "start_writer");
    wait_flag(&fixture->writer_ready, "writer_ready");
    pcc_py_gc_minor_graph_lock();
    require_forwarded(fixture);
    require(graph_tail() == 0, 41, "initial_queue_empty");
    PyObject *loaded = fixture->roots[SHARED];
    require(loaded == fixture->roots[FROM], 42, "reader_loaded_source");
    publish(&fixture->writer_go);
    int64_t deadline = now() + 5000000;
    while (graph_tail() == 0) {
        require(!acquire(&fixture->writer_done), 43, "writer_bypassed_graph_lock");
        require(now() < deadline, 44, "writer_never_queued");
        pcc_thread_safepoint();
    }
    fixture->queue_witnesses += 1;
    require(!acquire(&fixture->writer_done) && fixture->roots[SHARED] == loaded,
            45, "writer_waited_without_mutating");
    require(pcc_thread_start(&collector_handle, collector, fixture) == 0,
            46, "start_collector");
    deadline = now() + 5000000;
    while (!pcc_thread_stop_requested_acquire())
        require(now() < deadline, 47, "collector_never_requested_stop");
    require(!acquire(&fixture->collector_done), 48, "stop_ignored_no_park");
    require(pcc_gc_resolve_root_slot_unlocked(&fixture->roots[SHARED], 0)
                == fixture->roots[TARGET], 49, "graph_locked_reader_heals");
    require(!acquire(&fixture->writer_done), 50, "writer_waited_through_heal");
    /* Release drains the queued writer before stop admission. The collector
     * then checks the final slot and exercises the production mark/sweep. */
    pcc_py_gc_minor_graph_unlock();
    void *result = 0;
    require(pcc_thread_join(writer_handle, &result) == 0 && !result, 51, "join_writer");
    require(pcc_thread_join(collector_handle, &result) == 0 && !result, 52, "join_collector");
    require(acquire(&fixture->writer_done) && acquire(&fixture->collector_done),
            53, "both_completed");
    require(fixture->roots[SHARED] == fixture->immediate, 54, "final_store_not_lost");
    require_balanced(0, 0);
    require(pcc_thread_live_count() == 1, 55, "thread_balance");
}
int main(int argc, char **argv) {
    require(argc == 4, 1, "arguments");
    int64_t backend = pcc_gc_backend();
    require((backend == 3 || backend == 4) && pcc_gc_backend4_no_refcounts == 1,
            2, "requires_count_free_gc3_or_gc4");
    require(pcc_threads_enabled() == 1 && pcc_current_thread_id() > 0,
            3, "requires_native_threads");
    require(pcc_gc_refcount_provenance_probe == 0 &&
            pcc_diagnostics_runtime_log_fast_state == 0 &&
            pcc_thread_conservative_roots_enabled == 1, 4, "production_fast_path_gates");
    /* Fixture control only: stop automatic work to own the handoff schedule.
     * The handoff still performs a real explicit collector transaction. This
     * is neither production scheduling nor QPS evidence. */
    py_gc_disable();
    require(pcc_gc_cms_stop_worker() == 0, 5, "stop_automatic_worker");
    require(pcc_thread_live_count() == 1, 6, "isolated_thread_schedule");
    Fixture fixture = {0};
    fixture.take = argv[1][0] == 't';
    NativeFrame frame = {0};
    NativeRange ranges[1];
    require(sizeof(NativeFrame) == 40 && sizeof(NativeRange) == 16,
            60, "native_frame_abi");
    pcc_thread_no_park_enter();
    require(pcc_gc_native_frame_enter(&frame, ranges, 1) == 0, 7, "native_frame_enter");
    void *token = pcc_gc_native_root_bind_range(fixture.roots, ROOT_COUNT);
    require(token != 0, 8, "native_root_bind");
    pcc_thread_no_park_exit();
    require(pcc_thread_native_root_contains(&fixture.roots[SHARED]) == 1,
            9, "shared_slot_registered");
    fixture.roots[FROM] = py_str_new("forwarded-root", 14);
    fixture.roots[TARGET] = py_str_new("forwarded-root", 14);
    require(fixture.roots[FROM] && fixture.roots[TARGET] &&
            fixture.roots[FROM] != fixture.roots[TARGET], 56, "real_distinct_objects");
    require(pcc_gc_install_forwarding(fixture.roots[FROM], fixture.roots[TARGET]) == 0,
            57, "public_forwarding_install");
    fixture.initial_population = __atomic_load_n(&pcc_gc_forwarding_population, __ATOMIC_ACQUIRE);
    if (argv[2][0] == 's') semantics(&fixture);
    else {
        if (argv[3][0] == 'n') fixture.immediate = 0;
        else if (argv[3][0] == 'l') fixture.immediate = tagged(-4611686018427387904LL);
        else fixture.immediate = tagged(4611686018427387903LL);
        handoff(&fixture);
    }
    for (int index = 0; index < ROOT_COUNT; index++)
        store_root(&fixture, &fixture.roots[index], 0);
    pcc_gc_native_root_unbind(token);
    require(pcc_gc_native_frame_leave(&frame) == 0, 58, "native_frame_leave");
    require(!pcc_thread_native_root_contains(&fixture.roots[SHARED]), 59, "root_unbound");
    require_balanced(0, 0);
    printf("FORWARDED_IMMEDIATE_ROOTS_OK gc=%lld take=%lld mode=%s value=%s "
           "forwarding_before=%lld forwarding_after=%d count_free=%d probe=%d log=%d "
           "conservative=%d queue_witnesses=%lld collections=%lld graph=%lld lease=%lld\n",
           (long long)backend, (long long)fixture.take, argv[2], argv[3],
           (long long)fixture.initial_population, pcc_gc_forwarding_population,
           pcc_gc_backend4_no_refcounts, pcc_gc_refcount_provenance_probe,
           pcc_diagnostics_runtime_log_fast_state, pcc_thread_conservative_roots_enabled,
           (long long)fixture.queue_witnesses, (long long)fixture.collections,
           (long long)pcc_py_gc_minor_graph_lock_depth(),
           (long long)pcc_thread_no_park_depth());
    return 0;
}
