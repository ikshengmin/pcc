/* Real automatic GC2, with the candidate and stock sharing one owned ELF. */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_telemetry(int64_t);
extern int32_t pcc_gc2_review_list_bridge_enabled;
extern int64_t pcc_gc2_exact_list_bridge_mode(void);
extern int64_t pcc_gc2_review_list_bridge_visits;
extern int64_t pcc_gc2_review_list_bridge_fallbacks;
extern int64_t pcc_gc_tracing_finish_commits;

enum {
    MAX_WORKERS = 4, TOTAL_LISTS = 48000,
    METRIC_WORKER_STARTS = 11, METRIC_ASSISTS = 14, METRIC_WORKER_TRACES = 18,
    METRIC_STW_COUNT = 119, METRIC_MAX_STW_US = 121,
    METRIC_SWEEP_CALLS = 126, LONG_LIST_SIZE = 40
};

typedef struct {
    int ready, go;
    PyObject *roots[MAX_WORKERS];
} Shared;

typedef struct {
    Shared *shared;
    int worker, count, completed;
    int64_t id;
} Task;

static void *allocate(void *context) {
    Task *task = context;
    task->id = pcc_current_thread_id();
    if (task->id <= 0) return (void *)1;
    PyObject *root = task->shared->roots[task->worker];
    __atomic_add_fetch(&task->shared->ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&task->shared->go, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    for (int i = 0; i < task->count; ++i) {
        PyObject *value = py_list_new(0);
        if (!value) return (void *)2;
        py_decref(value);
        pcc_gc_safepoint();
    }
    task->completed = task->count;
    return pcc_gc_pointer_is_managed(root) ? NULL : (void *)3;
}

int main(int argc, char **argv) {
    if (argc != 3 || (argv[1][0] != '1' && argv[1][0] != '4') || argv[1][1]
            || (argv[2][0] < '0' || argv[2][0] > '2') || argv[2][1])
        return 1;
    int workers = argv[1][0] - '0';
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    int enabled = argv[2][0] - '0';
    if (enabled == 1)
        __atomic_store_n(&pcc_gc2_review_list_bridge_enabled, 1, __ATOMIC_RELEASE);
    else if (pcc_gc2_exact_list_bridge_mode() != enabled)
        return 11;
    int64_t start_commits = pcc_gc_tracing_finish_commits;
    Shared shared = {0};
    Task tasks[MAX_WORKERS];
    void *handles[MAX_WORKERS], *threads[MAX_WORKERS];
    PyObject *originals[MAX_WORKERS], *children[MAX_WORKERS];
    for (int i = 0; i < MAX_WORKERS; ++i) {
        handles[i] = pcc_gc_scheduler_root_register_handle(&shared.roots[i]);
        int length = i % 2 == 0 ? 1 : LONG_LIST_SIZE;
        originals[i] = py_list_new(length);
        children[i] = py_list_new(0);
        if (!handles[i] || !originals[i] || !children[i]) return 3;
        pcc_gc_store_root(&shared.roots[i], originals[i]);
        for (int j = 0; j < length; j++) py_list_append(originals[i], children[i]);
    }
    for (int i = 0; i < workers; ++i) {
        tasks[i].shared = &shared;
        tasks[i].worker = i;
        tasks[i].count = TOTAL_LISTS / workers;
        tasks[i].completed = 0;
        if (pcc_thread_start(&threads[i], allocate, &tasks[i]) != 0 || !threads[i])
            return 4;
    }
    while (__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE) != workers)
        pcc_thread_safepoint();
    py_gc_enable();
    __atomic_store_n(&shared.go, 1, __ATOMIC_RELEASE);
    int completed = 0;
    for (int i = 0; i < workers; ++i) {
        void *status = NULL;
        if (pcc_thread_join(threads[i], &status) != 0 || status != NULL) return 5;
        completed += tasks[i].completed;
        for (int j = 0; j < i; ++j)
            if (tasks[i].id == tasks[j].id) return 6;
    }
    if (completed != TOTAL_LISTS) return 7;
    /* A finite workload can exit with the CMS mark unfinished. Keep a
     * mutator at ordinary gates briefly to observe the eventual handoff. */
    int64_t deadline = pcc_runtime_monotonic_us() + 1500000;
    while ((pcc_gc_telemetry(METRIC_SWEEP_CALLS) == 0 ||
            (enabled && (!__atomic_load_n(&pcc_gc2_review_list_bridge_visits, __ATOMIC_ACQUIRE) ||
                         !__atomic_load_n(&pcc_gc2_review_list_bridge_fallbacks, __ATOMIC_ACQUIRE)))) &&
           pcc_runtime_monotonic_us() < deadline)
        pcc_gc_safepoint();
    py_gc_disable();
    int64_t starts = pcc_gc_telemetry(METRIC_WORKER_STARTS);
    int64_t assists = pcc_gc_telemetry(METRIC_ASSISTS);
    int64_t traces = pcc_gc_telemetry(METRIC_WORKER_TRACES);
    int64_t sweeps = pcc_gc_telemetry(METRIC_SWEEP_CALLS);
    int64_t stw = pcc_gc_telemetry(METRIC_STW_COUNT);
    int64_t max_us = pcc_gc_telemetry(METRIC_MAX_STW_US);
    if (starts != 1 || assists < 0 || traces <= 0 || sweeps <= 0 || stw <= 0 || max_us <= 0) {
        fprintf(stderr, "telemetry starts=%lld assists=%lld traces=%lld sweeps=%lld stw=%lld max_stw_us=%lld\n",
                (long long)starts, (long long)assists, (long long)traces,
                (long long)sweeps, (long long)stw, (long long)max_us);
        return 8;
    }
    int64_t visits = __atomic_load_n(&pcc_gc2_review_list_bridge_visits, __ATOMIC_ACQUIRE);
    int64_t fallbacks = __atomic_load_n(&pcc_gc2_review_list_bridge_fallbacks, __ATOMIC_ACQUIRE);
    int64_t commits = pcc_gc_tracing_finish_commits - start_commits;
    if (commits <= 0 || (enabled ? (visits <= 0 || fallbacks <= 0) : (visits != 0 || fallbacks != 0))) {
        fprintf(stderr, "bridge: enabled=%d visits=%lld fallback=%lld commit=%lld\n",
            enabled, (long long)visits, (long long)fallbacks, (long long)commits);
        return 10;
    }
    for (int i = 0; i < MAX_WORKERS; ++i) {
        if (shared.roots[i] != originals[i] || !pcc_gc_pointer_is_managed(shared.roots[i]))
            return 9;
        PyObject *first = py_list_get(shared.roots[i], 0);
        if (first != children[i] || !pcc_gc_pointer_is_managed(first))
            return 9;
        py_decref(first);
        pcc_gc_store_root(&shared.roots[i], NULL);
        pcc_gc_scheduler_root_unregister_handle(handles[i]);
    }
    printf("GC2_ACTUAL_LIST_BRIDGE_OK workers=%d enabled=%d visits=%lld fallbacks=%lld commits=%lld\n",
           workers, enabled, (long long)visits, (long long)fallbacks, (long long)commits);
    return 0;
}
