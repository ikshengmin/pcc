#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
extern int64_t pcc_gc_mode_lease_shard_index(PyObject *);
/* Runtime i64 export; py_runtime.h does not currently declare this reader. */
extern int64_t py_int_value_i64(PyObject *);
extern int64_t pcc_gc_foreign_lease_active;
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { CANDIDATES = 128, MOVED = 128, COPIED = 129, INDEXED = 130, ROOTS = 131 };
static int64_t ready, leave;
struct Worker { PyObject **source; int64_t token; };

static void wait_for(int64_t *word, int64_t desired) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) < desired) pcc_thread_safepoint();
}
static void *acquire_then_exit(void *argument) {
    struct Worker *worker = (struct Worker *)argument;
    worker->token = pcc_gc_foreign_lease_acquire(worker->source);
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&leave, 1);
    /* Deliberately transfer release responsibility to main after this
     * originating thread exits. Main keeps the owning slot registered. */
    return NULL;
}
static int64_t stopped_count(void) {
    while (pcc_stop_the_world() != 0) pcc_thread_safepoint();
    int64_t result = pcc_gc_foreign_lease_count_quiescent();
    pcc_resume_world();
    return result;
}
/* The collector is fixed once an object exists, so every other mode is
 * refused. A counted lease also refuses a same-mode reset while it lives;
 * a no-op token (GC0-2 without the provenance probe) does not. */
static int trivial;
static int refuses_all_modes(int64_t original) {
    for (int64_t target = 0; target < 5; target++) {
        if (trivial && target == original) continue;
        if (pcc_gc_set_backend(target) != -1 || pcc_gc_backend() != original) return 0;
    }
    return 1;
}
int main(int argc, char **argv) {
    if (argc != 3) return 1;
    int64_t backend = atoi(argv[1]);
    int different = atoi(argv[2]);
    const char *probe = getenv("PCC_GC_REFCOUNT_PROVENANCE_PROBE");
    trivial = backend <= 2 && !(probe && probe[0] != '0');
    /* A held lease's token and how much it adds to the stopped count. */
    int64_t leaf = trivial ? 0 : 2, held = trivial ? 0 : 1;
    if (pcc_gc_set_backend(backend) != 0 || pcc_threads_enabled() == 0) return 2;
    PyObject *roots[ROOTS] = {NULL};
    int32_t map = ROOTS;
    pcc_gc_frame_enter(&map, roots);
    int64_t baseline = stopped_count();
    int64_t central_before = __atomic_load_n(&pcc_gc_foreign_lease_active, __ATOMIC_ACQUIRE);
    roots[0] = py_int_from_i64(INT64_C(4611686018427388001));
    if (!roots[0] || ((uintptr_t)roots[0] & 1)) return 3;
    int second = 0;
    if (different) {
        for (int i = 1; i < CANDIDATES; i++) {
            roots[i] = py_int_from_i64(INT64_C(4611686018427388001) + i);
            if (!roots[i] || ((uintptr_t)roots[i] & 1)) return 4;
            if (pcc_gc_mode_lease_shard_index(roots[i]) != pcc_gc_mode_lease_shard_index(roots[0])) {
                second = i;
                break;
            }
        }
        if (second == 0) return 5;
    }
    struct Worker workers[2] = {{&roots[0], -99}, {&roots[second], -99}};
    void *handles[2] = {NULL, NULL};
    void *result = NULL;
    if (pcc_thread_start(&handles[0], acquire_then_exit, &workers[0]) != 0) return 6;
    if (pcc_thread_start(&handles[1], acquire_then_exit, &workers[1]) != 0) return 7;
    wait_for(&ready, 2);
    if (workers[0].token != leaf || workers[1].token != leaf) return 8;
    if (stopped_count() != baseline + 2 * held) return 9;
    /* Token2 traffic must not quietly retain writes to the old shared line. */
    if (__atomic_load_n(&pcc_gc_foreign_lease_active, __ATOMIC_ACQUIRE) != central_before) return 10;
    if (!refuses_all_modes(backend)) return 11;
    __atomic_store_n(&leave, 1, __ATOMIC_RELEASE);
    if (pcc_thread_join(handles[0], &result) != 0 || pcc_thread_join(handles[1], &result) != 0) return 12;
    if (stopped_count() != baseline + 2 * held || !refuses_all_modes(backend)) return 13;
    if (pcc_gc_root_move(&roots[MOVED], &roots[0]) != 0 || roots[0] != NULL) return 14;
    int64_t copy_token = pcc_gc_root_copy_lease(&roots[COPIED], &roots[MOVED]);
    if (copy_token != leaf || roots[COPIED] != roots[MOVED] || stopped_count() != baseline + 3 * held) return 15;
    pcc_gc_collect(1);
    if (py_int_value_i64(roots[MOVED]) != INT64_C(4611686018427388001)) return 16;
    if (pcc_gc_foreign_lease_release(&roots[MOVED], workers[0].token) != 0) return 17;
    if (stopped_count() != baseline + 2 * held) return 18;
    if (pcc_gc_foreign_lease_release(&roots[COPIED], copy_token) != 0) return 19;
    PyObject **second_release = different ? &roots[second] : &roots[MOVED];
    if (pcc_gc_foreign_lease_release(second_release, workers[1].token) != 0) return 20;
    if (stopped_count() != baseline) return 21;

    roots[INDEXED] = py_list_new(0);
    if (!roots[INDEXED]) return 22;
    int64_t indexed_token = pcc_gc_foreign_lease_acquire(&roots[INDEXED]);
    int64_t leaf_token = pcc_gc_foreign_lease_acquire(&roots[MOVED]);
    if (indexed_token != (trivial ? 0 : backend == 0 ? 2 : 1) || leaf_token != leaf) return 23;
    if (stopped_count() != baseline + 2 * held || !refuses_all_modes(backend)) return 24;
    if (pcc_gc_foreign_lease_release(&roots[MOVED], leaf_token) != 0) return 25;
    if (pcc_gc_foreign_lease_release(&roots[INDEXED], indexed_token) != 0) return 26;
    if (stopped_count() != baseline || pcc_gc_set_backend(backend) != 0) return 27;
    for (int i = ROOTS - 1; i >= 0; i--) pcc_gc_store_root(&roots[i], NULL);
    pcc_gc_collect(1);
    if (stopped_count() != baseline) return 28;
    pcc_gc_frame_leave(roots);
    puts("ADDRESS_SHARDED_LEASES_OK");
    return 0;
}
