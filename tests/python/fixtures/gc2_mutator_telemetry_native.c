/* Stock GC2 scheduler diagnostic, not a throughput or latency benchmark. */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_telemetry(int64_t);
extern int64_t pcc_gc2_pacer_mode(void);

enum {
    MAX_WORKERS = 4, TOTAL_LISTS = 48000,
    METRIC_WORKER_STARTS = 11, METRIC_ASSISTS = 14, METRIC_WORKER_TRACES = 18,
    METRIC_STW_COUNT = 119, METRIC_MAX_STW_US = 121,
    METRIC_SWEEP_CALLS = 126
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
    if ((argc != 2 && argc != 3) || (argv[1][0] != '1' && argv[1][0] != '4') || argv[1][1])
        return 1;
    if (argc == 3 && (argv[2][0] != 'h' || argv[2][1])) return 10;
    int total = argc == 3 ? 240000 : TOTAL_LISTS;
    int workers = argv[1][0] - '0';
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    Shared shared = {0};
    Task tasks[MAX_WORKERS];
    void *handles[MAX_WORKERS], *threads[MAX_WORKERS];
    PyObject *originals[MAX_WORKERS];
    for (int i = 0; i < MAX_WORKERS; ++i) {
        handles[i] = pcc_gc_scheduler_root_register_handle(&shared.roots[i]);
        originals[i] = py_list_new(0);
        if (!handles[i] || !originals[i]) return 3;
        pcc_gc_store_root(&shared.roots[i], originals[i]);
    }
    for (int i = 0; i < workers; ++i) {
        tasks[i].shared = &shared;
        tasks[i].worker = i;
        tasks[i].count = total / workers;
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
    if (completed != total) return 7;
    int64_t work_stw = pcc_gc_telemetry(METRIC_STW_COUNT);
    /* A finite workload can exit with the CMS mark unfinished. Keep a
     * mutator at ordinary gates briefly to observe the eventual handoff.
     * Record how many pauses had already happened before this drain. */
    int64_t deadline = pcc_runtime_monotonic_us() + 500000;
    while (pcc_gc_telemetry(METRIC_STW_COUNT) == 0 &&
           pcc_runtime_monotonic_us() < deadline)
        pcc_gc_safepoint();
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
    for (int i = 0; i < MAX_WORKERS; ++i) {
        if (shared.roots[i] != originals[i] || !pcc_gc_pointer_is_managed(shared.roots[i]))
            return 9;
        pcc_gc_store_root(&shared.roots[i], NULL);
        pcc_gc_scheduler_root_unregister_handle(handles[i]);
    }
    printf("GC2_MUTATOR_TELEMETRY_OK workers=%d lists=%d assists=%lld traces=%lld "
           "sweeps=%lld work_stw=%lld stw=%lld max_stw_us=%lld", workers, completed,
           (long long)assists, (long long)traces, (long long)sweeps,
           (long long)work_stw, (long long)stw, (long long)max_us);
    if (argc == 3) printf(" pacer=%lld", (long long)pcc_gc2_pacer_mode());
    puts("");
    return 0;
}
