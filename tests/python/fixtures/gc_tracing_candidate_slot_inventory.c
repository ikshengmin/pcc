/* Owned native contract probe: real slots and candidate-list storage, with
 * the stopped fixture supplying the verdicts a finish would have produced. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

typedef void (*SlotVisitor)(PyObject **, int64_t, void *);
extern int64_t pcc_gc_visit_object_slots_slice(
    PyObject *, int64_t, int64_t, SlotVisitor, void *, void *);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern PyObject *py_class_new(const char *, void *, int32_t, void *, int32_t);
extern PyObject *py_instance_new(void *);
extern void pcc_gc_tracing_candidates_reset(void);
extern void pcc_gc_tracing_candidates_note(void *);
extern void pcc_gc_tracing_candidates_seal(int64_t);
extern void pcc_gc_tracing_candidates_retire(void);
extern int64_t pcc_gc_tracing_candidate_count, pcc_gc_tracing_candidate_commit;
extern void **pcc_gc_tracing_candidate_nodes;

enum { SLOT_OWNING = 1, SLOT_OWNING_TYPE = 2, SLOT_DERIVED_OR_WEAK = 3 };
enum { NEXT_CURSOR = 0, EXAMINED = 1, STATE_WORDS = 2, DONE = -1 };
enum { SEALED = 7 };

struct Observed {
    PyObject *target, *callback, *outside, *cls;
    int64_t owning_target, weak_target, owning_callback, owning_outside, owning_type, calls;
};

static PyObject *never_call(void) { return NULL; }

static void observe(PyObject **slot, int64_t role, void *context) {
    struct Observed *o = context;
    o->calls += 1;
    if (*slot == o->cls && role == SLOT_OWNING_TYPE) o->owning_type += 1;
    if (*slot == o->target && role == SLOT_OWNING) o->owning_target += 1;
    if (*slot == o->target && role == SLOT_DERIVED_OR_WEAK) o->weak_target += 1;
    if (*slot == o->callback && role == SLOT_OWNING) o->owning_callback += 1;
    if (*slot == o->outside && role == SLOT_OWNING) o->owning_outside += 1;
}

static int64_t visit_slots(PyObject *owner, struct Observed *o) {
    int64_t cursor = 0, examined = 0;
    do {
        int64_t state[STATE_WORDS] = {0, 0};
        if (pcc_gc_visit_object_slots_slice(owner, cursor, 1, observe, o, state) != 1
            || state[EXAMINED] != 1
            || (state[NEXT_CURSOR] != DONE && state[NEXT_CURSOR] != cursor + 1))
            return -1;
        examined += state[EXAMINED];
        cursor = state[NEXT_CURSOR];
    } while (cursor != DONE);
    return examined;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    if (backend < 1 || backend > 4) return 1;
    py_gc_disable();
    PyObject *owner = py_list_new(32);
    PyObject *cls = py_class_new("CandidateTarget", NULL, 0, NULL, 0);
    PyObject *child = cls ? py_instance_new(cls) : NULL;
    PyObject *outside = py_list_new(0);
    PyObject *callback = py_func_new((void *)never_call, NULL);
    if (!owner || !child || !outside || !callback) return 2;
    PyObject *weakref = py_weakref_new(child, callback);
    if (!weakref) return 3;
    py_list_append(owner, child);
    py_list_append(owner, child);
    py_list_append(owner, outside);
    if (pcc_stop_the_world() != 0 || pcc_thread_world_stop_epoch_held() < 0) return 4;
    pcc_py_gc_minor_graph_lock();
    void *owner_node = pcc_gc_object_index_find(owner);
    void *child_node = pcc_gc_object_index_find(child);
    void *weakref_node = pcc_gc_object_index_find(weakref);
    if (!owner_node || !child_node || !weakref_node) return 5;
    pcc_gc_tracing_candidates_reset();
    pcc_gc_tracing_candidates_note(owner_node);
    pcc_gc_tracing_candidates_note(child_node);
    pcc_gc_tracing_candidates_note(weakref_node);

    if (pcc_gc_tracing_candidate_commit >= 0) return 6;
    pcc_gc_tracing_candidates_seal(SEALED);
    if (pcc_gc_tracing_candidate_commit != SEALED || pcc_gc_tracing_candidate_count != 3
        || pcc_gc_tracing_candidate_nodes[0] != owner_node
        || pcc_gc_tracing_candidate_nodes[1] != child_node
        || pcc_gc_tracing_candidate_nodes[2] != weakref_node) return 7;

    struct Observed list_slots = {child, callback, outside, cls, 0, 0, 0, 0, 0, 0};
    if (visit_slots(owner, &list_slots) != 3 || list_slots.calls != 3
        || list_slots.owning_target != 2 || list_slots.weak_target
        || list_slots.owning_callback || list_slots.owning_outside != 1) return 8;
    struct Observed ref_slots = {child, callback, outside, cls, 0, 0, 0, 0, 0, 0};
    if (visit_slots(weakref, &ref_slots) != 2 || ref_slots.calls != 2
        || ref_slots.owning_target || ref_slots.weak_target != 1
        || ref_slots.owning_callback != 1 || ref_slots.owning_outside) return 9;

    struct Observed instance_slots = {child, callback, outside, cls, 0, 0, 0, 0, 0, 0};
    if (visit_slots(child, &instance_slots) <= 0 || instance_slots.owning_type != 1) return 12;

    pcc_gc_tracing_candidates_retire();
    if (pcc_gc_tracing_candidate_commit >= 0 || pcc_gc_tracing_candidate_count != 0) return 10;
    pcc_py_gc_minor_graph_unlock();
    if (pcc_resume_world() != 0 || pcc_thread_world_stop_epoch_held() >= 0) return 11;
    puts("GC_TRACING_CANDIDATE_SLOT_INVENTORY_OK");
    return 0;
}
