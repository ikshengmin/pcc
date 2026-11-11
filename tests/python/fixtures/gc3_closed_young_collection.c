/* Controlled, closed native component fixture. The ordinary native-host case
 * must be rejected. Other cases explicitly establish the fixture's closed
 * managed domain; this does not stand in for the compiler's admission proof.
 * All objects, allocation, roots, remembered edges and frees use the runtime.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
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
extern int64_t pcc_thread_join(void *, void *);
extern int32_t pcc_gc_backend3_oldify_suspended;
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_tracing_finish_commits;

/* Release compiler temporaries in a returned frame before the native stack
 * snapshot. The test requires some unreachable objects, not an exact count:
 * conservative machine words may legitimately keep the last allocation. */
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
