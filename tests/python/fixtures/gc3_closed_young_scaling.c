/* A cold young-cut complexity probe, not a throughput benchmark. Each process
 * owns one rooted old list and exactly 64 young candidates. Growing the old
 * ballast must not grow the root count or place an array on the native stack.
 * Only the collection is timed; setup, validation and output are outside it.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void pcc_gc_backend3_enable_closed_young_domain(void);
extern int64_t pcc_gc_backend3_young_cut_admitted(void);
extern int64_t pcc_gc_backend3_collect_closed_young(void);
extern int64_t pcc_gc_backend3_young_cut_count(void);
extern void *pcc_gc_backend3_young_list_head(void);
extern void *pcc_gc_object_node_young_next(void *);
extern int64_t pcc_platform_monotonic_ns(void);
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_tracing_finish_commits;
extern void *pcc_gc_backend3_remembered_owner_head;
extern void *pcc_gc_backend3_promotion_head;

static int young_count(void) {
    int count = 0;
    void *node = pcc_gc_backend3_young_list_head();
    while (node != NULL) {
        count++;
        node = pcc_gc_object_node_young_next(node);
    }
    return count;
}

static void make_candidates(void) {
    for (int i = 0; i < 64; i++) {
        PyObject *obj = py_float_from_f64((double)i + 0.5);
        py_decref(obj);
    }
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int count = atoi(argv[1]);
    if (count != 1024 && count != 4096 && count != 16384) return 2;
    if (pcc_gc_set_backend(3) != 0) return 3;
    py_gc_disable();
    pcc_gc_collect(1);
    pcc_gc_backend3_enable_closed_young_domain();
    PyObject *keeper = py_list_new(count);
    void *registration = pcc_gc_scheduler_root_register_handle(&keeper);
    if (registration == NULL) return 4;
    for (int i = 0; i < count; i++) {
        PyObject *obj = py_float_from_f64((double)i + 0.25);
        py_list_append(keeper, obj);
        py_decref(obj);
    }
    if (pcc_gc_backend3_collect_closed_young() < 0) return 5;
    if (young_count() != 0) return 6;
    for (int i = 0; i < count; i++) {
        PyObject *obj = py_list_get(keeper, i);
        int32_t flags = ((int32_t *)obj)[3];
        if ((flags & 384) != 256) return 7;
    }
    make_candidates();
    if (young_count() != 64 || pcc_gc_backend3_young_cut_admitted() != 1 ||
        pcc_gc_mark_active != 0 || pcc_gc_backend3_remembered_owner_head != NULL ||
        pcc_gc_backend3_promotion_head != NULL) return 8;
    int64_t majors = pcc_gc_tracing_finish_commits;
    int64_t cuts = pcc_gc_backend3_young_cut_count();
    int64_t started = pcc_platform_monotonic_ns();
    int64_t freed = pcc_gc_backend3_collect_closed_young();
    int64_t elapsed = pcc_platform_monotonic_ns() - started;
    if (freed <= 0 || freed > 64 || elapsed < 0 || young_count() != 0 ||
        pcc_gc_backend3_young_cut_count() != cuts + 1 ||
        pcc_gc_tracing_finish_commits != majors || pcc_gc_mark_active != 0) return 9;
    for (int i = 0; i < count; i++) {
        if (py_float_value_of(py_list_get(keeper, i)) != (double)i + 0.25) return 10;
    }
    printf("YOUNG_SCALE old=%d young=64 freed=%lld retained=%lld ns=%lld\n",
           count, (long long)freed, (long long)(64 - freed), (long long)elapsed);
    pcc_gc_scheduler_root_unregister_handle(registration);
    return 0;
}
