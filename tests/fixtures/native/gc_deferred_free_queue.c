/* Real allocator, provenance and TLS boundary. Synthetic terminal verdicts
 * isolate physical release; high-level collector semantics live separately. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_gc_deferred_free_close(void);
extern int64_t pcc_gc_deferred_free_try_enqueue(PyObject *, int64_t);
extern int64_t pcc_gc_deferred_free_counter(int64_t);
extern int32_t pcc_capi_cext_tag_for(void *);
extern void pcc_gc_free_object_memory(PyObject *);
extern void pcc_gc_note_object_freeing(PyObject *);
extern int64_t malloc_usable_size(void *);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_cms_stop_worker(void);
extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_gc_managed_pointer_index_contains(void *);
extern void *pcc_gc_object_index_find(void *);
static PyObject *objects[4097];
/* Registry-only synthetic C-extension type, including tp_version_tag. */
static uint64_t extension_type[64];
static int64_t ready, done, observed, worker_error;
static int64_t resume_request, resume_ack, queued_live_bytes;
#define C(n) pcc_gc_deferred_free_counter(n)
#define GRANULE_STATE_OFFSET (-48) /* freestanding_allocator lifecycle word */
#define CHECK(x, n) do { if (!(x)) return n; } while (0)
static void *carrier(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    int witnessed_resume = 0;
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&done, __ATOMIC_ACQUIRE)) {
        if (C(0) || C(1)) worker_error = 1;
        /* A registered native carrier must park for the collector's stop. */
        pcc_thread_safepoint();
        if (!witnessed_resume && __atomic_load_n(&resume_request, __ATOMIC_ACQUIRE)) {
            /* Owner published only after resume, while retaining its queue.
             * No pointer to retired payload is exposed to this carrier. */
            if (pcc_thread_stop_requested_acquire() || pcc_thread_owns_stopped_world())
                worker_error = 2;
            if (C(0) || C(1) || C(7)) worker_error = 3;
            if (pcc_allocator_live_usable_bytes() != queued_live_bytes) worker_error = 4;
            witnessed_resume = 1;
            __atomic_store_n(&resume_ack, 1, __ATOMIC_RELEASE);
        }
        /* Best-effort observation of this single real drain, not a pass gate. */
        if (C(7) > 0) __atomic_fetch_add(&observed, 1, __ATOMIC_RELAXED);
    }
    return 0;
}
static int batch(int size, int count, int cap_kind) {
    CHECK(pcc_stop_the_world() == 0, 20);
    CHECK(pcc_gc_deferred_free_begin(0) == 1, 21);
    int64_t enqueued = C(3), drained = C(4), attempts = C(2), fallback = C(5);
    int64_t total = 0, expected = 0;
    for (int i = 0; i < count; i++) {
        objects[i] = pcc_gc_alloc(size, PY_TYPE_FLOAT, 0);
        CHECK(objects[i] != 0, 22);
        /* Model a completed sweep verdict, not a fresh allocation. */
        PyObjectHeader *header = (PyObjectHeader *)objects[i];
        header->flags = (header->flags & ~16384) | 1024 | 524288;
    }
    for (int i = 0; i < count; i++) {
        PyObject *obj = objects[i];
        int64_t bytes = malloc_usable_size(obj);
        CHECK(bytes >= size, 23);
        int fits = expected < 4096 && total + bytes <= 1048576;
        uint64_t header_word = ((uint64_t *)obj)[1];
        int64_t before_note = pcc_allocator_live_usable_bytes();
        pcc_gc_note_object_freeing(obj);
        int64_t live = pcc_allocator_live_usable_bytes();
        pcc_gc_free_object_memory(obj);
        int64_t after_free = pcc_allocator_live_usable_bytes();
        if (fits) {
            expected++; total += bytes;
            CHECK(((uint64_t *)obj)[1] == header_word, 38);
        }
        CHECK(C(0) == expected && C(1) == total, 24);
        CHECK(pcc_gc_pointer_is_managed(obj) == 0, 25);
        CHECK(pcc_gc_object_is_known_no_lock(obj) == 0, 26);
        int64_t after_queries = pcc_allocator_live_usable_bytes();
        if (after_free != live - (fits ? 0 : bytes)) {
            /* The immediate snapshot isolates terminal physical release.
             * Later negative provenance queries can lazily allocate their
             * local-root registry; retain those bytes only as diagnostics. */
            int64_t pending_count = C(0), pending_bytes = C(1);
            printf("queue-live-mismatch index=%d fits=%d usable=%lld before_note=%lld "
                   "after_note=%lld after_free=%lld after_queries=%lld "
                   "pending_count=%lld pending_bytes=%lld header_before=%llu\n",
                   i, fits, (long long)bytes, (long long)before_note,
                   (long long)live, (long long)after_free, (long long)after_queries,
                   (long long)pending_count, (long long)pending_bytes,
                   (unsigned long long)header_word);
            return 27;
        }
        int64_t accepted = C(3);
        pcc_gc_free_object_memory(obj);
        CHECK(C(3) == accepted && C(0) == expected, 28);
    }
    CHECK(C(2) == attempts && C(3) == enqueued && C(4) == drained, 31);
    if (cap_kind == 1) CHECK(expected == 4096, 32);
    if (cap_kind == 2) CHECK(expected < count && expected < 4096, 33);
    int64_t before_resume = pcc_allocator_live_usable_bytes();
    CHECK(pcc_resume_world() == 0, 34);
    int64_t after_resume = pcc_allocator_live_usable_bytes();
    if (cap_kind == 0) {
        CHECK(pcc_thread_owns_stopped_world() == 0, 60);
        CHECK(C(0) == expected && C(1) == total && C(7) == 0, 61);
        queued_live_bytes = after_resume;
        __atomic_store_n(&resume_request, 1, __ATOMIC_RELEASE);
        while (!__atomic_load_n(&resume_ack, __ATOMIC_ACQUIRE))
            pcc_thread_safepoint();
        /* Acknowledged progress precedes free, independent of OS timeslice.
         * Pending bytes and live allocator bytes have not changed meanwhile. */
        CHECK(C(0) == expected && C(1) == total && C(4) == drained, 62);
        CHECK(pcc_allocator_live_usable_bytes() == after_resume, 63);
        CHECK(pcc_thread_owns_stopped_world() == 0, 64);
    }
    int64_t before_close = pcc_allocator_live_usable_bytes();
    int64_t closed = pcc_gc_deferred_free_close();
    int64_t after_close = pcc_allocator_live_usable_bytes();
    CHECK(closed == expected, 39);
    CHECK(C(0) == 0 && C(1) == 0 && C(4) - drained == expected, 35);
    CHECK(C(2) - attempts == count, 29);
    CHECK(C(3) - enqueued == expected && C(5) - fallback == count - expected, 30);
    CHECK(C(6) >= total && C(6) <= 1048576, 37);
    if (after_close != before_close - total) {
        printf("queue-drain-mismatch before_resume=%lld after_resume=%lld "
               "before_close=%lld after_close=%lld pending_bytes=%lld drained=%lld\n",
               (long long)before_resume, (long long)after_resume,
               (long long)before_close, (long long)after_close,
               (long long)total, (long long)closed);
        return 36;
    }
    return 0;
}
static int exclusions(void) {
    CHECK(pcc_stop_the_world() == 0, 50);
    PyObject *obj = pcc_gc_alloc(32, PY_TYPE_FLOAT, 0);
    CHECK(obj != 0 && pcc_gc_deferred_free_begin(0) == 1, 51);
    int64_t attempts = C(2);
    int flags[7] = {1024, 524288, 1024 | 524288 | 1,
        1024 | 524288 | 64, 1024 | 524288 | 4096,
        1024 | 524288 | 16384, 1024 | 524288 | 65536};
    for (int i = 0; i < 7; i++) {
        CHECK(pcc_gc_deferred_free_try_enqueue(obj, flags[i]) == 0, 52);
        CHECK(C(0) == 0 && C(1) == 0, 53);
    }
    int32_t tag = pcc_capi_cext_tag_for(extension_type);
    CHECK(tag != 0, 57);
    ((PyObjectHeader *)obj)->type_tag = tag;
    CHECK(pcc_gc_deferred_free_try_enqueue(obj, 1024 | 524288) == 0, 58);
    CHECK(C(0) == 0 && C(1) == 0, 59);
    ((PyObjectHeader *)obj)->type_tag = PY_TYPE_FLOAT;
    /* The fresh negative above is separate from this admitted terminal free. */
    PyObjectHeader *header = (PyObjectHeader *)obj;
    header->flags = (header->flags & ~16384) | 1024 | 524288;
    pcc_gc_free_object_memory(obj);
    CHECK(C(0) == 1, 54);
    CHECK(pcc_resume_world() == 0, 55);
    CHECK(pcc_gc_deferred_free_close() == 1 && C(2) == attempts + 1, 56);
    return 0;
}
static int terminal_provenance(void) {
    CHECK(pcc_stop_the_world() == 0, 70);
    CHECK(pcc_gc_deferred_free_begin(0) == 1, 71);
    /* Real LIVE, RESERVED, already-FREE and large exact-only allocations.
     * FREE here means retired provenance, not already returned to malloc. */
    for (int origin = 0; origin < 4; origin++) {
        PyObject *obj = origin == 0 || origin == 3
            ? pcc_gc_alloc(origin == 3 ? 32768 : 64, PY_TYPE_FLOAT, 0)
            : (PyObject *)pcc_allocator_alloc_object(64);
        CHECK(obj != 0, 72);
        PyObjectHeader *header = (PyObjectHeader *)obj;
        if (origin == 1 || origin == 2) {
            header->refcount = 1;
            header->type_tag = PY_TYPE_FLOAT;
            header->flags = 1024 | 524288;
        } else header->flags = (header->flags & ~16384) | 1024 | 524288;
        if (origin == 2) CHECK(pcc_gc_granule_object_retire(obj) == 1, 73);
        pcc_gc_note_object_freeing(obj);
        CHECK(pcc_gc_granule_is_object_start(obj) == (origin == 0 ? 1 : -1), 74);
        CHECK(pcc_gc_managed_pointer_index_contains(obj) == (origin != 0), 75);
        int64_t queued = C(0), live = pcc_allocator_live_usable_bytes();
        pcc_gc_free_object_memory(obj);
        CHECK(C(0) == queued + 1 && pcc_allocator_live_usable_bytes() == live, 76);
        CHECK(pcc_gc_granule_is_object_start(obj) == -1 &&
              pcc_gc_managed_pointer_index_contains(obj) == 0, 77);
        CHECK(pcc_gc_pointer_is_managed(obj) == 0, 78);
        pcc_gc_free_object_memory(obj);
        CHECK(C(0) == queued + 1, 79);
    }
    /* A corrupt lifecycle is not an allocator free or exact-key removal.
     * Keep this deliberate quarantine alive until process exit. */
    PyObject *bad = (PyObject *)pcc_allocator_alloc_object(64);
    CHECK(bad != 0, 80);
    ((PyObjectHeader *)bad)->refcount = 1;
    ((PyObjectHeader *)bad)->type_tag = PY_TYPE_FLOAT;
    ((PyObjectHeader *)bad)->flags = 1024 | 524288;
    pcc_gc_note_object_freeing(bad);
    CHECK(pcc_gc_managed_pointer_index_contains(bad) == 1, 81);
    *(int64_t *)((char *)bad + GRANULE_STATE_OFFSET) = 123;
    int64_t live = pcc_allocator_live_usable_bytes();
    pcc_gc_free_object_memory(bad);
    CHECK(C(0) == 4 && pcc_allocator_live_usable_bytes() == live &&
          pcc_gc_managed_pointer_index_contains(bad) == 1, 82);
    CHECK(pcc_gc_pointer_unregister(bad) == -1, 83);
    /* The provenance-probe configuration takes a real indexed foreign lease
     * even for nonmoving GC1/2. The terminal entry must preserve that owner. */
    PyObject *leased = pcc_gc_alloc(32768, PY_TYPE_FLOAT, 0);
    CHECK(leased != 0, 86);
    pcc_gc_publish_initialized(leased);
    int32_t map = 1;
    pcc_gc_frame_enter(&map, &leased);
    int64_t token = pcc_gc_foreign_lease_acquire(&leased);
    CHECK(token == 1, 87);
    void *node = pcc_gc_object_index_find(leased);
    CHECK(node && *(int64_t *)((char *)node + 80) == 1, 88);
    int flags = ((PyObjectHeader *)leased)->flags;
    live = pcc_allocator_live_usable_bytes();
    pcc_gc_note_object_freeing(leased);
    pcc_gc_free_object_memory(leased);
    CHECK(C(0) == 4 && pcc_allocator_live_usable_bytes() == live &&
          pcc_gc_object_index_find(leased) == node &&
          pcc_gc_granule_is_object_start(leased) == -1 &&
          ((PyObjectHeader *)leased)->flags == flags, 89);
    CHECK(pcc_gc_foreign_lease_release(&leased, token) == 0, 90);
    ((PyObjectHeader *)leased)->flags = (flags & ~16384) | 1024 | 524288;
    pcc_gc_note_object_freeing(leased);
    pcc_gc_free_object_memory(leased);
    leased = 0;
    pcc_gc_frame_leave(&leased);
    CHECK(C(0) == 5, 91);
    CHECK(pcc_resume_world() == 0, 84);
    CHECK(pcc_gc_deferred_free_close() == 5, 85);
    return 0;
}
static int run(int mode) {
    CHECK(pcc_current_thread_id() > 0 && pcc_threads_enabled() == 1, 10);
    CHECK(pcc_refcount_strategy() == PCC_REFCOUNT_STRATEGY_ATOMIC, 11);
    /* This is the allocator-only control: join the CMS worker before any
     * stop so process-wide byte totals cannot include its late registration.
     * Public collector-owner controls keep their ordinary CMS worker. */
    (void)pcc_gc_backend();
    CHECK(pcc_gc_cms_stop_worker() == 0, 65);
    CHECK(pcc_gc_deferred_free_begin(0) == 0, 12);
    CHECK(pcc_stop_the_world() == 0, 13);
    CHECK(pcc_gc_deferred_free_begin(1) == 0, 14);
    pcc_py_gc_minor_graph_lock();
    int64_t graph = pcc_gc_deferred_free_begin(0);
    pcc_py_gc_minor_graph_unlock();
    CHECK(graph == 0, 15);
    pcc_thread_no_park_enter();
    int64_t nopark = pcc_gc_deferred_free_begin(0);
    pcc_thread_no_park_exit();
    CHECK(nopark == 0, 16);
    int backend = pcc_gc_backend();
    int enabled = backend == 1 || backend == 2;
    CHECK(pcc_gc_deferred_free_begin(0) == enabled, 17);
    CHECK(pcc_gc_deferred_free_begin(0) == 0, 18);
    CHECK(pcc_resume_world() == 0, 19);
    (void)pcc_gc_deferred_free_close();
    if (!enabled) return C(0) || C(1) || C(2) || C(3) || C(4) ? 40 : 0;
    if (mode == 5) return terminal_provenance();
    if (mode == 4) return exclusions();
    if (mode == 1) return batch(32, 4097, 1);
    if (mode == 2) return batch(32768, 40, 2);
    void *thread = 0, *result = 0;
    CHECK(pcc_thread_start(&thread, carrier, 0) == 0, 41);
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    /* One drain only: require resumed-carrier progress before releasing the
     * queued allocations. Simultaneous physical frees remain diagnostic. */
    int status = batch(32, 4096, 0);
    /* A failed batch may still own STW. Never join a parked carrier. */
    if (pcc_thread_owns_stopped_world()) {
        pcc_resume_world();
        (void)pcc_gc_deferred_free_close();
    }
    __atomic_store_n(&done, 1, __ATOMIC_RELEASE);
    CHECK(pcc_thread_join(thread, &result) == 0 && result == 0, 42);
    CHECK(status == 0, status);
    CHECK(worker_error == 0 && __atomic_load_n(&resume_ack, __ATOMIC_ACQUIRE) == 1, 43);
    printf("carrier-resume acknowledged=1 overlap_observations=%lld\n",
           (long long)__atomic_load_n(&observed, __ATOMIC_RELAXED));
    return 0;
}
int main(int argc, char **argv) {
    int mode = argc == 2 ? argv[1][0] - '0' : 0;
    if (mode < 1 || mode > 5) return 99;
    int status = run(mode);
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    pcc_gc_cms_stop_worker();
    printf("deferred-queue backend=%d mode=%d status=%d\n", pcc_gc_backend(), mode, status);
    return status;
}
