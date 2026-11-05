#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

/* Existing runtime symbols only. These declarations add no runtime API. */
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_generational_step(int64_t budget, int64_t promote_all_young);
extern int64_t pcc_gc_backend3_promotion_cursor(void *node);
extern void *pcc_gc_backend3_promotion_head;
extern void *pcc_gc_backend3_promotion_tail;
extern void *pcc_gc_backend3_remembered_owner_list_head(void);
extern int32_t pcc_gc_backend3_remembered_overflow;
extern void *pcc_gc_backend3_remembered_scan_cursor;
extern int64_t pcc_gc_backend3_remembered_scan_revision;

#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "line %d: %s\n", __LINE__, #test); return 1; \
} } while (0)

enum { OWNER_LENGTH = 65, STEP_BUDGET = 4, MAX_STEPS = 128 };

struct Pending {
    void *owner_node;
    void *promotion_head;
    void *promotion_tail;
    int64_t promotion_cursor;
    void *remembered_head;
    void *remembered_cursor;
    int64_t remembered_revision;
    int32_t overflow;
    int32_t owner_flags;
};

static struct Pending pending(PyObject *owner) {
    struct Pending result;
    pcc_py_gc_minor_graph_lock();
    result.owner_node = pcc_gc_object_index_find(owner);
    result.promotion_head = pcc_gc_backend3_promotion_head;
    result.promotion_tail = pcc_gc_backend3_promotion_tail;
    result.promotion_cursor = result.owner_node
        ? pcc_gc_backend3_promotion_cursor(result.owner_node) : -1;
    result.remembered_head = pcc_gc_backend3_remembered_owner_list_head();
    result.remembered_cursor = pcc_gc_backend3_remembered_scan_cursor;
    result.remembered_revision = pcc_gc_backend3_remembered_scan_revision;
    result.overflow = pcc_gc_backend3_remembered_overflow;
    result.owner_flags = ((PyObjectHeader *)owner)->flags;
    pcc_py_gc_minor_graph_unlock();
    return result;
}

static int same_pending(struct Pending left, struct Pending right) {
    return left.owner_node == right.owner_node
        && left.promotion_head == right.promotion_head
        && left.promotion_tail == right.promotion_tail
        && left.promotion_cursor == right.promotion_cursor
        && left.remembered_head == right.remembered_head
        && left.remembered_cursor == right.remembered_cursor
        && left.remembered_revision == right.remembered_revision
        && left.overflow == right.overflow
        && left.owner_flags == right.owner_flags;
}

static int idle(struct Pending value) {
    return value.owner_node != 0 && value.promotion_head == 0
        && value.promotion_tail == 0 && value.promotion_cursor == 0
        && value.remembered_head == 0 && value.remembered_cursor == 0
        && value.remembered_revision == 0 && value.overflow == 0
        && !(value.owner_flags & PY_FLAG_GC_REMEMBERED);
}

static int step(void) {
    if (pcc_thread_owns_stopped_world() != 0) return 0;
    /* The real nursery-style entry, with whole-young-list scanning off. */
    (void)pcc_gc_generational_step(STEP_BUDGET, 0);
    return pcc_thread_owns_stopped_world() == 0;
}

static int young(PyObject *value) {
    int32_t flags = ((PyObjectHeader *)value)->flags;
    return (flags & PY_FLAG_GC_YOUNG) && !(flags & PY_FLAG_GC_OLD);
}

static int old_child_matches(PyObject *owner, int64_t index,
                             int64_t identity, double expected) {
    PyObject *child = py_list_get(owner, index);
    if (!child) return 0;
    int32_t flags = ((PyObjectHeader *)child)->flags;
    int ok = py_obj_id(child) == identity && py_float_to_f64(child) == expected
        && (flags & PY_FLAG_GC_OLD) && !(flags & PY_FLAG_GC_YOUNG);
    pcc_gc_release(child);
    return ok;
}

int main(int argc, char **argv) {
    CHECK(argc == 3);
    int mode = atoi(argv[1]);  /* 0: unchanged, 1: prefix, 2: suffix. */
    int resets = atoi(argv[2]);
    CHECK((mode == 0 && resets == 0) || (mode == 2 && resets == 0)
          || (mode == 1 && (resets == 0 || resets == 2)));

    /* Disable automatic assistance, then establish a no-worker condition.
     * This may reserve an idle worker state; it does not prove a real join.
     * A refused lifecycle call is a failure, never retried. The driver uses
     * MINOR_ALLOC_MAX=16 so these list/float allocations cannot cause an
     * independent nursery-refill step, which py_gc_disable does not disable. */
    py_gc_disable();
    CHECK(pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) == 0);
    CHECK(pcc_threads_enabled() == 1);
    CHECK(pcc_gc_cms_stop_worker() == 0);
    CHECK(pcc_thread_owns_stopped_world() == 0);

    /* Only the list has an explicit scheduler root. No child is registered.
     * Raw C locals can pin an address conservatively; that is not promotion.
     * The independent young sentinel below detects an accidental broad scan. */
    PyObject *owner_root = 0;
    pcc_gc_scheduler_root_register(&owner_root);
    pcc_gc_store_root(&owner_root, py_list_new(OWNER_LENGTH));
    CHECK(owner_root != 0);
    PyObject *owner = pcc_gc_load_ptr(0, &owner_root);
    for (int index = 0; index < OWNER_LENGTH; index++) {
        py_list_append(owner, py_int_from_i64(index + 1000));
    }
    CHECK(py_list_len(owner) == OWNER_LENGTH);

    /* Obtain OLD and an initially drained worklist only through GC steps. */
    for (int count = 0; count < MAX_STEPS; count++) {
        struct Pending current = pending(owner);
        if ((current.owner_flags & PY_FLAG_GC_OLD) && idle(current)) break;
        CHECK(step());
        owner = pcc_gc_load_ptr(0, &owner_root);
    }
    CHECK((((PyObjectHeader *)owner)->flags & PY_FLAG_GC_OLD) != 0);
    /* GC3 uses 0x800 for a forwarded source. A canonical OLD list root
     * does not retrace children; a forwarded root alias could mask a miss. */
    CHECK(!(((PyObjectHeader *)owner)->flags
            & (PY_FLAG_GC_YOUNG | PY_FLAG_GC_RELOCATION_CANDIDATE)));
    CHECK(idle(pending(owner)));
    int64_t owner_identity = py_obj_id(owner);
    CHECK(owner_identity != 0);

    PyObject *first = py_float_from_f64(101.25);
    CHECK(first != 0 && young(first));
    int64_t first_identity = py_obj_id(first);
    CHECK(first_identity != 0);
    py_list_set(owner, 0, first);
    CHECK(young(first));
    CHECK(pending(owner).owner_flags & PY_FLAG_GC_REMEMBERED);
    CHECK(pending(owner).remembered_head != 0);
    CHECK(step());
    owner = pcc_gc_load_ptr(0, &owner_root);
    struct Pending partial = pending(owner);
    CHECK(partial.owner_node != 0 && partial.promotion_head == partial.owner_node);
    CHECK(partial.promotion_cursor > 0 && partial.promotion_cursor < OWNER_LENGTH - 1);
    CHECK(partial.remembered_head == 0 && partial.overflow == 0);
    CHECK(!(partial.owner_flags & PY_FLAG_GC_REMEMBERED));
    CHECK(old_child_matches(owner, 0, first_identity, 101.25));

    int64_t changed_index = mode == 2 ? OWNER_LENGTH - 1 : 0;
    int64_t changed_identity = first_identity;
    double changed_value = 101.25;
    if (mode != 0) {
        CHECK(pcc_thread_owns_stopped_world() == 0);
        PyObject *child = py_float_from_f64(202.5);
        CHECK(child != 0 && young(child));
        changed_identity = py_obj_id(child);
        changed_value = 202.5;
        CHECK(changed_identity != 0 && changed_identity != first_identity);
        /* This is the production list transaction and production barrier,
         * between complete steps. No raw slot/flags/cursor store is used. */
        py_list_set(owner, changed_index, child);
        CHECK(young(child));
        CHECK(pcc_thread_owns_stopped_world() == 0);
        struct Pending dirty = pending(owner);
        CHECK(dirty.owner_node == partial.owner_node);
        CHECK(dirty.promotion_head == partial.promotion_head);
        CHECK(dirty.promotion_cursor == partial.promotion_cursor);
        CHECK(dirty.owner_flags & PY_FLAG_GC_REMEMBERED);
        CHECK(dirty.remembered_head != 0 && dirty.overflow == 0);
        pcc_gc_release(child);
    }

    /* No owning edge and no registered root lead to this separate scalar.
     * It must remain YOUNG during every explicit bounded step, so neither
     * promote-all-young nor promotion of arbitrary C locals can mask a miss.
     * This is not a sole-root lifetime or reclamation test. */
    PyObject *sentinel = py_float_from_f64(303.75);
    CHECK(sentinel != 0 && young(sentinel));
    int64_t sentinel_identity = py_obj_id(sentinel);
    struct Pending before_reset = pending(owner);
    CHECK(before_reset.promotion_cursor == partial.promotion_cursor);
    for (int reset = 0; reset < resets; reset++) {
        pcc_gc_telemetry_reset();
        CHECK(same_pending(before_reset, pending(owner)));
        CHECK(young(sentinel));
    }

    for (int count = 0; count < MAX_STEPS; count++) {
        if (idle(pending(owner))) break;
        CHECK(step());
        owner = pcc_gc_load_ptr(0, &owner_root);
        CHECK(young(sentinel));
    }
    CHECK(idle(pending(owner)));
    CHECK(py_obj_id(owner) == owner_identity);
    CHECK(py_list_len(owner) == OWNER_LENGTH);
    CHECK(old_child_matches(owner, changed_index, changed_identity, changed_value));
    if (mode == 2) CHECK(old_child_matches(owner, 0, first_identity, 101.25));
    CHECK(py_obj_id(sentinel) == sentinel_identity);
    CHECK(py_float_to_f64(sentinel) == 303.75 && young(sentinel));

    /* More normal steps leave both queues and the owner's cursor empty. */
    for (int stable = 0; stable < 4; stable++) {
        CHECK(step());
        owner = pcc_gc_load_ptr(0, &owner_root);
        CHECK(idle(pending(owner)));
        CHECK(py_obj_id(owner) == owner_identity);
        CHECK(py_list_len(owner) == OWNER_LENGTH);
        CHECK(old_child_matches(owner, changed_index, changed_identity, changed_value));
        if (mode == 2) CHECK(old_child_matches(owner, 0, first_identity, 101.25));
        CHECK(py_obj_id(sentinel) == sentinel_identity);
        CHECK(py_float_to_f64(sentinel) == 303.75 && young(sentinel));
    }

    pcc_gc_release(first);
    pcc_gc_release(sentinel);
    pcc_gc_store_root(&owner_root, 0);
    pcc_gc_scheduler_root_unregister(&owner_root);
    puts("gc3-prefix-rescan-native-ok");
    return 0;
}
