/* Standalone bounded nursery metadata prototype. No production allocator,
 * object index, GC dispatch, or mutator behavior is changed.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

enum { CELL_BITS = 64, CELL_COUNT = 2 * CELL_BITS };
enum { CURSOR_STALE = -2, CURSOR_END = -1 };

typedef struct {
    uint64_t epoch;
    unsigned published;
    uint64_t young[2];
    uint64_t old[2];
} Span;

typedef struct {
    uint64_t epoch;
    unsigned limit;
    unsigned next;
} Cursor;

static void publish(Span *span, unsigned slot) {
    span->young[slot / CELL_BITS] |= (uint64_t)1 << (slot % CELL_BITS);
    __atomic_store_n(&span->published, slot + 1, __ATOMIC_RELEASE);
}

static void retire_young(Span *span, unsigned cell, int promote) {
    unsigned word = cell / CELL_BITS;
    uint64_t bit = (uint64_t)1 << (cell % CELL_BITS);
    span->young[word] &= ~bit;
    if (promote) span->old[word] |= bit;
}

static Cursor snapshot(Span *span) {
    Cursor result;
    result.epoch = span->epoch;
    result.limit = __atomic_load_n(&span->published, __ATOMIC_ACQUIRE);
    result.next = 0;
    return result;
}

static int next_young(Span *span, Cursor *cursor) {
    /* Snapshot, reuse and scan are stopped-world operations in this model. */
    if (cursor->epoch != span->epoch) return CURSOR_STALE;
    while (cursor->next < cursor->limit) {
        unsigned slot = cursor->next;
        uint64_t bits = span->young[slot / CELL_BITS] >> (slot % CELL_BITS);
        if (!bits) {
            cursor->next = (slot / CELL_BITS + 1) * CELL_BITS;
            continue;
        }
        while ((bits & 1) == 0) {
            bits >>= 1;
            slot++;
        }
        if (slot >= cursor->limit) break;
        cursor->next = slot + 1;
        return (int)slot;
    }
    return CURSOR_END;
}

static int reset_if_empty(Span *span) {
    if (span->young[0] || span->young[1] || span->old[0] || span->old[1])
        return 0;
    span->epoch++;
    __atomic_store_n(&span->published, 0, __ATOMIC_RELEASE);
    return 1;
}

static int check_cursor(void) {
    Span span = {1, 0, {0, 0}, {0, 0}};
    for (unsigned cell = 0; cell < CELL_COUNT - 1; ++cell)
        publish(&span, cell);
    Cursor frozen = snapshot(&span);
    publish(&span, CELL_COUNT - 1);
    for (unsigned cell = 0; cell < CELL_COUNT - 1; ++cell)
        if (cell != 63 && cell != 64) retire_young(&span, cell, cell == 10);
    if (next_young(&span, &frozen) != 63) return 1;
    if (next_young(&span, &frozen) != 64) return 2;
    if (next_young(&span, &frozen) != CURSOR_END) return 3;
    Cursor fresh = snapshot(&span);
    if (next_young(&span, &fresh) != 63 || next_young(&span, &fresh) != 64
        || next_young(&span, &fresh) != 127 || next_young(&span, &fresh) != CURSOR_END)
        return 4;
    retire_young(&span, 63, 0);
    retire_young(&span, 64, 0);
    retire_young(&span, 127, 0);
    if (reset_if_empty(&span)) return 5;
    span.old[0] &= ~((uint64_t)1 << 10);
    if (!reset_if_empty(&span)) return 6;
    publish(&span, 0);
    if (next_young(&span, &frozen) != CURSOR_STALE) return 7;
    fresh = snapshot(&span);
    if (next_young(&span, &fresh) != 0 || next_young(&span, &fresh) != CURSOR_END)
        return 8;
    puts("GC3_NURSERY_CURSOR_NATIVE_OK");
    return 0;
}

/* Interleave a private reservation with the real PCC stop protocol.  The
 * publisher must finish its payload and acquire/release tail handoff
 * before its outermost no-park exit permits the collector to proceed.
 * This still does not allocate through a production nursery.
 */
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

typedef struct {
    Span span;
    unsigned ready;
    unsigned resumed;
    uint64_t header[2];
    int protect_publication;
} StopProbe;

static void *publish_during_stop(void *arg) {
    StopProbe *probe = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    if (probe->protect_publication) pcc_thread_no_park_enter();
    probe->header[0] = 0x1234;
    __atomic_store_n(&probe->ready, 1, __ATOMIC_RELEASE);
    while (pcc_thread_stop_requested_acquire() == 0) {
        /* No scheduler calls or data access that could publish the cell. */
    }
    pcc_thread_safepoint();  /* Negative control parks before commit. */
    probe->header[1] = 0x5678;
    publish(&probe->span, 0);
    /* The protected path polls the pending stop at this outermost exit. */
    if (probe->protect_publication) pcc_thread_no_park_exit();
    __atomic_store_n(&probe->resumed, 1, __ATOMIC_RELEASE);
    publish(&probe->span, 1);   /* Cannot extend the frozen range. */
    return NULL;
}

static int check_stop_handoff(int protect_publication) {
    StopProbe probe = {{1, 0, {0, 0}, {0, 0}}, 0, 0, {0, 0}, protect_publication};
    void *thread = NULL, *status = NULL;
    if (pcc_gc_backend() != 3) return 20;
    py_gc_disable();
    if (pcc_current_thread_id() <= 0) return 21;
    if (pcc_thread_start(&thread, publish_during_stop, &probe) != 0 || !thread)
        return 22;
    while (!__atomic_load_n(&probe.ready, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();

    unsigned incomplete = __atomic_load_n(&probe.span.published, __ATOMIC_ACQUIRE);
    if (pcc_stop_the_world() != 0) return 23;
    Cursor frozen = snapshot(&probe.span);
    unsigned parked = __atomic_load_n(&probe.resumed, __ATOMIC_ACQUIRE) == 0;
    unsigned published = protect_publication
        ? frozen.limit == 1 && probe.header[0] == 0x1234 &&
          probe.header[1] == 0x5678 && next_young(&probe.span, &frozen) == 0
        : frozen.limit == 0 && probe.header[0] == 0x1234 &&
          probe.header[1] == 0 && next_young(&probe.span, &frozen) == CURSOR_END;
    if (pcc_resume_world() != 0) return 24;
    if (pcc_thread_join(thread, &status) != 0 || status != NULL) return 25;
    if (incomplete != 0 || !parked || !published) return 26;
    if (next_young(&probe.span, &frozen) != CURSOR_END) return 27;
    Cursor fresh = snapshot(&probe.span);
    if (next_young(&probe.span, &fresh) != 0 || next_young(&probe.span, &fresh) != 1)
        return 28;
    puts(protect_publication ? "GC3_NURSERY_CURSOR_STW_OK"
                             : "GC3_NURSERY_CURSOR_UNPROTECTED_CONTROL_OK");
    return 0;
}

int main(int argc, char **argv) {
    return argc == 2 ? check_stop_handoff(argv[1][0] == 's') : check_cursor();
}
