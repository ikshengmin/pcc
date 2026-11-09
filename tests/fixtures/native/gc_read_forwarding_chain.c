/* Public forwarding-ABI chain coverage, not automatic evacuation or retirement.
 * Each process performs exactly one selected read on a fresh raw source alias.
 * Keep all sources alive; do not collect, free source storage, clear pin bits,
 * or disable conservative capture to manufacture forwarding admission.
 */
#include "../src/py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_relocation_barrier_forwards;

enum { MAX_EDGES = 16, CLASS_ROOT = 17, OWNER_ROOT = 18, ROOT_COUNT = 19 };
enum { DIRECT = 0, OWNING_SLOT = 1, BORROWED_SLOT = 2,
       OWNED_VALUE = 3, WEAKREF = 4 };

static int fail(int code, const char *message) {
    fprintf(stderr, "GC_READ_CHAIN_FAILED %d: %s\n", code, message);
    return code;
}

int main(int argc, char **argv) {
    if (argc != 3) return fail(10, "expected edge count and read kind");
    int edges = atoi(argv[1]);
    int kind = atoi(argv[2]);
    if (edges < 0 || edges > MAX_EDGES || kind < DIRECT || kind > WEAKREF)
        return fail(11, "invalid arguments");
    int backend = (int)pcc_gc_backend();
    if (backend < 0 || backend > 4) return fail(12, "unexpected backend");
    if (backend >= 3 && pcc_gc_backend4_no_refcounts != 1)
        return fail(13, "moving test requires actual NoRC mode");
    if (backend < 3 && edges != 0)
        return fail(14, "nonmoving control cannot install chains");
    /* GC3's public installer whitelist excludes user instances. Do not use
     * a float/tuple as a fictitiously Python-weakrefable substitute. */
    if (backend == 3 && kind == WEAKREF && edges != 0)
        return fail(15, "GC3 cannot publicly forward user instances");

    PyObject *roots[ROOT_COUNT] = {0};
    void *handles[ROOT_COUNT] = {0};
    for (int i = 0; i < ROOT_COUNT; i++) {
        handles[i] = pcc_gc_scheduler_root_register_handle(&roots[i]);
        if (!handles[i]) return fail(16, "root registration failed");
    }
    if (kind == WEAKREF) {
        PyObject *cls = (PyObject *)py_class_new("ReadChainTarget", 0, 0, 0, 0);
        if (!cls) return fail(17, "ordinary user class allocation failed");
        pcc_gc_store_root(&roots[CLASS_ROOT], cls);
        pcc_gc_release(cls);
    }
    for (int i = 0; i <= edges; i++) {
        PyObject *value = kind == WEAKREF
            ? py_instance_new((PyClassObject *)roots[CLASS_ROOT])
            : py_float_from_f64(42.5);
        if (!value) return fail(18, "target allocation failed");
        pcc_gc_store_root(&roots[i], value);
        pcc_gc_release(value);
    }
    PyObject *owner = kind == WEAKREF
        ? py_weakref_new(roots[0], 0) : py_tuple_new(1);
    if (!owner) return fail(19, "slot owner allocation failed");
    if (kind != WEAKREF) py_tuple_set_item(owner, 0, roots[0]);
    pcc_gc_store_root(&roots[OWNER_ROOT], owner);
    pcc_gc_release(owner);
    /* This raw local is borrowed for BORROWED_SLOT and owns an extra hold for
     * OWNED_VALUE. Initialize it before forwarding so setup cannot heal it. */
    PyObject *input = roots[0];
    if (kind == OWNED_VALUE) pcc_gc_retain(input);

    if (backend >= 3 && edges > 0) {
        pcc_gc_pin(roots[0]);
        if (!pcc_gc_object_is_address_pinned(roots[0]))
            return fail(20, "manual pin was not observed");
        if (pcc_gc_install_forwarding(roots[0], roots[1]) != -2)
            return fail(21, "installer admitted a pinned source");
        pcc_gc_unpin(roots[0]);
        for (int i = 0; i < edges; i++) {
            if (pcc_gc_object_is_address_pinned(roots[i]))
                return fail(22, "source still pinned: chain not admitted");
            if (pcc_gc_install_forwarding(roots[i], roots[i + 1]) != 0)
                return fail(23, "public installer rejected chain");
        }
    } else if (backend < 3) {
        if (pcc_gc_install_forwarding(roots[0], roots[OWNER_ROOT]) != -1)
            return fail(24, "nonmoving installer must reject");
    }

    pcc_py_gc_minor_graph_lock();
    int64_t refs[MAX_EDGES + 1];
    for (int i = 0; i <= edges; i++) {
        /* GC0 has no ObjectNode index; allocator flags are GC3/4-only. */
        if (backend != 0 && !pcc_gc_object_is_known_no_lock(roots[i]))
            return fail(25, "target lacks live allocator registration");
        int32_t flags = ((PyObjectHeader *)roots[i])->flags;
        if (backend >= 3 && !(flags & (PY_FLAG_GC_MINOR_ARENA |
                                      PY_FLAG_GC_ZPAGE_ALLOC |
                                      PY_FLAG_GC_MALLOC_ALLOC)))
            return fail(26, "target lacks moving allocator provenance");
        void *edge = pcc_gc_forwarding_index_find(roots[i]);
        if (i < edges) {
            if (!edge || ((PyObject **)edge)[1] != roots[i + 1])
                return fail(27, "installer did not preserve complete chain");
        } else if (edge) {
            return fail(28, "expected nonforwarded terminal");
        }
        refs[i] = ((PyObjectHeader *)roots[i])->refcount;
    }
    owner = roots[OWNER_ROOT];
    PyObject **slot = kind == WEAKREF ? &((PyWeakRefObject *)owner)->target
                                     : &((PyTupleObject *)owner)->items[0];
    if (*slot != roots[0] || input != roots[0])
        return fail(29, "setup unexpectedly healed source alias");
    int32_t before = pcc_gc_relocation_barrier_forwards;
    PyObject *result = 0;
    if (kind == DIRECT) {
        result = pcc_gc_note_relocation_read(input);
    } else if (kind == OWNING_SLOT) {
        result = pcc_gc_load_ptr(owner, slot);
        if (*slot != roots[edges])
            return fail(30, "first owning load did not heal to terminal");
    } else if (kind == BORROWED_SLOT) {
        result = pcc_gc_load_borrowed_ptr(0, &input);
        if (input != roots[edges])
            return fail(31, "first borrowed load did not heal to terminal");
    } else if (kind == OWNED_VALUE) {
        result = pcc_gc_resolve_owned_ptr(input);
    } else {
        result = py_weakref_call(owner);
        if (*slot != roots[edges])
            return fail(32, "first weakref call did not heal to terminal");
    }
    /* No second barrier, getter, release, or root resolver precedes these
     * raw comparisons: a prefix result cannot be repaired by the assertion. */
    if (result != roots[edges]) return fail(33, "first read returned a prefix");
    if (pcc_gc_relocation_barrier_forwards - before != (edges > 0 ? 1 : 0))
        return fail(34, "forward counter is not one per logical read");
    if (backend >= 3) {
        for (int i = 0; i <= edges; i++) {
            if (((PyObjectHeader *)roots[i])->refcount != refs[i])
                return fail(35, "NoRC read changed a reference count");
        }
    }
    pcc_py_gc_minor_graph_unlock();
    /* Exit with registered owners intact. This does not claim retirement,
     * reclamation, concurrent read scheduling, pcc1, or bootstrap coverage. */
    puts("GC_READ_CHAIN_NATIVE_OK");
    return 0;
}
