/* Native ABI test caller only; the production runtime remains pcc-Python.
 * Exercise the real setter/graph-lock window before managed allocation. */
#include "py_internal.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int sched_yield(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int32_t pcc_gc_backend_selected;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_backend_frozen;
extern int32_t pcc_gc_cms_worker_started;
static int64_t setter_result = 99;

static void fail(int code, const char *where, int64_t value) {
    fprintf(stderr, "SETTER_RACE_FAIL code=%d where=%s got=%lld\n",
            code, where, (long long)value);
    _Exit(code);
}

static void *setter(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) fail(1, "worker_registration", 0);
    int64_t result = pcc_gc_set_backend(0);
    __atomic_store_n(&setter_result, result, __ATOMIC_RELEASE);
    pcc_thread_unregister_current();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2) fail(2, "argument", argc);
    int late_backend = atoi(argv[1]);
    if (late_backend != 1 && late_backend != 2 && late_backend != 4)
        fail(3, "late_backend", late_backend);
    if (pcc_gc_backend() != 0 || pcc_gc_backend4_no_refcounts != 0 ||
        pcc_gc_backend_frozen != 0) fail(4, "initial_configuration", 0);
    py_gc_disable();
    if (pcc_current_thread_id() <= 0) fail(5, "main_registration", 0);

    pcc_py_gc_minor_graph_lock();
    pthread_t thread;
    if (pthread_create(&thread, NULL, setter, NULL) != 0)
        fail(6, "pthread_create", 0);
    /* Pause=4 proves the setter passed its optimistic checks. This thread
     * still owns the graph word, so the setter cannot yet re-read state. */
    while (__atomic_load_n(&pcc_gc_cms_worker_started, __ATOMIC_ACQUIRE) != 4)
        sched_yield();
    if (__atomic_load_n(&setter_result, __ATOMIC_ACQUIRE) != 99)
        fail(7, "setter_crossed_graph", setter_result);
    __atomic_store_n(&pcc_gc_backend4_no_refcounts, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_gc_backend_selected, late_backend, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();

    int64_t token = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (token != 1) fail(8, "join_token", token);
    if (pthread_join(thread, NULL) != 0) fail(9, "pthread_join", 0);
    pcc_thread_blocking_leave(token);
    if (__atomic_load_n(&setter_result, __ATOMIC_ACQUIRE) != -1)
        fail(10, "late_switch_not_rejected", setter_result);
    if (pcc_gc_backend_selected != late_backend || pcc_gc_backend4_no_refcounts != 1 ||
        pcc_gc_backend_frozen != 0) fail(11, "configuration_changed", pcc_gc_backend_selected);
    if (pcc_gc_cms_worker_started != 1)
        fail(12, "pause_not_released_and_restarted", pcc_gc_cms_worker_started);
    if (pcc_gc_cms_stop_worker() != 0) fail(13, "collector_cleanup", 0);
    printf("SETTER_RACE_NATIVE_OK backend=%d rejected=1 pause_restored=1\n", late_backend);
    return 0;
}
