#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern PyObject *pcc_str_ascii_char(int64_t);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum {
    CARRIERS = 4,
    ASCII_COUNT = 128,
    STR_BYTE_LEN_OFFSET = 16,
    STR_CP_LEN_OFFSET = 24,
    STR_DATA_OFFSET = 40,
    ASCII_CELL_STRIDE = 48
};
static int64_t ready, go;
static PyObject *seen[CARRIERS][ASCII_COUNT];

static void *worker(void *arg) {
    intptr_t lane = (intptr_t)arg;
    if (pcc_current_thread_id() <= 0) return (void *)1;
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&go, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    for (int repeat = 0; repeat < 20; repeat++) {
        for (int c = 0; c < ASCII_COUNT; c++) {
            PyObject *p = pcc_str_ascii_char(c);
            if (!p) return (void *)2;
            if (((PyObjectHeader *)p)->type_tag != PY_TYPE_STR) return (void *)3;
            if (((PyObjectHeader *)p)->refcount != 1) return (void *)4;
            if (*(int64_t *)((char *)p + STR_BYTE_LEN_OFFSET) != 1) return (void *)5;
            if (*(int64_t *)((char *)p + STR_CP_LEN_OFFSET) != 1) return (void *)6;
            if (((unsigned char *)p)[STR_DATA_OFFSET] != c) return (void *)7;
            if (((unsigned char *)p)[STR_DATA_OFFSET + 1] != 0) return (void *)8;
            if (repeat && seen[lane][c] != p) return (void *)9;
            seen[lane][c] = p;
        }
    }
    return 0;
}

int main(void) {
    if (pcc_gc_set_backend(0) != 0) return 10;
    void *threads[CARRIERS];
    for (int i = 0; i < CARRIERS; i++) {
        if (pcc_thread_start(&threads[i], worker, (void *)(intptr_t)i) != 0) return 11;
    }
    while (__atomic_load_n(&ready, __ATOMIC_ACQUIRE) != CARRIERS) pcc_thread_safepoint();
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < CARRIERS; i++) {
        void *status = 0;
        if (pcc_thread_join(threads[i], &status) != 0) return 12;
        if (status) {
            fprintf(stderr, "ASCII worker %d failed check %ld\n", i, (long)(intptr_t)status);
            return 100 + (intptr_t)status;
        }
    }
    for (int backend = 0; backend < 5; backend++) {
        if (pcc_gc_set_backend(backend) != 0) return 13;
        if (pcc_str_ascii_char(-1) || pcc_str_ascii_char(128)) return 14;
        for (int c = 0; c < ASCII_COUNT; c++) {
            PyObject *p = pcc_str_ascii_char(c);
            for (int i = 0; i < CARRIERS; i++) {
                if (seen[i][c] != p) return 15;
            }
            if ((char *)p - (char *)seen[0][0] != c * ASCII_CELL_STRIDE) return 16;
            if (pcc_gc_pointer_is_managed(p) != 1) return 17;
            py_incref(p);
            py_decref(p);
            if (((PyObjectHeader *)p)->refcount != 1) return 18;
            PyObject *slot = p;
            void *handle = pcc_gc_scheduler_root_register_handle(&slot);
            if (!handle) return 19;
            int64_t token = pcc_gc_foreign_lease_acquire(&slot);
            if (token != 2) return 20;
            if (pcc_gc_set_backend(backend) == 0) return 21;
            if (pcc_gc_foreign_lease_release(&slot, token) != 0) return 22;
            pcc_gc_scheduler_root_unregister_handle(handle);
            if (slot != p) return 23;
        }
        pcc_gc_collect(0);
        for (int c = 0; c < ASCII_COUNT; c++) {
            if (pcc_str_ascii_char(c) != seen[0][c]) return 24;
        }
    }
    puts("ASCII_CANONICAL_CACHE_OK");
    return 0;
}
