/* Native runtime ABI coverage, not an automatic-relocation reproduction.
 * The public forwarding installer permits chains even though automatic
 * relocation normally excludes incoming targets. Exercise the real remap
 * entrypoints before any retirement or ordinary read barrier can hide a
 * partial rewrite. This deliberately retains raw source aliases; it proves
 * neither a conservative-root-free retirement cut nor allocator reclamation.
 */
#include "../src/py_internal.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int32_t pcc_gc_backend4_no_refcounts;
extern void pcc_gc_backend4_remap_heal_slot(void *base, int64_t offset);
extern void pcc_gc_backend4_remap_slot(void *slot, int64_t role, void *context);
extern void pcc_gc_backend4_remap_referents(PyObject *object);

enum { MAX_EDGES = 16, OWNER_COUNT = 3, ROOT_COUNT = 20 };

static int fail(int code, const char *message) {
    fprintf(stderr, "GC_REMAP_CHAIN_FAILED %d: %s\n", code, message);
    return code;
}

int main(int argc, char **argv) {
    if (argc != 2) return fail(10, "expected edge count");
    int edges = atoi(argv[1]);
    if (edges < 0 || edges > MAX_EDGES) return fail(11, "invalid edge count");
    int backend = (int)pcc_gc_backend();
    if (backend < 0 || backend > 4) return fail(12, "unexpected backend");
    if (backend >= 3 && pcc_gc_backend4_no_refcounts != 1)
        return fail(13, "moving test requires actual NoRC mode");
    if (backend < 3 && edges != 0)
        return fail(14, "nonmoving control cannot install chains");

    PyObject *roots[ROOT_COUNT] = {0};
    void *handles[ROOT_COUNT] = {0};
    int used = edges + 1 + OWNER_COUNT;
    for (int i = 0; i < used; i++) {
        handles[i] = pcc_gc_scheduler_root_register_handle(&roots[i]);
        if (!handles[i]) return fail(15, "root registration failed");
    }
    /* All nodes contain the same scalar value, as copies of one logical
     * object would. No stores/root resolves are needed after installation. */
    for (int i = 0; i <= edges; i++) {
        PyObject *value = py_float_from_f64(42.5);
        if (!value) return fail(16, "scalar allocation failed");
        pcc_gc_store_root(&roots[i], value);
        pcc_gc_release(value);
    }
    for (int i = 0; i < OWNER_COUNT; i++) {
        PyObject *owner = py_tuple_new(1);
        if (!owner) return fail(17, "ordinary heap owner allocation failed");
        py_tuple_set_item(owner, 0, roots[0]);
        pcc_gc_store_root(&roots[edges + 1 + i], owner);
        pcc_gc_release(owner);
    }

    if (backend >= 3 && edges > 0) {
        /* Test the real installer admission gate, never clear node pin bits
         * or disable conservative capture to manufacture admission. */
        pcc_gc_pin(roots[0]);
        if (pcc_gc_object_is_address_pinned(roots[0]) == 0)
            return fail(18, "manual pin was not observed");
        if (pcc_gc_install_forwarding(roots[0], roots[1]) != -2)
            return fail(19, "installer admitted a pinned source");
        pcc_gc_unpin(roots[0]);
        for (int i = 0; i < edges; i++) {
            if (pcc_gc_object_is_address_pinned(roots[i]) != 0)
                return fail(20, "source still pinned: chain not admitted");
            if (pcc_gc_install_forwarding(roots[i], roots[i + 1]) != 0)
                return fail(21, "public installer rejected chain");
        }
    } else if (backend < 3) {
        if (pcc_gc_install_forwarding(roots[0], roots[1]) != -1)
            return fail(22, "nonmoving installer must reject");
    }

    pcc_py_gc_minor_graph_lock();
    for (int i = 0; i <= edges; i++) {
        /* GC0 deliberately has no ObjectNode allocation index. */
        if (backend != 0 && (!pcc_gc_object_index_find(roots[i]) ||
                            !pcc_gc_object_is_known_no_lock(roots[i])))
            return fail(23, "chain node lacks live allocator registration");
        int32_t flags = ((PyObjectHeader *)roots[i])->flags;
        /* pcc_gc_alloc records these arena-vs-fallback bits only for GC3/4;
         * nonmoving allocations use structural pointer provenance instead. */
        if (backend >= 3 &&
            !(flags & (PY_FLAG_GC_MINOR_ARENA | PY_FLAG_GC_ZPAGE_ALLOC |
                       PY_FLAG_GC_MALLOC_ALLOC)))
            return fail(24, "chain node has no allocator provenance");
        void *edge = pcc_gc_forwarding_index_find(roots[i]);
        if (i < edges) {
            if (!edge || ((PyObject **)edge)[1] != roots[i + 1])
                return fail(25, "public installer did not preserve full chain");
        } else if (edge) {
            return fail(26, "expected nonforwarded terminal");
        }
    }
    int64_t refs[MAX_EDGES + 1];
    for (int i = 0; i <= edges; i++)
        refs[i] = ((PyObjectHeader *)roots[i])->refcount;
    for (int entry = 0; entry < OWNER_COUNT; entry++) {
        PyTupleObject *owner = (PyTupleObject *)roots[edges + 1 + entry];
        if ((backend != 0 && !pcc_gc_object_is_known_no_lock((PyObject *)owner)) ||
            (owner->h.flags & (PY_FLAG_GC_RELOCATION_CANDIDATE |
                               PY_FLAG_GC_DEALLOCATING)) ||
            owner->len != 1 || owner->items[0] != roots[0])
            return fail(27, "expected live nonforwarded owner with raw source field");
        if (entry == 0)
            pcc_gc_backend4_remap_heal_slot(owner, offsetof(PyTupleObject, items));
        else if (entry == 1)
            pcc_gc_backend4_remap_slot(&owner->items[0], 1, 0);
        else
            pcc_gc_backend4_remap_referents((PyObject *)owner);
        /* The first raw field inspection after ONE remap must be terminal.
         * No pcc_gc_load_ptr, tuple getter, lease or root resolver precedes it. */
        if (owner->items[0] != roots[edges])
            return fail(28 + entry, "first remap left an intermediate source");
        for (int i = 0; i <= edges; i++) {
            if (((PyObjectHeader *)roots[i])->refcount != refs[i])
                return fail(31, "remap unexpectedly changed reference counts");
        }
    }
    pcc_py_gc_minor_graph_unlock();
    /* Each process exits here with roots intact. Do not masquerade this as a
     * retirement test by collecting while stack aliases still name sources. */
    puts("GC_REMAP_CHAIN_NATIVE_OK");
    return 0;
}
