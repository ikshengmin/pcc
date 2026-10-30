#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

/* Every published element is a valid int64 outside the tagged range.
 * Only list[0] and the writer's temporary root own the current element.
 * Retired elements have no extra fixture owner. Negative boxed decoys are
 * allocated after replacement to expose reuse of an unowned stale element.
 * argv[1]: 0 normal (also exercises -1 when length is one), 1 nonnegative.
 * argv[2]: 1 additionally appends tagged integers, forcing buffer growth. */
#define CHURN 50000
#define READS 150000
#define VALUE_A INT64_C(4611686018427388001)
#define VALUE_B INT64_C(4611686018427388127)
#define DECOY INT64_C(-9223372036854775000)
static int64_t ready, go, writer_done, reader_done, failures, copied, first_bad;
static int64_t nonnegative, growth;
struct Context { PyObject *container; };

static void fail_value(int64_t value) {
    if (__atomic_fetch_add(&failures, 1, __ATOMIC_RELAXED) == 0)
        __atomic_store_n(&first_bad, value, __ATOMIC_RELAXED);
}
static void wait_for(int64_t *word, int64_t desired) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) < desired) pcc_thread_safepoint();
}
static int64_t active(void) {
    /* Called before worker start or after joins: all lease changes quiesced. */
    return pcc_gc_foreign_lease_count_quiescent();
}
static void *write_values(void *argument) {
    struct Context *context = (struct Context *)argument;
    PyObject *roots[1] = {NULL};
    int32_t map = 1;
    pcc_gc_frame_enter(&map, roots);
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&go, 1);
    for (int64_t iteration = 0; iteration < CHURN; iteration++) {
        roots[0] = py_int_from_i64((iteration & 1) ? VALUE_A : VALUE_B);
        if (!roots[0] || ((uintptr_t)roots[0] & 1)) { fail_value(-1); break; }
        if (py_list_setitem(context->container, 0, roots[0]) != 0) { fail_value(-2); break; }
        pcc_gc_store_root(&roots[0], NULL);
        roots[0] = py_int_from_i64(DECOY);
        if (!roots[0] || ((uintptr_t)roots[0] & 1)) { fail_value(-3); break; }
        pcc_gc_store_root(&roots[0], NULL);
        if (growth) {
            /* Tagged append has no extra owner of any replaced boxed int. */
            py_list_append(context->container, py_int_from_i64(iteration));
            if (py_err_occurred()) { fail_value(-4); break; }
        }
        if ((iteration & 63) == 0) pcc_thread_safepoint();
    }
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    __atomic_store_n(&writer_done, 1, __ATOMIC_RELEASE);
    return NULL;
}
static void *read_values(void *argument) {
    struct Context *context = (struct Context *)argument;
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&go, 1);
    for (int64_t iteration = 0; iteration < READS; iteration++) {
        /* Scalar APIs owe no returned object owner. No caller-side graph
         * lock or element lease shields their load/conversion window. */
        int64_t value;
        if (nonnegative) value = py_list_get_i64_nonnegative(context->container, 0);
        else value = py_list_get_i64(context->container, growth ? 0 : -1);
        if (value != VALUE_A && value != VALUE_B) { fail_value(value); break; }
        __atomic_fetch_add(&copied, 1, __ATOMIC_RELAXED);
        if ((iteration & 63) == 0) pcc_thread_safepoint();
    }
    __atomic_store_n(&reader_done, 1, __ATOMIC_RELEASE);
    return NULL;
}
int main(int argc, char **argv) {
    nonnegative = argc > 1 && argv[1][0] == '1';
    growth = argc > 2 && argv[2][0] == '1';
    if (pcc_gc_set_backend(0) != 0 || pcc_threads_enabled() == 0) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    int64_t baseline = active();
    roots[0] = py_list_new(1);
    roots[1] = py_int_from_i64(VALUE_A);
    if (!roots[0] || !roots[1] || ((uintptr_t)roots[1] & 1)) return 2;
    py_list_append(roots[0], roots[1]);
    pcc_gc_store_root(&roots[1], NULL);
    int64_t token = pcc_gc_foreign_lease_acquire(&roots[0]);
    if (token != 2) return 3;
    /* The list root and its mode lease survive both workers. The lease
     * does not freeze the item field or retain old elements/buffers. */
    struct Context context = {roots[0]};
    void *writer = NULL, *reader = NULL, *result = NULL;
    if (pcc_thread_start(&writer, write_values, &context) != 0) return 4;
    if (pcc_thread_start(&reader, read_values, &context) != 0) return 5;
    wait_for(&ready, 2);
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    wait_for(&writer_done, 1);
    wait_for(&reader_done, 1);
    if (pcc_thread_join(writer, &result) != 0 || pcc_thread_join(reader, &result) != 0) return 6;
    if (failures || copied != READS || active() != baseline + 1) {
        printf("BAD value=%lld copied=%lld failures=%lld\n", (long long)first_bad,
               (long long)copied, (long long)failures);
        return 7;
    }
    if (pcc_gc_foreign_lease_release(&roots[0], token) != 0) return 8;
    pcc_gc_store_root(&roots[0], NULL);
    if (active() != baseline) return 9;
    pcc_gc_frame_leave(roots);
    puts("LIST_GET_SCALAR_CONCURRENCY_OK");
    return 0;
}
