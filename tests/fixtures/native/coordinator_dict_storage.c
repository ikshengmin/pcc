#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_live_dict_storage_snapshot(int64_t);
extern int64_t malloc_usable_size(void *);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
static int64_t ready, done, iterations;

static int snapshot(int64_t *out) {
    return pcc_gc_live_dict_storage_snapshot((int64_t)(uintptr_t)out) == 0;
}
static int64_t *bucket(int64_t *out, int64_t capacity) {
    int lane = 0;
    while (capacity > 4) {
        capacity /= 2;
        lane++;
    }
    return out + lane * 4;
}
static int delta(int64_t *before, int64_t *after, PyObject *d) {
    int64_t capacity = *(int64_t *)((char *)d + 24);
    void *indices = *(void **)((char *)d + 32);
    void *entries = *(void **)((char *)d + 40);
    int64_t *old = bucket(before, capacity), *now = bucket(after, capacity);
    return now[0] - old[0] == 1 && now[1] - old[1] == 2
        && now[2] - old[2] == capacity * 32
        && now[3] - old[3] == malloc_usable_size(indices) + malloc_usable_size(entries);
}

/* Race the cold walk with live dictionary construction, grow, clear, and
 * terminal release. Every worker reaches a safepoint between histories;
 * the snapshot itself must never see an odd partial table transaction. */
static void *worker(void *arg) {
    PyObject *roots[1] = {NULL};
    int32_t map[1] = {1};
    if (pcc_current_thread_id() <= 0) return (void *)1;
    pcc_gc_frame_enter(map, roots);
    __atomic_add_fetch(&ready, 1, __ATOMIC_ACQ_REL);
    while (!__atomic_load_n(&done, __ATOMIC_ACQUIRE)) {
        roots[0] = py_dict_new_presized(2);
        if (!roots[0]) return (void *)2;
        for (int i = 0; i < 11; i++)
            py_dict_set(roots[0], py_int_from_i64(i), py_int_from_i64(i + 1));
        if (py_dict_len(roots[0]) != 11 || py_err_occurred()) return (void *)3;
        py_dict_clear(roots[0]);
        pcc_gc_store_root(&roots[0], NULL);
        __atomic_add_fetch(&iterations, 1, __ATOMIC_ACQ_REL);
        pcc_thread_safepoint();
    }
    pcc_gc_frame_leave(roots);
    return NULL;
}

static int consistent(int64_t *out) {
    int64_t capacity = 4;
    for (int lane = 0; lane < 56; lane++) {
        int64_t *b = out + lane * 4;
        if (b[0] < 0 || b[1] != b[0] * 2
                || (b[0] != 0 && b[2] != capacity * 32 * b[0]) || b[3] < b[2]) return 0;
        capacity *= 2;
    }
    return 1;
}

int main(void) {
    PyObject *roots[3] = {NULL, NULL, NULL};
    int32_t map[1] = {3};
    int64_t before[224], after[224], final[224];
    pcc_gc_frame_enter(map, roots);
    if (pcc_gc_collect(1) < 0 || !snapshot(before)) return 1;
    roots[0] = py_dict_new_presized(2);
    roots[1] = py_dict_new();
    roots[2] = py_dict_new_presized(342);
    if (!roots[0] || !roots[1] || !roots[2]) return 2;
    int64_t live = pcc_allocator_live_requested_bytes();
    if (!snapshot(after) || pcc_allocator_live_requested_bytes() != live) return 3;
    for (int i = 0; i < 3; i++) if (!delta(before, after, roots[i])) return 4;
    if (pcc_stop_the_world() != 0 || !snapshot(final)
            || !pcc_thread_owns_stopped_world()) return 5;
    pcc_resume_world();
    for (int i = 0; i < 224; i++) if (after[i] != final[i]) return 6;
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], NULL);
    if (!snapshot(final)) return 7;
    for (int i = 0; i < 224; i++) if (before[i] != final[i]) return 8;
    roots[0] = py_dict_new_presized(2);
    py_dict_set(roots[0], py_int_from_i64(1), roots[0]);
    pcc_gc_store_root(&roots[0], NULL);
    if (pcc_gc_collect(1) < 0 || !snapshot(final)) return 9;
    for (int i = 0; i < 224; i++) if (before[i] != final[i]) return 10;
    void *threads[4];
    for (int i = 0; i < 4; i++)
        if (pcc_thread_start(&threads[i], worker, NULL) != 0) return 11;
    while (__atomic_load_n(&ready, __ATOMIC_ACQUIRE) < 4
            || __atomic_load_n(&iterations, __ATOMIC_ACQUIRE) < 16)
        pcc_thread_safepoint();
    for (int i = 0; i < 20; i++)
        if (!snapshot(after) || !consistent(after)) return 12;
    __atomic_store_n(&done, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < 4; i++) {
        void *status = NULL;
        if (pcc_thread_join(threads[i], &status) != 0 || status) return 13;
    }
    if (pcc_gc_collect(1) < 0 || !snapshot(final)) return 14;
    for (int i = 0; i < 224; i++) if (before[i] != final[i]) return 15;
    pcc_gc_frame_leave(roots);
    puts("DICT_STORAGE_PROFILE_OK");
    return 0;
}
