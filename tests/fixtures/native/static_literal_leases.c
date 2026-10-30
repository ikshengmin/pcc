#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int64_t pcc_gc_static_literal_contains(PyObject *);

/* Permanent image storage with the same header and leaf tags as compiler
   literals. Payloads are not traversed by registration or address leases. */
static PyObjectHeader literals[4] = {
    {1, PY_TYPE_STR, 1}, {1, PY_TYPE_BYTES, 1},
    {1, PY_TYPE_INT, 1}, {1, PY_TYPE_FLOAT, 1}
};

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int backend = atoi(argv[1]);
    /* The collector is selected before the first managed allocation and is
       fixed from then on: one process per collector. Image literals leave
       the choice open (checked after the heap literal below). */
    if (pcc_gc_set_backend(backend) != 0) return 10;
    for (int i = 0; i < 4; i++) {
        if (pcc_gc_pointer_register_static_literal((PyObject *)&literals[i]) < 0) return 11;
    }
    if (pcc_gc_backend() != backend) return 12;
    {
        for (int i = 0; i < 4; i++) {
            PyObject *slot = (PyObject *)&literals[i];
            void *handle = pcc_gc_scheduler_root_register_handle(&slot);
            if (!handle) return 13;
            int64_t count = pcc_gc_foreign_lease_count_quiescent();
            int64_t token = pcc_gc_foreign_lease_acquire(&slot);
            if (token != 0) return 14;
            if (pcc_gc_foreign_lease_count_quiescent() != count) return 15;
            if (pcc_gc_object_is_address_pinned(slot) != 1) return 16;
            if (pcc_gc_static_literal_contains((PyObject *)((char *)slot + 8))) return 17;
            if (pcc_gc_set_backend(backend) != 0) return 18;
            pcc_gc_collect(0);
            if (slot != (PyObject *)&literals[i]) return 19;
            if (pcc_gc_foreign_lease_release(&slot, token) != 0) return 20;
            pcc_gc_scheduler_root_unregister_handle(handle);
        }
        /* An allocator-backed immortal scalar: a moving collector counts
           its lease and refuses a reset while it lives. GC0-2 never move it,
           so its lease is the no-op token and a reset proceeds. */
        PyObject *heap = py_str_new("heap literal boundary", 21);
        if (!heap) return 21;
        if (pcc_gc_set_backend((backend + 1) % 5) != -1 || pcc_gc_backend() != backend) return 12;
        ((PyObjectHeader *)heap)->flags |= 1;
        if (pcc_gc_pointer_register_static_literal(heap) < 0) return 22;
        if (pcc_gc_static_literal_contains(heap) != 0) return 23;
        void *handle = pcc_gc_scheduler_root_register_handle(&heap);
        if (!handle) return 24;
        int64_t token = pcc_gc_foreign_lease_acquire(&heap);
        if (backend <= 2) {
            if (token != 0) return 25;
            if (pcc_gc_set_backend(backend) != 0) return 26;
        } else {
            if (token <= 0) return 25;
            if (pcc_gc_set_backend(backend) == 0) return 26;
        }
        if (pcc_gc_foreign_lease_release(&heap, token) != 0) return 27;
        pcc_gc_scheduler_root_unregister_handle(handle);
        ((PyObjectHeader *)heap)->flags &= ~1u;
        py_decref(heap);
    }
    puts("STATIC_LITERAL_LEASES_OK");
    return 0;
}
