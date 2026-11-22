/* Own the entire test cohort. The fixture supplies all roots explicitly. */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern int64_t pcc_gc3_review_owner_cards_set_stopped(void *);
extern int64_t pcc_gc3_review_scan_owner_cards_stopped(void *, void *);
extern int64_t pcc_gc3_review_collect_closed_young_stopped(void *, PyObject **, int64_t, void *, void *);

typedef struct {
    int64_t capacity, used, overflow;
    PyObject *owners[2];
} Cards;

typedef struct {
    int64_t capacity, count, owners_visited, strong_slots, failed;
    PyObject *young[4];
} Frontier;

typedef struct {
    int64_t count;
    PyObject **cells;
    int64_t *marked, *queue;
    int64_t enqueued, invalid;
} YoungCut;

static PyTupleObject *young_tuple(void) {
    PyTupleObject *obj = pcc_allocator_alloc_object(sizeof(PyTupleObject) + sizeof(PyObject *));
    if (!obj) return NULL;
    obj->h.refcount = 1;
    obj->h.type_tag = PY_TYPE_TUPLE;
    obj->h.flags = PY_FLAG_GC_YOUNG;
    obj->len = 1;
    obj->items[0] = NULL;
    if (pcc_allocator_object_publish_fresh(obj) != 1) return NULL;
    return obj;
}

static PyListObject *young_list(PyObject *child) {
    PyListObject *obj = pcc_allocator_alloc_object(sizeof(PyListObject));
    if (!obj) return NULL;
    obj->items = malloc(sizeof(PyObject *));
    if (!obj->items) return NULL;
    obj->h.refcount = 1;
    obj->h.type_tag = PY_TYPE_LIST;
    obj->h.flags = PY_FLAG_GC_YOUNG;
    obj->length = obj->capacity = 1;
    obj->items[0] = child;
    if (pcc_allocator_object_publish_fresh(obj) != 1) return NULL;
    return obj;
}

static int alive(PyObject *obj) {
    return pcc_gc_granule_is_object_start(obj) == 1;
}

int main(void) {
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
    /* List->tuple->list cycle, plus a separate list->tuple pair and one
       dead list. One root and one dirty old edge must find both graphs. */
    PyTupleObject *cycle = young_tuple(), *card_child = young_tuple();
    if (!cycle || !card_child) return 2;
    PyListObject *root = young_list((PyObject *)cycle);
    PyListObject *kept_by_card = young_list((PyObject *)card_child);
    PyListObject *dead = young_list(NULL);
    PyTupleObject *outside = young_tuple();
    PyListObject *old = (PyListObject *)py_list_new(1);
    if (!root || !kept_by_card || !dead || !outside || !old) return 3;
    old->h.flags |= PY_FLAG_GC_OLD;
    cycle->items[0] = (PyObject *)root;

    PyObject *cells[5] = {(PyObject *)cycle, (PyObject *)card_child, (PyObject *)root,
                          (PyObject *)kept_by_card, (PyObject *)dead};
    for (int i = 1; i < 5; ++i) {
        PyObject *cell = cells[i];
        int j = i;
        while (j > 0 && (uintptr_t)cells[j - 1] > (uintptr_t)cell) {
            cells[j] = cells[j - 1];
            --j;
        }
        cells[j] = cell;
    }
    int64_t marked[5], queue[5];
    YoungCut cohort = {5, cells, marked, queue, 0, 0};
    Cards cards = {2, 0, 0, {NULL}};
    Frontier frontier = {4, 0, 0, 0, 0, {NULL}};
    PyObject *roots[2] = {(PyObject *)root, (PyObject *)outside};

    if (pcc_stop_the_world() != 0) return 4;
    if (pcc_gc3_review_owner_cards_set_stopped(&cards) != 1) return 5;
    if (pcc_resume_world() != 0) return 6;
    py_list_append((PyObject *)old, (PyObject *)kept_by_card);
    if (pcc_gc3_review_collect_closed_young_stopped(&cohort, roots, 1, &cards, &frontier) != -1)
        return 7;
    if (pcc_stop_the_world() != 0) return 8;
    if (pcc_gc3_review_scan_owner_cards_stopped(&cards, &frontier) != 1
        || frontier.young[0] != (PyObject *)kept_by_card) return 9;
    /* Uncovered young in an explicit root must veto all reclamation. */
    if (pcc_gc3_review_collect_closed_young_stopped(&cohort, roots, 2, &cards, &frontier) != -1
        || !alive((PyObject *)dead) || !alive((PyObject *)outside)) return 10;
    cards.overflow = 1;
    if (pcc_gc3_review_collect_closed_young_stopped(&cohort, roots, 1, &cards, &frontier) != -1
        || !alive((PyObject *)dead)) return 11;
    cards.overflow = 0;
    dead->items[0] = (PyObject *)(uintptr_t)0x4000;
    if (pcc_gc3_review_collect_closed_young_stopped(&cohort, roots, 1, &cards, &frontier) != -1
        || !alive((PyObject *)dead)) return 16;
    dead->items[0] = NULL;
    if (pcc_gc3_review_collect_closed_young_stopped(&cohort, roots, 1, &cards, &frontier) != 1
        || cohort.enqueued != 4 || alive((PyObject *)dead)) return 12;
    if (!alive((PyObject *)root) || !alive((PyObject *)cycle)
        || !alive((PyObject *)kept_by_card) || !alive((PyObject *)card_child)
        || !alive((PyObject *)outside) || root->items[0] != (PyObject *)cycle
        || cycle->items[0] != (PyObject *)root
        || kept_by_card->items[0] != (PyObject *)card_child) return 13;
    if (pcc_gc3_review_owner_cards_set_stopped(NULL) != 1) return 14;
    if (pcc_resume_world() != 0) return 15;
    puts("GC3_CLOSED_YOUNG_OK traced=4 reclaimed=1 vetoed=4");
    return 0;
}
