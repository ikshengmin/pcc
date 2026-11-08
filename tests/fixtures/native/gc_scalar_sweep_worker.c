/* Controlled dead verdicts on real allocations, consumed by the production CMS
 * loop. This is not an unmodified-workload or full finalizer-lifecycle test. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void PyObject_Free(void *);
extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_gc_deferred_free_close(void);
extern void pcc_gc_scalar_sweep_seal(void);
extern int64_t pcc_gc_scalar_sweep_counter(int64_t);
extern int64_t pcc_gc_scalar_sweep_try_enqueue(void *, void *);
extern int64_t pcc_gc_scalar_sweep_owns(void *);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern void pcc_gc_tracing_finalize_unreachable(void *);
extern void pcc_gc_prepare_object_list_mark(int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_platform_sleep_ns(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void *pcc_gc_object_index_find(void *);
extern void pcc_gc_note_object_freeing(void *);
extern void pcc_gc_free_object_memory(void *);
extern int64_t pcc_gc_pointer_unregister(void *);
extern int64_t pcc_gc_live_bytes;
extern int64_t pcc_gc_tracing_finish_stop_epoch;
extern int32_t py_gc_enabled, py_gc_threshold0;
#define C(n) pcc_gc_scalar_sweep_counter(n)
#define CHECK(x, n) do { if (!(x)) return n; } while (0)
#define MAX 257
static PyObject *objects[MAX];
static void *nodes[MAX];
static int64_t original_rc[MAX];
static PyObject *canary;
static int64_t producer_id, worker_id, admitted, pending_at_seal, reset_ok;
static int64_t live_before_claim, live_after_claim, tail_accounted;
static int64_t concurrent_count, concurrent_status, stop_allocations, node_reused;
static void *node_for(void *obj) {
    void *node;
    pcc_py_gc_minor_graph_lock();
    node = pcc_gc_object_index_find(obj);
    pcc_py_gc_minor_graph_unlock();
    return node;
}
static int64_t field(void *base, int offset) {
    return *(int64_t *)((char *)base + offset);
}
static void *allocate_carrier(void *unused) {
    (void)unused;
    PyObject *prior = 0;
    int64_t serial = 0;
    while (__atomic_load_n(&stop_allocations, __ATOMIC_ACQUIRE) == 0) {
        PyObject *obj = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
        if (!obj) { concurrent_status = 1; break; }
        *(int64_t *)((char *)obj + 16) = ++serial;
        pcc_gc_pin(obj);
        if (*(int64_t *)((char *)obj + 16) != serial || node_for(obj) == 0) {
            concurrent_status = 2; break;
        }
        if (prior && *(int64_t *)((char *)prior + 16) != serial - 1) {
            concurrent_status = 3; break;
        }
        /* Keep this bounded and deliberately live. There is no allocator
         * cleanup posing as GC retirement in the concurrent canary thread. */
        prior = obj;
        __atomic_store_n(&concurrent_count, serial, __ATOMIC_RELEASE);
        if (serial >= 256) break;
        pcc_thread_safepoint();
        pcc_platform_sleep_ns(100000);
    }
    return 0;
}
static int make_public_garbage(void) {
    for (int i = 0; i < 192; i++) {
        PyObject *obj = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
        if (!obj) return 1;
        *(double *)((char *)obj + 16) = 3.25 + i;
        pcc_gc_publish_initialized(obj);
    }
    return 0;
}
static int public_path(int backend) {
    /* Ordinary public collection with its real mark/root/sweep path. No
     * candidate bits, mark epochs, or direct enqueue/drain calls here. */
    py_gc_enabled = 0;
    CHECK(make_public_garbage() == 0, 60);
    (void)pcc_gc_collect(0);
    admitted = C(3);
    pending_at_seal = C(12);
    if (backend == 1 || backend == 2) CHECK(admitted > 0, 61);
    else CHECK(admitted == 0, 62);
    int rounds = 0;
    while ((C(5) != admitted || C(0) != 0) && rounds++ < 2000) {
        pcc_thread_safepoint(); pcc_platform_sleep_ns(1000000);
    }
    CHECK(pcc_gc_cms_stop_worker() == 0, 63);
    worker_id = C(8);
    CHECK(C(4) == admitted && C(5) == admitted && C(6) == admitted, 64);
    CHECK(C(0) == 0 && C(1) == 0 && C(9) == 0, 65);
    if (admitted) CHECK(worker_id > 0 && worker_id != producer_id, 66);
    CHECK(*(int64_t *)((char *)canary + 16) == 987654321, 67);
    return 0;
}
static int run(int mode) {
    producer_id = pcc_current_thread_id();
    int backend = pcc_gc_backend();
    int enabled = backend == 1 || backend == 2;
    CHECK(producer_id > 0 && pcc_threads_enabled() == 1, 10);
    CHECK(pcc_refcount_strategy() == PCC_REFCOUNT_STRATEGY_ATOMIC, 11);
    canary = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
    CHECK(canary != 0, 12);
    *(int64_t *)((char *)canary + 16) = 987654321;
    pcc_gc_pin(canary);
    if (backend != 0) {
        int rounds = 0;
        while (pcc_thread_live_count() < 2 && rounds++ < 2000) {
            pcc_thread_safepoint(); pcc_platform_sleep_ns(1000000);
        }
        CHECK(pcc_thread_live_count() == 2, 13);
    }
    if (mode == 9) return public_path(backend);
    if (mode == 5) CHECK(pcc_gc_cms_stop_worker() == 0, 14);
    int count = mode == 6 ? MAX : 48;
    for (int i = 0; i < count; i++) {
        int tag = i % 3 == 0 ? PY_TYPE_INT : (i % 3 == 1 ? PY_TYPE_FLOAT : PY_TYPE_STR);
        objects[i] = pcc_gc_alloc(64, tag, 0);
        CHECK(objects[i] != 0, 15);
        original_rc[i] = ((PyObjectHeader *)objects[i])->refcount;
    }
    void *carrier = 0;
    if (mode == 8 && enabled) {
        CHECK(pcc_thread_start(&carrier, allocate_carrier, 0) == 0, 16);
        int rounds = 0;
        while (__atomic_load_n(&concurrent_count, __ATOMIC_ACQUIRE) < 1 && rounds++ < 2000) {
            pcc_thread_safepoint(); pcc_platform_sleep_ns(100000);
        }
        CHECK(concurrent_count > 0, 17);
    }
    CHECK(pcc_stop_the_world() == 0, 18);
    if (mode == 2) py_gc_threshold0 = 0;
    else py_gc_enabled = 0;
    CHECK(pcc_gc_deferred_free_begin(0) == enabled, 19);
    for (int i = 0; i < count; i++) {
        nodes[i] = pcc_gc_object_index_find(objects[i]);
        PyObjectHeader *h = (PyObjectHeader *)objects[i];
        h->flags = (h->flags & ~(16384 | 16 | 32)) | 1024 | 8;
        if (enabled) CHECK(nodes[i] != 0, 20);
    }
    live_before_claim = pcc_gc_live_bytes;
    if (mode == 7 && enabled) {
        /* These objects have valid scalar storage; exclusions are restored
         * before production sweep. No invalid typed destructor is called. */
        int64_t bits[8] = {1, 2, 16, 32, 64, 4096, 16384, 65536};
        for (int i = 0; i < 8; i++) {
            PyObjectHeader *h = (PyObjectHeader *)objects[i];
            int old = h->flags;
            h->flags |= bits[i];
            CHECK(pcc_gc_scalar_sweep_try_enqueue(objects[i], nodes[i]) == 0, 21);
            h->flags = old;
        }
        int old_tag = ((PyObjectHeader *)objects[8])->type_tag;
        ((PyObjectHeader *)objects[8])->type_tag = 65536;
        CHECK(pcc_gc_scalar_sweep_try_enqueue(objects[8], nodes[8]) == 0, 22);
        ((PyObjectHeader *)objects[8])->type_tag = old_tag;
    }
    if (mode == 4 && enabled) {
        /* Commit first, reset while BUILDING, then run the actual new-mark
         * preparation. The independent batch token must keep its dead set. */
        for (int i = 0; i < count; i++) {
            CHECK(pcc_gc_scalar_sweep_try_enqueue(objects[i], nodes[i]) == 1, 23);
        }
        CHECK(pcc_gc_set_backend(backend) == 0, 24);
        reset_ok++;
        pcc_gc_prepare_object_list_mark(1);
    } else if (backend != 0) {
        pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
        CHECK(pcc_gc_tracing_sweep_unreachable(9223372036854775807LL) == count, 25);
    }
    admitted = C(3);
    int expected = enabled && mode != 5 ? (count < 256 ? count : 256) : 0;
    CHECK(admitted == expected, 26);
    live_after_claim = pcc_gc_live_bytes;
    if (enabled && expected) {
        CHECK(C(4) == 0 && C(5) == 0, 27);
        for (int i = 0; i < count; i++) {
            void *node = pcc_gc_object_index_find(objects[i]);
            if (node) {
                CHECK(node == nodes[i] && field(node, 32) == -C(11), 28);
                CHECK(((PyObjectHeader *)objects[i])->refcount == original_rc[i], 29);
                CHECK(pcc_gc_scalar_sweep_owns(objects[i]) == 1, 30);
                /* Direct cleanup cannot take the pending lifetime away. */
                pcc_gc_note_object_freeing(objects[i]);
                pcc_gc_free_object_memory(objects[i]);
                CHECK(pcc_gc_pointer_unregister(objects[i]) == -1, 31);
                PyObject_Free(objects[i]);
                CHECK(pcc_gc_object_index_find(objects[i]) == nodes[i], 32);
                tail_accounted += field(node, 8);
            }
        }
        CHECK(live_before_claim - live_after_claim >= tail_accounted, 33);
    }
    if (enabled) pcc_gc_scalar_sweep_seal();
    pending_at_seal = C(1);
    CHECK(pending_at_seal == expected && C(4) == 0, 34);
    if (mode == 10 && enabled) {
        /* A READY reservation cannot accept another candidate even while
         * the worker is still parked. It keeps the ordinary synchronous
         * logical-retirement path and cannot overwrite the sealed task. */
        PyObject *extra = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
        CHECK(extra != 0, 54);
        ((PyObjectHeader *)extra)->flags = (((PyObjectHeader *)extra)->flags & ~(16384 | 16 | 32)) | 1024 | 8;
        void *extra_node = pcc_gc_object_index_find(extra);
        CHECK(pcc_gc_scalar_sweep_try_enqueue(extra, extra_node) == 0, 55);
        CHECK(C(1) == expected && C(3) == expected, 56);
        pcc_gc_tracing_finalize_unreachable(extra);
        CHECK(pcc_gc_object_index_find(extra) == 0, 57);
    }
    if (mode == 4 && enabled) {
        CHECK(pcc_gc_set_backend(backend) == 0, 35);
        reset_ok++;
        CHECK(C(1) == count && pcc_gc_live_bytes == live_after_claim, 36);
        for (int i = 0; i < count; i++) CHECK(pcc_gc_object_index_find(objects[i]) == nodes[i], 37);
    }
    CHECK(pcc_resume_world() == 0, 38);
    if (enabled) pcc_gc_deferred_free_close();
    if (mode == 3) {
        CHECK(pcc_gc_cms_stop_worker() == 0, 39);
    } else if (expected) {
        int rounds = 0;
        while ((C(5) != expected || C(0) != 0) && rounds++ < 2000) {
            pcc_thread_safepoint(); pcc_platform_sleep_ns(1000000);
        }
    }
    if (carrier) {
        __atomic_store_n(&stop_allocations, 1, __ATOMIC_RELEASE);
        CHECK(pcc_thread_join(carrier, 0) == 0, 40);
        CHECK(concurrent_status == 0 && concurrent_count > 0, 41);
    }
    CHECK(C(3) == C(4) && C(4) == C(5) && C(5) == expected, 42);
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(9) == 0, 43);
    CHECK(C(6) + C(7) == expected, 44);
    if (expected && mode != 8) {
        /* Independent logical-retirement witness: no old key remains.
         * Then one fresh allocation must map to its own reusable node,
         * not to an old object's index key (node-pool ABA control). */
        for (int i = 0; i < count; i++) CHECK(node_for(objects[i]) == 0, 50);
        PyObject *replacement = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
        CHECK(replacement != 0, 51);
        void *new_node = node_for(replacement);
        CHECK(new_node != 0 && *(void **)new_node == replacement, 52);
        for (int i = 0; i < count; i++) if (new_node == nodes[i]) node_reused++;
        CHECK(node_reused > 0, 53);
    }
    worker_id = C(8);
    if (expected && mode != 3) CHECK(C(6) == expected && worker_id > 0 && worker_id != producer_id, 45);
    CHECK(*(int64_t *)((char *)canary + 16) == 987654321, 46);
    if (backend != 0) CHECK(node_for(canary) != 0, 47);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 48);
    if (mode != 3 && mode != 5) CHECK(pcc_gc_cms_stop_worker() == 0, 49);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 99;
    int mode = argv[1][0] - '0';
    if (argv[1][1]) mode = mode * 10 + argv[1][1] - '0';
    if (mode < 1 || mode > 10) return 98;
    int status = run(mode);
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    printf("scalar-sweep backend=%d mode=%d producer=%lld worker=%lld admitted=%lld "
           "sealed_pending=%lld retired=%lld released=%lld worker_retired=%lld local=%lld "
           "reset=%lld canary_allocations=%lld node_reused=%lld pending=%lld quarantine=%lld status=%d\n",
           pcc_gc_backend(), mode, (long long)producer_id, (long long)worker_id,
           (long long)admitted, (long long)pending_at_seal, (long long)C(4),
           (long long)C(5), (long long)C(6), (long long)C(7), (long long)reset_ok,
           (long long)concurrent_count, (long long)node_reused, (long long)C(1), (long long)C(9), status);
    return status;
}
