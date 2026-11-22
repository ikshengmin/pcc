/* Invoke the dormant GC2 kernel against an explicitly rooted list.
 * A controlled active epoch exercises the source without a full GC. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern int64_t pcc_gc_object_index_seen_stable(PyObject *);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern int64_t pcc_gc_trace_gc2_rooted_short_list(PyObject **, int64_t);
extern int64_t pcc_gc_tracing_cycle_epoch;
extern void pcc_gc_gray_worklist_flush(void);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int32_t pcc_gc_mark_active;

enum { WHITE = 8, GRAY = 16, COLORS = 56, EPOCH = 41 };

typedef struct {
    PyObject *owner, *early_child, *late_child;
    int go, completed;
} Task;

static void *append_late(void *arg) {
    Task *task = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    while (!__atomic_load_n(&task->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    py_list_append(task->owner, task->late_child);
    pcc_gc_gray_worklist_flush();
    __atomic_store_n(&task->completed, 1, __ATOMIC_RELEASE);
    return NULL;
}

static int64_t color(PyObject *obj) {
    return py_header_flags_load(py_header(obj)) & COLORS;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    if (pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    Task task = {0};
    task.owner = py_list_new(4);
    task.early_child = py_list_new(0);
    task.late_child = py_list_new(0);
    PyObject *unsupported = py_tuple_new(0);
    PyObject *oversized = py_list_new(40);
    PyObject *control = py_list_new(0);
    if (!task.owner || !task.early_child || !task.late_child ||
        !unsupported || !oversized || !control) return 2;
    for (int i = 0; i < 40; ++i) py_list_append(oversized, control);
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!handle) return 3;
    pcc_gc_store_root(&root, task.owner);
    if (backend != 2) {
        __atomic_store_n(&pcc_gc_tracing_cycle_epoch, EPOCH, __ATOMIC_RELEASE);
        __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
        int64_t work = pcc_gc_trace_gc2_rooted_short_list(&root, EPOCH);
        __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
        pcc_gc_store_root(&root, NULL);
        pcc_gc_scheduler_root_unregister_handle(handle);
        if (work != 0) return 12;
        printf("GC2_LIST_SHADE_UNSUPPORTED backend=%lld\n", (long long)backend);
        return 0;
    }
    py_list_append(task.owner, task.early_child);
    py_list_append(task.owner, task.early_child);
    if (pcc_gc_trace_gc2_rooted_short_list(&root, EPOCH) != 0) return 10;
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    int indexed = pcc_gc_object_index_seen_stable(task.early_child) &&
                  pcc_gc_object_index_seen_stable(task.late_child);
    int idle = pcc_gc_gray_count_load_acquire() == 0 && pcc_gc_mark_active == 0;
    pcc_gc_set_mark_color(task.early_child, WHITE);
    pcc_gc_set_mark_color(task.late_child, WHITE);
    pcc_gc_set_mark_color(control, WHITE);
    __atomic_store_n(&pcc_gc_tracing_cycle_epoch, EPOCH, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    int graph_rejected = pcc_gc_trace_gc2_rooted_short_list(&root, EPOCH) == 0;
    pcc_py_gc_minor_graph_unlock();
    if (!indexed || !idle || !graph_rejected) return 4;
    if (pcc_gc_trace_gc2_rooted_short_list(&root, EPOCH - 1) != 0 ||
        pcc_gc_trace_gc2_rooted_short_list(&unsupported, EPOCH) != 0 ||
        pcc_gc_trace_gc2_rooted_short_list(&oversized, EPOCH) != 0 ||
        color(control) != WHITE || color(task.early_child) != WHITE ||
        pcc_gc_gray_count_load_acquire() != 0)
        return 11;

    int64_t work = pcc_gc_trace_gc2_rooted_short_list(&root, EPOCH);
    if (work != 2 || color(task.early_child) != GRAY ||
        color(task.late_child) != WHITE || pcc_gc_gray_count_load_acquire() != 1)
        return 7;
    void *thread = NULL;
    if (pcc_thread_start(&thread, append_late, &task) != 0 || !thread) return 5;
    __atomic_store_n(&task.go, 1, __ATOMIC_RELEASE);
    pcc_gc_gray_worklist_flush();
    void *status = NULL;
    if (pcc_thread_join(thread, &status) != 0 || status) return 6;
    if (!task.completed || color(task.late_child) != GRAY ||
        pcc_gc_gray_count_load_acquire() != 2 || py_list_len(task.owner) != 3)
        return 8;
    pcc_py_gc_minor_graph_lock();
    PyObject *a = pcc_gc_gray_worklist_take_locked();
    PyObject *b = pcc_gc_gray_worklist_take_locked();
    PyObject *extra = pcc_gc_gray_worklist_take_locked();
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (!((a == task.early_child && b == task.late_child) ||
          (b == task.early_child && a == task.late_child)) || extra)
        return 9;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    puts("GC2_LIST_SHADE_OK scanned=1 late_barrier=1 queue=2 rejections=5");
    return 0;
}
