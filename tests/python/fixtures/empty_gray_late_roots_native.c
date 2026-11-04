/* Regression for early-empty completion with late mutator work.
 * A controlled positive-epoch mark phase drives the actual changed step,
 * completion claim, pthread stop, final root rescan, gray drain and commit.
 * It does not replace any of those functions or claim whole-cycle/sweep proof.
 */
#include "py_internal.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int sched_yield(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_thread_owns_stopped_world(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_trace_cursor_store(void *);
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_cycle_requested;
extern int64_t pcc_gc_tracing_cycle_epoch;
extern int64_t pcc_gc_tracing_finish_claim_epoch;
extern int64_t pcc_gc_tracing_finish_claim_backend;
extern int64_t pcc_gc_tracing_finish_commits;
extern int64_t pcc_gc_tracing_finish_stop_epoch;

static PyObject *owner;
static PyObject *child;
static PyObject *late_root;
static PyObject *stored_root;
static PyObject *stored_value;
static PyObject *target;
static PyObject *tail[16];
static int ready;
static int injected;
static int resumed;
static int release_worker;
static int64_t expected_epoch;
static int64_t selected_backend;
static int take_root;

static void fail(int code, const char *where, int64_t value) {
    fprintf(stderr, "EMPTY_GRAY_WINDOW_FAIL code=%d where=%s got=%lld\n",
            code, where, (long long)value);
    _Exit(code);
}

static void wait_running(int *word) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) == 0) {
        pcc_thread_safepoint();
        sched_yield();
    }
}

static void set_aged_color(PyObject *object, int64_t color) {
    __atomic_fetch_and(&py_header(object)->flags,
                      ~(PY_FLAG_GC_FRESH_ALLOC | PY_FLAG_GC_SWEEP_CANDIDATE),
                      __ATOMIC_ACQ_REL);
    pcc_gc_set_mark_color(object, color);
}

static int is_black_live(PyObject *object) {
    int32_t flags = py_header_flags_load(py_header(object));
    return (flags & (PY_FLAG_GC_WHITE | PY_FLAG_GC_GRAY | PY_FLAG_GC_BLACK |
                     PY_FLAG_GC_SWEEP_CANDIDATE)) == PY_FLAG_GC_BLACK;
}

static void *late_work(void *unused) {
    (void)unused;
    /* This is a registered running mutator, not an exited callback. Its
     * bounded no-park interval forces the collector to wait for the mutation. */
    pcc_thread_no_park_enter();
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (pcc_thread_stop_requested_acquire() == 0) sched_yield();

    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_tracing_finish_claim_epoch != expected_epoch ||
        pcc_gc_tracing_finish_claim_backend != selected_backend ||
        pcc_gc_tracing_cycle_epoch != expected_epoch ||
        pcc_gc_mark_active != 1) fail(20, "stop_without_matching_claim", 0);
    if (pcc_gc_gray_count_load_acquire() != 0) fail(21, "claim_not_empty", 0);
    if (!is_black_live(owner) ||
        (py_header_flags_load(py_header(child)) & 56) != PY_FLAG_GC_WHITE ||
        (py_header_flags_load(py_header(late_root)) & 56) != PY_FLAG_GC_WHITE)
        fail(22, "window_color_precondition", 0);
    if (pcc_thread_owns_stopped_world() != 0 || pcc_thread_no_park_depth() == 0)
        fail(23, "mutation_not_running_under_lease", 0);
    pcc_py_gc_minor_graph_unlock();

    /* New gray work after the completion claim: a real black-to-white edge. */
    pcc_gc_store_ptr(owner, &((PyListObject *)owner)->items[0], child);
    if ((py_header_flags_load(py_header(child)) & 56) != PY_FLAG_GC_GRAY)
        fail(24, "insertion_barrier_did_not_gray", 0);
    if (pcc_gc_gray_count_load_acquire() < 1) fail(25, "new_gray_count_missing", 0);

    /* A pre-registered empty root exercises the actual root-store fast path
     * after a completion claim, while the collector waits for this no-park
     * mutator. Both retaining and transfer ABIs must finish their gray work
     * before this original no-park scope allows the stopped-world verdict. */
    if (take_root) pcc_gc_store_root_take(&stored_root, stored_value);
    else pcc_gc_store_root(&stored_root, stored_value);
    if (stored_root != stored_value ||
        (py_header_flags_load(py_header(stored_value)) & 56) != PY_FLAG_GC_GRAY)
        fail(28, "root_store_did_not_publish_and_gray", 0);
    if (pcc_gc_gray_count_load_acquire() < 2) fail(29, "root_gray_count_missing", 0);

    /* A distinct newly registered white root tests the final root rescan. */
    void *handle = pcc_gc_scheduler_root_register_handle(&late_root);
    if (!handle) fail(26, "late_root_registration", 0);
    if ((py_header_flags_load(py_header(late_root)) & 56) != PY_FLAG_GC_WHITE)
        fail(27, "late_root_was_not_white_before_remark", 0);
    __atomic_store_n(&injected, 1, __ATOMIC_RELEASE);
    pcc_thread_no_park_exit();
    __atomic_store_n(&resumed, 1, __ATOMIC_RELEASE);
    wait_running(&release_worker);
    pcc_gc_scheduler_root_unregister_handle(handle);
    pcc_thread_unregister_current();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) fail(1, "entry_argument", argc);
    int last_gray = atoi(argv[1]);
    take_root = atoi(argv[2]);
    if (last_gray != 0 && last_gray != 1) fail(2, "entry_mode", last_gray);
    if (take_root != 0 && take_root != 1) fail(2, "root_store_mode", take_root);
    selected_backend = pcc_gc_backend();
    if ((selected_backend != 1 && selected_backend != 2 && selected_backend != 4)
        || pcc_gc_backend4_no_refcounts != 1)
        fail(3, "requires_nonmoving_or_gc4_no_rc", 0);
    py_gc_disable();
    if (pcc_current_thread_id() <= 0) fail(4, "main_registration", 0);
    owner = py_list_new(1);
    child = py_list_new(0);
    late_root = py_list_new(0);
    stored_value = py_list_new(0);
    if (!owner || !child || !late_root || !stored_value) fail(5, "allocation", 0);
    py_list_append(owner, py_int_from_i64(0));
    for (int i = 0; i < 16; ++i) {
        tail[i] = py_list_new(0);
        if (!tail[i]) fail(6, "black_tail", i);
    }
    target = py_list_new(0);
    if (!target) fail(7, "target", 0);
    void *owner_handle = pcc_gc_scheduler_root_register_handle(&owner);
    if (!owner_handle) fail(8, "owner_root", 0);
    void *stored_handle = pcc_gc_scheduler_root_register_handle(&stored_root);
    if (!stored_handle) fail(8, "store_root", 0);

    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_mark_active != 0 || pcc_gc_tracing_finish_claim_epoch != 0)
        fail(9, "unexpected_active_cycle", 0);
    set_aged_color(owner, PY_FLAG_GC_BLACK);
    set_aged_color(child, PY_FLAG_GC_WHITE);
    set_aged_color(late_root, PY_FLAG_GC_WHITE);
    set_aged_color(stored_value, PY_FLAG_GC_WHITE);
    for (int i = 0; i < 16; ++i) set_aged_color(tail[i], PY_FLAG_GC_BLACK);
    set_aged_color(target, last_gray ? PY_FLAG_GC_GRAY : PY_FLAG_GC_BLACK);
    void *target_node = pcc_gc_object_index_find(target);
    if (!target_node || *((void **)((char *)target_node + 16)) == NULL)
        fail(10, "target_requires_nonempty_tail", 0);
    if (pcc_gc_tracing_cycle_epoch == INT64_MAX) fail(11, "epoch_exhausted", 0);
    expected_epoch = pcc_gc_tracing_cycle_epoch + 1;
    if (expected_epoch <= 0) fail(11, "positive_epoch", expected_epoch);
    pcc_gc_tracing_cycle_epoch = expected_epoch;
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_gc_gray_count_store_release(last_gray);
    pcc_gc_trace_cursor_store(target_node);
    int64_t commits_before = pcc_gc_tracing_finish_commits;
    pcc_py_gc_minor_graph_unlock();

    pthread_t worker;
    if (pthread_create(&worker, NULL, late_work, NULL) != 0) fail(12, "pthread_create", 0);
    wait_running(&ready);
    int64_t processed = pcc_gc_tracing_step_cycle(1);
    /* On the parent implementation one budget-1 step leaves the black tail
     * cursor and returns before claiming. Fail now, instead of hanging join. */
    if (__atomic_load_n(&injected, __ATOMIC_ACQUIRE) != 1)
        fail(13, "step_did_not_enter_completion_window", processed);
    if (processed != last_gray) fail(14, "slice_credit", processed);
    if (pcc_gc_tracing_finish_commits != commits_before + 1 ||
        pcc_gc_tracing_cycle_epoch != expected_epoch ||
        pcc_gc_tracing_finish_claim_epoch != 0 ||
        pcc_gc_tracing_finish_claim_backend != -1)
        fail(15, "commit_or_epoch", pcc_gc_tracing_finish_commits);
    if (pcc_gc_tracing_finish_stop_epoch <= 0 || pcc_gc_mark_active != 0 ||
        pcc_gc_gray_count_load_acquire() != 0 || pcc_gc_cycle_requested == 0)
        fail(16, "final_stw_state_or_request", pcc_gc_tracing_finish_stop_epoch);
    if (!is_black_live(owner) || !is_black_live(child) || !is_black_live(late_root)
        || stored_root != stored_value || !is_black_live(stored_value))
        fail(17, "remark_lost_late_work", 0);

    wait_running(&resumed);
    __atomic_store_n(&release_worker, 1, __ATOMIC_RELEASE);
    int64_t token = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (token != 1) fail(18, "join_blocking_token", token);
    if (pthread_join(worker, NULL) != 0) fail(19, "pthread_join", 0);
    pcc_thread_blocking_leave(token);
    pcc_gc_scheduler_root_unregister_handle(owner_handle);
    pcc_gc_scheduler_root_unregister_handle(stored_handle);
    printf("EMPTY_GRAY_WINDOW_OK entry=%d backend=%lld take=%d tracing=1 commits=1 late_root=black stored_root=black child=black stw=1\n",
           last_gray, (long long)selected_backend, take_root);
    return 0;
}

