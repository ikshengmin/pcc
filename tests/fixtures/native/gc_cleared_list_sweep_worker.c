/* Real populated cyclic LISTs on the owned runtime. Mode 1 uses public
 * collection with a joined creator and no injected candidate/mark verdict.
 * Modes 2/3 deliberately install candidate flags and a same-stop-epoch mark
 * verdict, then run production PASS0/PASS1/PASS2 at budget one. No finalizer,
 * weakref, or post-callback reachability recheck is replaced or disabled.
 * Mode 3 holds a real materialized merged tracking view during the sweep. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_gc_deferred_free_close(void);
extern void pcc_gc_scalar_sweep_seal(void);
extern int64_t pcc_gc_scalar_sweep_counter(int64_t);
extern int64_t pcc_gc_scalar_sweep_try_enqueue(void *, void *);
extern int64_t pcc_gc_scalar_sweep_owns(void *);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_platform_sleep_ns(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void *pcc_gc_object_index_find(void *);
extern void *py_gc_index_find(void *);
extern void pcc_gc_default_table_lock(void);
extern void pcc_gc_default_table_unlock(void);
extern int64_t pcc_gc_tracked_materialize(void);
extern void pcc_gc_tracked_dematerialize(int64_t);
extern int64_t malloc_usable_size(void *);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_gc_tracing_finish_stop_epoch;
extern int64_t pcc_gc_user_finalizer_runs;
extern int32_t py_gc_enabled, py_gc_tracked_count;
extern int32_t pcc_gc_tracking_flag_mode, pcc_gc_tracking_materialized;

#define C(n) pcc_gc_scalar_sweep_counter(n)
#define CHECK(x, n) do { if (!(x)) { if (!first_failure) first_failure = n; return n; } } while (0)
#define MANY 777
#define BOUNDED 12
#define TRACKED 2
#define CANDIDATE 1024
#define WHITE 8
#define LIST_LENGTH 16
#define LIST_CAPACITY 24
#define LIST_ITEMS 32
#define NODE_OWNER 32
#define NODE_TAG 64
/* Diagnostic identities are not registered roots. Public target addresses
 * are not read on the collector's stack until collection has completed. */
static PyObject *lists[MANY], *sentinel;
static void *nodes[BOUNDED], *original_items[BOUNDED];
static int64_t original_rc[BOUNDED], item_bytes[BOUNDED], original_capacity[BOUNDED];
static int observed[BOUNDED];
static int mode, first_failure, setup_status;
static int64_t producer, admitted, observed_list, public_list_gone;
static int64_t bounded_calls, rejected, tracking_checks, tracked_delta;
static int64_t sentinel_checks, item_release_checks, items_released_bytes;
static int64_t finalizer_delta, materialized_checks;

static int64_t word(void *base, int offset) {
    return *(int64_t *)((char *)base + offset);
}
static void *pointer(void *base, int offset) {
    return *(void **)((char *)base + offset);
}
static void *node_for(PyObject *obj) {
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_index_find(obj);
    pcc_py_gc_minor_graph_unlock();
    return node;
}
static int check_sentinel(void) {
    CHECK(((PyObjectHeader *)sentinel)->type_tag == PY_TYPE_LIST, 80);
    CHECK(word(sentinel, LIST_LENGTH) == 1 && word(sentinel, LIST_CAPACITY) >= 4, 81);
    CHECK(pointer(sentinel, LIST_ITEMS) && pointer(pointer(sentinel, LIST_ITEMS), 0) == sentinel, 82);
    if (pcc_gc_backend() != 0) CHECK(node_for(sentinel), 83);
    sentinel_checks++;
    return 0;
}
static int finish_worker(void) {
    int rounds = 0;
    while ((C(5) != admitted || C(0) != 0) && rounds++ < 2000) {
        pcc_thread_safepoint();
        pcc_platform_sleep_ns(1000000);
    }
    CHECK(C(3) == admitted && C(4) == admitted && C(5) == admitted, 84);
    CHECK(C(6) == admitted && C(7) == 0, 85);
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(9) == 0, 86);
    if (admitted) CHECK(C(8) > 0 && C(8) != producer, 87);
    CHECK(pcc_gc_cms_stop_worker() == 0, 88);
    CHECK(pcc_gc_cms_stop_worker() == -1, 89);
    CHECK(C(4) == admitted && C(5) == admitted, 90);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 91);
    return check_sentinel();
}
static int make_garbage(void) {
    int count = mode == 1 ? MANY : BOUNDED;
    for (int i = 0; i < count; i++) {
        PyObject *obj = py_list_new(8 + i % 3 * 8);
        CHECK(obj, 10);
        py_list_append(obj, obj);
        py_list_append(obj, sentinel);
        CHECK(word(obj, LIST_LENGTH) == 2 && word(obj, LIST_CAPACITY) >= 8, 11);
        lists[i] = obj;
        py_decref(obj);
        if (mode != 1) {
            nodes[i] = node_for(obj);
            original_items[i] = pointer(obj, LIST_ITEMS);
            original_capacity[i] = word(obj, LIST_CAPACITY);
            original_rc[i] = ((PyObjectHeader *)obj)->refcount;
            item_bytes[i] = malloc_usable_size(original_items[i]);
            CHECK(nodes[i] && original_items[i] && item_bytes[i] > 0, 12);
            CHECK((((PyObjectHeader *)obj)->flags & TRACKED) && !py_gc_index_find(obj), 13);
        }
    }
    if (mode == 1) return 0;
    CHECK(pcc_stop_the_world() == 0, 14);
    CHECK(pcc_gc_deferred_free_begin(0) == 1 && C(0) == 1, 15);
    for (int i = 0; i < count; i++) {
        PyObject *obj = lists[i];
        PyObjectHeader *header = (PyObjectHeader *)obj;
        header->flags = (header->flags & ~(16384 | 16 | 32)) | CANDIDATE | WHITE;
        int flags = header->flags;
        /* A populated real cycle cannot be stolen by direct enqueue. */
        CHECK(pcc_gc_scalar_sweep_try_enqueue(obj, nodes[i]) == 0, 16);
        CHECK(header->flags == flags && header->refcount == original_rc[i], 17);
        CHECK(word(obj, LIST_LENGTH) == 2 && word(obj, LIST_CAPACITY) == original_capacity[i], 18);
        CHECK(pointer(obj, LIST_ITEMS) == original_items[i], 19);
        CHECK(pointer(original_items[i], 0) == obj && pointer(original_items[i], 8) == sentinel, 20);
        CHECK(node_for(obj) == nodes[i] && word(nodes[i], NODE_OWNER) == 0, 21);
        CHECK(!py_gc_index_find(obj) && (header->flags & TRACKED), 22);
        CHECK(C(3) == 0 && pcc_gc_scalar_sweep_owns(obj) == 0, 23);
        rejected++;
    }
    pcc_gc_scalar_sweep_seal();
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(12) == 0, 24);
    CHECK(pcc_resume_world() == 0, 25);
    pcc_gc_deferred_free_close();
    return 0;
}
static void *create_garbage(void *unused) {
    (void)unused;
    setup_status = make_garbage();
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    return 0;
}
static int observe_building(void) {
    CHECK(C(0) == 1 && C(4) == 0 && C(5) == 0, 30);
    for (int i = 0; i < BOUNDED; i++) {
        if (observed[i]) continue;
        PyObject *obj = lists[i];
        void *node = node_for(obj);
        /* Require indexed memory before reading any header, including on a
         * regression that took the old synchronous destruction path. */
        CHECK(node == nodes[i], 31);
        if (word(node, NODE_OWNER) >= 0) continue;
        CHECK(word(node, NODE_OWNER) == -C(11) && word(node, NODE_TAG) == PY_TYPE_LIST, 32);
        CHECK(((PyObjectHeader *)obj)->type_tag == PY_TYPE_LIST, 33);
        CHECK(((PyObjectHeader *)obj)->refcount == original_rc[i], 34);
        CHECK(!(((PyObjectHeader *)obj)->flags & TRACKED) && !py_gc_index_find(obj), 35);
        CHECK(word(obj, LIST_LENGTH) == 0 && word(obj, LIST_CAPACITY) == 0 && !pointer(obj, LIST_ITEMS), 36);
        CHECK(pcc_gc_granule_is_object_start(obj) == 1 && pcc_gc_scalar_sweep_owns(obj) == 1, 37);
        int32_t before = py_gc_tracked_count;
        py_gc_untrack(obj);
        CHECK(py_gc_tracked_count == before && !py_gc_index_find(obj), 38);
        CHECK(node_for(obj) == node, 39);
        observed[i] = 1;
        observed_list++;
        tracking_checks++;
        items_released_bytes += item_bytes[i];
    }
    return 0;
}
static int controlled_sweep(void) {
    CHECK(pcc_gc_backend() == 1 || pcc_gc_backend() == 2, 40);
    CHECK(pcc_stop_the_world() == 0, 41);
    CHECK(pcc_gc_deferred_free_begin(0) == 1 && C(0) == 1, 42);
    if (mode == 3) {
        pcc_gc_default_table_lock();
        CHECK(pcc_gc_tracking_flag_mode == 1 && pcc_gc_tracking_materialized == 0, 43);
        CHECK(pcc_gc_tracked_materialize() >= BOUNDED, 44);
        for (int i = 0; i < BOUNDED; i++) {
            void *tracking = py_gc_index_find(lists[i]);
            CHECK(tracking && pointer(tracking, 0) == lists[i], 45);
            materialized_checks++;
        }
    }
    int64_t tracked_before = py_gc_tracked_count;
    int64_t finalizers_before = pcc_gc_user_finalizer_runs;
    /* Controlled same-stop completed-mark verdict only. Real weakref,
     * finalizer and resurrection machinery executes inside every call. */
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    for (int step = 0; step <= BOUNDED; step++) {
        int64_t released_before = items_released_bytes;
        int64_t live_before = pcc_allocator_live_usable_bytes();
        int64_t result = pcc_gc_tracing_sweep_unreachable(1);
        int64_t live_after = pcc_allocator_live_usable_bytes();
        CHECK(result >= 0 && result <= 1, 46);
        bounded_calls++;
        CHECK(observe_building() == 0, 47);
        /* Only raw arrays are freed in this interval: shells are BUILDING,
         * flag mode allocates no GCNode, and the merged view defers GCNode
         * recycling until table unlock. Self/sentinel edges create no death. */
        CHECK(live_before - live_after == items_released_bytes - released_before, 48);
        if (result) item_release_checks++;
        if (!result) break;
    }
    finalizer_delta = pcc_gc_user_finalizer_runs - finalizers_before;
    tracked_delta = tracked_before - py_gc_tracked_count;
    CHECK(observed_list == BOUNDED && C(3) == observed_list, 49);
    CHECK(item_release_checks == BOUNDED && tracked_delta == BOUNDED && finalizer_delta == 0, 50);
    if (mode == 3) {
        pcc_gc_tracked_dematerialize(1);
        pcc_gc_default_table_unlock();
    }
    admitted = C(3);
    pcc_gc_scalar_sweep_seal();
    CHECK(C(12) == admitted && C(4) == 0 && C(5) == 0, 51);
    CHECK(pcc_resume_world() == 0, 52);
    pcc_gc_deferred_free_close();
    CHECK(finish_worker() == 0, 53);
    for (int i = 0; i < BOUNDED; i++) {
        CHECK(!node_for(lists[i]) && !py_gc_index_find(lists[i]), 54);
    }
    return 0;
}
static int run(void) {
    producer = pcc_current_thread_id();
    CHECK(producer > 0 && pcc_threads_enabled() == 1, 1);
    CHECK(pcc_refcount_strategy() == PCC_REFCOUNT_STRATEGY_ATOMIC, 2);
    py_gc_enabled = 0;
    sentinel = py_list_new(8);
    CHECK(sentinel, 3);
    py_list_append(sentinel, sentinel);
    pcc_gc_pin(sentinel);
    if (pcc_gc_backend() != 0) {
        int rounds = 0;
        while (pcc_thread_live_count() < 2 && rounds++ < 2000) {
            pcc_thread_safepoint();
            pcc_platform_sleep_ns(1000000);
        }
        CHECK(pcc_thread_live_count() == 2, 4);
    }
    CHECK(check_sentinel() == 0, 5);
    void *creator = 0;
    CHECK(pcc_thread_start(&creator, create_garbage, 0) == 0, 6);
    CHECK(pcc_thread_join(creator, 0) == 0 && setup_status == 0, 7);
    if (mode != 1) return controlled_sweep();
    /* Public collection has no surrounding stop, forged flags or enqueue. */
    (void)pcc_gc_collect(0);
    admitted = C(3);
    if (pcc_gc_backend() == 1 || pcc_gc_backend() == 2) CHECK(admitted > 512, 60);
    else CHECK(admitted == 0, 61);
    CHECK(finish_worker() == 0, 62);
    if (pcc_gc_backend() == 1 || pcc_gc_backend() == 2) {
        for (int i = 0; i < MANY; i++) {
            if (!node_for(lists[i])) {
                CHECK(!py_gc_index_find(lists[i]), 63);
                public_list_gone++;
            }
        }
        CHECK(public_list_gone > 512, 64);
    }
    /* Admissions are total queue deaths, not an inferred LIST count. */
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 98;
    mode = argv[1][0] - '0';
    if (mode < 1 || mode > 3 || argv[1][1]) return 99;
    int status = run();
    /* On assertion failure process exit preserves the receipt without
     * attempting to unwind a possibly held materialized tracking table. */
    printf("cleared-list-sweep backend=%d mode=%d status=%d first_failure=%d setup_status=%d "
           "tracking_flags=%d tracking_view=%d producer=%lld worker=%lld "
           "admitted=%lld observed_list=%lld public_list_gone=%lld "
           "retired=%lld released=%lld worker_retired=%lld local=%lld pending=%lld "
           "state=%lld quarantine=%lld chunks=%lld peak_objects=%lld peak_bytes=%lld "
           "bounded_calls=%lld rejected=%lld finalizers=%lld tracking_checks=%lld "
           "tracked_delta=%lld sentinel_checks=%lld item_release_checks=%lld "
           "items_released_bytes=%lld materialized_checks=%lld double_frees=%lld\n",
           pcc_gc_backend(), mode, status, first_failure, setup_status,
           pcc_gc_tracking_flag_mode, pcc_gc_tracking_materialized,
           (long long)producer, (long long)C(8), (long long)admitted,
           (long long)observed_list, (long long)public_list_gone,
           (long long)C(4), (long long)C(5), (long long)C(6), (long long)C(7),
           (long long)C(1), (long long)C(0), (long long)C(9), (long long)C(13),
           (long long)C(14), (long long)C(15), (long long)bounded_calls,
           (long long)rejected, (long long)finalizer_delta, (long long)tracking_checks,
           (long long)tracked_delta, (long long)sentinel_checks,
           (long long)item_release_checks, (long long)items_released_bytes,
           (long long)materialized_checks,
           (long long)pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES));
    return status;
}
