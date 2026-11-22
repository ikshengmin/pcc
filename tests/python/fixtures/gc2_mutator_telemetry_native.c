/* Stock GC2 scheduler diagnostic, not a throughput or latency benchmark. */
#include "py_runtime.h"
#include <stdio.h>
#include <string.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_telemetry(int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_live_bytes_flush(void);
extern int64_t pcc_gc_tracing_cycle_epoch, pcc_gc_tracing_finish_commits, pcc_gc_live_bytes;
extern int32_t pcc_gc_mark_active, pcc_gc_gray_worklist_flush_requested;
extern int32_t pcc_gc_cms_finish_pending, pcc_gc_cms_sweep_pending;
extern int32_t pcc_gc_gray_worklist_fallback_required, pcc_gc_cms_worker_traces;
extern int32_t pcc_gc_cms_mutator_assists, pcc_gc_cms_wb_flushes;
extern int64_t pcc_gc_debt_bytes, pcc_gc_metric_sweep_candidate_calls;
extern void *pcc_gc_gray_published_batches, *pcc_gc_gray_pending_batches;

enum {
    MAX_WORKERS = 4, TOTAL_LISTS = 48000,
    METRIC_WORKER_STARTS = 11, METRIC_ASSISTS = 14, METRIC_WORKER_TRACES = 18,
    METRIC_STW_COUNT = 119, METRIC_MAX_STW_US = 121,
    METRIC_SWEEP_CALLS = 126, HOLD_MAX_US = 50000
};

typedef struct {
    int64_t epoch, finishes, sweeps, live, gray, debt;
    int32_t mark, flush, finish, sweep, published, pending, fallback, traces, assists, write_shades;
} Phase;

/* Direct loads: pcc_gc_telemetry would flush even this producer's local
 * allocation and live-byte batches. No extra gate or flush here, and the
 * individual counter reads do not form a stopped-world snapshot. */
static void observe(Phase *out) {
    out->epoch = __atomic_load_n(&pcc_gc_tracing_cycle_epoch, __ATOMIC_ACQUIRE);
    out->finishes = __atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE);
    out->sweeps = __atomic_load_n(&pcc_gc_metric_sweep_candidate_calls, __ATOMIC_RELAXED);
    out->live = __atomic_load_n(&pcc_gc_live_bytes, __ATOMIC_RELAXED);
    out->mark = __atomic_load_n(&pcc_gc_mark_active, __ATOMIC_ACQUIRE);
    out->gray = pcc_gc_gray_count_load_acquire();
    out->flush = __atomic_load_n(&pcc_gc_gray_worklist_flush_requested, __ATOMIC_ACQUIRE);
    out->finish = __atomic_load_n(&pcc_gc_cms_finish_pending, __ATOMIC_ACQUIRE);
    out->sweep = __atomic_load_n(&pcc_gc_cms_sweep_pending, __ATOMIC_ACQUIRE);
    out->published = __atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches,
                                     __ATOMIC_ACQUIRE) != 0;
    out->pending = __atomic_load_n((uintptr_t *)&pcc_gc_gray_pending_batches,
                                   __ATOMIC_RELAXED) != 0;
    out->fallback = __atomic_load_n(&pcc_gc_gray_worklist_fallback_required, __ATOMIC_ACQUIRE);
    out->debt = __atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_ACQUIRE);
    out->traces = __atomic_load_n(&pcc_gc_cms_worker_traces, __ATOMIC_RELAXED);
    out->assists = __atomic_load_n(&pcc_gc_cms_mutator_assists, __ATOMIC_RELAXED);
    out->write_shades = __atomic_load_n(&pcc_gc_cms_wb_flushes, __ATOMIC_RELAXED);
}

static void report(const char *name, const Phase *p) {
    printf("%s epoch=%lld finishes=%lld sweeps=%lld live_bytes=%lld "
           "mark=%d gray=%lld flush=%d finish=%d sweep=%d "
           "published=%d pending=%d fallback=%d debt=%lld traces=%d assists=%d write_shades=%d\n", name,
           (long long)p->epoch, (long long)p->finishes, (long long)p->sweeps,
           (long long)p->live, p->mark, (long long)p->gray,
           p->flush, p->finish, p->sweep, p->published, p->pending, p->fallback,
           (long long)p->debt, p->traces, p->assists, p->write_shades);
}

typedef struct {
    int ready, go, hold, workers, finished, poll, leave;
    Phase midpoint, held, polled;
    PyObject *roots[MAX_WORKERS];
} Shared;

typedef struct {
    Shared *shared;
    int worker, count, completed;
    int64_t id;
} Task;

static void hold_mutators(Task *task) {
    Shared *shared = task->shared;
    __atomic_add_fetch(&shared->finished, 1, __ATOMIC_RELEASE);
    if (task->worker != 0) {
        while (!__atomic_load_n(&shared->poll, __ATOMIC_ACQUIRE))
            pcc_thread_safepoint();
        while (!__atomic_load_n(&shared->leave, __ATOMIC_ACQUIRE))
            pcc_gc_safepoint();
        return;
    }
    while (__atomic_load_n(&shared->finished, __ATOMIC_ACQUIRE) != shared->workers)
        pcc_thread_safepoint();
    observe(&shared->held);
    __atomic_store_n(&shared->poll, 1, __ATOMIC_RELEASE);
    int64_t deadline = pcc_runtime_monotonic_us() + HOLD_MAX_US;
    do {
        pcc_gc_safepoint();
        observe(&shared->polled);
    } while (shared->held.mark && shared->polled.finishes == shared->held.finishes
             && pcc_runtime_monotonic_us() < deadline);
    __atomic_store_n(&shared->leave, 1, __ATOMIC_RELEASE);
}

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
        if (task->worker == 0 && i == task->count / 2)
            observe(&task->shared->midpoint);
    }
    task->completed = task->count;
    if (task->shared->hold) hold_mutators(task);
    return pcc_gc_pointer_is_managed(root) ? NULL : (void *)3;
}

int main(int argc, char **argv) {
    if ((argc != 2 && argc != 3) || (argv[1][0] != '1' && argv[1][0] != '4') || argv[1][1])
        return 1;
    int workers = argv[1][0] - '0';
    if (pcc_gc_backend() != 2 || pcc_current_thread_id() <= 0) return 2;
    py_gc_disable();
    Shared shared = {0};
    if (argc == 3 && strcmp(argv[2], "hold") != 0) return 1;
    shared.hold = argc == 3;
    shared.workers = workers;
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
        tasks[i].count = TOTAL_LISTS / workers;
        tasks[i].completed = 0;
        if (pcc_thread_start(&threads[i], allocate, &tasks[i]) != 0 || !threads[i])
            return 4;
    }
    while (__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE) != workers)
        pcc_thread_safepoint();
    Phase before, joined, drained;
    pcc_gc_live_bytes_flush();
    observe(&before);
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
    observe(&joined);
    int64_t work_stw = pcc_gc_telemetry(METRIC_STW_COUNT);
    /* A finite workload can exit with the CMS mark unfinished. Keep a
     * mutator at ordinary gates briefly to observe the eventual handoff.
     * Record how many pauses had already happened before this drain. */
    int64_t deadline = pcc_runtime_monotonic_us() + 500000;
    while (pcc_gc_telemetry(METRIC_STW_COUNT) == 0 &&
           pcc_runtime_monotonic_us() < deadline)
        pcc_gc_safepoint();
    observe(&drained);
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
           "sweeps=%lld work_stw=%lld stw=%lld max_stw_us=%lld\n", workers, completed,
           (long long)assists, (long long)traces, (long long)sweeps,
           (long long)work_stw, (long long)stw, (long long)max_us);
    report("before", &before);
    report("mid", &shared.midpoint);
    if (shared.hold) {
        report("held", &shared.held);
        report("polled", &shared.polled);
    }
    report("joined", &joined);
    report("drained", &drained);
    return 0;
}
