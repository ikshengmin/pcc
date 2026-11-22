/* Use real allocator cells without adding a collector record. This is the
 * current object-cell protocol, not the proposed bump nursery allocator.
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_allocator_object_retire_live(void *);
extern void *pcc_allocator_object_cell_span(void *);
extern int64_t pcc_gc_register_object_cell(PyObject *, int64_t);

static int known(PyObject *object) {
    return pcc_gc_granule_is_object_start(object) == 1
        || pcc_gc_pointer_is_managed(object) != 0;
}

static void initialize(PyObjectHeader *header) {
    header->refcount = 1;
    header->type_tag = PY_TYPE_INT;
    header->flags = 0;
}

static int check_unindexed_lifecycle(void) {
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

/* Exercise the existing ordinary GC3 record path at the same address. This
 * does not implement promotion: the fixture alone controls the age bits,
 * keeps an explicit root and allocates outside any collection transaction.
 */
static int check_old_record(void) {
    if (pcc_gc_backend() != 3) return 20;
    py_gc_disable();
    const int64_t bytes = sizeof(PyBytesObject) + 4;
    PyBytesObject *cell = pcc_allocator_alloc_object(bytes);
    if (!cell) return 21;
    cell->h.refcount = 1;
    cell->h.type_tag = PY_TYPE_BYTES;
    cell->h.flags = PY_FLAG_GC_YOUNG;
    cell->byte_len = 4;
    cell->data[0] = 'p'; cell->data[1] = 'r';
    cell->data[2] = 'o'; cell->data[3] = 'v';
    PyObject *object = (PyObject *)cell;
    if (pcc_allocator_object_publish_fresh(cell) != 1 || !known(object))
        return 22;
    if (pcc_gc_register_object_cell(object, bytes) != 0 ||
        pcc_gc_object_index_find(object))
        return 23;

    PyObject *root = NULL;
    void *root_handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!root_handle) return 24;
    pcc_gc_store_root(&root, object);
    /* GC3's current ordinary indexed objects need neither legacy age bit.
     * This fixture has no other mutator; production would need a genuine
     * no-park commit and fallback when its metadata reserve fails.
     */
    cell->h.flags &= ~PY_FLAG_GC_YOUNG;
    if (pcc_gc_register_object_cell(object, bytes) != 1) return 25;
    void *record = pcc_gc_object_index_find(object);
    void *span = pcc_allocator_object_cell_span(object);
    if (!record || !span || root != object) return 26;
    if (pcc_gc_register_object_cell(object, bytes) != 1 ||
        pcc_gc_object_index_find(object) != record) return 27;
    for (int round = 0; round < 2; ++round) {
        pcc_gc_collect(0);
        if (root != object || !known(object) ||
            pcc_gc_object_index_find(object) != record ||
            pcc_allocator_object_cell_span(object) != span ||
            cell->byte_len != 4 || cell->data[0] != 'p' || cell->data[3] != 'v')
            return 28;
    }
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(root_handle);
    puts("GC3_INDEXLESS_TO_INDEXED_BASELINE_OK");
    return 0;
}

int main(int argc, char **argv) {
    (void)argv;
    return argc == 2 ? check_old_record() : check_unindexed_lifecycle();
}
