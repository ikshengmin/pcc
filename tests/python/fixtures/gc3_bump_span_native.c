/* Own a review nursery span; never give its cells to ordinary free(). */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern void *pcc_allocator_gc3_review_bump_open(int64_t);
extern void *pcc_allocator_gc3_review_bump_reserve(void *, int64_t);
extern int64_t pcc_allocator_gc3_review_bump_commit(void *, void *);
extern int64_t pcc_allocator_gc3_review_bump_cancel(void *, void *);
extern void *pcc_allocator_gc3_review_bump_lookup(void *);
extern int64_t pcc_allocator_gc3_review_bump_reset_stopped(void *, int64_t, int64_t, int64_t);
extern int64_t pcc_allocator_gc3_review_bump_generation(void *);
extern void *pcc_allocator_object_node_record_find(void *);
extern int64_t pcc_allocator_granule_count;
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

typedef struct {
    void *span, *cell;
    int32_t ready, staged, checked;
} Peer;

static void *observe(void *argument) {
    Peer *peer = argument;
    pcc_thread_no_park_enter();
    int64_t deadline = pcc_runtime_monotonic_us() + 2000000;
    __atomic_store_n(&peer->ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&peer->staged, __ATOMIC_ACQUIRE)) {
        if (pcc_runtime_monotonic_us() > deadline) {
            pcc_thread_no_park_exit();
            return (void *)1;
        }
    }
    if (pcc_allocator_gc3_review_bump_lookup(peer->cell)
        || pcc_allocator_gc3_review_bump_reserve(peer->span, sizeof(PyTupleObject))) {
        pcc_thread_no_park_exit();
        return (void *)2;
    }
    __atomic_store_n(&peer->checked, 1, __ATOMIC_RELEASE);
    /* Acquire only the span frontier after the owner's initialization. */
    while (!pcc_allocator_gc3_review_bump_lookup(peer->cell)) {
        if (pcc_runtime_monotonic_us() > deadline) {
            pcc_thread_no_park_exit();
            return (void *)3;
        }
    }
    PyTupleObject *tuple = peer->cell;
    int visible = tuple->h.type_tag == PY_TYPE_TUPLE && tuple->h.flags == PY_FLAG_GC_YOUNG;
    pcc_thread_no_park_exit();
    return visible ? NULL : (void *)4;
}

static void init_tuple(void *cell) {
    PyTupleObject *tuple = cell;
    tuple->h.refcount = 1;
    tuple->h.type_tag = PY_TYPE_TUPLE;
    tuple->h.flags = PY_FLAG_GC_YOUNG;
    tuple->len = 0;
}

int main(void) {
    if (pcc_gc_backend() != 3) {
        if (pcc_allocator_gc3_review_bump_open(32)) return 1;
        puts("GC3_BUMP_OTHER_BACKEND_INERT_OK");
        return 0;
    }
    py_gc_disable();
    if (pcc_allocator_gc3_review_bump_open(48)) return 2;
    int64_t before = pcc_allocator_granule_count;
    void *span = pcc_allocator_gc3_review_bump_open(32);
    if (!span || pcc_allocator_granule_count != before + 16
        || pcc_allocator_gc3_review_bump_generation(span) != 1) return 3;
    if (pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject))) return 4;
    Peer peer = {span, NULL, 0, 0, 0};
    void *thread = NULL, *status = NULL;
    if (pcc_thread_start(&thread, observe, &peer) != 0) return 21;
    int64_t deadline = pcc_runtime_monotonic_us() + 2000000;
    while (!__atomic_load_n(&peer.ready, __ATOMIC_ACQUIRE)) {
        if (pcc_runtime_monotonic_us() > deadline) return 22;
        pcc_thread_safepoint();
    }

    pcc_thread_no_park_enter();
    if (pcc_allocator_gc3_review_bump_reserve(span, 33)) return 5;
    void *a = pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject));
    if (!a || pcc_allocator_gc3_review_bump_lookup(a)
        || pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject))) return 6;
    peer.cell = a;
    __atomic_store_n(&peer.staged, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&peer.checked, __ATOMIC_ACQUIRE)) {
        if (pcc_runtime_monotonic_us() > deadline) return 23;
    }
    init_tuple(a);
    if (pcc_allocator_gc3_review_bump_commit(span, (char *)a + 1)
        || pcc_allocator_gc3_review_bump_lookup(a)) return 7;
    if (pcc_allocator_gc3_review_bump_commit(span, a) != 1
        || pcc_allocator_gc3_review_bump_lookup(a) != span
        || pcc_allocator_gc3_review_bump_lookup((char *)a + 1)
        || pcc_allocator_object_node_record_find(a)
        || pcc_gc_object_index_find((PyObject *)a)
        || pcc_gc_granule_is_object_start(a) == 1) return 8;

    void *b = pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject));
    if ((char *)b != (char *)a + 32 || pcc_allocator_gc3_review_bump_lookup(b))
        return 9;
    init_tuple(b);
    if (pcc_allocator_gc3_review_bump_cancel(span, b) != 1
        || pcc_allocator_gc3_review_bump_lookup(b)) return 10;
    void *retry = pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject));
    if (retry != b || ((PyTupleObject *)retry)->h.type_tag != 0) return 11;
    init_tuple(retry);
    if (pcc_allocator_gc3_review_bump_commit(span, retry) != 1) return 12;
    pcc_thread_no_park_exit();
    if (pcc_thread_join(thread, &status) != 0 || status) return 24;
    int64_t keys_after_peer = pcc_allocator_granule_count;

    if (pcc_allocator_gc3_review_bump_reset_stopped(span, 1, 2, 0)) return 13;
    if (pcc_stop_the_world() != 0) return 14;
    if (pcc_allocator_gc3_review_bump_reset_stopped(span, 1, 2, 1)
        || pcc_allocator_gc3_review_bump_reset_stopped(span, 1, 1, 0)
        || pcc_allocator_gc3_review_bump_lookup(a) != span
        || pcc_allocator_gc3_review_bump_lookup(b) != span) return 15;
    if (pcc_allocator_gc3_review_bump_reset_stopped(span, 1, 2, 0) != 1
        || pcc_allocator_gc3_review_bump_lookup(a)
        || pcc_allocator_gc3_review_bump_lookup(b)
        || pcc_allocator_gc3_review_bump_generation(span) != 2
        || pcc_allocator_gc3_review_bump_reset_stopped(span, 1, 0, 0)) return 16;
    if (pcc_resume_world() != 0) return 17;

    pcc_thread_no_park_enter();
    for (int64_t i = 0; i < 2048; ++i) {
        void *cell = pcc_allocator_gc3_review_bump_reserve(span, sizeof(PyTupleObject));
        if ((char *)cell != (char *)a + i * 32) return 18;
        init_tuple(cell);
        if (pcc_allocator_gc3_review_bump_commit(span, cell) != 1
            || pcc_allocator_gc3_review_bump_lookup(cell) != span
            || pcc_allocator_object_node_record_find(cell)) return 19;
    }
    if (pcc_allocator_gc3_review_bump_reserve(span, 16)
        || pcc_allocator_granule_count != keys_after_peer) return 20;
    pcc_thread_no_park_exit();
    puts("GC3_BUMP_SPAN_OK committed=2048 new_keys=16 registered_cells=0 reused=1");
    return 0;
}
