/* Ordinary default ownership: no tracing-only environment flag is consulted. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_backend_frozen;
extern int32_t pcc_gc_refcount_atomic;
extern int64_t pcc_gc_cms_stop_worker(void);

static void fail(int code, const char *where, int64_t got) {
    fprintf(stderr, "DEFAULT_OWNERSHIP_FAIL code=%d where=%s got=%lld\n", code, where, (long long)got);
    _Exit(code);
}

int main(int argc, char **argv) {
    if (argc != 2) fail(1, "argc", argc);
    int backend = atoi(argv[1]);
    if (backend < 0 || backend > 4) fail(2, "backend", backend);
    if (pcc_gc_backend() != backend || pcc_gc_backend4_no_refcounts != (backend != 0))
        fail(3, "default_policy", pcc_gc_backend4_no_refcounts);
    if (pcc_current_thread_id() <= 0) fail(4, "registration", 0);
    py_gc_disable();
    PyObject *object = py_list_new(0);
    if (!object || pcc_gc_backend_frozen != 1) fail(5, "allocation_freeze", pcc_gc_backend_frozen);
    int64_t initial = *(int64_t *)object;
    if (pcc_gc_retain(object) != object) fail(6, "retain_identity", 0);
    int64_t retained = *(int64_t *)object;
    py_incref(object);
    int64_t incremented = *(int64_t *)object;
    pcc_gc_release(object);
    py_decref(object);
    int64_t balanced = *(int64_t *)object;
    int64_t delta = backend == 0 ? 1 : 0;
    if (initial != 1 || retained != initial + delta || incremented != initial + 2*delta || balanced != initial)
        fail(7, "header_counts", incremented);
    if (backend != 0 && pcc_gc_refcount_atomic != 0) fail(8, "counted_fast_path", pcc_gc_refcount_atomic);
    if (pcc_gc_set_backend((backend+1)%5) != -1 || pcc_gc_backend() != backend ||
        pcc_gc_backend4_no_refcounts != (backend != 0)) fail(9, "frozen_change", pcc_gc_backend());
    py_decref(object);
    if (pcc_gc_cms_stop_worker() != 0) fail(10, "worker_cleanup", 0);
    printf("DEFAULT_OWNERSHIP_OK backend=%d initial=%lld retained=%lld incremented=%lld balanced=%lld frozen=1\n",
           backend, (long long)initial, (long long)retained, (long long)incremented, (long long)balanced);
    return 0;
}
