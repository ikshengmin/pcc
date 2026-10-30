#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern void pcc_allocator_lock_acquire(void);
extern void pcc_allocator_lock_release(void);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

/* Four threads contend for the central allocator lock directly and through
   magazine refills and drains: each frees what its neighbour allocated, so
   one thread's caches keep emptying while the next one's keep filling. */
#define THREADS 4
#define ROUNDS 20000
#define SLOTS 64

static int64_t guarded; /* read and written only under the allocator lock */
static void *mailbox[THREADS][SLOTS];
/* Thread start/exit and each thread's first TLS use allocate bookkeeping:
   the accounting window opens after every worker is ready and closes
   before any of them exits. */
static int64_t ready, finished, go, leave;

static void wait_for(int64_t *flag, int64_t value) {
    while (__atomic_load_n(flag, __ATOMIC_ACQUIRE) < value) pcc_thread_safepoint();
}

static void *worker(void *arg) {
    int64_t id = (int64_t)arg;
    int64_t previous = (id + THREADS - 1) % THREADS;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    free(pcc_allocator_alloc_object(64));
    free(malloc(64));
    __atomic_add_fetch(&ready, 1, __ATOMIC_ACQ_REL);
    wait_for(&go, 1);
    for (int64_t i = 0; i < ROUNDS; i++) {
        pcc_allocator_lock_acquire();
        int64_t seen = __atomic_load_n(&guarded, __ATOMIC_RELAXED);
        for (volatile int widen = 0; widen < 8; widen++) {
        }
        __atomic_store_n(&guarded, seen + 1, __ATOMIC_RELAXED);
        pcc_allocator_lock_release();
        int64_t size = (int64_t)16 << (i % 8);
        void *cell = (i & 1) ? pcc_allocator_alloc_object(size) : malloc((size_t)size);
        if (!cell) return (void *)2;
        ((unsigned char *)cell)[size - 1] = (unsigned char)id;
        void *mine = __atomic_exchange_n(&mailbox[id][i % SLOTS], cell, __ATOMIC_ACQ_REL);
        if (mine) free(mine);
        void *theirs = __atomic_exchange_n(
            &mailbox[previous][(i * 7) % SLOTS], (void *)0, __ATOMIC_ACQ_REL);
        if (theirs) free(theirs);
    }
    __atomic_add_fetch(&finished, 1, __ATOMIC_ACQ_REL);
    wait_for(&leave, 1);
    return 0;
}

int main(void) {
    if (pcc_current_thread_id() <= 0) return 10;
    void *threads[THREADS];
    for (int64_t t = 0; t < THREADS; t++) {
        if (pcc_thread_start(&threads[t], worker, (void *)t) != 0) return 11;
    }
    wait_for(&ready, THREADS);
    int64_t requested = pcc_allocator_live_requested_bytes();
    int64_t usable = pcc_allocator_live_usable_bytes();
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    wait_for(&finished, THREADS);
    if (__atomic_load_n(&guarded, __ATOMIC_RELAXED) != (int64_t)THREADS * ROUNDS) return 13;
    for (int t = 0; t < THREADS; t++) {
        for (int s = 0; s < SLOTS; s++) {
            if (mailbox[t][s]) free(mailbox[t][s]);
        }
    }
    if (pcc_allocator_live_requested_bytes() != requested) return 14;
    if (pcc_allocator_live_usable_bytes() != usable) return 15;
    __atomic_store_n(&leave, 1, __ATOMIC_RELEASE);
    for (int t = 0; t < THREADS; t++) {
        void *status = 0;
        if (pcc_thread_join(threads[t], &status) != 0 || status) return 12;
    }
    if (pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) != 0) return 16;
    puts("ALLOCATOR_LOCK_OK");
    return 0;
}
