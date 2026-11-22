/* Native baseline before moving GC1 marking to the allocating mutator.
 * Uses actual startup/worker counters, not a test override of the scheduler.
 */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_gc_telemetry(int64_t);
extern void pcc_gc_maybe_start_cms_worker(void);
extern void pcc_gc_incremental_maybe_auto_step(void);

extern int64_t pcc_gc_tracing_finish_commits;
extern int32_t pcc_gc_cms_worker_starts;

enum { METRIC_CMS_WORKER_STARTS = 11, METRIC_SWEEP_CANDIDATE_CALLS = 126 };

static int check_startup(void) {
    int64_t backend = pcc_gc_backend();
    if (backend < 0 || backend > 4 || pcc_threads_enabled() != 1) return 1;
    py_gc_disable();
    PyObject *root = NULL;
    void *root_handle = pcc_gc_scheduler_root_register_handle(&root);
    if (!root_handle) return 2;
    PyObject *value = py_list_new(0);
    if (!value) return 3;
    pcc_gc_store_root(&root, value);
    if (root != value) return 4;

    int64_t starts = pcc_gc_telemetry(METRIC_CMS_WORKER_STARTS);
    int64_t expected = backend == 0 ? 0 : 1;
    if (starts != expected) return 5;
    for (int i = 0; i < 3; ++i) {
        pcc_gc_maybe_start_cms_worker();
        pcc_gc_incremental_maybe_auto_step();
        if (pcc_gc_telemetry(METRIC_CMS_WORKER_STARTS) != starts) return 6;
    }
    pcc_gc_collect(0);
    pcc_gc_collect(0);
    if (root != value || !pcc_gc_pointer_is_managed(root)) return 7;
    if (pcc_gc_telemetry(METRIC_CMS_WORKER_STARTS) != starts) return 8;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(root_handle);
    printf("GC1_MARKER_BASELINE_OK starts=%lld\n", (long long)starts);
    return 0;
}

static int check_automatic_progress(int expected_workers) {
    if (pcc_gc_backend() != 1) return 20;
    PyObject *owner = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&owner);
    if (!handle) return 21;
    PyObject *rooted = py_list_new(0);
    if (!rooted) return 22;
    pcc_gc_store_root(&owner, rooted);

    int64_t initial_commits = __atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE);
    for (int i = 0; i < 80000; ++i) {
        PyObject *temporary = py_list_new(0);
        if (!temporary) return 23;
        py_decref(temporary);
        pcc_gc_safepoint();
        if (__atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE) > initial_commits &&
            pcc_gc_telemetry(METRIC_SWEEP_CANDIDATE_CALLS) > 0)
            break;
    }
    if (__atomic_load_n(&pcc_gc_tracing_finish_commits, __ATOMIC_ACQUIRE) <= initial_commits)
        return 24;
    if (pcc_gc_telemetry(METRIC_SWEEP_CANDIDATE_CALLS) <= 0) return 25;
    if (__atomic_load_n(&pcc_gc_cms_worker_starts, __ATOMIC_ACQUIRE) != expected_workers)
        return 26;
    if (owner != rooted || !pcc_gc_pointer_is_managed(owner)) return 27;
    pcc_gc_store_root(&owner, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    printf("GC1_AUTOMATIC_PROGRESS_OK starts=%d\n", expected_workers);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 1) return check_startup();
    if (argc == 2 && (argv[1][0] == '0' || argv[1][0] == '1'))
        return check_automatic_progress(argv[1][0] - '0');
    return 28;
}
