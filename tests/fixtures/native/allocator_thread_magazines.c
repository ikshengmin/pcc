#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern size_t malloc_usable_size(void *);
extern int64_t pcc_allocator_trim_objects(void);
extern int64_t pcc_allocator_object_slab_counter(int64_t);
extern int64_t pcc_gc_granule_is_object_start(void *);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern void pcc_allocator_lock_acquire(void);
extern void pcc_allocator_lock_release(void);
extern void pcc_allocator_thread_cache_drain(void);
extern void pcc_allocator_thread_cache_enter(void);
extern int64_t pcc_allocator_mapped_bytes(void);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
static void *cells[13][32];
static int64_t published;

static void *producer(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)1;
    int64_t size = 16;
    for (int k = 0; k < 13; k++, size *= 2) {
        for (int i = 0; i < 32; i++) {
            cells[k][i] = pcc_allocator_alloc_object(k == 11 ? 40 : (k == 12 ? 72 : size));
            if (cells[k][i] && malloc_usable_size(cells[k][i]) != (size_t)(k == 11 ? 48 : (k == 12 ? 80 : size))) return (void *)5;
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
    for (int k = 0; k < 13; k++) {
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
    /* Boundary, raw ABI, exact provenance, exhaustion (more than two 682
       cell slabs), trim/recarve and the byte-aligned 48-byte object payload. */
    for (int n = 32; n <= 49; n++) {
        void *obj = pcc_allocator_alloc_object(n);
        void *raw = malloc(n);
        if (!obj || !raw || ((uintptr_t)obj & 15) || ((uintptr_t)raw & 15)) return 30;
        size_t expected = n == 32 ? 32 : (n <= 48 ? 48 : 64);
        if (malloc_usable_size(obj) != expected || malloc_usable_size(raw) != (size_t)(n == 32 ? 32 : 64)) return 31;
        if (pcc_gc_granule_is_object_start(obj) == 1 || pcc_allocator_object_publish_fresh(obj) != 1) return 32;
        if (pcc_gc_granule_is_object_start(obj) != 1 || pcc_gc_granule_is_object_start((char *)obj+16) == 1) return 33;
        free(obj); free(raw);
    }
    void *burst48[1400];
    int64_t reused_before = pcc_allocator_object_slab_counter(1);
    for (int round = 0; round < 2; round++) {
        for (int i = 0; i < 1400; i++) {
            burst48[i] = pcc_allocator_alloc_object(48);
            if (!burst48[i] || malloc_usable_size(burst48[i]) != 48) return 34;
            for (int j = 0; j < i; j++) if (burst48[i] == burst48[j]) return 35;
        }
        if (round == 1 && pcc_allocator_object_slab_counter(1) <= reused_before) return 39;
        for (int i = 0; i < 1400; i++) free(burst48[i]);
        if (pcc_allocator_trim_objects() <= 0) return 36;
    }
    if (pcc_allocator_live_requested_bytes() != requested || pcc_allocator_live_usable_bytes() != usable) return 37;

    for (int n = 64; n <= 81; n++) {
        void *obj = pcc_allocator_alloc_object(n);
        void *raw = malloc(n);
        if (!obj || !raw || ((uintptr_t)obj & 15) || ((uintptr_t)raw & 15)) return 40;
        size_t expected = n == 64 ? 64 : (n <= 80 ? 80 : 128);
        if (malloc_usable_size(obj) != expected || malloc_usable_size(raw) != (size_t)(n == 64 ? 64 : 128)) return 41;
        if (pcc_gc_granule_is_object_start(obj) == 1 || pcc_allocator_object_publish_fresh(obj) != 1) return 42;
        if (pcc_gc_granule_is_object_start(obj) != 1 || pcc_gc_granule_is_object_start((char *)obj+16) == 1) return 43;
        free(obj); free(raw);
    }
    void *burst80[1100];
    reused_before = pcc_allocator_object_slab_counter(1);
    for (int round = 0; round < 2; round++) {
        for (int i = 0; i < 1100; i++) {
            burst80[i] = pcc_allocator_alloc_object(80);
            if (!burst80[i] || malloc_usable_size(burst80[i]) != 80) return 44;
            for (int j = 0; j < i; j++) if (burst80[i] == burst80[j]) return 45;
        }
        if (round == 1 && pcc_allocator_object_slab_counter(1) <= reused_before) return 46;
        for (int i = 0; i < 1100; i++) free(burst80[i]);
        if (pcc_allocator_trim_objects() <= 0) return 47;
    }
    if (pcc_allocator_live_requested_bytes() != requested || pcc_allocator_live_usable_bytes() != usable) return 48;
    /* trim drained the warm magazines; rewarm before lock-only control. */
    warm = pcc_allocator_alloc_object(64);
    if (!warm) return 38;
    free(warm);
    /* A warmed single-cell churn must not touch the central lock. The
       subprocess timeout makes a regression fail instead of hanging pytest. */
    pcc_allocator_lock_acquire();
    for (int i = 0; i < 100; i++) {
        void *p = pcc_allocator_alloc_object(64);
        if (!p) return 12;
        free(p);
    }
    pcc_allocator_lock_release();
    /* Exceed the 256-cell cache limit, then consume the drained and
       cached cells again. Every detached cell must remain distinct. */
    void *burst[600];
    for (int round = 0; round < 2; ++round) {
        for (int i = 0; i < 600; ++i) {
            burst[i] = pcc_allocator_alloc_object(64);
            if (!burst[i]) return 26;
            for (int j = 0; j < i; ++j) {
                if (burst[i] == burst[j]) return 27;
            }
        }
        for (int i = 0; i < 600; ++i) free(burst[i]);
    }
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
    void *duplicate = pcc_allocator_alloc_object(48);
    free(duplicate);
    free(duplicate);
    if (pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) != faults + 1) return 21;
    void *one = pcc_allocator_alloc_object(48);
    void *two = pcc_allocator_alloc_object(48);
    if (!one || !two || one == two) return 22;
    free(one);
    free(two);
    if (pcc_allocator_live_requested_bytes() != requested) return 23;
    if (pcc_allocator_live_usable_bytes() != usable) return 24;
    faults = pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES);
    duplicate = pcc_allocator_alloc_object(80);
    free(duplicate); free(duplicate);
    if (pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) != faults + 1) return 49;
    one = pcc_allocator_alloc_object(80); two = pcc_allocator_alloc_object(80);
    if (!one || !two || one == two) return 50;
    free(one); free(two);
    if (pcc_allocator_live_requested_bytes() != requested || pcc_allocator_live_usable_bytes() != usable) return 51;
    pcc_allocator_thread_cache_drain();
    puts("OBJECT_MAGAZINES_OK");
    return 0;
}
