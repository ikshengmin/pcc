#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern void pcc_allocator_lock_acquire(void);
extern void pcc_allocator_lock_release(void);
extern void pcc_allocator_thread_cache_drain(void);
extern void pcc_allocator_thread_cache_enter(void);
extern int64_t pcc_allocator_mapped_bytes(void);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
static void *cells[11][32];
static int64_t published;

static void *producer(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)1;
    int64_t size = 16;
    for (int k = 0; k < 11; k++, size *= 2) {
        for (int i = 0; i < 32; i++) {
            cells[k][i] = pcc_allocator_alloc_object(size);
            if (!cells[k][i]) return (void *)2;
            for (int j = 0; j < i; j++) {
                if (cells[k][j] == cells[k][i]) return (void *)3;
            }
        }
    }
    __atomic_store_n(&published, 1, __ATOMIC_RELEASE);
    return 0;
}

static void *consumer(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)4;
    while (!__atomic_load_n(&published, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    for (int k = 0; k < 11; k++) {
        for (int i = 0; i < 32; i++) free(cells[k][i]);
    }
    return 0;
}

int main(void) {
    if (pcc_current_thread_id() <= 0) return 10;
    void *warm = pcc_allocator_alloc_object(64);
    if (!warm) return 11;
    free(warm);
    int64_t requested = pcc_allocator_live_requested_bytes();
    int64_t usable = pcc_allocator_live_usable_bytes();
    /* A warmed single-cell churn must not touch the central lock. The
       subprocess timeout makes a regression fail instead of hanging pytest. */
    pcc_allocator_lock_acquire();
    for (int i = 0; i < 100; i++) {
        void *p = pcc_allocator_alloc_object(64);
        if (!p) return 12;
        free(p);
    }
    pcc_allocator_lock_release();
    if (pcc_allocator_live_requested_bytes() != requested) return 13;
    if (pcc_allocator_live_usable_bytes() != usable) return 14;
    int64_t mapped = pcc_allocator_mapped_bytes();
    pcc_allocator_thread_cache_drain();
    if (pcc_allocator_mapped_bytes() != mapped - 4096) return 15;
    int64_t plateau = 0;
    for (int repeat = 0; repeat < 12; repeat++) {
        __atomic_store_n(&published, 0, __ATOMIC_RELEASE);
        void *a, *b, *status = 0;
        if (pcc_thread_start(&a, producer, 0) != 0) return 16;
        if (pcc_thread_start(&b, consumer, 0) != 0) return 17;
        if (pcc_thread_join(a, &status) != 0 || status) return 18;
        if (pcc_thread_join(b, &status) != 0 || status) return 19;
        if (repeat == 1) plateau = pcc_allocator_mapped_bytes();
    }
    if (pcc_allocator_mapped_bytes() > plateau + 4096) return 20;
    pcc_allocator_thread_cache_enter();
    int64_t faults = pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES);
    if (faults != 0) return 25;
    requested = pcc_allocator_live_requested_bytes();
    usable = pcc_allocator_live_usable_bytes();
    void *duplicate = pcc_allocator_alloc_object(64);
    free(duplicate);
    free(duplicate);
    if (pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) != faults + 1) return 21;
    void *one = pcc_allocator_alloc_object(64);
    void *two = pcc_allocator_alloc_object(64);
    if (!one || !two || one == two) return 22;
    free(one);
    free(two);
    if (pcc_allocator_live_requested_bytes() != requested) return 23;
    if (pcc_allocator_live_usable_bytes() != usable) return 24;
    pcc_allocator_thread_cache_drain();
    puts("OBJECT_MAGAZINES_OK");
    return 0;
}
