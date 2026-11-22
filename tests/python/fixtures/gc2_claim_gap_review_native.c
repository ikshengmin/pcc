/* Exercise a dormant single-claim coordinator at two real stopped cuts. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern void pcc_gc_gray_worklist_flush(void);
extern void pcc_gc_gray_worklist_clear_stopped(void);
extern void pcc_gc_page_marks_begin(void);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int64_t pcc_gc_trace_gc2_rooted_short_list(PyObject **, int64_t);
extern int64_t pcc_gc2_review_claim_open_locked(PyObject **, int64_t);
extern int64_t pcc_gc2_review_claim_may_request_remark_locked(int64_t);
extern int64_t pcc_gc2_review_claim_close_locked(PyObject **, int64_t, int64_t, int64_t);
extern int64_t pcc_gc2_review_claim_discard_stale_stopped_locked(int64_t, int64_t);
extern int64_t pcc_gc_tracing_cycle_epoch_advance_unlocked(void);
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_tracing_cycle_epoch;

enum { MARK_WHITE = 8, MARK_GRAY = 16, MARK_BLACK = 32, COLORS = 56,
       EPOCH = 37, BEFORE_SCAN = 1, AFTER_SCAN = 2, WAIT_US = 1000000 };

typedef struct {
    PyObject *root, *child;
    int phase, permit, mode;
    int64_t token, work, closed, new_token;
} Scan;

static void parkable_gate(Scan *scan, int phase) {
    __atomic_store_n(&scan->phase, phase, __ATOMIC_RELEASE);
    while (__atomic_load_n(&scan->permit, __ATOMIC_ACQUIRE) < phase)
        pcc_thread_safepoint();
}

static void *run_scan(void *arg) {
    Scan *scan = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    parkable_gate(scan, BEFORE_SCAN);
    scan->work = pcc_gc_trace_gc2_rooted_short_list(&scan->root, EPOCH);
    pcc_gc_gray_worklist_flush();
    parkable_gate(scan, AFTER_SCAN);
    pcc_py_gc_minor_graph_lock();
    scan->closed = pcc_gc2_review_claim_close_locked(
        &scan->root, EPOCH, scan->token, scan->mode == 1 ? 0 : scan->work);
    pcc_py_gc_minor_graph_unlock();
    return NULL;
}

static int check_cut(Scan *scan, int phase, int64_t expected) {
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (__atomic_load_n(&scan->phase, __ATOMIC_ACQUIRE) < phase) {
        if (pcc_runtime_monotonic_us() >= deadline) return 0;
        pcc_thread_safepoint();
    }
    if (pcc_stop_the_world() != 0) return 0;
    pcc_py_gc_minor_graph_lock();
    /* This is the original owner's credit before scan, then that credit
     * plus the strong child after flush. Even after unlocking and blocking
     * at a safepoint, only the matching token can retire the claim. */
    int good = pcc_gc_gray_count_load_acquire() == expected &&
               pcc_gc2_review_claim_may_request_remark_locked(EPOCH) == 0 &&
               pcc_gc2_review_claim_close_locked(
                   &scan->root, EPOCH, scan->token + 1, 1) == 0 &&
               pcc_gc2_review_claim_open_locked(&scan->root, EPOCH) == 0;
    /* Isolate the token guard from gray credit, then immediately restore
     * the controlled world's real count before it can resume. */
    int64_t actual = pcc_gc_gray_count_load_acquire();
    pcc_gc_gray_count_store_release(0);
    good = good && pcc_gc2_review_claim_may_request_remark_locked(EPOCH) == 0;
    pcc_gc_gray_count_store_release(actual);
    good = good && pcc_gc2_review_claim_discard_stale_stopped_locked(EPOCH, scan->token) == 0;
    if (phase == AFTER_SCAN && scan->mode == 2) {
        /* Use the real epoch transition and queue reset, with whitening
         * limited to the fixture-owned graph. */
        pcc_gc_gray_worklist_clear_stopped();
        pcc_gc_page_marks_begin();
        pcc_gc_gray_count_store_release(0);
        pcc_gc_set_mark_color(scan->root, MARK_WHITE);
        pcc_gc_set_mark_color(scan->child, MARK_WHITE);
        int64_t new_epoch = pcc_gc_tracing_cycle_epoch_advance_unlocked();
        int64_t wrong = pcc_gc2_review_claim_discard_stale_stopped_locked(EPOCH, scan->token + 1);
        int64_t retired = pcc_gc2_review_claim_discard_stale_stopped_locked(EPOCH, scan->token);
        int64_t credit = pcc_gc_gray_count_load_acquire();
        if (!(new_epoch == EPOCH + 1 && wrong == 0 && retired == 1 && credit == 0)) {
            fprintf(stderr, "reset epoch=%lld wrong=%lld retired=%lld credit=%lld\n",
                    (long long)new_epoch, (long long)wrong, (long long)retired, (long long)credit);
            good = 0;
        }
        pcc_gc_shade_gray_atomic(scan->root, 0);
        pcc_gc_gray_worklist_flush();
        PyObject *again = pcc_gc_gray_worklist_take_locked();
        scan->new_token = pcc_gc2_review_claim_open_locked(&scan->root, new_epoch);
        if (again != scan->root || scan->new_token <= scan->token) {
            fprintf(stderr, "reset dequeue=%d old=%lld new=%lld gray=%lld\n", again == scan->root,
                    (long long)scan->token, (long long)scan->new_token, (long long)pcc_gc_gray_count_load_acquire());
            good = 0;
        }
    }
    pcc_py_gc_minor_graph_unlock();
    if (pcc_resume_world() != 0) return 0;
    __atomic_store_n(&scan->permit, phase, __ATOMIC_RELEASE);
    return good;
}

int main(int argc, char **argv) {
    if (argc != 2 || (argv[1][0] < '0' || argv[1][0] > '2') || argv[1][1])
        return 1;
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    Scan scan = {0};
    scan.mode = argv[1][0] - '0';
    PyObject *owner = py_list_new(1);
    PyObject *child = py_list_new(0);
    scan.child = child;
    void *handle = pcc_gc_scheduler_root_register_handle(&scan.root);
    if (!owner || !child || !handle) return 3;
    pcc_gc_store_root(&scan.root, owner);
    py_list_append(owner, child);

    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    pcc_gc_set_mark_color(owner, MARK_WHITE);
    pcc_gc_set_mark_color(child, MARK_WHITE);
    __atomic_store_n(&pcc_gc_tracing_cycle_epoch, EPOCH, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    int ready = pcc_gc2_review_claim_may_request_remark_locked(EPOCH) == 1;
    pcc_gc_shade_gray_atomic(owner, 0);
    pcc_gc_gray_worklist_flush();
    PyObject *dequeued = pcc_gc_gray_worklist_take_locked();
    scan.token = pcc_gc2_review_claim_open_locked(&scan.root, EPOCH);
    pcc_py_gc_minor_graph_unlock();
    if (!ready || dequeued != owner || scan.token <= 0) return 4;

    void *thread = NULL, *result = NULL;
    if (pcc_thread_start(&thread, run_scan, &scan) != 0 || !thread) return 5;
    if (!check_cut(&scan, BEFORE_SCAN, 1) || !check_cut(&scan, AFTER_SCAN, 2))
        return 6;
    if (pcc_thread_join(thread, &result) != 0 || result) return 7;
    int64_t credit = pcc_gc_gray_count_load_acquire();
    int32_t owner_color = py_header_flags_load(py_header(owner)) & COLORS;
    if (scan.work != 1 || scan.closed != (2 - scan.mode) ||
        credit != (scan.mode == 1 ? 2 : 1) ||
        owner_color != (scan.mode == 0 ? MARK_BLACK : MARK_GRAY))
        return 8;
    pcc_py_gc_minor_graph_lock();
    if (scan.mode == 2 &&
        (pcc_gc2_review_claim_may_request_remark_locked(EPOCH + 1) != 0 ||
         pcc_gc2_review_claim_close_locked(&scan.root, EPOCH + 1, scan.token, 1) != 0 ||
         pcc_gc2_review_claim_close_locked(&scan.root, EPOCH + 1, scan.new_token, 0) != 1)) {
        pcc_py_gc_minor_graph_unlock();
        return 12;
    }
    PyObject *a = pcc_gc_gray_worklist_take_locked();
    PyObject *b = pcc_gc_gray_worklist_take_locked();
    PyObject *extra = pcc_gc_gray_worklist_take_locked();
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    int tokens = scan.mode == 1 ? ((a == owner && b == child) || (a == child && b == owner))
                               : (a == (scan.mode == 0 ? child : owner) && b == NULL);
    if (!tokens || extra || scan.root != owner) return 9;
    pcc_gc_store_root(&scan.root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    printf("GC2_CLAIM_GAP_OK mode=%d cuts=2 remaining=%lld\n",
           scan.mode, (long long)credit);
    return 0;
}
