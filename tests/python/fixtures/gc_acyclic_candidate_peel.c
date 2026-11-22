/* Native caller of the inactive freestanding #9 classifier; production
 * dispatch does not invoke the classifier. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_tracing_classify_callback_cohort(
    PyObject *const *, int64_t, int64_t, int64_t, int64_t *);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);

enum { CROWDED_CHILDREN = 25 };
enum { ACYCLIC_DROP = 1, CYCLICALLY_RETAINED = 2, ACYCLIC_HEAD = 3 };

static void mark_candidate(PyObject *obj) {
    pcc_gc_unpin(obj);
    pcc_gc_set_mark_color(obj, PY_FLAG_GC_WHITE);
    __atomic_fetch_or(&py_header(obj)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
}

static PyObject *make_list(void) { return py_list_new(CROWDED_CHILDREN); }

int main(void) {
    int64_t backend = pcc_gc_backend();
    if (backend < 0 || backend > 4) return 1;
    py_gc_disable();

    PyObject *plain_owner = make_list(), *plain_child = make_list();
    PyObject *cycle = make_list(), *downstream = make_list();
    PyObject *crowded = make_list();
    if (!plain_owner || !plain_child || !cycle || !downstream || !crowded) return 2;
    py_list_append(plain_owner, plain_child);
    py_list_append(plain_owner, plain_child);
    py_list_append(cycle, cycle);
    py_list_append(cycle, downstream);
    for (int i = 0; i < CROWDED_CHILDREN; ++i) py_list_append(crowded, downstream);

    if (pcc_stop_the_world() != 0) return 3;
    int64_t epoch = pcc_thread_world_stop_epoch_held();
    pcc_py_gc_minor_graph_lock();
    mark_candidate(cycle);
    mark_candidate(plain_child);
    mark_candidate(downstream);
    mark_candidate(plain_owner);
    mark_candidate(crowded);
    pcc_py_gc_minor_graph_unlock();
    PyObject *vertices[] = {cycle, plain_child, downstream, plain_owner};
    int64_t expected[] = {CYCLICALLY_RETAINED, ACYCLIC_DROP, CYCLICALLY_RETAINED, ACYCLIC_HEAD};
    int64_t out[] = {0, 0, 0, 0};
    if (backend == 0 || backend == 4) {
        if (pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 16, out)
            || out[0] || out[1] || out[2] || out[3]) return 10;
        if (pcc_resume_world() != 0) return 11;
        puts("GC_ACYCLIC_CANDIDATE_PEEL_OK");
        return 0;
    }
    if (!pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 16, out)) return 4;
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 5;
    PyObject *overflow[] = {crowded, downstream};
    PyObject *duplicates[] = {plain_owner, plain_owner};
    if (pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 1, out)
        || pcc_gc_tracing_classify_callback_cohort(overflow, 2, epoch, 16, out)
        || pcc_gc_tracing_classify_callback_cohort(duplicates, 2, epoch, 16, out)) return 6;
    pcc_gc_pin(plain_owner);
    if (pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 16, out)) return 12;
    pcc_gc_unpin(plain_owner);
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 7;
    if (pcc_resume_world() != 0
        || pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 16, out)) return 8;
    if (pcc_stop_the_world() != 0 || pcc_thread_world_stop_epoch_held() <= epoch
        || pcc_gc_tracing_classify_callback_cohort(vertices, 4, epoch, 16, out)
        || pcc_resume_world() != 0) return 13;
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 9;
    puts("GC_ACYCLIC_CANDIDATE_PEEL_OK");
    return 0;
}
