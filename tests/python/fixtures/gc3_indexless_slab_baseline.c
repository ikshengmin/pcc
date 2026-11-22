/* Use real allocator cells without adding a collector record. This is the
 * current object-cell protocol, not the proposed bump nursery allocator.
 */
#include "py_runtime.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern void *pcc_allocator_object_cell_span(void *);

static int known(PyObject *object) {
    return pcc_gc_granule_is_object_start(object) == 1
        || pcc_gc_pointer_is_managed(object) != 0;
}

static void initialize(PyObjectHeader *header) {
    header->refcount = 1;
    header->type_tag = PY_TYPE_INT;
    header->flags = 0;
}

int main(void) {
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
    PyObject *first = pcc_allocator_alloc_object(64);
    PyObject *reserved = pcc_allocator_alloc_object(64);
    PyObject *raw = malloc(64);
    if (!first || !reserved || !raw) return 2;
    initialize((PyObjectHeader *)first);
    initialize((PyObjectHeader *)reserved);
    initialize((PyObjectHeader *)raw);
    if (known(first) || known(reserved) || known(raw)) return 3;
    if (pcc_gc_object_index_find(first) || pcc_allocator_object_cell_span(first))
        return 4;
    if (pcc_allocator_object_publish_fresh(first) != 1) return 5;
    if (pcc_gc_granule_is_object_start(first) != 1 || !pcc_gc_pointer_is_managed(first))
        return 6;
    if (pcc_gc_object_index_find(first) || pcc_allocator_object_cell_span(first))
        return 7;
    if (known((PyObject *)((char *)first + sizeof(PyObjectHeader)))
        || known(reserved) || known(raw)) return 8;
    if (pcc_allocator_object_publish_fresh(reserved) != 1 || !known(reserved))
        return 9;
    if (pcc_allocator_object_retire_live(first) != 1 || known(first)) return 10;
    if (!known(reserved) || pcc_gc_object_index_find(reserved)) return 11;
    if (pcc_allocator_object_retire_live(reserved) != 1 || known(reserved)) return 12;
    free(first);
    free(reserved);
    free(raw);
    puts("GC3_INDEXLESS_SLAB_BASELINE_OK");
    return 0;
}
