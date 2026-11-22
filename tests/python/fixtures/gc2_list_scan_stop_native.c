/* Real GC2 list, slot visitor, atomic shade, and stop cut. The negative
 * control may park before publication; no one modifies its rooted list. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_list_lock(PyObject *);
extern void pcc_list_unlock(PyObject *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern void pcc_gc_gray_worklist_flush(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int32_t pcc_gc_mark_active;

enum { MARK_WHITE = 8, WAIT_US = 1000000 };

typedef struct {
    PyObject *owner, *child;
    int locked, entered, published, resumed, failed;
} Scan;

static void visit(PyObject **slot, int32_t role, void *arg) {
    Scan *scan = arg;
    if (role != PY_OBJ_SLOT_OWNED || *slot != scan->child) {
        scan->failed = 1;
        return;
    }
    __atomic_store_n(&scan->entered, 1, __ATOMIC_RELEASE);
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US * 3;
    while (!pcc_thread_stop_requested_acquire() &&
           pcc_runtime_monotonic_us() < deadline) {}
    if (!pcc_thread_stop_requested_acquire()) {
        scan->failed = 1;
        return;
    }
    pcc_thread_safepoint();
    /* The unlocked contrast is paused above; even there perform the real
     * shade under a no-park guard after resuming. */
    pcc_thread_no_park_enter();
    pcc_gc_shade_gray_atomic(*slot, 0);
    pcc_gc_gray_worklist_flush();
    __atomic_store_n(&scan->published, 1, __ATOMIC_RELEASE);
    pcc_thread_no_park_exit();
}

static void *run_scan(void *arg) {
    Scan *scan = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    if (scan->locked) pcc_list_lock(scan->owner);
    int64_t state[2];
    int64_t handled =
        pcc_gc_visit_object_slots_slice(scan->owner, 0, 1, visit, scan, state);
    if (scan->locked) pcc_list_unlock(scan->owner);
    if (handled != 1 || state[0] != -1 || state[1] != 1 || scan->failed)
        return (void *)2;
    __atomic_store_n(&scan->resumed, 1, __ATOMIC_RELEASE);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2 || (argv[1][0] != '0' && argv[1][0] != '1') || argv[1][1])
        return 1;
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    Scan scan = {0};
    scan.locked = argv[1][0] == '1';
    scan.owner = py_list_new(1);
    scan.child = py_list_new(0);
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!scan.owner || !scan.child || !handle) return 3;
    pcc_gc_store_root(&root, scan.owner);
    py_list_append(scan.owner, scan.child);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    int idle = pcc_gc_mark_active == 0 && pcc_gc_gray_count_load_acquire() == 0;
    pcc_gc_set_mark_color(scan.child, MARK_WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (!idle) return 4;

    void *thread = NULL;
    if (pcc_thread_start(&thread, run_scan, &scan) != 0 || !thread) return 5;
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (!__atomic_load_n(&scan.entered, __ATOMIC_ACQUIRE)) {
        if (pcc_runtime_monotonic_us() >= deadline) return 6;
        pcc_thread_safepoint();
    }
    if (pcc_stop_the_world() != 0) return 7;
    int published = __atomic_load_n(&scan.published, __ATOMIC_ACQUIRE);
    int resumed = __atomic_load_n(&scan.resumed, __ATOMIC_ACQUIRE);
    int64_t credit = pcc_gc_gray_count_load_acquire();
    PyObject *token = pcc_gc_gray_worklist_take_locked();
    if (pcc_resume_world() != 0) return 8;
    void *status = NULL;
    if (pcc_thread_join(thread, &status) != 0 || status) return 9;
    if (resumed || published != scan.locked || credit != scan.locked ||
        token != (scan.locked ? scan.child : NULL))
        return 10;
    pcc_py_gc_minor_graph_lock();
    PyObject *after = pcc_gc_gray_worklist_take_locked();
    PyObject *extra = pcc_gc_gray_worklist_take_locked();
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (after != (scan.locked ? NULL : scan.child) || extra ||
        !scan.published || !scan.resumed || root != scan.owner)
        return 11;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    printf("GC2_LIST_SCAN_STOP_OK locked=%d credit_at_cut=%lld\n",
           scan.locked, (long long)credit);
    return 0;
}
