/* Isolate the intended GC2 scan/barrier handoff on one built-in list.
 * The fixture constructs a mark-active epoch; it does not run a full GC. */
#include "py_internal.h"
#include <stdio.h>
#include <string.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_list_lock(PyObject *);
extern void pcc_list_unlock(PyObject *);
extern int64_t pcc_gc_alloc_stage_drain_locked(void);
extern int64_t pcc_gc_object_index_seen_stable(PyObject *);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern void pcc_gc_gray_worklist_flush(void);
extern PyObject *pcc_gc_gray_worklist_take_locked(void);
extern int32_t pcc_gc_mark_active, pcc_gc_cms_wb_flushes;

enum { WHITE = PY_FLAG_GC_WHITE, GRAY = PY_FLAG_GC_GRAY, BLACK = PY_FLAG_GC_BLACK,
       COLORS = WHITE | GRAY | BLACK, WAIT_US = 1000000 };

typedef struct {
    PyObject *owner, *early_child, *late_child;
    int attempting, go, completed, visits, invalid;
} Task;

static void shade_owned(PyObject **slot, int32_t role, void *arg) {
    Task *task = arg;
    task->visits++;
    PyObject *child = *slot;
    if (role != PY_OBJ_SLOT_OWNED || child != task->early_child ||
        pcc_gc_object_index_seen_stable(child) == 0) {
        task->invalid = 1;
        return;
    }
    pcc_gc_shade_gray_atomic(child, 0);
}

static void *append_late(void *arg) {
    Task *task = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    while (!__atomic_load_n(&task->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    __atomic_store_n(&task->attempting, 1, __ATOMIC_RELEASE);
    py_list_append(task->owner, task->late_child);
    pcc_gc_gray_worklist_flush();
    __atomic_store_n(&task->completed, 1, __ATOMIC_RELEASE);
    return NULL;
}

static int64_t color(PyObject *obj) {
    return py_header_flags_load(py_header(obj)) & COLORS;
}

static int private_root_shade(void) {
    PyObject *black = py_list_new(1), *white = py_list_new(0);
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!black || !white || !handle) return 21;
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    pcc_gc_set_mark_color(black, BLACK);
    pcc_gc_set_mark_color(white, WHITE);
    if (pcc_gc_gray_count_load_acquire() != 0 || pcc_gc_mark_active) return 22;
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    int32_t shades = __atomic_load_n(&pcc_gc_cms_wb_flushes, __ATOMIC_RELAXED);
    /* Real list allocation + private root/lease initialization; no children. */
    PyObject *fresh = py_list_new(0);
    int32_t extra = __atomic_load_n(&pcc_gc_cms_wb_flushes, __ATOMIC_RELAXED) - shades;
    if (!fresh || py_list_len(fresh) != 0 || !pcc_gc_object_index_seen_stable(fresh)
        || color(fresh) != GRAY || pcc_gc_gray_count_load_acquire() != 1 || extra != 1) {
        fprintf(stderr, "fresh shade=%d color=%lld gray=%lld\n", extra,
                (long long)(fresh ? color(fresh) : 0),
                (long long)pcc_gc_gray_count_load_acquire());
        return 23;
    }
    /* General existing-root and genuine owning-field stores are separate. */
    pcc_gc_store_root(&root, black);
    if (root != black || color(black) != GRAY || pcc_gc_gray_count_load_acquire() != 2)
        return 24;
    py_list_append(black, white);
    if (py_list_len(black) != 1 || color(white) != GRAY
        || pcc_gc_gray_count_load_acquire() != 3) return 25;
    pcc_gc_gray_worklist_flush();
    pcc_py_gc_minor_graph_lock();
    int found = 0;
    for (int i = 0; i < 3; ++i) {
        PyObject *item = pcc_gc_gray_worklist_take_locked();
        if (item == fresh) found |= 1;
        if (item == black) found |= 2;
        if (item == white) found |= 4;
    }
    int complete = found == 7 && pcc_gc_gray_worklist_take_locked() == NULL;
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (!complete) return 26;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    puts("GC2_PRIVATE_ROOT_SHADE_OK fresh=1 old_black=1 old_white=1");
    return 0;
}

int main(int argc, char **argv) {
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    if (argc == 2 && strcmp(argv[1], "private") == 0) return private_root_shade();
    if (argc != 1) return 1;
    Task task = {0};
    task.owner = py_list_new(4);
    task.early_child = py_list_new(0);
    task.late_child = py_list_new(0);
    if (!task.owner || !task.early_child || !task.late_child) return 2;
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!handle) return 3;
    pcc_gc_store_root(&root, task.owner);
    py_list_append(task.owner, task.early_child);
    py_list_append(task.owner, task.early_child);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_alloc_stage_drain_locked();
    int indexed = pcc_gc_object_index_seen_stable(task.early_child) &&
                  pcc_gc_object_index_seen_stable(task.late_child);
    int idle = pcc_gc_gray_count_load_acquire() == 0 && pcc_gc_mark_active == 0;
    pcc_gc_set_mark_color(task.early_child, WHITE);
    pcc_gc_set_mark_color(task.late_child, WHITE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    if (!indexed || !idle) return 4;

    void *thread = NULL;
    if (pcc_thread_start(&thread, append_late, &task) != 0 || !thread) return 5;
    int64_t state[2];
    pcc_list_lock(task.owner);
    __atomic_store_n(&task.go, 1, __ATOMIC_RELEASE);
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (!__atomic_load_n(&task.attempting, __ATOMIC_ACQUIRE) &&
           pcc_runtime_monotonic_us() < deadline) {
    }
    int attempted = __atomic_load_n(&task.attempting, __ATOMIC_ACQUIRE);
    int64_t handled =
        pcc_gc_visit_object_slots_slice(task.owner, 0, 4, shade_owned, &task, state);
    int before = handled == 1 && state[0] == -1 && state[1] == 2 &&
                 task.visits == 2 && !task.invalid &&
                 color(task.early_child) == GRAY && color(task.late_child) == WHITE &&
                 pcc_gc_gray_count_load_acquire() == 1 &&
                 !__atomic_load_n(&task.completed, __ATOMIC_ACQUIRE);
    pcc_list_unlock(task.owner);
    pcc_gc_gray_worklist_flush();
    void *status = NULL;
    if (pcc_thread_join(thread, &status) != 0 || status) return 6;
    if (!attempted || !before) return 7;
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
    puts("GC2_LIST_SHADE_OK scanned=1 late_barrier=1 queue=2");
    return 0;
}
