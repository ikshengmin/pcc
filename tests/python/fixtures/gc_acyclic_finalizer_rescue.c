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
extern int64_t pcc_gc_tracing_callback_store_instance_field(
    PyObject *, int32_t, PyObject *, PyObject **);
extern PyObject *py_instance_get_field(PyObject *, int32_t);
extern int64_t pcc_gc_tracing_classify_callback_cohort(
    PyObject *const *, int64_t, int64_t, int64_t, int64_t *);
extern int64_t pcc_gc_tracing_dispatch_callback_head(PyObject *, int64_t, int64_t);
extern int64_t pcc_gc_tracing_dispatch_classified_head(
    PyObject *const *, int64_t, int64_t, int64_t, int64_t);
extern void py_user_del_dispatch(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_tracing_refresh_callback_cohort(
    PyObject *const *, int64_t, PyObject *const *, int64_t,
    int64_t, int64_t, int64_t, int64_t *);
extern void pcc_gc_tracing_user_code_window_open(void *);
extern void pcc_gc_tracing_user_code_window_close(void *);
extern void pcc_gc_tracing_recheck_reachability_after_finalizers(void);
extern int64_t pcc_gc_metric_sweep_recheck_calls, pcc_gc_metric_sweep_recheck_nodes;
extern int64_t pcc_gc_metric_sweep_recheck_work, pcc_gc_metric_sweep_recheck_sum_us;

enum { ONLY_FIELD = 0, WINDOW_WORDS = 2, SCRUB_WORDS = 4096 };
enum { PARENT_FINALIZER, CHILD_FINALIZER };
enum { FIRST_PARENT = 1, SECOND_CHILD = 2, THIRD_WEAK = 3 };

static PyObject *restored, *live_heap, *outside_owner;
static PyObject *captured_owner;
static int64_t use_captured_owner;
static int64_t use_old_owner, use_new_owner, owner_in_journal, journal_incomplete;
/* The two candidate identities stand in for collector metadata, not roots.
 * Never load those raw pointers into a live recheck frame. */
static PyObject *candidate_parent, *candidate_child;
static PyObject *child_watch, *cycle_watch;
/* These are collector metadata, never registered as roots. */
enum { MIXED_CYCLE, MIXED_CHILD, MIXED_CYCLE_LEAF, MIXED_PARENT, MIXED_SIZE };
static PyObject *mixed_cohort[MIXED_SIZE];
static int64_t use_mixed_cohort, cycle_weak_calls, cycle_del_calls;
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
    if (should_rescue && use_captured_owner) {
        if (pcc_gc_tracing_callback_store_instance_field(
                outside_owner, -1, self, &captured_owner) || captured_owner
            || !pcc_gc_tracing_callback_store_instance_field(
                outside_owner, ONLY_FIELD, self, &captured_owner)
            || captured_owner != outside_owner
            || !pcc_gc_tracing_callback_store_instance_field(
                outside_owner, ONLY_FIELD, self, &captured_owner)
            || captured_owner != outside_owner
            || pcc_gc_tracing_callback_store_instance_field(
                self, ONLY_FIELD, self, &captured_owner)
            || py_instance_get_field(self, ONLY_FIELD) != candidate_child)
            callback_errors++;
    } else if (should_rescue && (use_old_owner || use_new_owner)) {
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

static PyObject *cycle_weak_callback(void) {
    if (pcc_thread_world_stop_epoch_held() >= 0 || cycle_del_calls
        || py_weakref_call(cycle_watch) != py_None) callback_errors++;
    cycle_weak_calls++;
    return py_int_from_i64(0);
}

static PyObject *cycle_leaf_del(PyObject *self) {
    (void)self;
    if (pcc_thread_world_stop_epoch_held() >= 0 || cycle_weak_calls != 1)
        callback_errors++;
    cycle_del_calls++;
    return py_int_from_i64(0);
}

static int make_mixed_graph(void) {
    PyObject *list = py_list_new(2);
    PyObject *leaf_class = py_class_new("CycleRetainedLeaf", NULL, 0, NULL, 0);
    if (!list || !leaf_class) return 0;
    py_class_add_method(leaf_class, "__del__", (void *)cycle_leaf_del);
    PyObject *leaf = py_instance_new(leaf_class);
    PyObject *callback = py_func_new((void *)cycle_weak_callback, NULL);
    if (!leaf || !callback) return 0;
    py_list_append(list, list);
    py_list_append(list, leaf);
    cycle_watch = py_weakref_new(leaf, callback);
    if (!cycle_watch) return 0;
    pcc_gc_scheduler_root_register(&cycle_watch);
    pcc_gc_unpin(list);
    pcc_gc_unpin(leaf);
    mixed_cohort[MIXED_PARENT] = candidate_parent;
    mixed_cohort[MIXED_CHILD] = candidate_child;
    mixed_cohort[MIXED_CYCLE] = list;
    mixed_cohort[MIXED_CYCLE_LEAF] = leaf;
    return 1;
}

static void mark_candidate(PyObject *o) {
    pcc_gc_set_mark_color(o, PY_FLAG_GC_WHITE);
    __atomic_fetch_or(&py_header(o)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
}

static int is_candidate(PyObject *o) {
    return (py_header_flags_load(py_header(o)) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0;
}

enum { TRIAL_ACYCLIC_CHILD = 1, TRIAL_ACYCLIC_HEAD = 3, HEAD_SLOT_BUDGET = 16 };

static int64_t select_one(int action, int64_t epoch) {
    PyObject *cohort[2] = {candidate_parent, candidate_child};
    int64_t cohort_verdicts[2] = {0, 0};
    if (action == CHILD_FINALIZER) {
        if (py_instance_get_field(candidate_parent, ONLY_FIELD) != NULL) return 0;
        cohort[0] = candidate_child;
        return pcc_gc_tracing_classify_callback_cohort(cohort, 1, epoch, HEAD_SLOT_BUDGET,
                                                      cohort_verdicts) ? cohort_verdicts[0] : 0;
    }
    if (!pcc_gc_tracing_classify_callback_cohort(cohort, 2, epoch, HEAD_SLOT_BUDGET,
                                                cohort_verdicts)
        || cohort_verdicts[1] != TRIAL_ACYCLIC_CHILD
        || pcc_gc_tracing_dispatch_callback_head(candidate_child, cohort_verdicts[1], epoch))
        return 0;
    return cohort_verdicts[0];
}

static int run_one(int action) {
    int64_t epoch = pcc_thread_world_stop_epoch_held();
    int64_t selected = select_one(action, epoch);
    if (selected != TRIAL_ACYCLIC_HEAD) return 0;
    PyObject *target = action == PARENT_FINALIZER ? candidate_parent : candidate_child;
    if (pcc_gc_tracing_dispatch_callback_head(target, selected, epoch - 1)) return 0;
    return pcc_gc_tracing_dispatch_callback_head(target, selected, epoch) == 1;
}

static int run_mixed(void) {
    int64_t epoch = pcc_thread_world_stop_epoch_held();
    if (pcc_gc_tracing_dispatch_classified_head(
            mixed_cohort, MIXED_SIZE, MIXED_CHILD, epoch, HEAD_SLOT_BUDGET)
        || pcc_gc_tracing_dispatch_classified_head(
            mixed_cohort, MIXED_SIZE, MIXED_CYCLE_LEAF, epoch, HEAD_SLOT_BUDGET)
        || cycle_weak_calls || cycle_del_calls || child_calls || parent_calls) return 0;
    /* Explicit cyclic reference phase. Keep the unrelated acyclic weakref
     * live until its parent can rescind the original verdict. */
    int64_t window[WINDOW_WORDS] = {0, 0};
    pcc_gc_tracing_user_code_window_open(window);
    py_weakref_invalidate(mixed_cohort[MIXED_CYCLE_LEAF]);
    py_user_del_dispatch(mixed_cohort[MIXED_CYCLE_LEAF]);
    pcc_gc_tracing_user_code_window_close(window);
    if (cycle_weak_calls != 1 || cycle_del_calls != 1 || weak_calls || child_calls || parent_calls
        || pcc_gc_tracing_dispatch_classified_head(
            mixed_cohort, MIXED_SIZE, MIXED_PARENT, epoch, HEAD_SLOT_BUDGET))
        return 0;
    /* Run a fresh stopped recheck before using any later callback verdict. */
    return 1;
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

enum { BOTH_REACHED = 3, REFERENCE_SLOT_BUDGET = 32 };
enum { REFRESH_MASK = 0, REFRESH_VISITS = 1, REFRESH_WORDS = 2 };

static int bounded_recheck(int64_t sealed_epoch, int64_t slots_left, int64_t with_journal,
                           int64_t *mask_out, int64_t *visits_out) {
    PyObject *cohort[] = {candidate_parent, candidate_child};
    PyObject *owner = use_captured_owner ? captured_owner :
        (owner_in_journal ? outside_owner : NULL);
    PyObject *seeds[] = {restored, with_journal ? owner : NULL};
    int64_t result[REFRESH_WORDS] = {-1, -1};
    int64_t complete = with_journal && journal_incomplete ? 0 : 1;
    if (!pcc_gc_tracing_refresh_callback_cohort(cohort, 2, seeds, 2, sealed_epoch,
                                                 slots_left, complete, result)) {
        if (result[REFRESH_MASK] != -1 || result[REFRESH_VISITS] != -1)
            callback_errors++;
        return 0;
    }
    *mask_out = result[REFRESH_MASK];
    *visits_out = result[REFRESH_VISITS];
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
    if (use_captured_owner) {
        if (restored || captured_owner != outside_owner
            || py_instance_get_field(outside_owner, ONLY_FIELD) != candidate_parent)
            return 0;
    } else if (use_old_owner || use_new_owner) {
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
    use_mixed_cohort = argc == 3 && argv[2][0] == 'm';
    reentry = argc == 3 && argv[2][0] == 'n';
    int64_t heap_size = argc == 4 ? atoi(argv[3]) : 0;
    use_captured_owner = argc == 4 && argv[2][0] == 'c';
    use_old_owner = argc == 4 && argv[2][0] == 'j';
    use_new_owner = argc == 4 && (argv[2][0] == 'a' || argv[2][0] == 'f');
    journal_incomplete = use_captured_owner || (argc == 4 && argv[2][0] == 'f');
    int trial_bounded = (argc == 4 && argv[2][0] == 'b') || use_old_owner || use_new_owner || use_captured_owner;
    if (argc == 4 && (!should_rescue || heap_size <= 0 || heap_size > 2048)) return 1;
    py_gc_disable();
    if (!make_graph() || (use_mixed_cohort && !make_mixed_graph()) || (heap_size && !make_fixed_live_graph(heap_size))) return 2;
    pcc_gc_scheduler_root_register(&restored);
    pcc_gc_scheduler_root_register(&child_watch);
    if (use_old_owner || use_captured_owner) {
        if (use_captured_owner) {
            const char *fields[] = {"stored"};
            PyObject *owner_class = py_class_new("ReferenceWriter", NULL, 0, fields, 1);
            if (!owner_class) return 2;
            outside_owner = py_instance_new(owner_class);
        } else outside_owner = py_list_new(1);
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
    if (use_mixed_cohort) {
        mark_candidate(mixed_cohort[MIXED_CYCLE]);
        mark_candidate(mixed_cohort[MIXED_CYCLE_LEAF]);
    }
    pcc_py_gc_minor_graph_unlock();

    if (!is_candidate(candidate_child)) return 4;
    if (use_mixed_cohort) {
        if (!run_mixed()) return 18;
        recheck_without_raw_candidate_locals();
        if (!pcc_gc_tracing_dispatch_classified_head(
                mixed_cohort, MIXED_SIZE, MIXED_PARENT,
                pcc_thread_world_stop_epoch_held(), HEAD_SLOT_BUDGET))
            return 19;
    } else if (!run_one(PARENT_FINALIZER)) return 4;
    int64_t bounded_mask = -1, bounded_visits = -1;
    if (trial_bounded) {
        int64_t stopped = pcc_thread_world_stop_epoch_held();
        if (bounded_recheck(stopped, 0, 1, &bounded_mask, &bounded_visits)
            || bounded_recheck(stopped - 1, REFERENCE_SLOT_BUDGET, 1,
                               &bounded_mask, &bounded_visits)
            || bounded_mask != -1 || bounded_visits != -1) return 15;
        if (use_old_owner || use_new_owner || use_captured_owner) {
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
    if (use_mixed_cohort && (cycle_weak_calls != 1 || cycle_del_calls != 1
        || py_weakref_call(cycle_watch) != py_None)) return 20;
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
    if (use_mixed_cohort) {
        pcc_gc_store_root(&cycle_watch, NULL);
        pcc_gc_scheduler_root_unregister(&cycle_watch);
    }
    pcc_gc_store_root(&restored, NULL);
    pcc_gc_store_root(&child_watch, NULL);
    pcc_gc_scheduler_root_unregister(&child_watch);
    pcc_gc_scheduler_root_unregister(&restored);
    if (use_old_owner || use_captured_owner) {
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
    if (use_captured_owner) puts("captured owner; fallback full");
    else if (journal_incomplete) puts("fallback full");
    else if (trial_bounded)
        printf("bounded %lld %lld\n", (long long)bounded_mask, (long long)bounded_visits);
    puts("GC_ACYCLIC_FINALIZER_RESCUE_OK");
    return 0;
}
