/* Executable, test-only #9 correctness arm for one parent finalizer.
 * Separate the callback's raw stack lifetime from the subsequent root scan.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern PyObject *py_class_new(const char *, void *, int32_t, const char **, int32_t);
extern PyObject *py_instance_new(void *);
extern void py_class_add_method(PyObject *, const char *, void *);
extern void py_instance_set_field(PyObject *, int32_t, PyObject *);
extern PyObject *py_instance_get_field(PyObject *, int32_t);
extern void py_user_del_dispatch(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
typedef void (*SlotVisitor)(PyObject **, int64_t, void *);
extern int64_t pcc_gc_visit_object_slots_slice(
    PyObject *, int64_t, int64_t, SlotVisitor, void *, void *);
extern void pcc_gc_tracing_user_code_window_open(void *);
extern void pcc_gc_tracing_user_code_window_close(void *);
extern void pcc_gc_tracing_recheck_reachability_after_finalizers(void);
extern int64_t pcc_gc_metric_sweep_recheck_calls, pcc_gc_metric_sweep_recheck_nodes;
extern int64_t pcc_gc_metric_sweep_recheck_work, pcc_gc_metric_sweep_recheck_sum_us;

enum { ONLY_FIELD = 0, WINDOW_WORDS = 2, SCRUB_WORDS = 4096 };
enum { PARENT_FINALIZER, CHILD_FINALIZER };
enum { FIRST_PARENT = 1, SECOND_CHILD = 2, THIRD_WEAK = 3 };

static PyObject *restored, *live_heap, *outside_owner;
static int64_t use_old_owner, use_new_owner, owner_in_journal, journal_incomplete;
/* The two candidate identities stand in for collector metadata, not roots.
 * Never load those raw pointers into a live recheck frame. */
static PyObject *candidate_parent, *candidate_child;
static PyObject *child_watch;
static int64_t parent_calls, child_calls, weak_calls, ran_while_stopped;
static int64_t should_rescue, reentry, nested_calls, callback_errors;
static PyObject *pending_error;
static int64_t sequence, parent_position, child_position, weak_position;

static void observe_callback_boundary(void) {
    if (pcc_thread_world_stop_epoch_held() >= 0) ran_while_stopped++;
    if (py_err_occurred()) callback_errors++;
    if (reentry) {
        nested_calls++;
        if (py_gc_collect() != 0 || py_err_occurred()) callback_errors++;
    }
}

static PyObject *parent_del(PyObject *self) {
    parent_calls++;
    parent_position = ++sequence;
    observe_callback_boundary();
    if (should_rescue && (use_old_owner || use_new_owner)) {
        if (use_new_owner) outside_owner = py_list_new(1);
        if (!outside_owner) {
            callback_errors++;
            return py_int_from_i64(0);
        }
        py_list_append(outside_owner, self);
        if (py_list_len(outside_owner) != 1) callback_errors++;
        else owner_in_journal = 1; /* Explicit record by the reference arm only. */
    } else if (should_rescue) {
        pcc_gc_store_root(&restored, self);
    }
    return py_int_from_i64(0);
}

static PyObject *child_del(PyObject *self) {
    (void)self;
    child_calls++;
    child_position = ++sequence;
    observe_callback_boundary();
    return py_int_from_i64(0);
}

static PyObject *child_weak_callback(void) {
    weak_calls++;
    weak_position = ++sequence;
    observe_callback_boundary();
    if (py_weakref_call(child_watch) != py_None) callback_errors++;
    return py_int_from_i64(0);
}

static void mark_candidate(PyObject *o) {
    pcc_gc_set_mark_color(o, PY_FLAG_GC_WHITE);
    __atomic_fetch_or(&py_header(o)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
}

static int is_candidate(PyObject *o) {
    return (py_header_flags_load(py_header(o)) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0;
}

static int run_one(int action) {
    int64_t old_epoch = pcc_thread_world_stop_epoch_held();
    PyObject *target = action == PARENT_FINALIZER ? candidate_parent : candidate_child;
    if (old_epoch < 0 || !is_candidate(target)) return 0;
    int64_t window[WINDOW_WORDS] = {0, 0};
    pcc_gc_tracing_user_code_window_open(window);
    if (pcc_thread_world_stop_epoch_held() >= 0) {
        pcc_gc_tracing_user_code_window_close(window);
        return 0;
    }
    /* Dispatch owns its own temporary traced slot and address lease. */
    py_user_del_dispatch(target);
    pcc_gc_tracing_user_code_window_close(window);
    return pcc_thread_world_stop_epoch_held() > old_epoch;
}

static void scrub_finished_callback_stack(void) {
    volatile uintptr_t scratch[SCRUB_WORDS];
    for (int64_t i = 0; i < SCRUB_WORDS; ++i) scratch[i] = 0;
}

static void recheck_without_raw_candidate_locals(void) {
    scrub_finished_callback_stack();
    pcc_py_gc_minor_graph_lock();
    pcc_gc_tracing_recheck_reachability_after_finalizers();
    pcc_py_gc_minor_graph_unlock();
}

static int make_graph(void) {
    const char *fields[] = {"dependent"};
    PyObject *pc = py_class_new("ReferenceParent", NULL, 0, fields, 1);
    PyObject *cc = py_class_new("ReferenceChild", NULL, 0, NULL, 0);
    if (!pc || !cc) return 0;
    py_class_add_method(pc, "__del__", (void *)parent_del);
    py_class_add_method(cc, "__del__", (void *)child_del);
    candidate_parent = py_instance_new(pc);
    candidate_child = py_instance_new(cc);
    if (!candidate_parent || !candidate_child) return 0;
    py_instance_set_field(candidate_parent, ONLY_FIELD, candidate_child);
    PyObject *callback = py_func_new((void *)child_weak_callback, NULL);
    child_watch = callback ? py_weakref_new(candidate_child, callback) : NULL;
    if (!child_watch) return 0;
    pcc_gc_unpin(candidate_parent);
    pcc_gc_unpin(candidate_child);
    return 1;
}

/* This arm knows its only parent->child edge. A generalized implementation
 * must prove the same condition for every current owning slot. */
static int make_fixed_live_graph(int64_t size) {
    live_heap = py_list_new(size);
    if (!live_heap) return 0;
    pcc_gc_scheduler_root_register(&live_heap);
    for (int64_t i = 0; i < size; i++) {
        PyObject *live = py_list_new(0);
        if (!live) return 0;
        py_list_append(live_heap, live);
    }
    return py_list_len(live_heap) == size;
}

static int retire_parent_edge(void) {
    if (!is_candidate(candidate_parent) || !is_candidate(candidate_child)
        || py_instance_get_field(candidate_parent, ONLY_FIELD) != candidate_child)
        return 0;
    py_instance_set_field(candidate_parent, ONLY_FIELD, NULL);
    return py_instance_get_field(candidate_parent, ONLY_FIELD) == NULL;
}

static int settle_child_weakref(void) {
    if (!is_candidate(candidate_child)) return 0;
    int64_t window[WINDOW_WORDS] = {0, 0};
    pcc_gc_tracing_user_code_window_open(window);
    py_weakref_invalidate(candidate_child);
    pcc_gc_tracing_user_code_window_close(window);
    return py_weakref_call(child_watch) == py_None;
}

enum { ROLE_VALUE = 1, ROLE_TYPE = 2, ROLE_WEAK = 3 };
enum { REACHED_PARENT = 1, REACHED_CHILD = 2, BOTH_REACHED = 3 };
enum { VISITOR_BATCH = 4, VISITOR_CURSOR = 0, VISITOR_VISITS = 1, VISITOR_WORDS = 2 };
enum { VISITOR_END = -1, REFERENCE_SLOT_BUDGET = 32 };

struct Trial {
    int64_t found;
    int64_t unsupported;
};

static void seed_child_from_slot(PyObject **slot, int64_t role, void *context) {
    struct Trial *trial = context;
    if (role == ROLE_WEAK) return;
    if (role != ROLE_VALUE && role != ROLE_TYPE) {
        trial->unsupported = 1;
        return;
    }
    if (*slot == candidate_parent) trial->found |= REACHED_PARENT;
    if (*slot == candidate_child) trial->found |= REACHED_CHILD;
}

/* The bounded arm knows this callback's one participating root and its
 * exact explicit owner journal. There is no production store hook here. */
static int inspect_owner_slots(PyObject *owner, int64_t *slots_left, struct Trial *trial) {
    int64_t cursor = 0;
    do {
        if (!*slots_left) return 0;
        int64_t state[VISITOR_WORDS] = {0, 0};
        int64_t limit = *slots_left < VISITOR_BATCH ? *slots_left : VISITOR_BATCH;
        if (!pcc_gc_visit_object_slots_slice(owner, cursor, limit,
                         seed_child_from_slot, trial, state)
            || trial->unsupported || state[VISITOR_VISITS] < 0
            || state[VISITOR_VISITS] > limit
            || (state[VISITOR_CURSOR] != VISITOR_END && state[VISITOR_CURSOR] <= cursor))
            return 0;
        *slots_left -= state[VISITOR_VISITS];
        cursor = state[VISITOR_CURSOR];
    } while (cursor != VISITOR_END);
    return 1;
}

static int bounded_recheck(int64_t sealed_epoch, int64_t slots_left, int64_t with_journal,
                           int64_t *mask_out, int64_t *visits_out) {
    if (sealed_epoch < 0 || pcc_thread_world_stop_epoch_held() != sealed_epoch) return 0;
    struct Trial trial = {0, 0};
    if (restored == candidate_parent) trial.found = REACHED_PARENT;
    else if (restored != NULL) return 0;
    int64_t examined = 0, seen = 0;
    if (with_journal && journal_incomplete) return 0;
    if (with_journal && owner_in_journal) {
        if (!inspect_owner_slots(outside_owner, &slots_left, &trial)) return 0;
        examined++;
    }
    while (seen != trial.found) {
        int64_t next = (trial.found & ~seen) & REACHED_PARENT ? REACHED_PARENT : REACHED_CHILD;
        PyObject *owner = next == REACHED_PARENT ? candidate_parent : candidate_child;
        if (!inspect_owner_slots(owner, &slots_left, &trial)) return 0;
        seen |= next;
        examined++;
    }
    if (pcc_thread_world_stop_epoch_held() != sealed_epoch) return 0;
    *mask_out = trial.found;
    *visits_out = examined;
    return 1;
}

static int check_first_verdict(void) {
    int parent_dead = is_candidate(candidate_parent);
    int child_dead = is_candidate(candidate_child);
    if (parent_dead != !should_rescue || child_dead != !should_rescue) {
        fprintf(stderr, "parent-candidate=%d child-candidate=%d rescue=%lld\n",
                parent_dead, child_dead, (long long)should_rescue);
        return 0;
    }
    if (parent_calls != 1 || child_calls || weak_calls || ran_while_stopped) return 0;
    if (use_old_owner || use_new_owner) {
        if (restored || !owner_in_journal || py_list_get(outside_owner, 0) != candidate_parent)
            return 0;
    } else if (should_rescue ? restored != candidate_parent : restored != NULL) return 0;
    if (py_weakref_call(child_watch) != candidate_child || parent_position != FIRST_PARENT)
        return 0;
    return 1;
}

int main(int argc, char **argv) {
    int64_t backend = pcc_gc_backend();
    if (argc < 2 || argc > 4 || backend < 1 || backend > 3) return 1;
    should_rescue = argv[1][0] == 'r';
    reentry = argc == 3 && argv[2][0] == 'n';
    int64_t heap_size = argc == 4 ? atoi(argv[3]) : 0;
    use_old_owner = argc == 4 && argv[2][0] == 'j';
    use_new_owner = argc == 4 && (argv[2][0] == 'a' || argv[2][0] == 'f');
    journal_incomplete = argc == 4 && argv[2][0] == 'f';
    int trial_bounded = (argc == 4 && argv[2][0] == 'b') || use_old_owner || use_new_owner;
    if (argc == 4 && (!should_rescue || heap_size <= 0 || heap_size > 2048)) return 1;
    py_gc_disable();
    if (!make_graph() || (heap_size && !make_fixed_live_graph(heap_size))) return 2;
    pcc_gc_scheduler_root_register(&restored);
    pcc_gc_scheduler_root_register(&child_watch);
    if (use_old_owner) {
        outside_owner = py_list_new(1);
        if (!outside_owner) return 2;
        pcc_gc_scheduler_root_register(&outside_owner);
    }
    if (reentry) {
        pending_error = py_exc_new(PY_EXC_VALUEERROR, "outer acyclic callback error");
        if (!pending_error) return 3;
        py_raise_owned(pending_error);
    }
    if (pcc_stop_the_world() != 0) return 3;
    pcc_py_gc_minor_graph_lock();
    mark_candidate(candidate_parent);
    mark_candidate(candidate_child);
    pcc_py_gc_minor_graph_unlock();

    if (!is_candidate(candidate_child) || !run_one(PARENT_FINALIZER)) return 4;
    int64_t bounded_mask = -1, bounded_visits = -1;
    if (trial_bounded) {
        int64_t stopped = pcc_thread_world_stop_epoch_held();
        if (bounded_recheck(stopped, 0, 1, &bounded_mask, &bounded_visits)
            || bounded_recheck(stopped - 1, REFERENCE_SLOT_BUDGET, 1,
                               &bounded_mask, &bounded_visits)
            || bounded_mask != -1 || bounded_visits != -1) return 15;
        if (use_old_owner || use_new_owner) {
            int64_t missed = -1, missed_visits = -1;
            if (!bounded_recheck(stopped, REFERENCE_SLOT_BUDGET, 0, &missed, &missed_visits)
                || missed || missed_visits) return 16;
        }
        int qualified = bounded_recheck(stopped, REFERENCE_SLOT_BUDGET, 1,
                                        &bounded_mask, &bounded_visits);
        if (journal_incomplete ? qualified || bounded_mask != -1 || bounded_visits != -1
                               : !qualified) return 16;
    }
    int64_t calls = __atomic_load_n(&pcc_gc_metric_sweep_recheck_calls, __ATOMIC_RELAXED);
    int64_t visits = __atomic_load_n(&pcc_gc_metric_sweep_recheck_nodes, __ATOMIC_RELAXED);
    int64_t saved = __atomic_load_n(&pcc_gc_metric_sweep_recheck_work, __ATOMIC_RELAXED);
    int64_t elapsed = __atomic_load_n(&pcc_gc_metric_sweep_recheck_sum_us, __ATOMIC_RELAXED);
    recheck_without_raw_candidate_locals();
    calls = __atomic_load_n(&pcc_gc_metric_sweep_recheck_calls, __ATOMIC_RELAXED) - calls;
    visits = __atomic_load_n(&pcc_gc_metric_sweep_recheck_nodes, __ATOMIC_RELAXED) - visits;
    saved = __atomic_load_n(&pcc_gc_metric_sweep_recheck_work, __ATOMIC_RELAXED) - saved;
    elapsed = __atomic_load_n(&pcc_gc_metric_sweep_recheck_sum_us, __ATOMIC_RELAXED) - elapsed;
    if (!check_first_verdict()) return 5;
    if (trial_bounded && saved != 2) return 17;
    if (trial_bounded && !journal_incomplete && (bounded_mask != BOTH_REACHED
        || bounded_visits != ((use_old_owner || use_new_owner) ? 3 : 2))) return 17;
    if (!should_rescue) {
        if (!retire_parent_edge() || !run_one(CHILD_FINALIZER)) return 8;
        recheck_without_raw_candidate_locals();
        if (child_calls != 1 || child_position != SECOND_CHILD
            || weak_calls || !settle_child_weakref()) return 9;
        if (weak_calls != 1 || weak_position != THIRD_WEAK || ran_while_stopped) return 10;
        /* The same terminal path is idempotent; there is no second callback. */
        if (!settle_child_weakref() || weak_calls != 1) return 11;
    }
    if (callback_errors || (
        reentry && (nested_calls != (should_rescue ? 1 : 3)
                   || py_current_exception() != pending_error)))
        return 12;
    if (reentry) py_clear_exception();
    if (pcc_resume_world() != 0) return 13;
    pcc_gc_store_root(&restored, NULL);
    pcc_gc_store_root(&child_watch, NULL);
    pcc_gc_scheduler_root_unregister(&child_watch);
    pcc_gc_scheduler_root_unregister(&restored);
    if (use_old_owner) {
        pcc_gc_store_root(&outside_owner, NULL);
        pcc_gc_scheduler_root_unregister(&outside_owner);
    }
    if (heap_size) {
        if (py_list_len(live_heap) != heap_size) return 14;
        pcc_gc_store_root(&live_heap, NULL);
        pcc_gc_scheduler_root_unregister(&live_heap);
        printf("recheck %lld %lld %lld %lld\n",
               (long long)calls, (long long)visits, (long long)saved, (long long)elapsed);
    }
    if (journal_incomplete) puts("fallback full");
    else if (trial_bounded)
        printf("bounded %lld %lld\n", (long long)bounded_mask, (long long)bounded_visits);
    puts("GC_ACYCLIC_FINALIZER_RESCUE_OK");
    return 0;
}
