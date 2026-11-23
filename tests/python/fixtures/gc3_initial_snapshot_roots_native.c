/* Exact probe of the real first stop; metadata itself is not a root. */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_allocator_object_publish_fresh(void *);
extern int64_t pcc_gc3_review_collect_young_with_initial_snapshot_stopped(void *, void *, void *);

typedef struct {
    int64_t count;
    PyObject **cells;
    int64_t *marked, *queue;
    int64_t enqueued, invalid;
} YoungCut;
typedef struct {
    int64_t capacity, used, overflow;
    PyObject *owners[1];
} Cards;
typedef struct {
    int64_t capacity, count, owners_visited, strong_slots, failed;
    PyObject *young[1];
} Frontier;
typedef struct {
    PyObject *caller, *registered, *dead, *cells[3];
    uintptr_t caller_interior;
} Input;

static PyObject *alloc_young(void) {
    PyTupleObject *tuple = pcc_allocator_alloc_object(sizeof(PyTupleObject));
    if (!tuple) return NULL;
    tuple->h.refcount = 1;
    tuple->h.type_tag = PY_TYPE_TUPLE;
    tuple->h.flags = PY_FLAG_GC_YOUNG;
    tuple->len = 0;
    if (pcc_allocator_object_publish_fresh(tuple) != 1) return NULL;
    return (PyObject *)tuple;
}

static void *build(void *raw) {
    Input *input = raw;
    input->caller = alloc_young();
    input->registered = alloc_young();
    input->dead = alloc_young();
    if (!input->caller || !input->registered || !input->dead) return (void *)1;
    input->caller_interior = (uintptr_t)input->caller + sizeof(PyObjectHeader) + 1;
    input->cells[0] = input->caller;
    input->cells[1] = input->registered;
    input->cells[2] = input->dead;
    for (int i = 1; i < 3; ++i) {
        PyObject *value = input->cells[i];
        int j = i;
        while (j > 0 && (uintptr_t)input->cells[j - 1] > (uintptr_t)value) {
            input->cells[j] = input->cells[j - 1];
            --j;
        }
        input->cells[j] = value;
    }
    return NULL;
}

int main(int argc, char **argv) {
    int use_caller = argc != 2 || argv[1][0] != 'c';
    int use_registered = argc != 2 || argv[1][0] != 'r';
    if (pcc_gc_backend() != 3) return 1;
    py_gc_disable();
    Input *input = malloc(sizeof(Input));
    if (!input) return 2;
    input->registered = NULL;
    void *root_handle = pcc_gc_scheduler_root_register_handle(&input->registered);
    if (!root_handle) return 3;
    void *thread = NULL, *status = NULL;
    if (pcc_thread_start(&thread, build, input) != 0
        || pcc_thread_join(thread, &status) != 0 || status) return 4;
    int64_t marks[3], queue[3];
    YoungCut cut = {3, input->cells, marks, queue, 0, 0};
    Cards cards = {1, 0, 0, {NULL}};
    Frontier frontier = {1, 0, 0, 0, 0, {NULL}};
    /* Only this value is loaded into the caller frame before the stop. */
    uintptr_t volatile caller_root = 0;
    if (use_caller) caller_root = input->caller_interior;
    if (!use_registered) pcc_gc_scheduler_root_unregister_handle(root_handle);
    if (pcc_stop_the_world() != 0) return 5;
    uintptr_t volatile collector_scratch = (uintptr_t)input->dead;
    int64_t reclaimed = pcc_gc3_review_collect_young_with_initial_snapshot_stopped(&cut, &cards, &frontier);
    int expected_roots = use_caller + use_registered;
    if (reclaimed != 3 - expected_roots || cut.enqueued != expected_roots) {
        fprintf(stderr, "root cut: reclaimed=%lld queued=%lld invalid=%lld\n",
                (long long)reclaimed, (long long)cut.enqueued, (long long)cut.invalid);
        return 6;
    }
    if ((pcc_gc_granule_is_object_start(input->caller) == 1) != use_caller
        || (pcc_gc_granule_is_object_start(input->registered) == 1) != use_registered
        || pcc_gc_granule_is_object_start((PyObject *)collector_scratch) == 1)
        return 7;
    if (pcc_resume_world() != 0) return 8;
    if (use_registered) pcc_gc_scheduler_root_unregister_handle(root_handle);
    if (use_caller && caller_root == 0) return 9;
    printf("GC3_REAL_ROOT_CUT_OK caller=%d registered=%d post_stop_scratch=0\n",
           use_caller, use_registered);
    return 0;
}
