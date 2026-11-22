/* Real runtime visitors at a test-invoked stopped cut; no young cells freed. */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern int64_t pcc_gc3_review_owner_cards_set_stopped(void *);
extern int64_t pcc_gc3_review_scan_owner_cards_stopped(void *, void *);

typedef struct {
    int64_t capacity, used, overflow;
    PyObject *owners[4];
} Cards;

typedef struct {
    int64_t capacity, count, owners_visited, strong_slots, failed;
    PyObject *young[4];
} Frontier;

int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_backend() != 3) return 1;
    int64_t n = atoi(argv[1]);
    if (n != 64 && n != 256 && n != 1024) return 2;
    py_gc_disable();
    PyObject **clean = malloc(n * sizeof(PyObject *));
    if (!clean) return 3;
    for (int64_t i = 0; i < n; ++i) {
        clean[i] = py_list_new(0);
        if (!clean[i]) return 4;
        ((PyListObject *)clean[i])->h.flags |= PY_FLAG_GC_OLD;
    }
    PyListObject *list = (PyListObject *)py_list_new(1);
    PyTupleObject *tuple = (PyTupleObject *)py_tuple_new(1);
    PyBytesObject *young = pcc_allocator_alloc_object(sizeof(PyBytesObject) + 1);
    if (!list || !tuple || !young) return 5;
    list->h.flags |= PY_FLAG_GC_OLD;
    tuple->h.flags |= PY_FLAG_GC_OLD;
    young->h.refcount = 1;
    young->h.type_tag = PY_TYPE_BYTES;
    young->h.flags = PY_FLAG_GC_YOUNG;
    young->byte_len = 1;
    young->data[0] = 'y';
    PyObject *child = (PyObject *)young;
    if (pcc_allocator_object_publish_fresh(young) != 1 || pcc_gc_object_index_find(child))
        return 6;

    Cards cards = {4, 0, 0, {NULL}};
    Frontier frontier = {4, 0, 0, 0, 0, {NULL}};
    if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != -1)
        return 7;
    if (pcc_stop_the_world() != 0) return 8;
    if (pcc_gc3_review_owner_cards_set_stopped(&cards) != 1) return 9;
    if (pcc_resume_world() != 0) return 10;
    py_list_append((PyObject *)list, child);
    py_tuple_set_item((PyObject *)tuple, 0, child);
    if (cards.used != 2 || cards.overflow || cards.owners[0] != (PyObject *)list
        || cards.owners[1] != (PyObject *)tuple) return 11;

    if (pcc_stop_the_world() != 0) return 12;
    int64_t start = pcc_runtime_monotonic_us();
    for (int64_t repeat = 0; repeat < 128; ++repeat) {
        if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != 2
            || frontier.count != 2 || frontier.owners_visited != 2
            || frontier.strong_slots != 2 || frontier.failed
            || frontier.young[0] != child || frontier.young[1] != child
            || cards.used != 2) return 13;
    }
    int64_t us = pcc_runtime_monotonic_us() - start;
    Cards overflow_cut = cards;
    Frontier tiny = {1, 0, 0, 0, 0, {NULL}};
    if (pcc_gc3_review_scan_owner_cards_stopped(&overflow_cut, &tiny) != -1
        || !overflow_cut.overflow || !tiny.failed || tiny.count != 1) return 22;
    if (pcc_resume_world() != 0) return 14;
    pcc_gc_store_ptr((PyObject *)list, list->items, NULL);
    pcc_gc_store_ptr((PyObject *)tuple, tuple->items, NULL);
    if (pcc_stop_the_world() != 0) return 15;
    if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != 0
        || frontier.count || frontier.owners_visited != 2
        || frontier.strong_slots != 2 || cards.used) return 16;
    if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != 0
        || frontier.owners_visited || frontier.strong_slots) return 17;
    Cards retired = {4, 1, 0, {(PyObject *)(uintptr_t)0x4000}};
    if (pcc_gc3_review_scan_owner_cards_stopped(&retired, &frontier) != 0
        || retired.used || frontier.owners_visited) return 23;
    cards.overflow = 1;
    if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != -1)
        return 18;
    if (pcc_gc3_review_owner_cards_set_stopped(NULL) != 1) return 19;
    if (pcc_resume_world() != 0) return 20;
    if (pcc_allocator_object_retire_live(young) != 1) return 21;
    free(young);
    free(clean);
    printf("GC3_SPARSE_CARDS_OK clean=%lld dirty=2 strong=2 repeats=128 us=%lld\n",
           (long long)n, (long long)us);
    return 0;
}
