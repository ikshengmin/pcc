#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
/* Exact c_void exports in freestanding_gc_tracking.py. */
extern void pcc_gc_default_table_lock(void);
extern void pcc_gc_default_table_unlock(void);

enum { OBJECTS = 128, ROOT_COUNT = OBJECTS + 3, RAW_COUNT = 5000 };
static PyObject *roots[ROOT_COUNT];
/* Static storage is outside allocator object cells: exercise fallback shard 0.
 * Each valid header remains C-owned until after its final untrack. */
static PyObjectHeader raw_objects[RAW_COUNT];
static int64_t ready, go;
static int workers, phase;
struct Worker { int index; };
static struct Worker contexts[4];

static void wait_for(int64_t *word, int64_t value) {
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) < value)
        pcc_thread_safepoint();
}
static void *work(void *opaque) {
    struct Worker *context = (struct Worker *)opaque;
    int index = context->index;
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    wait_for(&go, 1);
    if (phase == 0) {
        for (int i = index; i < OBJECTS; i += workers) {
            for (int repeat = 0; repeat < 16; repeat++) {
                py_gc_track(roots[i]);
                py_gc_track(roots[i]);
                py_gc_untrack(roots[i]);
                py_gc_untrack(roots[i]);
            }
            py_gc_track(roots[i]);
        }
    } else if (phase == 1) {
        /* Release on a different worker from the one that tracked it. */
        int start = (index + 1) % workers;
        for (int i = start; i < OBJECTS; i += workers) {
            py_gc_untrack(roots[i]);
            pcc_gc_unpin(roots[i]);
            pcc_gc_store_root(&roots[i], 0);
        }
    } else {
        for (int repeat = 0; repeat < 1000; repeat++) {
            py_gc_track(roots[0]);
            py_gc_untrack(roots[0]);
        }
    }
    return 0;
}
static int run_workers(int selected_phase) {
    void *handles[4] = {0, 0, 0, 0};
    phase = selected_phase;
    __atomic_store_n(&ready, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&go, 0, __ATOMIC_RELEASE);
    for (int i = 0; i < workers; i++) {
        contexts[i].index = i;
        if (pcc_thread_start(&handles[i], work, &contexts[i]) != 0) return 1;
    }
    wait_for(&ready, workers);
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < workers; i++) {
        void *result = 0;
        if (pcc_thread_join(handles[i], &result) != 0 || result) return 2;
    }
    return 0;
}
static int contains_identity(PyObject *list, PyObject *target) {
    int64_t count = py_list_len(list);
    for (int64_t i = 0; i < count; i++) {
        PyObject *item = py_list_get(list, i);
        int matches = item == target;
        py_decref(item);
        if (matches) return 1;
    }
    return 0;
}
static void drop(int index) {
    if (!roots[index]) return;
    py_gc_untrack(roots[index]);
    pcc_gc_unpin(roots[index]);
    pcc_gc_store_root(&roots[index], 0);
}
static int make_untracked(int index) {
    roots[index] = py_list_new(0);
    if (!roots[index]) return 1;
    pcc_gc_pin(roots[index]);
    py_gc_untrack(roots[index]);
    return 0;
}
static int fail(int code, const char *phase_name) {
    fprintf(stderr, "tracked-shards failure=%d phase=%s count=%lld\n", code,
            phase_name, (long long)py_gc_get_count(0));
    return code;
}
int main(int argc, char **argv) {
    workers = argc > 1 ? atoi(argv[1]) : 4;
    if (workers != 1 && workers != 4) return 1;
    int backend = (int)pcc_gc_backend();
    if (backend < 0 || backend > 4 || pcc_threads_enabled() != 1) return 2;
    py_gc_enable();
    py_gc_set_threshold(0, 10, 10);
    int32_t map = ROOT_COUNT;
    pcc_gc_frame_enter(&map, roots);
    int64_t baseline = py_gc_get_count(0);
    int tracked_backend = backend != 4;

    for (int i = 0; i < OBJECTS; i++)
        if (make_untracked(i)) return fail(10, "allocate");
    if (py_gc_get_count(0) != baseline) return fail(11, "initial-untrack");
    if (run_workers(0)) return fail(12, "parallel-track");
    if (py_gc_get_count(0) != baseline + (tracked_backend ? OBJECTS : 0))
        return fail(13, "parallel-count");
    for (int i = 0; i < OBJECTS; i++)
        if (py_gc_is_tracked(roots[i]) != tracked_backend)
            return fail(14, "parallel-flags");
    if (run_workers(1)) return fail(15, "cross-thread-drop");
    if (py_gc_get_count(0) != baseline) return fail(16, "cross-thread-count");
    for (int i = 0; i < OBJECTS; i++)
        if (roots[i]) return fail(17, "cross-thread-root");

    if (make_untracked(0)) return fail(20, "shared-allocate");
    if (run_workers(2)) return fail(21, "shared-race");
    py_gc_track(roots[0]);
    py_gc_track(roots[0]);
    if (py_gc_get_count(0) != baseline + tracked_backend
            || py_gc_is_tracked(roots[0]) != tracked_backend)
        return fail(22, "shared-final-track");
    py_gc_untrack(roots[0]);
    py_gc_untrack(roots[0]);
    if (py_gc_get_count(0) != baseline || py_gc_is_tracked(roots[0]) != 0)
        return fail(23, "shared-final-untrack");
    drop(0);

    if (make_untracked(0) || make_untracked(1)) return fail(30, "nested-allocate");
    py_list_append(roots[1], roots[0]);
    /* Match collector/finalizer lock order: own STW before the global table.
       Introspection must borrow that STW and preserve both nested locks. */
    while (pcc_stop_the_world() != 0) pcc_thread_safepoint();
    pcc_gc_default_table_lock();
    pcc_gc_default_table_lock();
    py_gc_track(roots[0]);
    py_gc_track(roots[1]);
    if (py_gc_get_count(0) != baseline + 2 * tracked_backend)
        return fail(31, "nested-track");
    if (pcc_gc_tracked_node_pool_cached_count() > 4096)
        return fail(32, "nested-pool");
    roots[OBJECTS] = py_gc_get_objects();
    roots[OBJECTS + 1] = py_gc_get_referrers(roots[0]);
    if (!roots[OBJECTS] || !roots[OBJECTS + 1]) return fail(33, "nested-snapshot");
    if (contains_identity(roots[OBJECTS], roots[0]) != tracked_backend
            || contains_identity(roots[OBJECTS], roots[1]) != tracked_backend
            || contains_identity(roots[OBJECTS + 1], roots[1]) != tracked_backend)
        return fail(34, "nested-snapshot-members");
    if (!pcc_thread_owns_stopped_world()) return fail(35, "borrowed-world");
    pcc_gc_store_root(&roots[OBJECTS + 1], 0);
    pcc_gc_store_root(&roots[OBJECTS], 0);
    py_gc_untrack(roots[0]);
    py_gc_untrack(roots[1]);
    pcc_gc_default_table_unlock();
    /* The outer lock still owns the merged list; mutate after inner leave. */
    py_gc_track(roots[1]);
    py_gc_untrack(roots[1]);
    pcc_gc_default_table_unlock();
    if (!pcc_thread_owns_stopped_world()) return fail(36, "outer-world");
    pcc_resume_world();
    drop(1);
    drop(0);
    if (py_gc_get_count(0) != baseline) return fail(37, "nested-count");

    pcc_gc_tracked_node_pool_drain();
    if (pcc_gc_tracked_node_pool_cached_count() != 0) return fail(40, "pool-drain");
    for (int i = 0; i < RAW_COUNT; i++) {
        raw_objects[i].refcount = 1;
        raw_objects[i].type_tag = PY_TYPE_NONE;
        raw_objects[i].flags = 0;
        py_gc_track((PyObject *)&raw_objects[i]);
    }
    if (py_gc_get_count(0) != baseline + (tracked_backend ? RAW_COUNT : 0))
        return fail(41, "fallback-count");
    for (int i = 0; i < RAW_COUNT; i++) py_gc_untrack((PyObject *)&raw_objects[i]);
    int64_t cached = pcc_gc_tracked_node_pool_cached_count();
    if (cached != (tracked_backend ? 4096 : 0)) return fail(42, "pool-bound");
    if (py_gc_get_count(0) != baseline) return fail(43, "fallback-untrack");
    pcc_gc_tracked_node_pool_drain();
    if (pcc_gc_tracked_node_pool_cached_count() != 0) return fail(44, "pool-final-drain");
    pcc_gc_frame_leave(roots);
    printf("TRACKED_SHARDS_OK gc=%d workers=%d pool=%lld\n", backend, workers, (long long)cached);
    return 0;
}
