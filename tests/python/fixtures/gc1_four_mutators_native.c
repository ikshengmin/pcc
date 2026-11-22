/* The isolated worker-free candidate must seed, mark and sweep while four
 * real PCC mutators allocate. No thread calls explicit collection.
 */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_telemetry(int64_t);
extern int64_t pcc_gc_tracing_finish_commits;
extern int32_t pcc_gc_cms_worker_starts;
extern int32_t pcc_gc_cms_mutator_assists;

enum {
    WORKERS = 4, ALLOCS = 6000, METRIC_SWEEP_CANDIDATE_CALLS = 126,
    METRIC_MAX_STW_US = 121
};

typedef struct {
    int ready;
    int go;
    PyObject *roots[WORKERS];
    void *root_handles[WORKERS];
} Shared;

typedef struct {
    Shared *shared;
    int worker;
    int64_t registered_thread;
    unsigned completed;
} Task;

static void *allocate_loop(void *context) {
    Task *task = context;
    Shared *shared = task->shared;
    task->registered_thread = pcc_current_thread_id();
    if (task->registered_thread <= 0) return (void *)1;
    PyObject *value = py_list_new(0);
    if (!value) return (void *)2;
    PyObject **root = &shared->roots[task->worker];
    pcc_gc_store_root(root, value);
    __atomic_add_fetch(&shared->ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&shared->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    for (int i = 0; i < ALLOCS; ++i) {
        PyObject *temporary = py_list_new(0);
        if (!temporary) return (void *)3;
        py_decref(temporary);
        pcc_gc_safepoint();
    }
    task->completed = ALLOCS;
    if (*root != value || !pcc_gc_pointer_is_managed(*root)) return (void *)4;
    return NULL;
}

int main(void) {
    if (pcc_gc_backend() != 1 || pcc_current_thread_id() <= 0) return 1;
    py_gc_disable();
    Shared shared = {0};
    Task task[WORKERS];
    void *threads[WORKERS];
    for (int i = 0; i < WORKERS; ++i) {
        task[i].shared = &shared;
        task[i].worker = i;
        task[i].completed = 0;
        threads[i] = NULL;
        shared.root_handles[i] = pcc_gc_scheduler_root_register_handle(&shared.roots[i]);
        if (!shared.root_handles[i]) return 2;
        if (pcc_thread_start(&threads[i], allocate_loop, &task[i]) != 0 ||
            !threads[i]) return 3;
    }
    while (__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE) != WORKERS)
        pcc_thread_safepoint();
    int64_t commits_before = __atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE);
    py_gc_enable();
    __atomic_store_n(&shared.go, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < WORKERS; ++i) {
        void *status = NULL;
        if (pcc_thread_join(threads[i], &status) != 0 || status != NULL)
            return 4;
    }
    for (int i = 0; i < WORKERS; ++i) {
        if (task[i].completed != ALLOCS || !shared.roots[i]
            || !pcc_gc_pointer_is_managed(shared.roots[i]))
            return 5;
        for (int j = 0; j < i; ++j)
            if (task[i].registered_thread == task[j].registered_thread ||
                shared.roots[i] == shared.roots[j])
                return 6;
    }
    for (int i = 0; i < WORKERS; ++i) {
        pcc_gc_store_root(&shared.roots[i], NULL);
        pcc_gc_scheduler_root_unregister_handle(shared.root_handles[i]);
    }
    if (__atomic_load_n(&pcc_gc_cms_worker_starts, __ATOMIC_ACQUIRE) != 0 ||
        __atomic_load_n(&pcc_gc_cms_mutator_assists, __ATOMIC_ACQUIRE) != 0)
        return 7;
    int64_t commits = __atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE) - commits_before;
    int64_t candidate_sweeps = pcc_gc_telemetry(METRIC_SWEEP_CANDIDATE_CALLS);
    int64_t max_pause = pcc_gc_telemetry(METRIC_MAX_STW_US);
    if (commits <= 0 || candidate_sweeps <= 0 || max_pause <= 0) return 8;
    printf("GC1_FOUR_MUTATORS_OK commits=%lld sweeps=%lld max_stw_us=%lld\n",
           (long long)commits, (long long)candidate_sweeps, (long long)max_pause);
    return 0;
}
