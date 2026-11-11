/* Controlled, closed native component fixture. The ordinary native-host case
 * must be rejected. Other cases explicitly establish the fixture's closed
 * managed domain; this does not stand in for the compiler's admission proof.
 * All objects, allocation, roots, remembered edges and frees use the runtime.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern void pcc_gc_backend3_enable_closed_young_domain(void);
extern void pcc_gc_backend3_revoke_closed_young_domain(void);
extern int64_t pcc_gc_backend3_collect_closed_young(void);
extern int64_t pcc_gc_backend3_young_cut_count(void);
extern int64_t pcc_gc_backend3_young_cut_reclaimed(void);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);
extern void pcc_gc_generational_promote_young_if_known(PyObject *);
extern int64_t pcc_gc_backend3_drain_promotion_worklist(int64_t);
extern int64_t pcc_thread_start(void *, void *, void *);
extern void *pcc_gc_scheduler_root_head;
extern int64_t pcc_thread_join(void *, void *);
extern int32_t pcc_gc_backend3_oldify_suspended;
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_tracing_finish_commits;

/* Release compiler temporaries in a returned frame before the native stack
 * snapshot. The test requires some unreachable objects, not an exact count:
 * conservative machine words may legitimately keep the last allocation. */
static int scheduler_root_count(void) {
    int count = 0;
    void *node = pcc_gc_scheduler_root_head;
    while (node != NULL) {
        count++;
        node = ((void **)node)[1];
    }
    return count;
}

static void discard_scalars(void) {
    for (int i = 0; i < 512; i++) {
        PyObject *obj = py_float_from_f64((double)i + 0.5);
        py_decref(obj);
    }
}

static void promote_owner(PyObject *owner) {
    pcc_py_gc_minor_graph_lock();
    int32_t before = pcc_gc_backend3_oldify_suspended;
    pcc_gc_backend3_oldify_suspended = 1;
    pcc_gc_generational_promote_young_if_known(owner);
    pcc_gc_backend3_drain_promotion_worklist(1024);
    pcc_gc_backend3_oldify_suspended = before;
    pcc_py_gc_minor_graph_unlock();
}

/* No managed pointer crosses this raw thread-start argument. The worker
 * creates its root after runtime registration and keeps it in its own live
 * native frame while it services ordinary safepoints. */
static int ready, release_worker, worker_ok;
static void *worker(void *unused) {
    (void)unused;
    pcc_current_thread_id();
    PyObject *volatile root = py_float_from_f64(73.25);
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&release_worker, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    worker_ok = py_float_value_of(root) == 73.25;
    py_decref(root);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) return 1;
    int backend = argv[1][0] - '0';
    const char *kind = argv[2];
    if (pcc_gc_set_backend(backend) != 0) return 2;
    py_gc_disable();
    pcc_gc_collect(1);
    int64_t majors = pcc_gc_tracing_finish_commits;
    if (strcmp(kind, "unproved") != 0)
        pcc_gc_backend3_enable_closed_young_domain();
    if (strcmp(kind, "revoked") == 0) {
        pcc_gc_backend3_revoke_closed_young_domain();
        pcc_gc_backend3_enable_closed_young_domain();
    }
    PyObject *volatile root = NULL;
    PyObject *owner = NULL;
    PyObject *weak = NULL;
    void *thread = NULL;
    PyObject **scalars = NULL;
    PyObject *original_scalars[5] = {NULL, NULL, NULL, NULL, NULL};
    void *scalar_handles[5] = {NULL, NULL, NULL, NULL, NULL};
    if (strcmp(kind, "scalars") == 0) {
        scalars = (PyObject **)calloc(5, sizeof(PyObject *));
        if (scalars == NULL) return 5;
        scalars[0] = py_int_from_i64(9223372036854775807LL);
        scalars[1] = py_float_from_f64(83.5);
        scalars[2] = py_str_new("young-owned-scalar", 18);
        scalars[3] = py_bytes_new("abc\0xyz", 7);
        scalars[4] = py_complex_new(3.5, 7.25);
        int roots_before = scheduler_root_count();
        for (int i = 0; i < 5; i++) {
            if (scalars[i] == NULL) return 5;
            original_scalars[i] = scalars[i];
            if (backend == 3 && (((int32_t *)scalars[i])[3] & 384) != 128) return 7;
            scalar_handles[i] = pcc_gc_scheduler_root_register_handle(&scalars[i]);
            if (scalar_handles[i] == NULL) return 6;
        }
        /* Heap slots must publish five actual records, not stack sentinels. */
        if (scheduler_root_count() != roots_before + 5) return 8;
    }
    if (strcmp(kind, "stack") == 0) root = py_float_from_f64(83.5);
    if (strcmp(kind, "old-edge") == 0) {
        owner = py_list_new(1);
        promote_owner(owner);
        PyObject *child = py_float_from_f64(91.5);
        py_list_append(owner, child);
        py_decref(child);
    }
    if (strcmp(kind, "weak") == 0) {
        root = py_float_from_f64(31.75);
        weak = py_weakref_new(root, NULL);
    }
    if (strcmp(kind, "thread") == 0) {
        if (pcc_thread_start(&thread, (void *)worker, NULL) != 0) return 3;
        while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    }
    discard_scalars();
    int64_t reclaimed = pcc_gc_backend3_collect_closed_young();
    int admitted = backend == 3 && strcmp(kind, "unproved") != 0 &&
                   strcmp(kind, "revoked") != 0 && strcmp(kind, "weak") != 0;
    int good = admitted ? reclaimed > 0 : reclaimed == -1;
    if (strcmp(kind, "stack") == 0)
        good = good && py_float_value_of(root) == 83.5 && pcc_gc_object_is_known_no_lock(root);
    if (strcmp(kind, "scalars") == 0) {
        const int tags[5] = {PY_TYPE_INT, PY_TYPE_FLOAT, PY_TYPE_STR, PY_TYPE_BYTES, PY_TYPE_COMPLEX};
        for (int i = 0; i < 5; i++) {
            good = good && scalars[i] == original_scalars[i];
            /* GC0 deliberately does not index pointer-free scalar allocations. */
            if (backend != 0)
                good = good && pcc_gc_object_is_known_no_lock(scalars[i]);
            good = good && ((int32_t *)scalars[i])[2] == tags[i];
            if (backend == 3)
                good = good && (((int32_t *)scalars[i])[3] & 384) == 256;
        }
        PyObject *decimal = py_int_format_decimal(scalars[0], 0, 0, 0);
        good = good && decimal != NULL &&
            strcmp(py_str_utf8(decimal), "9223372036854775807") == 0;
        py_decref(decimal);
        good = good && py_float_value_of(scalars[1]) == 83.5;
        good = good && strcmp(py_str_utf8(scalars[2]), "young-owned-scalar") == 0;
        PyObject *needle = py_bytes_new("xyz", 3);
        good = good && py_bytes_len(scalars[3]) == 7 &&
            py_bytes_find(scalars[3], needle) == 4;
        py_decref(needle);
        PyObject *real = py_complex_real(scalars[4]);
        PyObject *imag = py_complex_imag(scalars[4]);
        good = good && py_float_value_of(real) == 3.5 &&
            py_float_value_of(imag) == 7.25;
        py_decref(real);
        py_decref(imag);
        for (int i = 0; i < 5; i++) {
            pcc_gc_scheduler_root_unregister_handle(scalar_handles[i]);
            py_decref(scalars[i]);
        }
        free(scalars);
    }
    if (strcmp(kind, "old-edge") == 0)
        good = good && py_float_value_of(py_list_get(owner, 0)) == 91.5;
    if (strcmp(kind, "weak") == 0)
        good = good && py_weakref_call(weak) == root;
    if (strcmp(kind, "thread") == 0) {
        __atomic_store_n(&release_worker, 1, __ATOMIC_RELEASE);
        void *result = NULL;
        if (pcc_thread_join(thread, &result) != 0) return 4;
        good = good && worker_ok && result == NULL;
    }
    good = good && pcc_gc_mark_active == 0 && pcc_gc_tracing_finish_commits == majors;
    good = good && pcc_gc_backend3_young_cut_count() == (admitted ? 1 : 0);
    good = good && pcc_gc_backend3_young_cut_reclaimed() == (admitted ? reclaimed : 0);
    printf("YOUNG backend=%d case=%s good=%d reclaimed=%lld cuts=%lld\n",
           backend, kind, good, (long long)reclaimed,
           (long long)pcc_gc_backend3_young_cut_count());
    return good ? 0 : 20;
}
