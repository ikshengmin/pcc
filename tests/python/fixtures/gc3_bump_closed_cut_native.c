/* Exercise actual bump storage with the rooted collector; no public admission. */
#include "py_internal.h"
#include <stdio.h>

extern void *pcc_allocator_gc3_review_bump_open(int64_t);
extern void *pcc_allocator_gc3_review_bump_reserve(void *, int64_t);
extern int64_t pcc_allocator_gc3_review_bump_commit(void *, void *);
extern void *pcc_allocator_gc3_review_bump_lookup(void *);
extern int64_t pcc_allocator_gc3_review_bump_generation(void *);
extern int64_t pcc_gc3_review_collect_bump_span_stopped(void *, void *, PyObject **, int64_t,
                                                        void *, void *);

typedef struct {
    int64_t capacity, used, overflow;
    PyObject *owners[1];
} Cards;
typedef struct {
    int64_t capacity, count, owners_visited, strong_slots, failed;
    PyObject *young[1];
} Frontier;
typedef struct {
    int64_t count;
    PyObject **cells;
    int64_t *marked, *queue;
    int64_t enqueued, invalid;
} YoungCut;

static PyTupleObject *put_tuple(void *span, int64_t len) {
    PyTupleObject *tuple = pcc_allocator_gc3_review_bump_reserve(
        span, sizeof(PyTupleObject) + len * sizeof(PyObject *));
    if (!tuple) return NULL;
    tuple->h.refcount = 1;
    tuple->h.type_tag = PY_TYPE_TUPLE;
    tuple->h.flags = PY_FLAG_GC_YOUNG;
    tuple->len = len;
    if (pcc_allocator_gc3_review_bump_commit(span, tuple) != 1) return NULL;
    return tuple;
}

int main(void) {
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
    void *span = pcc_allocator_gc3_review_bump_open(32);
    void *other_span = pcc_allocator_gc3_review_bump_open(32);
    if (!span || !other_span) return 2;
    pcc_thread_no_park_enter();
    PyTupleObject *first = put_tuple(span, 1), *second = put_tuple(span, 1);
    PyTupleObject *third = put_tuple(span, 0);
    PyTupleObject *outside = put_tuple(other_span, 0);
    if (!first || !second || !third || !outside) return 3;
    /* Complete this closed test graph before the collection entry. */
    first->items[0] = (PyObject *)second;
    second->items[0] = (PyObject *)first;
    pcc_thread_no_park_exit();

    PyObject *roots[2] = {(PyObject *)first, (PyObject *)outside}, *cells[4];
    int64_t marks[4], queue[4];
    YoungCut cut = {4, cells, marks, queue, 0, 0};
    Cards cards = {1, 0, 0, {NULL}};
    Frontier frontier = {1, 0, 0, 0, 0, {NULL}};
    if (pcc_gc3_review_collect_bump_span_stopped(span, &cut, roots, 1, &cards, &frontier) != -1)
        return 4;
    if (pcc_stop_the_world() != 0) return 5;
    cards.used = 1;
    if (pcc_gc3_review_collect_bump_span_stopped(span, &cut, roots, 1, &cards, &frontier) != -1
        || pcc_allocator_gc3_review_bump_lookup(third) != span) return 6;
    cards.used = 0;
    if (pcc_gc3_review_collect_bump_span_stopped(span, &cut, roots, 2, &cards, &frontier) != -1
        || pcc_allocator_gc3_review_bump_lookup(third) != span) return 11;
    if (pcc_gc3_review_collect_bump_span_stopped(span, &cut, roots, 1, &cards, &frontier) != 0
        || cut.enqueued != 2 || pcc_allocator_gc3_review_bump_lookup(third) != span
        || pcc_allocator_gc3_review_bump_generation(span) != 1) return 7;
    if (pcc_gc3_review_collect_bump_span_stopped(span, &cut, NULL, 0, &cards, &frontier) != 3
        || cut.enqueued || pcc_allocator_gc3_review_bump_lookup(first)
        || pcc_allocator_gc3_review_bump_lookup(second)
        || pcc_allocator_gc3_review_bump_lookup(third)
        || pcc_allocator_gc3_review_bump_lookup(outside) != other_span
        || pcc_allocator_gc3_review_bump_generation(span) != 2) return 8;
    if (pcc_resume_world() != 0) return 9;
    pcc_thread_no_park_enter();
    if (put_tuple(span, 0) != first) return 10;
    pcc_thread_no_park_exit();
    puts("GC3_BUMP_CUT_OK survivors=2 deferred=1 rewound=3 reused=1");
    return 0;
}
