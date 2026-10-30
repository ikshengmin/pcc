#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

/* GC0 targeted stress. All shared owning slots are traced; the heap source's
 * container and fixed-capacity payload stay leased throughout both workers.
 * No timing sleep is used. This is stress, not a deterministic lock witness. */
#define CHURN 20000
#define RACES 2000
#define CHECKSUM 19317
static int64_t start_gate;
static int64_t heap_ready;
static int64_t writer_done;
static int64_t reader_done;
static int64_t failures;
static int64_t copied;
static int64_t active(void) {
    /* Before start, after joins, or after both racers publish done and wait
     * for the next generation: no worker can change a lease during the sum. */
    return pcc_gc_foreign_lease_count_quiescent();
}
static int64_t refs(PyObject *p) {
    return __atomic_load_n((int64_t *)p, __ATOMIC_ACQUIRE);
}
static void fail(void) { __atomic_fetch_add(&failures, 1, __ATOMIC_RELAXED); }
static void wait_for(int64_t *p, int64_t value) {
    while (__atomic_load_n(p, __ATOMIC_ACQUIRE) < value) pcc_thread_safepoint();
}
struct HeapSource { PyObject *container; PyObject **field; };
static void *replace_heap_source(void *argument) {
    struct HeapSource *context = (struct HeapSource *)argument;
    PyObject *roots[1] = {NULL};
    int32_t map = 1;
    pcc_gc_frame_enter(&map, roots);
    __atomic_fetch_add(&heap_ready, 1, __ATOMIC_RELEASE);
    wait_for(&start_gate, 1);
    for (int64_t i = 1; i <= CHURN; i++) {
        roots[0] = py_list_new(2);
        if (!roots[0]) { fail(); break; }
        py_list_append(roots[0], py_int_from_i64(i));
        py_list_append(roots[0], py_int_from_i64(i ^ CHECKSUM));
        /* The normal list setter uses the graph-protected store plan and
         * retires the previous value after unlock. Bare GC0 store_ptr does
         * not acquire that lock, so it would be the wrong writer control. */
        if (py_list_setitem(context->container, 0, roots[0]) != 0) fail();
        pcc_gc_store_root(&roots[0], NULL);
        if ((i & 63) == 0) pcc_thread_safepoint();
    }
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    __atomic_store_n(&writer_done, 1, __ATOMIC_RELEASE);
    return NULL;
}
static void *copy_heap_source(void *argument) {
    struct HeapSource *context = (struct HeapSource *)argument;
    PyObject *roots[1] = {NULL};
    int32_t map = 1;
    pcc_gc_frame_enter(&map, roots);
    __atomic_fetch_add(&heap_ready, 1, __ATOMIC_RELEASE);
    wait_for(&start_gate, 1);
    for (int64_t i = 0; i < CHURN; i++) {
        int64_t token = pcc_gc_root_copy_lease(&roots[0], context->field);
        if (token != 2 || !roots[0]) { fail(); break; }
        if (((PyObjectHeader *)roots[0])->type_tag != PY_TYPE_LIST || refs(roots[0]) < 1) fail();
        int64_t first = py_list_get_i64(roots[0], 0);
        int64_t second = py_list_get_i64(roots[0], 1);
        /* Yield while copied owner is live; the writer can retire and reuse
         * arbitrarily many preceding objects before these second reads. */
        pcc_thread_safepoint();
        if (first < 0 || first > CHURN || second != (first ^ CHECKSUM)) fail();
        if (py_list_get_i64(roots[0], 0) != first || py_list_get_i64(roots[0], 1) != second) fail();
        if (pcc_gc_foreign_lease_release(&roots[0], token) != 0) fail();
        pcc_gc_store_root(&roots[0], NULL);
        __atomic_fetch_add(&copied, 1, __ATOMIC_RELAXED);
    }
    pcc_gc_frame_leave(roots);
    __atomic_store_n(&reader_done, 1, __ATOMIC_RELEASE);
    return NULL;
}

static int64_t race_generation;
struct Racer { PyObject **source; PyObject **destination; int64_t token; int64_t done; };
static void *race_destination(void *argument) {
    struct Racer *racer = (struct Racer *)argument;
    for (int64_t generation = 1; generation <= RACES; generation++) {
        wait_for(&race_generation, generation);
        racer->token = pcc_gc_root_copy_lease(racer->destination, racer->source);
        __atomic_store_n(&racer->done, generation, __ATOMIC_RELEASE);
    }
    return NULL;
}
int main(void) {
    if (pcc_gc_set_backend(0) != 0 || pcc_threads_enabled() == 0) return 1;
    PyObject *roots[4] = {NULL, NULL, NULL, NULL};
    int32_t map = 4;
    pcc_gc_frame_enter(&map, roots);
    int64_t baseline = active();
    roots[0] = py_list_new(1);
    roots[1] = py_list_new(2);
    if (!roots[0] || !roots[1]) return 2;
    py_list_append(roots[1], py_int_from_i64(0));
    py_list_append(roots[1], py_int_from_i64(CHECKSUM));
    py_list_append(roots[0], roots[1]);
    pcc_gc_store_root(&roots[1], NULL);
    int64_t container_token = pcc_gc_foreign_lease_acquire(&roots[0]);
    if (container_token != 2) return 3;
    struct HeapSource context = {roots[0], *(PyObject ***)((unsigned char *)roots[0] + 32)};
    void *writer = NULL;
    void *reader = NULL;
    void *result = NULL;
    if (pcc_thread_start(&writer, replace_heap_source, &context) != 0) return 4;
    if (pcc_thread_start(&reader, copy_heap_source, &context) != 0) return 5;
    wait_for(&heap_ready, 2);
    __atomic_store_n(&start_gate, 1, __ATOMIC_RELEASE);
    wait_for(&writer_done, 1);
    wait_for(&reader_done, 1);
    if (pcc_thread_join(writer, &result) != 0 || pcc_thread_join(reader, &result) != 0) return 6;
    if (failures != 0 || copied != CHURN || active() != baseline + 1) return 7;
    if (pcc_gc_foreign_lease_release(&roots[0], container_token) != 0) return 8;
    pcc_gc_store_root(&roots[0], NULL);
    if (active() != baseline) return 9;

    struct Racer one = {&roots[0], &roots[2], -99, 0};
    struct Racer two = {&roots[1], &roots[2], -99, 0};
    void *first = NULL;
    void *second = NULL;
    if (pcc_thread_start(&first, race_destination, &one) != 0) return 10;
    if (pcc_thread_start(&second, race_destination, &two) != 0) return 11;
    for (int64_t generation = 1; generation <= RACES; generation++) {
        roots[0] = py_list_new(0);
        roots[1] = py_list_new(0);
        if (!roots[0] || !roots[1] || refs(roots[0]) != 1 || refs(roots[1]) != 1) return 12;
        __atomic_store_n(&race_generation, generation, __ATOMIC_RELEASE);
        wait_for(&one.done, generation);
        wait_for(&two.done, generation);
        int64_t first_won = one.token == 2 && two.token == -4;
        int64_t second_won = two.token == 2 && one.token == -4;
        if (!first_won && !second_won) return 13;
        PyObject *winner = first_won ? roots[0] : roots[1];
        PyObject *loser = first_won ? roots[1] : roots[0];
        if (roots[2] != winner || refs(winner) != 2 || refs(loser) != 1 || active() != baseline + 1) return 14;
        if (pcc_gc_foreign_lease_release(&roots[2], 2) != 0) return 15;
        pcc_gc_store_root(&roots[2], NULL);
        if (refs(roots[0]) != 1 || refs(roots[1]) != 1 || active() != baseline) return 16;
        pcc_gc_store_root(&roots[0], NULL);
        pcc_gc_store_root(&roots[1], NULL);
    }
    if (pcc_thread_join(first, &result) != 0 || pcc_thread_join(second, &result) != 0) return 17;
    pcc_gc_frame_leave(roots);
    puts("ROOT_COPY_CONCURRENCY_OK");
    return 0;
}
