/* Executable, test-only #9 correctness arm for one parent finalizer.
 * Separate the callback's raw stack lifetime from the subsequent root scan.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern PyObject *py_class_new(const char *, void *, int32_t, const char **, int32_t);
extern PyObject *py_instance_new(void *);
extern void py_class_add_method(PyObject *, const char *, void *);
extern void py_instance_set_field(PyObject *, int32_t, PyObject *);
extern PyObject *py_instance_get_field(PyObject *, int32_t);
extern void py_user_del_dispatch(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void pcc_gc_tracing_user_code_window_open(void *);
extern void pcc_gc_tracing_user_code_window_close(void *);
extern void pcc_gc_tracing_recheck_reachability_after_finalizers(void);

enum { ONLY_FIELD = 0, WINDOW_WORDS = 2, SCRUB_WORDS = 4096 };
enum { PARENT_FINALIZER, CHILD_FINALIZER };
enum { FIRST_PARENT = 1, SECOND_CHILD = 2, THIRD_WEAK = 3 };

static PyObject *restored;
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
    if (should_rescue) pcc_gc_store_root(&restored, self);
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

int main(int argc, char **argv) {
    int64_t backend = pcc_gc_backend();
    if ((argc != 2 && argc != 3) || backend < 1 || backend > 3) return 1;
    should_rescue = argv[1][0] == 'r';
    reentry = argc == 3 && argv[2][0] == 'n';
    py_gc_disable();
    if (!make_graph()) return 2;
    pcc_gc_scheduler_root_register(&restored);
    pcc_gc_scheduler_root_register(&child_watch);
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
    recheck_without_raw_candidate_locals();
    int parent_dead = is_candidate(candidate_parent);
    int child_dead = is_candidate(candidate_child);
    if (parent_dead != !should_rescue || child_dead != !should_rescue) {
        fprintf(stderr, "parent-candidate=%d child-candidate=%d rescue=%lld\n",
                parent_dead, child_dead, (long long)should_rescue);
        return 5;
    }
    if (parent_calls != 1 || child_calls || weak_calls || ran_while_stopped
        || (should_rescue ? restored != candidate_parent : restored != NULL)) return 6;
    if (py_weakref_call(child_watch) != candidate_child || parent_position != FIRST_PARENT)
        return 7;
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
    puts("GC_ACYCLIC_FINALIZER_RESCUE_OK");
    return 0;
}
