/* Prove slot visitation of two owned GC2 lists can overlap without graph
 * entry. No tracing, real writer race, or worker-pool behavior is implied. */
#include "py_internal.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_list_lock(PyObject *);
extern void pcc_list_unlock(PyObject *);
extern int32_t pcc_gc_list_container_mode;

enum { WORKERS = 2, WAIT_US = 1000000, MAX_LISTS = 64 };
typedef struct { int ready, go, entered, finish; } Shared;
typedef struct {
    Shared *shared;
    PyObject *list;
    PyObject *child;
    int valid, visits;
} Task;

/* Match the currently published 64-stripe selection. A collision is legal
 * and serializes, so that would not be an independent-container probe. */
static uintptr_t stripe(PyObject *list) {
    uintptr_t address = (uintptr_t)list;
    return ((address >> 4) ^ (address >> 13)) & 63;
}

static void observe(PyObject **slot, int32_t role, void *argument) {
    Task *task = argument;
    task->visits++;
    task->valid = role == PY_OBJ_SLOT_OWNED && *slot == task->child;
    __atomic_add_fetch(&task->shared->entered, 1, __ATOMIC_RELEASE);
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US * 3;
    while (!__atomic_load_n(&task->shared->finish, __ATOMIC_ACQUIRE) &&
           pcc_runtime_monotonic_us() < deadline) {
        /* A collector callback must not park or obtain the graph lock. */
    }
    if (!__atomic_load_n(&task->shared->finish, __ATOMIC_ACQUIRE))
        task->valid = 0;
}

static void *scan(void *arg) {
    Task *task = arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    __atomic_add_fetch(&task->shared->ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&task->shared->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    int64_t state[2];
    pcc_list_lock(task->list);
    int64_t handled =
        pcc_gc_visit_object_slots_slice(task->list, 0, 1, observe, task, state);
    pcc_list_unlock(task->list);
    if (handled != 1 || state[0] != -1 || state[1] != 1 ||
        task->visits != 1 || !task->valid) return (void *)2;
    return NULL;
}

int main(void) {
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    Shared shared = {0};
    Task tasks[WORKERS] = {{&shared}, {&shared}};
    void *threads[WORKERS], *handles[WORKERS];
    PyObject *roots[WORKERS] = {NULL, NULL};
    PyObject *first = py_list_new(0);
    PyObject *second = NULL;
    if (!first) return 3;
    if (pcc_gc_list_container_mode == 0) return 2;
    for (int i = 0; i < MAX_LISTS; ++i) {
        PyObject *list = py_list_new(0);
        if (!list) return 4;
        if (stripe(list) != stripe(first)) {
            second = list;
            break;
        }
    }
    if (!second) return 5;
    tasks[0].list = first;
    tasks[1].list = second;
    for (int i = 0; i < WORKERS; ++i) {
        handles[i] = pcc_gc_scheduler_root_register_handle(&roots[i]);
        if (!handles[i]) return 6;
        pcc_gc_store_root(&roots[i], tasks[i].list);
        tasks[i].child = py_list_new(0);
        if (!tasks[i].child) return 7;
        py_list_append(tasks[i].list, tasks[i].child);
        if (pcc_thread_start(&threads[i], scan, &tasks[i]) != 0 || !threads[i])
            return 8;
    }
    int64_t deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE) != WORKERS) {
        if (pcc_runtime_monotonic_us() >= deadline) return 9;
        pcc_thread_safepoint();
    }
    pcc_py_gc_minor_graph_lock();
    __atomic_store_n(&shared.go, 1, __ATOMIC_RELEASE);
    deadline = pcc_runtime_monotonic_us() + WAIT_US;
    while (__atomic_load_n(&shared.entered, __ATOMIC_ACQUIRE) != WORKERS &&
           pcc_runtime_monotonic_us() < deadline) {
    }
    int entered_under_graph = __atomic_load_n(&shared.entered, __ATOMIC_ACQUIRE);
    __atomic_store_n(&shared.finish, 1, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    for (int i = 0; i < WORKERS; ++i) {
        void *result = NULL;
        if (pcc_thread_join(threads[i], &result) != 0 || result != NULL) return 10;
        pcc_gc_store_root(&roots[i], NULL);
        pcc_gc_scheduler_root_unregister_handle(handles[i]);
    }
    if (entered_under_graph != WORKERS) return 11;
    puts("GC2_DISJOINT_LIST_SLOT_OK overlap=2 exact_strong_slots=2");
    return 0;
}
