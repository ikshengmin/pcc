/* Owned C / real runtime probe. No managed access is permitted between
 * pcc_thread_blocking_enter and leave. Atomic handshakes and predicate reads
 * in that interval access raw storage or this thread's runtime record only.
 */
#include "py_internal.h"
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int sched_yield(void);
extern int64_t pcc_thread_is_running(void);
extern int64_t pcc_threads_enabled(void);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_gc_root_registry_note_mutation_locked(void);
extern int64_t pcc_gc_visit_scheduler_root_slots(int64_t mode, int64_t resolve);
extern int64_t pcc_gc_root_registry_revision;
extern int32_t pcc_gc_config_initialized;
extern int32_t pcc_gc_backend_frozen;
extern int32_t pcc_gc_backend4_no_refcounts;

enum { THREADS = 4, ROUNDS = 512 };
static PyObject *slots[THREADS];
static PyObject **live_slots[THREADS];
static PyObject *originals[THREADS];
static PyObject *replacement;
static int started;
static int go;
static int exited_ready;
static int release_exited;
static int attempting_leave;
static int left_blocking;

static void fail(int code, const char *where, int64_t observed) {
    fprintf(stderr, "ROOT_HANDLE_FAIL code=%d where=%s got=%lld\n",
            code, where, (long long)observed);
    /* A worker failure must terminate the child, not strand the main handshake. */
    _Exit(code);
}

static int64_t revision_after(int64_t value, int count) {
    for (int i = 0; i < count; ++i) {
        value = value == INT64_MAX ? 1 : value + 1;
    }
    return value;
}

static void wait_running(int *word, int value) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) < value) {
        pcc_thread_safepoint();
        sched_yield();
    }
}

static void *worker(void *raw) {
    intptr_t index = (intptr_t)raw;
    if (pcc_thread_is_running() != 0) fail(1, "raw_thread_not_cold", index);
    if (pcc_current_thread_id() <= 0) fail(2, "registration", index);
    if (pcc_thread_is_running() != 1) fail(3, "registered_not_running", index);

    int64_t token = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (token != 1 || pcc_thread_is_running() != 0) fail(4, "initial_blocking_state", token);
    /* No handle or object operation occurs in this exited interval. */
    pcc_thread_blocking_leave(token);
    if (pcc_thread_is_running() != 1) fail(5, "initial_leave_state", index);
    PyObject *local = NULL;
    pcc_gc_store_root(&local, slots[index]);
    live_slots[index] = &local;
    __atomic_fetch_add(&started, 1, __ATOMIC_RELEASE);
    wait_running(&go, 1);

    for (int round = 0; round < ROUNDS; ++round) {
        void *handle = pcc_gc_scheduler_root_register_handle(&local);
        if (!handle) fail(6, "handle_allocation", round);
        /* This writer shares the graph lock with frame/continuation mutation;
         * GC4 count-free handle writers hold only the scheduler list word. */
        pcc_py_gc_minor_graph_lock();
        pcc_gc_root_registry_note_mutation_locked();
        pcc_py_gc_minor_graph_unlock();
        if ((round & 15) == 0) {
            int64_t count = pcc_gc_scheduler_root_count();
            if (count < 1 || count > THREADS) fail(7, "concurrent_list_count", count);
        }
        pcc_gc_scheduler_root_unregister_handle(handle);
        pcc_thread_safepoint();
    }

    void *final_handle = pcc_gc_scheduler_root_register_handle(&local);
    if (!final_handle) fail(8, "final_handle", index);
    token = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (token != 1 || pcc_thread_is_running() != 0) fail(9, "final_blocking_state", token);
    __atomic_fetch_add(&exited_ready, 1, __ATOMIC_RELEASE);
    while (__atomic_load_n(&release_exited, __ATOMIC_ACQUIRE) == 0) {
        sched_yield();
    }
    __atomic_fetch_add(&attempting_leave, 1, __ATOMIC_RELEASE);
    pcc_thread_blocking_leave(token);
    if (pcc_thread_is_running() != 1) fail(10, "leave_after_stw", index);
    __atomic_fetch_add(&left_blocking, 1, __ATOMIC_RELEASE);
    /* The thread is running again. Only now may it inspect its rewritten root. */
    if (local != replacement) fail(11, "rewritten_root_not_observed", index);
    if (py_header(local)->type_tag != PY_TYPE_LIST) fail(12, "rewritten_object", index);
    pcc_gc_scheduler_root_unregister_handle(final_handle);
    pcc_gc_store_root(&local, NULL);
    live_slots[index] = NULL;
    pcc_thread_unregister_current();
    if (pcc_thread_is_running() != 0) fail(13, "unregistered_still_running", index);
    return NULL;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    py_gc_disable();
    if (pcc_threads_enabled() != 1 || pcc_current_thread_id() <= 0) fail(14, "threaded_runtime", 0);
    if (pcc_thread_is_running() != 1) fail(15, "main_not_running", 0);
    for (int i = 0; i < THREADS; ++i) {
        originals[i] = py_list_new(0);
        if (!originals[i]) fail(16, "object_allocation", i);
        pcc_gc_store_root(&slots[i], originals[i]);
    }
    replacement = py_list_new(0);
    if (!replacement) fail(17, "replacement_allocation", 0);
    if (pcc_gc_config_initialized != 1 || pcc_gc_backend_frozen != 1) fail(18, "configuration_not_frozen", 0);
    if (pcc_gc_scheduler_root_count() != 0) fail(19, "nonempty_initial_list", 0);
    int64_t before = __atomic_load_n(&pcc_gc_root_registry_revision, __ATOMIC_ACQUIRE);
    pthread_t threads[THREADS];
    for (intptr_t i = 0; i < THREADS; ++i) {
        if (pthread_create(&threads[i], NULL, worker, (void *)i) != 0) fail(20, "pthread_create", i);
    }
    wait_running(&started, THREADS);
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    wait_running(&exited_ready, THREADS);

    if (pcc_stop_the_world() != 0) fail(21, "stop_world", 0);
    int64_t registered = pcc_gc_scheduler_root_count();
    int64_t enumerated = pcc_gc_visit_scheduler_root_slots(0, 0);
    if (registered != THREADS || enumerated != THREADS) fail(22, "stopped_root_enumeration", enumerated);
    for (int i = 0; i < THREADS; ++i) {
        if (!live_slots[i] || *live_slots[i] != originals[i]) fail(23, "root_changed_before_rewrite", i);
        /* Production root-store replacement under real STW. This does not
         * claim to exercise forwarding-pointer relocation or remap walkers. */
        pcc_gc_store_root(live_slots[i], replacement);
    }
    __atomic_store_n(&release_exited, 1, __ATOMIC_RELEASE);
    while (__atomic_load_n(&attempting_leave, __ATOMIC_ACQUIRE) < THREADS) sched_yield();
    if (__atomic_load_n(&left_blocking, __ATOMIC_ACQUIRE) != 0) fail(24, "left_before_resume", 0);
    if (pcc_resume_world() != 0) fail(25, "resume_world", 0);

    int64_t token = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (token != 1 || pcc_thread_is_running() != 0) fail(26, "main_join_state", token);
    for (int i = 0; i < THREADS; ++i) {
        if (pthread_join(threads[i], NULL) != 0) fail(27, "pthread_join", i);
    }
    pcc_thread_blocking_leave(token);
    if (pcc_thread_is_running() != 1) fail(28, "main_after_join", 0);
    if (__atomic_load_n(&left_blocking, __ATOMIC_ACQUIRE) != THREADS) fail(29, "missing_leave", 0);
    int64_t after = __atomic_load_n(&pcc_gc_root_registry_revision, __ATOMIC_ACQUIRE);
    int mutations = THREADS * (3 * ROUNDS + 2);
    if (after != revision_after(before, mutations)) fail(30, "revision_lost_update", after);
    if (pcc_gc_scheduler_root_count() != 0) fail(31, "nonempty_final_list", 0);

    for (int i = 0; i < THREADS; ++i) {
        pcc_gc_store_root(&slots[i], NULL);
        py_decref(originals[i]);
    }
    py_decref(replacement);
    printf("ROOT_HANDLE_NATIVE_OK backend=%lld tracing=%d mutations=%d roots=0 enumerated=%lld\n",
           (long long)backend, pcc_gc_backend4_no_refcounts != 0,
           mutations, (long long)enumerated);
    return 0;
}

