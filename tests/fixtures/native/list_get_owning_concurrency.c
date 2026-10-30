#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

/* Run against the store-fixed runtime first, then the getter candidate.
 * Default uses public py_list_getitem. Argument "0" selects py_list_get.
 * Both have the public NEW-reference contract. No test owner keeps replaced
 * values alive: source field + writer root + actual returned owners only. */
#define CHURN 30000
#define CHECKSUM 19317
static int64_t ready;
static int64_t go;
static int64_t writer_done;
static int64_t reader_done;
static int64_t failures;
static int64_t copied;
static int64_t raising = 1;
static void fail(void) { __atomic_fetch_add(&failures, 1, __ATOMIC_RELAXED); }
static void wait_for(int64_t *word, int64_t desired) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) < desired) pcc_thread_safepoint();
}
static int64_t active(void) {
    /* Called before worker start or after joins: all lease changes quiesced. */
    return pcc_gc_foreign_lease_count_quiescent();
}
static int64_t refs(PyObject *object) {
    return __atomic_load_n((int64_t *)object, __ATOMIC_ACQUIRE);
}
struct Context { PyObject *container; };

static void *write_values(void *argument) {
    struct Context *context = (struct Context *)argument;
    PyObject *roots[1] = {NULL};
    int32_t map = 1;
    pcc_gc_frame_enter(&map, roots);
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&go, 1);
    for (int64_t iteration = 1; iteration <= CHURN; iteration++) {
        /* Transfer constructor's NEW reference directly to the registered
         * owning slot before the next call/safepoint. */
        roots[0] = py_list_new(2);
        if (!roots[0]) { fail(); break; }
        py_list_append(roots[0], py_int_from_i64(iteration));
        py_list_append(roots[0], py_int_from_i64(iteration ^ CHECKSUM));
        /* Round24 store fix makes this public write use graph exclusion. */
        if (py_list_setitem(context->container, 0, roots[0]) != 0) fail();
        pcc_gc_store_root(&roots[0], NULL);
        if ((iteration & 63) == 0) pcc_thread_safepoint();
    }
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    __atomic_store_n(&writer_done, 1, __ATOMIC_RELEASE);
    return NULL;
}

static void *read_values(void *argument) {
    struct Context *context = (struct Context *)argument;
    PyObject *roots[1] = {NULL};
    int32_t map = 1;
    pcc_gc_frame_enter(&map, roots);
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&go, 1);
    for (int64_t iteration = 0; iteration < CHURN; iteration++) {
        /* The API owes a NEW owner. Root that return immediately; neither
         * caller-side locking nor a pre-retained element masks its race. */
        if (raising) roots[0] = py_list_getitem(context->container, 0);
        else roots[0] = py_list_get(context->container, 0);
        if (!roots[0]) { fail(); break; }
        if (((PyObjectHeader *)roots[0])->type_tag != PY_TYPE_LIST || refs(roots[0]) < 1) fail();
        /* Only this returned value is read. Its two tagged fields never
         * change after publication, and GC0 does not move its own result. */
        int64_t first = py_list_get_i64(roots[0], 0);
        int64_t second = py_list_get_i64(roots[0], 1);
        pcc_thread_safepoint();
        if (first < 0 || first > CHURN || second != (first ^ CHECKSUM)) fail();
        if (py_list_get_i64(roots[0], 0) != first || py_list_get_i64(roots[0], 1) != second) fail();
        pcc_gc_store_root(&roots[0], NULL);
        __atomic_fetch_add(&copied, 1, __ATOMIC_RELAXED);
    }
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    __atomic_store_n(&reader_done, 1, __ATOMIC_RELEASE);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc > 1 && argv[1][0] == '0') raising = 0;
    if (pcc_gc_set_backend(0) != 0 || pcc_threads_enabled() == 0) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    int64_t baseline = active();
    roots[0] = py_list_new(1);
    roots[1] = py_list_new(2);
    if (!roots[0] || !roots[1]) return 2;
    py_list_append(roots[1], py_int_from_i64(0));
    py_list_append(roots[1], py_int_from_i64(CHECKSUM));
    py_list_append(roots[0], roots[1]);
    pcc_gc_store_root(&roots[1], NULL);
    int64_t token = pcc_gc_foreign_lease_acquire(&roots[0]);
    if (token != 2) return 3;
    /* This root/lease protects the outer container for both workers. Its
     * one-element backing array is never resized or removed in the test. */
    struct Context context = {roots[0]};
    void *writer = NULL;
    void *reader = NULL;
    void *result = NULL;
    if (pcc_thread_start(&writer, write_values, &context) != 0) return 4;
    if (pcc_thread_start(&reader, read_values, &context) != 0) return 5;
    wait_for(&ready, 2);
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    wait_for(&writer_done, 1);
    wait_for(&reader_done, 1);
    if (pcc_thread_join(writer, &result) != 0 || pcc_thread_join(reader, &result) != 0) return 6;
    if (failures != 0 || copied != CHURN || active() != baseline + 1) return 7;
    if (pcc_gc_foreign_lease_release(&roots[0], token) != 0) return 8;
    pcc_gc_store_root(&roots[0], NULL);
    if (active() != baseline) return 9;
    pcc_gc_frame_leave(roots);
    if (raising) puts("LIST_GETITEM_OWNING_CONCURRENCY_OK");
    else puts("LIST_GET_OWNING_CONCURRENCY_OK");
    return 0;
}
