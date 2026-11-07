/* Actual CMS-thread consumption of retired allocator blocks. Terminal verdicts
 * are controlled; logical collection/finalizer semantics have separate tests. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_gc_deferred_free_close(void);
extern int64_t pcc_gc_deferred_free_counter(int64_t);
extern void pcc_gc_free_object_memory(PyObject *);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_platform_sleep_ns(int64_t);
extern int64_t malloc_usable_size(void *);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern void *pcc_allocator_alloc_object(int64_t);
extern int32_t py_gc_enabled, py_gc_threshold0;
#define C(n) pcc_gc_deferred_free_counter(n)
#define CHECK(x, n) do { if (!(x)) return n; } while (0)
#define COUNT 128
static PyObject *objects[COUNT];
static void *replacements[COUNT * 2];
static int64_t producer_id, worker_id, reused, before_join, after_join;
static int batch(int mode) {
    producer_id = pcc_current_thread_id();
    CHECK(producer_id > 0 && pcc_threads_enabled() == 1, 10);
    CHECK(pcc_refcount_strategy() == PCC_REFCOUNT_STRATEGY_ATOMIC, 11);
    int backend = pcc_gc_backend();
    if (backend != 0) {
        int rounds = 0;
        while (pcc_thread_live_count() < 2 && rounds++ < 2000) {
            pcc_thread_safepoint();
            pcc_platform_sleep_ns(1000000);
        }
        CHECK(pcc_thread_live_count() == 2, 12);
    }
    CHECK(pcc_stop_the_world() == 0, 13);
    /* Pause new tracing work, not reclamation of already-dead raw blocks. */
    if (mode == 1) py_gc_enabled = 0;
    if (mode == 2) py_gc_threshold0 = 0;
    int enabled = backend == 1 || backend == 2;
    CHECK(pcc_gc_deferred_free_begin(0) == enabled, 14);
    if (!enabled) {
        CHECK(pcc_resume_world() == 0, 15);
        CHECK(pcc_gc_deferred_free_close() == 0, 16);
        CHECK(pcc_gc_cms_stop_worker() == 0, 17);
        for (int i = 0; i < 14; i++) CHECK(C(i) == 0, 18);
        return 0;
    }
    int64_t bytes = 0;
    for (int i = 0; i < COUNT; i++) {
        objects[i] = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
        CHECK(objects[i] != 0, 20);
        PyObjectHeader *header = (PyObjectHeader *)objects[i];
        header->flags = (header->flags & ~16384) | 1024 | 524288;
        bytes += malloc_usable_size(objects[i]);
    }
    for (int i = 0; i < COUNT; i++) pcc_gc_free_object_memory(objects[i]);
    CHECK(C(0) == COUNT && C(1) == bytes && C(4) == 0, 21);
    CHECK(C(8) == 0 && C(9) == 0 && C(11) == 0, 22);
    CHECK(pcc_resume_world() == 0, 23);
    CHECK(pcc_gc_deferred_free_close() == COUNT, 24);
    CHECK(C(0) == 0 && C(1) == 0 && C(8) == COUNT, 25);
    if (mode != 3) {
        /* No manual drain call: only the production CMS loop can progress. */
        int rounds = 0;
        while ((C(9) != COUNT || C(11) != 0) && rounds++ < 2000) {
            pcc_thread_safepoint();
            pcc_platform_sleep_ns(1000000);
        }
        CHECK(C(9) == COUNT && C(11) == 0, 26);
    }
    before_join = pcc_allocator_live_usable_bytes();
    /* In mode3 this immediately races the worker's ordinary loop/final drain.
     * A successful join is the terminal oracle, not an in-flight snapshot. */
    CHECK(pcc_gc_cms_stop_worker() == 0, 27);
    after_join = pcc_allocator_live_usable_bytes();
    CHECK(C(3) == C(4) && C(4) == COUNT, 28);
    CHECK(C(8) == C(9) && C(9) == COUNT && C(10) == 0, 29);
    CHECK(C(7) == 0 && C(11) == 0 && C(12) == 0, 30);
    worker_id = C(13);
    CHECK(worker_id > 0 && worker_id != producer_id, 31);
    CHECK(pcc_thread_live_count() == 1, 32);
    /* Physical witness independent of counters: successful worker exit must
     * drain its allocator magazine, making previously retired addresses
     * available to a DIFFERENT thread. Never dereference a freed old block. */
    int64_t live = pcc_allocator_live_usable_bytes();
    for (int i = 0; i < COUNT * 2; i++) {
        replacements[i] = pcc_allocator_alloc_object(64);
        CHECK(replacements[i] != 0, 33);
        for (int j = 0; j < i; j++) CHECK(replacements[i] != replacements[j], 34);
        for (int j = 0; j < COUNT; j++) {
            if (replacements[i] == (void *)objects[j]) reused++;
        }
    }
    CHECK(reused > 0, 35);
    CHECK(pcc_allocator_live_usable_bytes() == live + COUNT * 2 * 64, 36);
    for (int i = 0; i < COUNT * 2; i++) free(replacements[i]);
    CHECK(pcc_allocator_live_usable_bytes() == live, 37);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 38);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 99;
    int mode = argv[1][0] - '0';
    if (mode < 1 || mode > 3) return 98;
    int status = batch(mode);
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    printf("cms-terminal backend=%d mode=%d producer=%lld worker=%lld "
           "queued=%lld drained=%lld handed=%lld worker_drained=%lld local=%lld "
           "pending=%lld pending_bytes=%lld reused=%lld "
           "before_join=%lld after_join=%lld status=%d\n",
           pcc_gc_backend(), mode, (long long)producer_id, (long long)worker_id,
           (long long)C(3), (long long)C(4), (long long)C(8), (long long)C(9),
           (long long)C(10), (long long)C(11), (long long)C(12),
           (long long)reused, (long long)before_join, (long long)after_join, status);
    return status;
}
