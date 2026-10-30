#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

int main(void) {
    for (int backend = 0; backend < 5; backend++) {
        if (pcc_gc_set_backend(backend) != 0) return 10;
        for (int borrowed = 0; borrowed < 2; borrowed++) {
            PyObject *source = py_str_new("private root copy", 17);
            PyObject *destination = 0;
            if (!source) return 11;
            void *source_handle = pcc_gc_scheduler_root_register_handle(&source);
            void *destination_handle = pcc_gc_scheduler_root_register_handle(&destination);
            if (!source_handle || !destination_handle) return 12;
            int64_t refs = ((PyObjectHeader *)source)->refcount;
            int64_t leases = pcc_gc_foreign_lease_count_quiescent();
            if (pcc_gc_root_copy_local(&source, &source, borrowed) != -1) return 13;
            if (pcc_gc_root_copy_local(&destination, &source, 2) != -1) return 14;
            if (pcc_gc_root_copy_local(&destination, &source, borrowed) != 0) return 15;
            if (destination != source) return 16;
            if (((PyObjectHeader *)source)->refcount != refs + 1) return 17;
            if (pcc_gc_foreign_lease_count_quiescent() != leases) return 18;
            if (pcc_gc_root_copy_local(&destination, &source, borrowed) != -4) return 19;
            if (((PyObjectHeader *)source)->refcount != refs + 1) return 20;
            /* Only rooted owners survive the call; no address lease should
               prevent an otherwise legal same-backend reset. */
            if (pcc_gc_set_backend(backend) != 0) return 21;
            pcc_gc_collect(0);
            if (destination != source) return 22;
            pcc_gc_store_root(&destination, 0);
            if (((PyObjectHeader *)source)->refcount != refs) return 23;
            pcc_gc_store_root(&source, 0);
            pcc_gc_scheduler_root_unregister_handle(destination_handle);
            pcc_gc_scheduler_root_unregister_handle(source_handle);
        }
    }
    puts("PRIVATE_ROOT_COPY_OK");
    return 0;
}
