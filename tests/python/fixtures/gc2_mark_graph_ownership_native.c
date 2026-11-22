/* Stock GC2: two registered threads exercise the real mark admission lock. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_py_gc_minor_graph_lock_contended(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern void pcc_gc_trace_cursor_store(void *);
extern void pcc_gc_gray_count_store_release(int64_t);
extern int32_t pcc_gc_mark_active;

enum { MARK_GRAY = 16, WAIT_US = 1000000 };

typedef struct {
    int ready, go, entering, completed;
    int64_t processed;
} Task;

static void *mark_one(void *arg) {
    Task *task = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    __atomic_store_n(&task->ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&task->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    __atomic_store_n(&task->entering, 1, __ATOMIC_RELEASE);
    task->processed = pcc_gc_tracing_step_cycle(1);
    __atomic_store_n(&task->completed, 1, __ATOMIC_RELEASE);
    return NULL;
}

int main(void) {
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    PyObject *object = py_list_new(0);
    if (!handle || !object) return 2;
    pcc_gc_store_root(&root, object);
    void *node = pcc_gc_object_index_find(object);
    if (!node) return 3;
    Task task = {0};
    void *thread = NULL;
    if (pcc_thread_start(&thread, mark_one, &task) != 0 || !thread) return 4;
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (!__atomic_load_n(&task.ready, __ATOMIC_ACQUIRE)) {
        if (pcc_runtime_monotonic_us() >= deadline) return 5;
        pcc_thread_safepoint();
    }

    pcc_py_gc_minor_graph_lock();
    int idle = pcc_gc_mark_active == 0;
    if (idle) {
        pcc_gc_set_mark_color(object, MARK_GRAY);
        pcc_gc_gray_count_store_release(1);
        pcc_gc_trace_cursor_store(node);
        __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&task.go, 1, __ATOMIC_RELEASE);
    deadline = pcc_runtime_monotonic_us() + WAIT_US;
    int waited = 0;
    while (idle && !__atomic_load_n(&task.completed, __ATOMIC_ACQUIRE) &&
           pcc_runtime_monotonic_us() < deadline) {
        if (__atomic_load_n(&task.entering, __ATOMIC_ACQUIRE) &&
            pcc_py_gc_minor_graph_lock_contended() != 0) {
            waited = 1;
            break;
        }
    }
    int early = __atomic_load_n(&task.completed, __ATOMIC_ACQUIRE);
    pcc_py_gc_minor_graph_unlock();
    void *status = NULL;
    if (pcc_thread_join(thread, &status) != 0 || status) return 6;
    if (!idle || !waited || early || task.processed != 1) {
        fprintf(stderr, "GC2 mark idle=%d waiter=%d early=%d processed=%lld\n",
                idle, waited, early, (long long)task.processed);
        return 7;
    }
    if (root != object || !pcc_gc_pointer_is_managed(root)) return 8;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    puts("GC2_MARK_GRAPH_OWNERSHIP_OK waited=1 before_unlock=0 processed=1");
    return 0;
}
