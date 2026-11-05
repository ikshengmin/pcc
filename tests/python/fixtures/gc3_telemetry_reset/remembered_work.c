
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

/* Existing strict-runtime exports; the probe adds no production API. */
extern int64_t pcc_gc_cms_stop_worker(void);
extern void *pcc_gc_backend3_remembered_owner_list_head(void);
extern int32_t pcc_gc_backend3_remembered_overflow;
extern void *pcc_gc_backend3_remembered_scan_cursor;
extern int64_t pcc_gc_backend3_remembered_scan_revision;

#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "line %d: %s\n", __LINE__, #test); return 1; \
} } while (0)

struct Pending {
    uintptr_t head;
    uintptr_t cursor;
    int64_t revision;
    int32_t overflow;
    int remembered;
};

static struct Pending pending(PyObject *owner) {
    struct Pending result;
    pcc_py_gc_minor_graph_lock();
    result.head = (uintptr_t)pcc_gc_backend3_remembered_owner_list_head();
    result.cursor = (uintptr_t)pcc_gc_backend3_remembered_scan_cursor;
    result.revision = pcc_gc_backend3_remembered_scan_revision;
    result.overflow = pcc_gc_backend3_remembered_overflow;
    result.remembered =
        (((PyObjectHeader *)owner)->flags & PY_FLAG_GC_REMEMBERED) != 0;
    pcc_py_gc_minor_graph_unlock();
    return result;
}

static int same_pending(struct Pending left, struct Pending right) {
    return left.head == right.head && left.cursor == right.cursor
        && left.revision == right.revision && left.overflow == right.overflow
        && left.remembered == right.remembered;
}

static int no_pending(struct Pending value) {
    return value.head == 0 && value.cursor == 0 && value.revision == 0
        && value.overflow == 0 && value.remembered == 0;
}

static int drain_owner(PyObject **root) {
    for (int step = 0; step < 128; step++) {
        PyObject *owner = pcc_gc_load_ptr(0, root);
        if (!owner) return 0;
        if (no_pending(pending(owner))) return 1;
        (void)pcc_gc_step(1024);
    }
    return 0;
}

static int child_matches(PyObject *owner, int64_t index,
                         int64_t identity, int64_t expected) {
    PyObject *child = py_list_get(owner, index);
    if (!child) return 0;
    int ok = py_obj_id(child) == identity && py_list_len(child) == 1;
    int32_t flags = ((PyObjectHeader *)child)->flags;
    ok = ok && (flags & PY_FLAG_GC_OLD) && !(flags & PY_FLAG_GC_YOUNG);
    if (ok) {
        PyObject *value = py_list_get(child, 0);
        if (!value) {
            ok = 0;
        } else {
            int overflow = 0;
            int64_t actual = py_int_to_i64(value, &overflow);
            ok = !overflow && actual == expected;
            pcc_gc_release(value);
        }
    }
    pcc_gc_release(child);
    return ok;
}

int main(int argc, char **argv) {
    CHECK(argc == 3);
    int overflow = atoi(argv[1]);
    int resets = atoi(argv[2]);
    CHECK((overflow == 0 || overflow == 1) && resets >= 0 && resets <= 2);
    /* Disable automatic assistance, then establish a no-worker condition.
     * The lifecycle call may only reserve idle state, so this is not a join
     * regression. Never retry a refused/uncertain lifecycle operation. */
    py_gc_disable();
    CHECK(pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) == 0);
    CHECK(pcc_threads_enabled() == 1);
    CHECK(pcc_gc_cms_stop_worker() == 0);

    PyObject *owner_root = 0;
    pcc_gc_scheduler_root_register(&owner_root);
    pcc_gc_store_root(&owner_root, py_list_new(2));
    CHECK(owner_root != 0);
    /* Establish OLD through the collector, not a fabricated header flag. */
    for (int step = 0; step < 64; step++) {
        PyObject *owner = pcc_gc_load_ptr(0, &owner_root);
        if (((PyObjectHeader *)owner)->flags & PY_FLAG_GC_OLD) break;
        (void)pcc_gc_step(1024);
    }
    PyObject *owner = pcc_gc_load_ptr(0, &owner_root);
    CHECK((((PyObjectHeader *)owner)->flags & PY_FLAG_GC_OLD) != 0);
    CHECK(no_pending(pending(owner)));
    int64_t owner_identity = py_obj_id(owner);
    CHECK(owner_identity != 0);

    if (overflow) {
        /* Newer graph nodes precede this old owner. One 16-node scan slice
         * must leave work and a non-null cursor for reset to preserve. */
        for (int i = 0; i < 40; i++) CHECK(py_list_new(0) != 0);
        pcc_gc_backend3_remembered_scan_probe_config(0);
    }
    PyObject *child = py_list_new(1);
    CHECK(child != 0);
    CHECK((((PyObjectHeader *)child)->flags & PY_FLAG_GC_YOUNG) != 0);
    py_list_append(child, py_int_from_i64(101));
    int64_t first_identity = py_obj_id(child);
    CHECK(first_identity != 0);
    py_list_append(owner, child);
    CHECK(pending(owner).remembered);
    if (overflow) {
        CHECK(pending(owner).overflow == 1);
        (void)pcc_gc_step(1024);
        owner = pcc_gc_load_ptr(0, &owner_root);
        CHECK(pending(owner).remembered);
        CHECK(pending(owner).cursor != 0);
        CHECK(pending(owner).revision != 0);
    } else {
        CHECK(pending(owner).head != 0);
        CHECK(pending(owner).overflow == 0);
    }

    struct Pending before = pending(owner);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_WORK_STEPS) > 0);
    for (int reset = 0; reset < resets; reset++) {
        pcc_gc_telemetry_reset();
        CHECK(same_pending(before, pending(owner)));
        CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_WORK_STEPS) == 0);
    }
    CHECK(drain_owner(&owner_root));
    owner = pcc_gc_load_ptr(0, &owner_root);
    CHECK(py_obj_id(owner) == owner_identity);
    CHECK(child_matches(owner, 0, first_identity, 101));

    /* After normal retirement, a fresh young edge must enqueue again.
     * Retained C locals are intentional: this checks queue handoff, not
     * collection of objects reachable solely through the old owner. */
    pcc_gc_backend3_remembered_scan_probe_config(-1);
    owner = pcc_gc_load_ptr(0, &owner_root);
    child = py_list_new(1);
    CHECK(child != 0);
    CHECK((((PyObjectHeader *)child)->flags & PY_FLAG_GC_YOUNG) != 0);
    py_list_append(child, py_int_from_i64(202));
    int64_t second_identity = py_obj_id(child);
    CHECK(second_identity != 0 && second_identity != first_identity);
    py_list_append(owner, child);
    CHECK(pending(owner).remembered);
    CHECK(pending(owner).head != 0);
    CHECK(drain_owner(&owner_root));
    owner = pcc_gc_load_ptr(0, &owner_root);
    /* Extra normal steps must keep the complete remembered state empty and
     * preserve identity, values and observed OLD state. Young-list scanning
     * also promotes objects, so promotion is not attributed only to this
     * remembered queue. This is not a reclamation or sole-root proof. */
    for (int stable = 0; stable < 4; stable++) {
        CHECK(no_pending(pending(owner)));
        CHECK(py_obj_id(owner) == owner_identity);
        CHECK(py_list_len(owner) == 2);
        CHECK(child_matches(owner, 0, first_identity, 101));
        CHECK(child_matches(owner, 1, second_identity, 202));
        (void)pcc_gc_step(1024);
        owner = pcc_gc_load_ptr(0, &owner_root);
    }
    CHECK(no_pending(pending(owner)));
    CHECK(py_obj_id(owner) == owner_identity);
    CHECK(py_list_len(owner) == 2);
    CHECK(child_matches(owner, 0, first_identity, 101));
    CHECK(child_matches(owner, 1, second_identity, 202));

    pcc_gc_store_root(&owner_root, 0);
    pcc_gc_scheduler_root_unregister(&owner_root);
    puts("gc3-reset-remembered-native-ok");
    return 0;
}
