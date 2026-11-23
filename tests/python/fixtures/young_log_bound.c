#include "py_runtime.h"
#include <stdint.h>

extern void *pcc_gc_young_log_chunks;
extern int32_t pcc_gc_young_log_lost;
extern int32_t pcc_gc_backend0_young_request;
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

static int chunk_count(void) {
    int count = 0;
    for (void *chunk = pcc_gc_young_log_chunks; chunk; chunk = *(void **)chunk)
        count++;
    return count;
}

static void *leave_young_cycle(void *unused) {
    PyObject *cycle = py_list_new(0);
    if (!cycle) return unused;
    py_list_append(cycle, cycle);
    py_decref(cycle);
    return 0;
}

static int retired_carriers(void) {
    py_gc_collect();
    int initial = chunk_count();
    for (int i = 0; i < 16; i++) {
        void *thread = 0;
        void *result = &thread;
        if (pcc_thread_start(&thread, leave_young_cycle, &thread) != 0 ||
            pcc_thread_join(thread, &result) != 0 || result != 0) return 20;
    }
    if (chunk_count() <= initial || py_gc_get_count(0) != 16) return 21;
    pcc_gc_backend0_young_request = 1;
    int64_t collected = pcc_gc_collect(0);
    pcc_gc_backend0_young_request = 0;
    if (collected != 16 || py_gc_get_count(0) != 0) return 22;
    if (chunk_count() != initial) return 23;
    return 0;
}

int main(int argc, char **argv) {
    py_gc_init();
    if (argc > 1) return retired_carriers();
    PyObject *old = py_list_new(0);
    if (!old) return 10;
    py_list_append(old, old);
    py_gc_collect();
    py_decref(old);

    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_COUNT);
    int bound = 0;
    for (int batch = 0; batch < 4; batch++) {
        for (int i = 0; i < 100000; i++) {
            PyObject *item = py_list_new(0);
            if (!item) return 11;
            py_decref(item);
        }
        if (py_gc_get_count(0) != 1) return 12;
        if (pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_COUNT) != before) return 13;
        int chunks = chunk_count();
        if (chunks > 128) return 24;
        if (batch == 1) bound = chunks;
        if (batch >= 2 && chunks != bound) return 14;
    }
    if (pcc_gc_young_log_lost != 1) return 15;

    PyObject *young = py_list_new(0);
    if (!young) return 16;
    py_list_append(young, young);
    py_decref(young);
    pcc_gc_backend0_young_request = 1;
    int64_t collected = pcc_gc_collect(0);
    pcc_gc_backend0_young_request = 0;
    if (collected != 1 || py_gc_get_count(0) != 1) return 17;
    if (pcc_gc_young_log_lost != 0) return 18;
    if (py_gc_collect() != 1 || py_gc_get_count(0) != 0) return 19;
    return 0;
}
