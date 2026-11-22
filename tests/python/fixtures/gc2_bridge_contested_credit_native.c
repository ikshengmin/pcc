/* Force the alternate scanner and locked fallback cursor onto the same owner. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_list_lock(PyObject *);
extern void pcc_list_unlock(PyObject *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern int64_t pcc_gc_tracing_cycle_epoch_advance_unlocked(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void pcc_gc_trace_cursor_store(void *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern void pcc_gc_gray_worklist_flush(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc2_review_list_bridge_enabled;
extern int32_t pcc_gc_gray_worklist_fallback_required;
extern int64_t pcc_gc2_review_claim_token;

enum { MARK_WHITE = 8, MARK_GRAY = 16, MARK_BLACK = 32, COLORS = 56,
       WAIT_US = 2000000 };

typedef struct {
    PyObject *owner;
    int list_locked, release_list, graph_locked, depth_first;
    int64_t alternate, ordinary;
} Race;

static int wait_flag(int *flag) {
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (!__atomic_load_n(flag, __ATOMIC_ACQUIRE) &&
           pcc_runtime_monotonic_us() < deadline)
        pcc_thread_safepoint();
    return __atomic_load_n(flag, __ATOMIC_ACQUIRE);
}

static void *hold_list(void *arg) {
    Race *race = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    pcc_list_lock(race->owner);
    __atomic_store_n(&race->list_locked, 1, __ATOMIC_RELEASE);
    int64_t deadline = pcc_runtime_monotonic_us() + 3 * WAIT_US;
    while (!__atomic_load_n(&race->release_list, __ATOMIC_ACQUIRE) &&
           pcc_runtime_monotonic_us() < deadline) {}
    pcc_list_unlock(race->owner);
    return __atomic_load_n(&race->release_list, __ATOMIC_ACQUIRE) ? NULL : (void *)2;
}

static void *scan_alternate(void *arg) {
    Race *race = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    race->alternate = pcc_gc_tracing_step_cycle(1);
    return NULL;
}

static void *scan_ordinary(void *arg) {
    Race *race = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    pcc_py_gc_minor_graph_lock();
    /* Keep the graph through the stock cursor scan. The alternate scanner
     * cannot retire even if it acquires the now-free container first. */
    __atomic_store_n(&race->graph_locked, 1, __ATOMIC_RELEASE);
    race->ordinary = pcc_gc_tracing_step_cycle(1 + race->depth_first);
    pcc_py_gc_minor_graph_unlock();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3 || (argv[1][0] != '1' && argv[1][0] != '2') || argv[1][1] ||
        (argv[2][0] != '0' && argv[2][0] != '1') || argv[2][1])
        return 9;
    int mode = argv[1][0] - '0', depth_first = argv[2][0] - '0';
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    __atomic_store_n(&pcc_gc2_review_list_bridge_enabled, mode, __ATOMIC_RELEASE);
    Race race = {0};
    PyObject *owner = py_list_new(1), *child = py_list_new(0), *root = NULL;
    PyObject *parent = depth_first ? py_list_new(1) : NULL;
    PyObject *parent_root = NULL;
    void *parent_handle = depth_first ? pcc_gc_scheduler_root_register_handle(&parent_root) : NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!owner || !child || !handle || (depth_first && (!parent || !parent_handle))) return 2;
    race.owner = owner;
    race.depth_first = depth_first;
    pcc_gc_store_root(&root, owner);
    py_list_append(owner, child);
    if (depth_first) {
        pcc_gc_store_root(&parent_root, parent);
        py_list_append(parent, owner);
    }
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    pcc_gc_set_mark_color(owner, MARK_WHITE);
    pcc_gc_set_mark_color(child, MARK_WHITE);
    int64_t epoch = pcc_gc_tracing_cycle_epoch_advance_unlocked();
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_gc_shade_gray_atomic(owner, 0);
    pcc_gc_gray_worklist_flush();
    if (depth_first) {
        /* The fallback latch will recover the gray parent by cursor. No
         * parent token lets the alternate worker claim it by accident. */
        pcc_gc_set_mark_color(parent, MARK_GRAY);
        pcc_gc_gray_count_store_release(2);
    }
    void *node = pcc_gc_object_index_find(depth_first ? parent : owner);
    pcc_gc_trace_cursor_store(node);
    pcc_py_gc_minor_graph_unlock();
    if (!node || epoch < 1 || pcc_gc_gray_count_load_acquire() != 1 + depth_first) return 3;

    void *threads[3];
    if (pcc_thread_start(&threads[0], hold_list, &race) != 0 || !wait_flag(&race.list_locked) ||
        pcc_thread_start(&threads[1], scan_alternate, &race) != 0)
        return 4;
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    int64_t claim = 0;
    while (pcc_runtime_monotonic_us() < deadline) {
        pcc_py_gc_minor_graph_lock();
        claim = pcc_gc2_review_claim_token;
        pcc_py_gc_minor_graph_unlock();
        if (claim != 0) break;
    }
    if (claim == 0) return 5;
    __atomic_store_n(&pcc_gc_gray_worklist_fallback_required, 1, __ATOMIC_RELEASE);
    if (pcc_thread_start(&threads[2], scan_ordinary, &race) != 0 || !wait_flag(&race.graph_locked))
        return 6;
    __atomic_store_n(&race.release_list, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < 3; ++i) {
        void *status = NULL;
        if (pcc_thread_join(threads[i], &status) != 0 || status) return 7;
    }

    int64_t credit = pcc_gc_gray_count_load_acquire();
    pcc_py_gc_minor_graph_lock();
    PyObject *token = pcc_gc_gray_worklist_take_locked();
    PyObject *extra = pcc_gc_gray_worklist_take_locked();
    int closed = pcc_gc2_review_claim_token == 0;
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (credit != 1 || !closed || token != child || extra || root != owner ||
        (py_header_flags_load(py_header(owner)) & COLORS) != MARK_BLACK ||
        (py_header_flags_load(py_header(child)) & COLORS) != MARK_GRAY ||
        (depth_first && (parent_root != parent ||
         (py_header_flags_load(py_header(parent)) & COLORS) != MARK_BLACK)) ||
        race.ordinary != depth_first + (mode == 1) || race.alternate != (mode == 2)) {
        fprintf(stderr, "ordinary=%lld alternate=%lld gray=%lld closed=%d child=%d extra=%d\n",
                (long long)race.ordinary, (long long)race.alternate, (long long)credit,
                closed, token == child, extra != NULL);
        return 8;
    }
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    if (depth_first) {
        pcc_gc_store_root(&parent_root, NULL);
        pcc_gc_scheduler_root_unregister_handle(parent_handle);
    }
    printf("GC2_CONTESTED_BRIDGE_OK mode=%d depth_first=%d ordinary=%lld alternate=%lld gray=1\n",
           mode, depth_first, (long long)race.ordinary, (long long)race.alternate);
    return 0;
}
