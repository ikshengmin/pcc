#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern void *pcc_allocator_alloc_object(int64_t);
extern void pcc_allocator_lock_acquire(void);
extern void pcc_allocator_lock_release(void);
extern void *pcc_allocator_take_small_object(int64_t);
extern void pcc_allocator_put_small_object(void *, int64_t);

static int64_t origin(void *p) { return *(int64_t *)((char *)p - 32); }
static void set_origin(void *p, int64_t value) { *(int64_t *)((char *)p - 32) = value; }

int main(void) {
    for (int backend = 0; backend < 5; backend++) {
        if (pcc_gc_set_backend(backend) != 0) return 10;
        void *object = pcc_allocator_alloc_object(64);
        if (!object || origin(object) != -1) return 11;
        if (pcc_gc_granule_is_object_start(object) != -1) return 12;
        memset(object, 0, 64);
        ((PyObjectHeader *)object)->refcount = 1;
        ((PyObjectHeader *)object)->type_tag = PY_TYPE_INT;
        if (pcc_gc_pointer_register(object) < 0) return 13;
        if (pcc_gc_granule_is_object_start(object) != 1) return 14;
        if (pcc_gc_pointer_unregister(object) < 0) return 15;
        if (pcc_gc_granule_is_object_start(object) != -1) return 16;
        free(object);

        /* Simulate a valid externally initialized, unmarked object cell.
           The original authoritative lookup must still route it correctly. */
        void *legacy = pcc_allocator_alloc_object(64);
        if (!legacy) return 17;
        set_origin(legacy, 0);
        free(legacy);
        pcc_allocator_lock_acquire();
        void *recovered = pcc_allocator_take_small_object(64);
        if (recovered != legacy || origin(recovered) != -1) return 18;
        pcc_allocator_put_small_object(recovered, 64);
        pcc_allocator_lock_release();

        /* Neither a Python-looking header nor the new private prefix hint
           may make a raw buffer pass the managed-object provenance check. */
        void *raw = malloc(64);
        if (!raw || origin(raw) != 0) return 19;
        memset(raw, 0, 64);
        ((PyObjectHeader *)raw)->refcount = 1;
        ((PyObjectHeader *)raw)->type_tag = PY_TYPE_INT;
        set_origin(raw, -1);
        if (pcc_gc_granule_is_object_start(raw) == 1) return 20;
        if (pcc_gc_pointer_is_managed(raw) != 0) return 21;
        set_origin(raw, 0);
        raw = realloc(raw, 256);
        if (!raw || origin(raw) != 0) return 22;
        free(raw);

        void *large = pcc_allocator_alloc_object(20000);
        if (!large || origin(large) <= 0) return 23;
        free(large);
    }
    puts("OBJECT_ORIGIN_CACHE_OK");
    return 0;
}
