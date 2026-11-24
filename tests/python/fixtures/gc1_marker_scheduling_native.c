/* A registered scheduler slot owns the object across explicit collections. */
#include "py_runtime.h"
#include <stdio.h>

int main(void) {
    if (pcc_threads_enabled() != 1) return 1;
    PyObject *root = NULL;
    void *root_handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!root_handle) return 2;
    PyObject *value = py_list_new(0);
    if (!value) return 3;
    pcc_gc_store_root(&root, value);
    py_decref(value);
    if (root != value) return 4;
    pcc_gc_collect(0);
    pcc_gc_collect(0);
    if (root != value || !pcc_gc_pointer_is_managed(root)) return 5;
    pcc_gc_store_root(&root, NULL);
    if (root != NULL) return 6;
    pcc_gc_scheduler_root_unregister_handle(root_handle);
    puts("SCHEDULER_ROOT_LIFETIME_OK");
    return 0;
}
