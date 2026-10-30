#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void pcc_allocator_lock_acquire(void);
extern void pcc_allocator_lock_release(void);
extern void pcc_allocator_thread_cache_enter(void);
extern void pcc_allocator_thread_cache_drain(void);
extern int64_t pcc_allocator_trim(void);
extern int64_t pcc_allocator_mapped_bytes(void);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_gc_granule_is_object_start(void *);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern size_t malloc_usable_size(void *);
extern void *aligned_alloc(size_t, size_t);

static unsigned char *cells[8][64];
static unsigned char *trim_cells[2500];
static int64_t ready;
static int64_t cache_ready;
static int64_t trim_finished;

static void *live_cache_owner(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)1;
    for (int64_t size = 16; size <= 2048; size *= 2) {
        void *p = malloc(size);
        if (!p) return (void *)2;
        free(p);
    }
    __atomic_store_n(&cache_ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&trim_finished, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    /* A different thread's trim must leave these cached cells available. */
    pcc_allocator_lock_acquire();
    for (int64_t size = 16; size <= 2048; size *= 2) {
        unsigned char *p = malloc(size);
        if (!p) return (void *)3;
        p[0] = 197;
        p[size - 1] = 231;
        if (p[0] != 197 || p[size - 1] != 231) return (void *)4;
        free(p);
    }
    pcc_allocator_lock_release();
    return 0;
}

static void *producer(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)1;
    int64_t size = 16;
    for (int k = 0; k < 8; ++k, size *= 2) {
        for (int i = 0; i < 64; ++i) {
            cells[k][i] = malloc(size);
            if (!cells[k][i]) return (void *)2;
            cells[k][i][0] = (unsigned char)i;
            cells[k][i][size - 1] = (unsigned char)(k + 101);
        }
    }
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    return 0;
}

static void *consumer(void *unused) {
    if (pcc_current_thread_id() <= 0) return (void *)3;
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    int64_t size = 16;
    for (int k = 0; k < 8; ++k, size *= 2) {
        for (int i = 0; i < 64; ++i) {
            if (cells[k][i][0] != (unsigned char)i ||
                    cells[k][i][size - 1] != (unsigned char)(k + 101)) return (void *)4;
            if (pcc_gc_granule_is_object_start(cells[k][i]) == 1) return (void *)5;
            free(cells[k][i]);
        }
    }
    return 0;
}

int main(void) {
    if (pcc_current_thread_id() <= 0) return 10;
    void *warm = malloc(64);
    if (!warm) return 11;
    free(warm);
    int64_t requested = pcc_allocator_live_requested_bytes();
    int64_t usable = pcc_allocator_live_usable_bytes();
    /* Warm raw churn must not acquire the central lock. */
    pcc_allocator_lock_acquire();
    for (int i = 0; i < 100; ++i) {
        unsigned char *p = malloc(64);
        if (!p) return 12;
        p[0] = 23;
        p[63] = 97;
        if (p[0] != 23 || p[63] != 97) return 13;
        free(p);
    }
    pcc_allocator_lock_release();
    if (pcc_allocator_live_requested_bytes() != requested ||
            pcc_allocator_live_usable_bytes() != usable) return 14;

    unsigned char *p = calloc(129, 1);
    if (!p) return 15;
    for (int i = 0; i < 129; ++i) {
        if (p[i] != 0) return 16;
        p[i] = (unsigned char)i;
    }
    p = realloc(p, 8193);
    if (!p || malloc_usable_size(p) < 8193) return 17;
    for (int i = 0; i < 129; ++i) if (p[i] != (unsigned char)i) return 18;
    p = realloc(p, 7);
    if (!p) return 19;
    for (int i = 0; i < 7; ++i) if (p[i] != (unsigned char)i) return 20;
    free(p);
    if (pcc_allocator_live_requested_bytes() != requested ||
            pcc_allocator_live_usable_bytes() != usable) return 21;
    p = aligned_alloc(64, 128);
    if (!p || ((uintptr_t)p & 63) != 0 || malloc_usable_size(p) < 128) return 32;
    free(p);
    p = malloc(0);
    free(p);
    if (calloc((size_t)-1, 2) != 0) return 33;

    int64_t remote_requested = 0, remote_usable = 0, plateau = 0;
    for (int round = 0; round < 8; ++round) {
        __atomic_store_n(&ready, 0, __ATOMIC_RELEASE);
        void *a, *b, *status = 0;
        if (pcc_thread_start(&a, producer, 0)) return 22;
        if (pcc_thread_start(&b, consumer, 0)) return 23;
        if (pcc_thread_join(a, &status) || status) return 24;
        if (pcc_thread_join(b, &status) || status) return 25;
        pcc_allocator_trim();
        if (round == 1) {
            remote_requested = pcc_allocator_live_requested_bytes();
            remote_usable = pcc_allocator_live_usable_bytes();
            plateau = pcc_allocator_mapped_bytes();
        } else if (round > 1) {
            if (pcc_allocator_live_requested_bytes() != remote_requested ||
                    pcc_allocator_live_usable_bytes() != remote_usable) return 36;
            if (pcc_allocator_mapped_bytes() > plateau + 4096) return 37;
        }
    }

    void *cache_worker, *cache_status = 0;
    if (pcc_thread_start(&cache_worker, live_cache_owner, 0)) return 38;
    while (!__atomic_load_n(&cache_ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    pcc_allocator_trim();
    __atomic_store_n(&trim_finished, 1, __ATOMIC_RELEASE);
    if (pcc_thread_join(cache_worker, &cache_status) || cache_status) return 39;

    requested = pcc_allocator_live_requested_bytes();
    usable = pcc_allocator_live_usable_bytes();
    for (int i = 0; i < 2500; ++i) {
        trim_cells[i] = malloc(64);
        if (!trim_cells[i]) return 26;
        trim_cells[i][0] = (unsigned char)i;
    }
    /* Trimming cache extras must not retire an allocated cell's slab. */
    for (int i = 0; i < 2500; ++i) {
        if (trim_cells[i][0] != (unsigned char)i) {
            printf("before trim: index=%d actual=%u expected=%u\n",
                i, (unsigned)trim_cells[i][0], (unsigned)(unsigned char)i);
            return 35;
        }
    }
    pcc_allocator_trim();
    for (int i = 0; i < 2500; ++i) {
        if (trim_cells[i][0] != (unsigned char)i) {
            printf("after trim: index=%d actual=%u expected=%u\n",
                i, (unsigned)trim_cells[i][0], (unsigned)(unsigned char)i);
            return 27;
        }
        free(trim_cells[i]);
    }
    if (pcc_allocator_live_requested_bytes() != requested ||
            pcc_allocator_live_usable_bytes() != usable) return 28;
    int64_t mapped = pcc_allocator_mapped_bytes();
    if (pcc_allocator_trim() <= 0) return 29;
    if (pcc_allocator_mapped_bytes() >= mapped) return 30;
    warm = malloc(64);
    if (!warm || pcc_gc_granule_is_object_start(warm) == 1) return 31;
    ((int64_t *)warm)[0] = 1;
    ((int32_t *)warm)[2] = PY_TYPE_LIST;
    ((int32_t *)warm)[3] = 1;
    if (pcc_gc_granule_is_object_start(warm) == 1) return 34;
    free(warm);
    mapped = pcc_allocator_mapped_bytes();
    pcc_allocator_thread_cache_drain();
    if (pcc_allocator_mapped_bytes() != mapped - 4096) return 40;
    pcc_allocator_trim();
    puts("RAW_MAGAZINES_OK");
    return 0;
}
