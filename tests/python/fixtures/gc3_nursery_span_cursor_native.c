/* Standalone bounded nursery metadata prototype. No production allocator,
 * object index, GC dispatch, or mutator behavior is changed.
 */
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

int main(void) {
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
