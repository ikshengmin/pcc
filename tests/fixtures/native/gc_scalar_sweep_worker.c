/* Controlled dead verdicts on real allocations, consumed by the production CMS
 * loop. Mode 9 instead uses ordinary public marking. Neither is a full
 * finalizer-lifecycle or unmodified-workload qualification. */
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
extern void *pcc_gc_object_list_head(void);
extern void *pcc_gc_scalar_sweep_head;
extern int64_t malloc_usable_size(void *);
extern void pcc_gc_note_object_freeing(void *);
extern void pcc_gc_free_object_memory(void *);
extern int64_t pcc_gc_pointer_unregister(void *);
extern int64_t pcc_gc_live_bytes;
extern int64_t pcc_gc_tracing_finish_stop_epoch;
extern int32_t pcc_gc_object_node_free_count;
extern int32_t py_gc_enabled, py_gc_threshold0;
#define C(n) pcc_gc_scalar_sweep_counter(n)
#define CHECK(x, n) do { if (!(x)) return n; } while (0)
#define CHUNK_OBJECTS 256
#define CHUNK_BYTES 65536
#define MANY 777
#define NODE_POOL 8192
#define MAX 8705
#define EPOCHS 3
static PyObject *objects[MAX];
static void *nodes[MAX];
static int64_t original_rc[MAX];
static PyObject *canary;
static PyObject *epoch_canaries[EPOCHS];
static PyObject *quarantined_objects[CHUNK_OBJECTS];
static void *quarantined_nodes[CHUNK_OBJECTS];
static int64_t quarantined_rc[CHUNK_OBJECTS];
static int64_t producer_id, worker_id, admitted, pending_at_seal, reset_ok;
static int64_t live_before_claim, live_after_claim, tail_accounted;
static int64_t concurrent_count, concurrent_status, stop_allocations, node_reused;
static int64_t object_usable, claimed_bytes, expected_chunks, completed_epochs;
static int64_t observer_checks, list_checks, overflow_expected, pool_at_seal;
static int64_t quarantined_count, quarantine_checks, stop_checks;
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
static void *pointer_field(void *base, int offset) {
    return *(void **)((char *)base + offset);
}
static int is_quarantined(void *obj) {
    for (int i = 0; i < quarantined_count; i++) {
        if (quarantined_objects[i] == obj) return 1;
    }
    return 0;
}
static int check_active_list(void) {
    /* Only live nodes may remain on the active doubly linked list. Negative
     * owners must remain indexed exclusively through the detached chain. */
    int valid = 1, seen = 0, found_canary = 0, found_epoch = 0;
    void *prev = 0;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_list_head();
    while (node) {
        if (++seen > MAX + 1024 || field(node, 32) != 0 ||
            pointer_field(node, 40) != prev ||
            pcc_gc_object_index_find(pointer_field(node, 0)) != node) {
            valid = 0;
            break;
        }
        if (pointer_field(node, 0) == canary) found_canary++;
        for (int i = 0; i < EPOCHS; i++) {
            if (epoch_canaries[i] && pointer_field(node, 0) == epoch_canaries[i]) found_epoch++;
        }
        prev = node;
        node = pointer_field(node, 16);
    }
    pcc_py_gc_minor_graph_unlock();
    int wanted_epoch = 0;
    for (int i = 0; i < EPOCHS; i++) {
        if (epoch_canaries[i]) {
            wanted_epoch++;
            if (*(int64_t *)((char *)epoch_canaries[i] + 16) != 24680 + i) valid = 0;
        }
    }
    if (found_canary != 1 || found_epoch != wanted_epoch) valid = 0;
    if (valid) list_checks++;
    return valid;
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
    for (int i = 0; i < MANY; i++) {
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
    if (backend == 1 || backend == 2) CHECK(admitted > 2 * CHUNK_OBJECTS, 61);
    else CHECK(admitted == 0, 62);
    int rounds = 0;
    while ((C(5) != admitted || C(0) != 0) && rounds++ < 2000) {
        pcc_thread_safepoint(); pcc_platform_sleep_ns(1000000);
    }
    CHECK(pcc_gc_cms_stop_worker() == 0, 63);
    worker_id = C(8);
    CHECK(C(4) == admitted && C(5) == admitted && C(6) == admitted, 64);
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(9) == 0, 65);
    if (admitted) CHECK(worker_id > 0 && worker_id != producer_id, 66);
    CHECK(*(int64_t *)((char *)canary + 16) == 987654321, 67);
    if (admitted) {
        CHECK(C(13) >= 3 && C(14) > 0 && C(14) <= CHUNK_OBJECTS, 68);
        CHECK(C(15) > 0 && C(15) <= CHUNK_BYTES && C(10) == 0, 69);
    }
    if (backend != 0) CHECK(check_active_list(), 70);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 71);
    completed_epochs = 1;
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
    int count = mode == 14 ? MAX : ((mode == 6 || mode >= 11) ? MANY : 48);
    int size = mode == 11 ? 1024 : 64;
    int epochs = mode == 13 ? EPOCHS : 1;
    int64_t expected_total = 0;
    int64_t previous_token = C(11);
    for (int epoch = 0; epoch < epochs; epoch++) {
        for (int i = 0; i < count; i++) {
            int tag = i % 3 == 0 ? PY_TYPE_INT : (i % 3 == 1 ? PY_TYPE_FLOAT : PY_TYPE_STR);
            objects[i] = pcc_gc_alloc(size, tag, 0);
            CHECK(objects[i] != 0, 15);
            original_rc[i] = ((PyObjectHeader *)objects[i])->refcount;
            if (enabled) {
                int64_t usable = malloc_usable_size(objects[i]);
                if (i == 0) object_usable = usable;
                CHECK(usable == object_usable && usable >= size && usable <= CHUNK_BYTES, 72);
            }
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
        if (enabled && mode != 5) {
            CHECK(C(11) > previous_token, 73);
            previous_token = C(11);
        }
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
        int expected = enabled && mode != 5 ? count : 0;
        CHECK(admitted == expected_total + expected, 26);
        live_after_claim = pcc_gc_live_bytes;
        if (enabled && expected) {
            CHECK(C(4) == expected_total && C(5) == expected_total, 27);
            tail_accounted = 0;
            for (int i = 0; i < count; i++) {
                void *node = pcc_gc_object_index_find(objects[i]);
                CHECK(node == nodes[i] && field(node, 32) == -C(11), 28);
                CHECK(((PyObjectHeader *)objects[i])->refcount == original_rc[i], 29);
                CHECK((((PyObjectHeader *)objects[i])->flags & (1024 | 8 | 4 | 524288)) ==
                      (1024 | 8 | 4 | 524288), 74);
                CHECK(pcc_gc_scalar_sweep_owns(objects[i]) == 1, 30);
                /* Direct cleanup cannot take the pending lifetime away. */
                pcc_gc_note_object_freeing(objects[i]);
                pcc_gc_free_object_memory(objects[i]);
                CHECK(pcc_gc_pointer_unregister(objects[i]) == -1, 31);
                PyObject_Free(objects[i]);
                CHECK(pcc_gc_object_index_find(objects[i]) == nodes[i], 32);
                CHECK(((PyObjectHeader *)objects[i])->refcount == original_rc[i], 75);
                tail_accounted += field(node, 8);
                observer_checks++;
            }
            CHECK(live_before_claim - live_after_claim >= tail_accounted, 33);
            CHECK(C(2) == count * object_usable, 76);
            CHECK(check_active_list(), 77);
        }
        if (enabled) pcc_gc_scalar_sweep_seal();
        pending_at_seal += C(1);
        CHECK(C(1) == expected && C(4) == expected_total, 34);
        CHECK(C(12) == expected_total + expected, 78);
        if (expected) {
            int64_t per_chunk = CHUNK_BYTES / object_usable;
            if (per_chunk > CHUNK_OBJECTS) per_chunk = CHUNK_OBJECTS;
            expected_chunks += (count + per_chunk - 1) / per_chunk;
            claimed_bytes += C(2);
            if (mode == 14) {
                pool_at_seal = pcc_gc_object_node_free_count;
                CHECK(pool_at_seal == 0, 79);
                overflow_expected = count - NODE_POOL;
            }
        }
        if (mode == 10 && enabled) {
            /* A READY reservation cannot accept another candidate even while
             * the worker is still parked. It keeps the ordinary synchronous
             * logical-retirement path and cannot overwrite the sealed task. */
            PyObject *extra = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
            CHECK(extra != 0, 54);
            ((PyObjectHeader *)extra)->flags = (((PyObjectHeader *)extra)->flags & ~(16384 | 16 | 32)) | 1024 | 8;
            void *extra_node = pcc_gc_object_index_find(extra);
            CHECK(pcc_gc_scalar_sweep_try_enqueue(extra, extra_node) == 0, 55);
            CHECK(C(1) == expected && C(3) == expected_total + expected, 56);
            pcc_gc_tracing_finalize_unreachable(extra);
            CHECK(pcc_gc_object_index_find(extra) == 0, 57);
        }
        if (mode == 4 && enabled) {
            CHECK(pcc_gc_set_backend(backend) == 0, 35);
            reset_ok++;
            CHECK(C(1) == count && pcc_gc_live_bytes == live_after_claim, 36);
            for (int i = 0; i < count; i++) CHECK(pcc_gc_object_index_find(objects[i]) == nodes[i], 37);
            CHECK(check_active_list(), 80);
        }
        if (mode == 13 && enabled) {
            /* A live allocation joins the active list while every pending node
             * is still detached. Same-GC reset must not rebuild that dead chain
             * into the live list or debit its bytes a second time. */
            epoch_canaries[epoch] = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
            CHECK(epoch_canaries[epoch] != 0, 81);
            *(int64_t *)((char *)epoch_canaries[epoch] + 16) = 24680 + epoch;
            pcc_gc_pin(epoch_canaries[epoch]);
            int64_t live_with_canary = pcc_gc_live_bytes;
            CHECK(check_active_list(), 82);
            CHECK(pcc_gc_set_backend(backend) == 0, 83);
            reset_ok++;
            pcc_gc_prepare_object_list_mark(1);
            CHECK(C(1) == count && C(2) == count * object_usable, 84);
            CHECK(pcc_gc_live_bytes == live_with_canary, 85);
            CHECK(check_active_list(), 86);
            for (int i = 0; i < count; i++) {
                CHECK(pcc_gc_object_index_find(objects[i]) == nodes[i], 87);
                CHECK(field(nodes[i], 32) == -C(11), 88);
                CHECK(((PyObjectHeader *)objects[i])->refcount == original_rc[i], 89);
            }
        }
        if (mode == 15 && enabled) {
            /* Poison only the first actual pending chunk's eligibility, not its
             * chain or ownership. An all-failed chunk must not end closing before
             * the following valid chunks have been consumed. */
            void *node = pcc_gc_scalar_sweep_head;
            for (int i = 0; i < CHUNK_OBJECTS; i++) {
                CHECK(node != 0 && field(node, 32) == -C(11), 96);
                PyObject *obj = pointer_field(node, 0);
                CHECK(pcc_gc_object_index_find(obj) == node, 97);
                quarantined_objects[i] = obj;
                quarantined_nodes[i] = node;
                quarantined_rc[i] = ((PyObjectHeader *)obj)->refcount;
                ((PyObjectHeader *)obj)->flags |= 64;
                node = pointer_field(node, 16);
                quarantined_count++;
            }
            CHECK(node != 0, 100);
        }
        CHECK(pcc_resume_world() == 0, 38);
        if (enabled) pcc_gc_deferred_free_close();
        if (mode == 3 || mode == 12 || mode == 15) {
            CHECK(pcc_gc_cms_stop_worker() == 0, 39);
        } else if (expected) {
            int rounds = 0;
            while ((C(5) != expected_total + expected || C(0) != 0) && rounds++ < 2000) {
                pcc_thread_safepoint(); pcc_platform_sleep_ns(1000000);
            }
        }
        if (carrier) {
            __atomic_store_n(&stop_allocations, 1, __ATOMIC_RELEASE);
            CHECK(pcc_thread_join(carrier, 0) == 0, 40);
            CHECK(concurrent_status == 0 && concurrent_count > 0, 41);
        }
        expected_total += expected;
        CHECK(C(3) == expected_total && C(4) == C(5) && C(5) == expected_total - quarantined_count, 42);
        CHECK(C(0) == (quarantined_count ? 4 : 0) && C(1) == quarantined_count &&
              C(2) == quarantined_count * object_usable && C(9) == quarantined_count, 43);
        CHECK(C(6) + C(7) == expected_total - quarantined_count, 44);
        CHECK(C(10) == 0 && C(13) == expected_chunks && C(16) == overflow_expected, 90);
        if (expected) {
            CHECK(C(14) > 0 && C(14) <= CHUNK_OBJECTS, 91);
            CHECK(C(15) == C(14) * object_usable && C(15) <= CHUNK_BYTES, 92);
            if (mode == 11) CHECK(C(13) >= 12 && C(14) < CHUNK_OBJECTS, 93);
            if (mode == 14) CHECK(pcc_gc_object_node_free_count == NODE_POOL, 94);
            CHECK(C(17) == count && C(18) == count * object_usable, 101);
        }
        if (mode == 15 && enabled) {
            CHECK(pcc_gc_scalar_sweep_head == 0, 102);
            for (int i = 0; i < quarantined_count; i++) {
                PyObject *obj = quarantined_objects[i];
                CHECK(node_for(obj) == quarantined_nodes[i], 103);
                CHECK(field(quarantined_nodes[i], 32) == -C(11), 104);
                CHECK(pcc_gc_scalar_sweep_owns(obj) == 1, 105);
                CHECK((((PyObjectHeader *)obj)->flags & 64) != 0, 106);
                pcc_gc_free_object_memory(obj);
                CHECK(node_for(obj) == quarantined_nodes[i], 116);
                CHECK(pcc_gc_pointer_unregister(obj) == -1, 107);
                CHECK(((PyObjectHeader *)obj)->refcount == quarantined_rc[i], 112);
                quarantine_checks++;
            }
            /* A completed stop is not a retry license for quarantined nodes. */
            CHECK(pcc_gc_cms_stop_worker() == -1, 108);
            CHECK(C(4) == expected_total - quarantined_count && C(4) == C(5), 109);
            CHECK(C(0) == 4 && C(1) == quarantined_count && C(9) == quarantined_count, 110);
            CHECK(C(13) == expected_chunks && pcc_gc_scalar_sweep_head == 0, 111);
            for (int i = 0; i < quarantined_count; i++) {
                CHECK(node_for(quarantined_objects[i]) == quarantined_nodes[i], 113);
                CHECK(field(quarantined_nodes[i], 32) == -C(11), 114);
                CHECK(((PyObjectHeader *)quarantined_objects[i])->refcount == quarantined_rc[i], 115);
            }
            stop_checks++;
        }
        if (expected && mode != 8) {
            /* Independent logical-retirement witness: no old key remains.
             * Then one fresh allocation must map to its own reusable node,
             * not to an old object's index key (node-pool ABA control). */
            for (int i = 0; i < count; i++) {
                if (!is_quarantined(objects[i])) CHECK(node_for(objects[i]) == 0, 50);
            }
            PyObject *replacement = pcc_gc_alloc(64, PY_TYPE_FLOAT, 0);
            CHECK(replacement != 0, 51);
            void *new_node = node_for(replacement);
            CHECK(new_node != 0 && *(void **)new_node == replacement, 52);
            pcc_gc_pin(replacement);
            for (int i = 0; i < count; i++) if (new_node == nodes[i]) node_reused++;
            CHECK(node_reused > 0, 53);
        }
        worker_id = C(8);
        if (expected && mode != 3 && mode != 12 && mode != 15) CHECK(C(6) == expected_total && worker_id > 0 && worker_id != producer_id, 45);
        CHECK(*(int64_t *)((char *)canary + 16) == 987654321, 46);
        if (backend != 0) CHECK(node_for(canary) != 0, 47);
        if (backend != 0) CHECK(check_active_list(), 95);
        CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 48);
        completed_epochs++;
    }
    if (mode != 3 && mode != 5 && mode != 12 && mode != 15) CHECK(pcc_gc_cms_stop_worker() == 0, 49);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 99;
    int mode = argv[1][0] - '0';
    if (argv[1][1]) mode = mode * 10 + argv[1][1] - '0';
    if (mode < 1 || mode > 15) return 98;
    int status = run(mode);
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    printf("scalar-sweep backend=%d mode=%d producer=%lld worker=%lld admitted=%lld "
           "sealed_pending=%lld retired=%lld released=%lld worker_retired=%lld local=%lld "
           "reset=%lld canary_allocations=%lld node_reused=%lld pending=%lld quarantine=%lld "
           "pending_bytes=%lld chunks=%lld peak_objects=%lld peak_bytes=%lld overflow_nodes=%lld "
           "usable=%lld claimed_bytes=%lld epochs=%lld observers=%lld list_checks=%lld "
           "peak_pending=%lld peak_pending_bytes=%lld quarantine_checks=%lld stop_checks=%lld "
           "state=%lld double_frees=%lld status=%d\n",
           pcc_gc_backend(), mode, (long long)producer_id, (long long)worker_id,
           (long long)admitted, (long long)pending_at_seal, (long long)C(4),
           (long long)C(5), (long long)C(6), (long long)C(7), (long long)reset_ok,
           (long long)concurrent_count, (long long)node_reused, (long long)C(1), (long long)C(9),
           (long long)C(2), (long long)C(13), (long long)C(14), (long long)C(15), (long long)C(16),
           (long long)object_usable, (long long)claimed_bytes, (long long)completed_epochs,
           (long long)observer_checks, (long long)list_checks,
           (long long)C(17), (long long)C(18), (long long)quarantine_checks, (long long)stop_checks,
           (long long)C(0),
           (long long)pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES), status);
    return status;
}
