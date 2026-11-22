/* A promoted test owner exposes an ordinary strong slot that the runtime
 * can read even when its child has allocator provenance but no GC record.
 * This is not an implementation of a remembered set or a minor collection.
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern int64_t pcc_gc_register_object_cell(PyObject *, int64_t);
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
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
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
    pcc_thread_no_park_enter();
    owner->h.flags &= ~PY_FLAG_GC_YOUNG;
    int64_t committed = pcc_gc_register_object_cell((PyObject *)owner, sizeof(PyListObject));
    pcc_thread_no_park_exit();
    if (committed != 1 || !pcc_gc_object_index_find((PyObject *)owner)) return 6;

    Seen seen = {owner->items, (PyObject *)child, 0, 0};
    if (pcc_stop_the_world() != 0) return 7;
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
