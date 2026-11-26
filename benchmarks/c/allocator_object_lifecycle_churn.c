// Linux host reference harness for the self-compiled allocator object.
// This measures a managed-cell lifecycle, not a whole Python request.
// argv: iteration count (default 2000000), central-path control (0/1).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

extern void pcc_allocator_thread_cache_enter(void);
extern void pcc_allocator_thread_cache_drain(void);
extern void *pcc_allocator_alloc_object(long);
extern long pcc_allocator_object_publish_fresh(void *);
extern long pcc_allocator_object_retire_live(void *);
extern long pcc_gc_granule_is_object_start(void *);
extern long pcc_allocator_live_requested_bytes(void);
extern long pcc_allocator_live_usable_bytes(void);

static uint64_t ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
    return t.tv_sec * 1000000000ULL + t.tv_nsec;
}

int main(int argc, char **argv) {
    int loops = argc > 1 ? atoi(argv[1]) : 2000000;
    unsigned control = argc > 2 ? atoi(argv[2]) : 0;
    if (loops <= 0 || control > 1) return 6;
    if (!control) pcc_allocator_thread_cache_enter();
    long before = pcc_allocator_live_requested_bytes();
    long usable = pcc_allocator_live_usable_bytes();
    uint64_t start = ns();
    unsigned long checksum = 0;
    for (int i = 0; i < loops; i++) {
        long size = 32 + 16 * (i & 7);
        uint64_t *p = pcc_allocator_alloc_object(size);
        if (!p) return 1;
        memset(p, 0, size);
        p[0] = 1;
        p[1] = 2;
        if (pcc_allocator_object_publish_fresh(p) != 1) return 2;
        if (pcc_gc_granule_is_object_start(p) != 1) return 3;
        checksum += p[0] + p[1] + size;
        if (pcc_allocator_object_retire_live(p) != 1) return 4;
        free(p);
    }
    uint64_t end = ns();
    if (pcc_allocator_live_requested_bytes() != before ||
        pcc_allocator_live_usable_bytes() != usable) return 5;
    pcc_allocator_thread_cache_drain();
    printf("%lu %lu %ld %ld\n", (unsigned long)(end - start), checksum,
           pcc_allocator_live_requested_bytes() - before,
           pcc_allocator_live_usable_bytes() - usable);
    return 0;
}
