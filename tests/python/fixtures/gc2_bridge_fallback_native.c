/* Force one exact queue token through the actual GC2 scheduling slice. */
#include "py_internal.h"
#include <stdio.h>

extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern int64_t pcc_gc_tracing_cycle_epoch_advance_unlocked(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern void pcc_gc_gray_worklist_flush(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc2_review_list_bridge_enabled;
extern int64_t pcc_gc2_review_list_bridge_visits;
extern int64_t pcc_gc2_review_list_bridge_fallbacks;

enum { AT_LIMIT, OVER_LIMIT, NOT_A_LIST,
       REVIEW_LIMIT = 32, MARK_WHITE = 8, MARK_GRAY = 16, MARK_BLACK = 32, COLORS = 56 };

static int64_t color(PyObject *object) {
    return py_header_flags_load(py_header(object)) & COLORS;
}

int main(int argc, char **argv) {
    if (argc != 3 || argv[1][0] < '0' || argv[1][0] > '2' || argv[1][1] ||
        (argv[2][0] != '0' && argv[2][0] != '1') || argv[2][1])
        return 1;
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    int shape = argv[1][0] - '0', enabled = argv[2][0] - '0';
    __atomic_store_n(&pcc_gc2_review_list_bridge_enabled, enabled, __ATOMIC_RELEASE);
    int length = REVIEW_LIMIT + (shape == OVER_LIMIT);
    PyObject *root = NULL;
    PyObject *owner = shape == NOT_A_LIST ? py_tuple_new(1) : py_list_new(length);
    PyObject *child = py_list_new(0);
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!owner || !child || !handle) return 3;
    pcc_gc_store_root(&root, owner);
    if (shape == NOT_A_LIST)
        py_tuple_set_item(owner, 0, child);
    else
        for (int i = 0; i < length; ++i) py_list_append(owner, child);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    int idle = pcc_gc_mark_active == 0 && pcc_gc_gray_count_load_acquire() == 0;
    pcc_gc_set_mark_color(owner, MARK_WHITE);
    pcc_gc_set_mark_color(child, MARK_WHITE);
    int64_t epoch = pcc_gc_tracing_cycle_epoch_advance_unlocked();
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_gc_shade_gray_atomic(owner, 0);
    pcc_gc_gray_worklist_flush();
    pcc_py_gc_minor_graph_unlock();
    if (!idle || epoch < 1 || pcc_gc_gray_count_load_acquire() != 1) return 4;

    int64_t processed = pcc_gc_tracing_step_cycle(1);
    int64_t visits = __atomic_load_n(&pcc_gc2_review_list_bridge_visits, __ATOMIC_ACQUIRE);
    int64_t fallbacks = __atomic_load_n(&pcc_gc2_review_list_bridge_fallbacks, __ATOMIC_ACQUIRE);
    if (processed != 1 || root != owner || color(owner) != MARK_BLACK ||
        color(child) != MARK_GRAY || pcc_gc_gray_count_load_acquire() != 1 ||
        visits != (enabled && shape == AT_LIMIT) ||
        fallbacks != (enabled && shape == OVER_LIMIT)) {
        fprintf(stderr, "step=%lld owner=%lld child=%lld gray=%lld visits=%lld fallback=%lld\n",
                (long long)processed, (long long)color(owner), (long long)color(child),
                (long long)pcc_gc_gray_count_load_acquire(), (long long)visits, (long long)fallbacks);
        return 5;
    }
    pcc_py_gc_minor_graph_lock();
    PyObject *published = pcc_gc_gray_worklist_take_locked();
    PyObject *extra = pcc_gc_gray_worklist_take_locked();
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (published != child || extra) return 6;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    printf("GC2_FORCED_BRIDGE_FALLBACK_OK shape=%d enabled=%d visits=%lld fallbacks=%lld\n",
           shape, enabled, (long long)visits, (long long)fallbacks);
    return 0;
}
