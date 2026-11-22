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
        if (pcc_stop_the_world() != 0) return 12;
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
    owner->length = owner->capacity = 1;
    owner->items = malloc(sizeof(PyObject *));
    if (!owner->items) return 3;
    owner->items[0] = (PyObject *)child;

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

    pcc_gc_store_ptr((PyObject *)owner, owner->items, NULL);
    if (pcc_allocator_object_retire_live(child) != 1) return 11;
    free(child);
    puts("GC3_PROMOTED_STRONG_SLOT_OK");
    return 0;
}
