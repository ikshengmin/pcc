/* Test the dormant GC3 promotion transaction with a caller-owned card
 * slot. This does not run a nursery, barrier, or minor collection. */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern int64_t pcc_gc_backend3_promote_published_cell_stopped(PyObject *, int64_t, PyObject **);
extern void *pcc_allocator_object_node_record_find(void *);
extern int32_t pcc_gc_mark_active, pcc_gc_cms_sweep_pending;
extern int64_t pcc_gc3_review_owner_cards_set_stopped(void *);

typedef struct {
    int64_t capacity, used, overflow;
    PyObject *owners[2];
} OwnerCards;
extern int64_t pcc_gc_object_slots_visit_core_container_slots(
    PyObject *, void (*)(PyObject **, int64_t, void *), void *);

enum { SLOT_STRONG = 1 };

typedef struct {
    PyObject **wanted_slot;
    PyObject *wanted_child;
    int visits;
    int matches;
} Seen;

static void inspect_slot(PyObject **slot, int64_t role, void *context) {
    Seen *seen = context;
    ++seen->visits;
    if (slot == seen->wanted_slot && role == SLOT_STRONG && *slot == seen->wanted_child)
        ++seen->matches;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    py_gc_disable();
    if (backend != 3) {
        PyObject *card = NULL;
        OwnerCards untouched = {2, 0, 0, {NULL, NULL}};
        if (pcc_stop_the_world() != 0) return 12;
        if (pcc_gc3_review_owner_cards_set_stopped(&untouched) != 0) return 32;
        int64_t refused = pcc_gc_backend3_promote_published_cell_stopped(
            (PyObject *)(uintptr_t)0x4000, sizeof(PyListObject), &card);
        if (pcc_resume_world() != 0 || refused != 0 || card) return 13;
        puts("GC3_PROMOTION_OTHER_BACKEND_INERT_OK");
        return 0;
    }
    PyListObject *owner = pcc_allocator_alloc_object(sizeof(PyListObject));
    PyBytesObject *child = pcc_allocator_alloc_object(sizeof(PyBytesObject) + 1);
    if (!owner || !child) return 2;
    owner->h.refcount = child->h.refcount = 1;
    owner->h.type_tag = PY_TYPE_LIST;
    child->h.type_tag = PY_TYPE_BYTES;
    owner->h.flags = child->h.flags = PY_FLAG_GC_YOUNG;
    child->byte_len = 1;
    child->data[0] = 'y';
    owner->length = 1;
    owner->capacity = 2;
    owner->items = malloc(2 * sizeof(PyObject *));
    if (!owner->items) return 3;
    owner->items[0] = (PyObject *)child;
    owner->items[1] = NULL;

    if (pcc_allocator_object_publish_fresh(child) != 1 ||
        pcc_allocator_object_publish_fresh(owner) != 1) return 4;
    if (pcc_gc_object_index_find((PyObject *)owner) ||
        pcc_gc_object_index_find((PyObject *)child)) return 5;
    PyObject *object = (PyObject *)owner;
    PyObject *card = NULL;
    if (pcc_gc_backend3_promote_published_cell_stopped(object, sizeof(*owner), &card)
        || card || pcc_gc_object_index_find(object)) return 14;
    if (pcc_stop_the_world() != 0) return 7;
    if (pcc_gc_backend3_promote_published_cell_stopped(
            (PyObject *)((char *)owner + 8), sizeof(*owner), &card) || card)
        return 15;
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    int64_t blocked = pcc_gc_backend3_promote_published_cell_stopped(
        object, sizeof(*owner), &card);
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    if (blocked || card || pcc_gc_object_index_find(object)) return 19;
    __atomic_store_n(&pcc_gc_cms_sweep_pending, 1, __ATOMIC_RELEASE);
    blocked = pcc_gc_backend3_promote_published_cell_stopped(
        object, sizeof(*owner), &card);
    __atomic_store_n(&pcc_gc_cms_sweep_pending, 0, __ATOMIC_RELEASE);
    if (blocked || card || pcc_gc_object_index_find(object)) return 20;
    card = (PyObject *)child;
    if (pcc_gc_backend3_promote_published_cell_stopped(object, sizeof(*owner), &card)
        || card != (PyObject *)child || pcc_gc_object_index_find(object)
        || owner->h.flags != PY_FLAG_GC_YOUNG) return 16;
    void *prepared = pcc_allocator_object_node_record_find(owner);
    if (!prepared) return 17;
    card = NULL;
    if (pcc_gc_backend3_promote_published_cell_stopped(object, sizeof(*owner), &card) != 1
        || pcc_gc_object_index_find(object) != prepared || card != object
        || !(owner->h.flags & PY_FLAG_GC_OLD) || (owner->h.flags & PY_FLAG_GC_YOUNG)) return 6;
    if (pcc_gc_backend3_promote_published_cell_stopped(object, sizeof(*owner), &card) != 1
        || pcc_gc_object_index_find(object) != prepared || card != object) return 18;

    Seen seen = {owner->items, (PyObject *)child, 0, 0};
    int64_t supported = pcc_gc_object_slots_visit_core_container_slots(
        (PyObject *)owner, inspect_slot, &seen);
    if (pcc_resume_world() != 0) return 8;
    if (supported != 1 || seen.visits != 1 || seen.matches != 1) return 9;
    if (!pcc_gc_pointer_is_managed((PyObject *)child) ||
        pcc_gc_object_index_find((PyObject *)child) ||
        !(child->h.flags & PY_FLAG_GC_YOUNG) ||
        child->data[0] != 'y')
        return 10;

    PyTupleObject *tuple = (PyTupleObject *)py_tuple_new(1);
    if (!tuple) return 21;
    /* A real indexed tuple, explicitly old as it would be after promotion.
       The age mark alone controls this review witness; allocation is stock. */
    tuple->h.flags |= PY_FLAG_GC_OLD;
    OwnerCards cards = {2, 0, 0, {NULL, NULL}};
    if (pcc_gc3_review_owner_cards_set_stopped(&cards) != 0) return 22;
    if (pcc_stop_the_world() != 0) return 23;
    if (pcc_gc3_review_owner_cards_set_stopped(&cards) != 1) return 24;
    if (pcc_resume_world() != 0) return 25;
    PyObject *root = NULL;
    pcc_gc_store_root(&root, (PyObject *)child);
    if (root != (PyObject *)child || cards.used != 0) return 33;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_store_ptr(object, owner->items, NULL);
    pcc_gc_store_ptr(object, owner->items, (PyObject *)child);
    if (*owner->items != (PyObject *)child || cards.used != 1 || cards.owners[0] != object)
        return 26;
    PccGcStoreRootPlan plan;
    pcc_gc_store_ptr_plan_init(&plan, (PyObject *)tuple, 3);
    pcc_py_gc_minor_graph_lock();
    int64_t planned = pcc_gc_store_ptr_plan_commit_locked(
        &plan, (PyObject *)tuple, tuple->items, (PyObject *)child);
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_ptr_plan_finish(&plan);
    if (planned != 1 || tuple->items[0] != (PyObject *)child || cards.used != 2
        || cards.owners[1] != (PyObject *)tuple || cards.overflow) return 27;
    /* A full table is never interpreted as a complete dirty cut. */
    pcc_gc_store_ptr(object, owner->items, (PyObject *)child);
    if (cards.used != 3 || cards.overflow != 1 || cards.owners[0] != object
        || cards.owners[1] != (PyObject *)tuple) return 28;
    if (pcc_stop_the_world() != 0) return 29;
    if (pcc_gc3_review_owner_cards_set_stopped(NULL) != 1) return 30;
    if (pcc_resume_world() != 0) return 31;
    OwnerCards append = {2, 0, 0, {NULL, NULL}};
    if (pcc_stop_the_world() != 0) return 34;
    if (pcc_gc3_review_owner_cards_set_stopped(&append) != 1) return 35;
    if (pcc_resume_world() != 0) return 36;
    py_list_append(object, (PyObject *)child); /* First write to spare storage. */
    if (owner->length != 2 || owner->capacity != 2 || append.used != 1
        || append.owners[0] != object || owner->items[1] != (PyObject *)child) return 37;
    py_list_append(object, (PyObject *)child); /* First write after publication of a larger array. */
    if (owner->length != 3 || owner->capacity != 4 || append.used != 2
        || append.owners[1] != object || append.overflow
        || owner->items[1] != (PyObject *)child || owner->items[2] != (PyObject *)child) return 38;
    if (pcc_stop_the_world() != 0) return 39;
    if (pcc_gc3_review_owner_cards_set_stopped(NULL) != 1) return 40;
    if (pcc_resume_world() != 0) return 41;
    pcc_gc_store_ptr((PyObject *)tuple, tuple->items, NULL);
    for (int i = 0; i < owner->length; ++i) pcc_gc_store_ptr(object, owner->items + i, NULL);
    if (pcc_allocator_object_retire_live(child) != 1) return 11;
    free(child);
    puts("GC3_PROMOTED_STRONG_SLOT_OK");
    return 0;
}
