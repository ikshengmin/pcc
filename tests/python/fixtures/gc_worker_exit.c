/* Real normal-exit callbacks bracket the collector's own shutdown. */
#include "py_runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
extern int64_t pcc_gc_backend0_full_set_concurrent(int64_t);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void *pcc_gc_full_worker_handle;

static int helper_ready, helper_done, observed_scan;
static void *helper_handle;

static void *mutator(void *unused) {
    __atomic_store_n(&helper_ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&helper_done, __ATOMIC_ACQUIRE))
        pcc_thread_safepoint();
    return NULL;
}

static void end_mutator(void) {
    __atomic_store_n(&helper_done, 1, __ATOMIC_RELEASE);
    if (pcc_thread_join(helper_handle, NULL) != 0) abort();
}

static void before_shutdown(void) {
    if (!pcc_gc_backend0_full_counter(3) || !pcc_gc_backend0_full_counter(5)
        || !pcc_gc_backend0_full_counter(4)) abort();
    observed_scan = 1;
}

static void after_shutdown(void) {
    if (!observed_scan || pcc_gc_backend0_full_counter(5) || pcc_gc_full_worker_handle) abort();
    end_mutator();
    puts("exited during scan; worker joined");
}

int main(int argc, char **argv) {
    int disabled = argc == 2 && strcmp(argv[1], "off") == 0;
    py_gc_init();
    py_gc_disable();
    PyObject *old = py_list_new(0);
    for (int i = 0; i < 30000; i++) {
        PyObject *item = py_list_new(0);
        py_list_append(old, item);
        py_decref(item);
    }
    if (py_gc_collect() != 0) return 1;
    if (argc == 2 && strcmp(argv[1], "single") == 0) {
        py_gc_enable();
        if (!pcc_gc_backend0_full_begin() || pcc_gc_backend0_full_counter(5)
            || pcc_gc_full_worker_handle) return 8;
        puts("one mutator scans without worker");
        return 0;
    }
    if (pcc_thread_start(&helper_handle, mutator, NULL) != 0) return 2;
    while (!__atomic_load_n(&helper_ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (disabled) {
        if (pcc_gc_backend0_full_set_concurrent(0) != 1) return 3;
    } else if (atexit(after_shutdown) != 0) return 4;
    pcc_gc_backend0_full_set_slice_us(0);
    py_gc_enable();
    if (!pcc_gc_backend0_full_begin()) return 5;
    if (disabled) {
        if (pcc_gc_backend0_full_counter(5) || pcc_gc_full_worker_handle) return 6;
        end_mutator();
        puts("explicitly disabled");
    } else {
        while (!pcc_gc_backend0_full_counter(4)) pcc_thread_safepoint();
        if (atexit(before_shutdown) != 0) return 7;
        if (argc == 2 && strcmp(argv[1], "exit") == 0) exit(19);
    }
    return 0;
}
