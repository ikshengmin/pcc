#include "py_internal.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sched.h>

extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int64_t pcc_thread_no_park_depth(void);
extern void pcc_gc_set_mark_color(void *, int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int64_t pcc_gc_object_is_known_no_lock(void *);
extern int64_t pcc_gc_slot_is_runtime_root(void *);
extern int64_t pcc_gc_backend4_store_buffer_enqueue(void *, void *, void *);
extern int64_t pcc_gc_store_buffer_slot_pending(void *);
extern int64_t pcc_gc_store_buffer_owner_entries(void *);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_forwarding_population;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
extern void *pcc_gc_backend4_store_buffer_medium_head;
static PyObject *keeper;
static PyObject *child;
static PyObject *published;
static int32_t ready, start, done, leave, failure, final_pin, checkpoint_calls;

static void *root_publisher(void *unused) {
    if (pcc_current_thread_id() <= 0) {
        __atomic_store_n(&failure, 1, __ATOMIC_RELEASE); return 0;
    }
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&start, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint_gate(); sched_yield();
    }
    if (pcc_py_gc_minor_graph_lock_depth() != 0) {
        __atomic_store_n(&failure, 2, __ATOMIC_RELEASE); return 0;
    }
    /* A keeps graph/no-park; these public paths must complete without it. */
    pcc_gc_store_root(&published, keeper);
    if (final_pin) pcc_gc_pin(keeper); else pcc_gc_unpin(keeper);
    if (pcc_py_gc_minor_graph_lock_depth() != 0 || pcc_thread_no_park_depth() != 0) {
        __atomic_store_n(&failure, 3, __ATOMIC_RELEASE); return 0;
    }
    __atomic_store_n(&done, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&leave, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint_gate(); sched_yield();
    }
    pcc_thread_unregister_current();
    return 0;
}

/* Only enqueue's existing call is relocated here; no runtime body is replaced. */
int64_t gc4_enqueue_checkpoint(void) {
    checkpoint_calls++;
    if (checkpoint_calls != 1 || pcc_py_gc_minor_graph_lock_depth() <= 0
            || pcc_thread_no_park_depth() <= 0) {
        __atomic_store_n(&failure, 4, __ATOMIC_RELEASE); return 0;
    }
    __atomic_store_n(&start, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&done, __ATOMIC_ACQUIRE)) {
        if (__atomic_load_n(&failure, __ATOMIC_ACQUIRE)) return 0;
        sched_yield(); /* No gate while holding A's graph lock. */
    }
    return pcc_current_thread_id();
}

int main(int argc, char **argv) {
    if (argc != 3 || pcc_gc_set_backend(4) != 0 || pcc_current_thread_id() <= 0) return 10;
    final_pin = argv[1][0] == 's';
    int historical = argv[2][0] == 'b';
    if (pcc_gc_backend4_no_refcounts != 1 || pcc_diagnostics_runtime_log_fast_state != 0
            || pcc_gc_refcount_provenance_probe != 0) return 11;
    py_gc_disable();
    pcc_gc_scheduler_root_register(&keeper);
    pcc_gc_scheduler_root_register(&child);
    pcc_gc_scheduler_root_register(&published);
    if (pcc_gc_slot_is_runtime_root(&keeper) != 1
            || pcc_gc_slot_is_runtime_root(&published) != 1) return 12;
    keeper = py_list_new(1);
    child = py_list_new(0);
    if (!keeper || !child) return 13;
    pcc_gc_pin(keeper);
    pcc_gc_pin(child);
    PyObject **slot = ((PyListObject *)keeper)->items;
    if (!slot || pcc_gc_backend4_store_buffer_medium_pending() != 0) return 14;
    pcc_py_gc_minor_graph_lock();
    *slot = child;
    ((PyListObject *)keeper)->length = 1;
    if (!pcc_gc_object_is_known_no_lock(keeper) || pcc_gc_forwarding_population != 0) return 15;
    pcc_py_gc_minor_graph_unlock();
    pthread_t publisher;
    if (pthread_create(&publisher, 0, root_publisher, 0) != 0) return 16;
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) {
        if (__atomic_load_n(&failure, __ATOMIC_ACQUIRE)) return 17;
        pcc_thread_safepoint_gate(); sched_yield();
    }
    pcc_py_gc_minor_graph_lock();
    if (final_pin) pcc_gc_unpin(keeper);
    pcc_gc_set_mark_color(keeper, 8);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    int32_t saved = py_header_flags_load(py_header(keeper));
    int64_t gray = pcc_gc_gray_count_load_acquire();
    int64_t depth = pcc_py_gc_minor_graph_lock_depth();
    if ((saved & 56) != 8 || (saved & 512) || (saved & 64) != (final_pin ? 0 : 64)) return 18;
    if (pcc_gc_backend4_store_buffer_enqueue(keeper, slot, child) != 1) return 19;
    if (failure || checkpoint_calls != 1 || published != keeper
            || pcc_py_gc_minor_graph_lock_depth() != depth) return 20;
    int32_t after = py_header_flags_load(py_header(keeper));
    int32_t expected = historical ? (saved | 512)
        : ((saved & ~(56 | 64)) | 16 | 512 | (final_pin ? 64 : 0));
    if (after != expected || pcc_gc_gray_count_load_acquire() != gray + 1) return 21;
    void **node = pcc_gc_backend4_store_buffer_medium_head;
    if (!node || node[0] != keeper || node[1] != slot || node[2] != child || node[3]) return 22;
    if (((int64_t *)node)[4] != pcc_current_thread_id()
            || pcc_gc_backend4_store_buffer_medium_pending() != 1
            || pcc_gc_store_buffer_slot_pending(slot) != 1
            || pcc_gc_store_buffer_owner_entries(keeper) != 1) return 23;
    if (pcc_gc_backend4_store_buffer_enqueue(keeper, slot, child) != 0
            || checkpoint_calls != 1 || py_header_flags_load(py_header(keeper)) != after
            || pcc_gc_backend4_store_buffer_medium_head != node) return 24;
    pcc_py_gc_minor_graph_unlock();
    __atomic_store_n(&leave, 1, __ATOMIC_RELEASE);
    if (pthread_join(publisher, 0) != 0 || failure) return 25;
    puts(historical ? "GC4_ENQUEUE_NATIVE_STALE" : "GC4_ENQUEUE_NATIVE_PRESERVED");
    return 0;
}
