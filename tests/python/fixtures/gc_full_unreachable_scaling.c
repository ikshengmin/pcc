/* n [grouped]: native automatic-full handover and exact young-cycle reclamation. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_step(void);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
extern int32_t pcc_gc_backend0_young_request;
extern int32_t pcc_gc_young_log_lost;
static int mixed_cycle(void) {
    py_gc_init();
    py_gc_disable();
    PyObject *list = py_list_new(0);
    PyObject *tuple = py_tuple_new(1);
    PyObject *dict = py_dict_new();
    PyObject *set = py_set_new();
    PyObject *live = py_list_new(0);
    if (!list || !tuple || !dict || !set || !live) return 10;
    py_tuple_set_item(tuple, 0, list);
    py_dict_set(dict, py_None, tuple);
    py_list_append(list, dict);
    py_list_append(list, set);
    py_list_append(list, live);
    py_set_add(set, py_None);
    py_decref(list);
    py_decref(tuple);
    py_decref(dict);
    py_decref(set);
    if (py_gc_collect() != 4 || py_gc_get_count(0) != 1) return 11;
    if (py_list_len(live) != 0) return 12;
    py_decref(live);
    return py_gc_get_count(0) != 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "mixed") == 0) return mixed_cycle();
    if (argc != 2 && argc != 3) return 2;
    const int grouped = argc == 3;
    int n = atoi(argv[1]);
    py_gc_init();
    py_gc_disable();
    PyObject *keep = py_list_new(0);
    PyObject *batch = keep;
    for (int i = 0; i < n; i++) {
        if (grouped && i % 256 == 0) {
            batch = py_list_new(0);
            if (batch == 0) return 5;
            py_list_append(keep, batch);
            py_decref(batch);
        }
        PyObject *obj = py_list_new(0);
        if (obj == 0) return 3;
        py_list_append(batch, obj);
        py_decref(obj);
    }
    py_list_append(keep, keep);
    py_gc_collect();
    py_decref(keep);
    py_gc_enable();
    pcc_gc_backend0_full_set_slice_us(200);
    printf("tracked %lld before stw max %lld\n", (long long)py_gc_get_count(0),
        (long long)pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_MAX_US));
    if (pcc_gc_backend0_full_begin() == 0) return 4;
    int64_t peak[10] = {0};
    int count = 0;
    int64_t handover = 0;
    while (pcc_gc_backend0_full_counter(3) != 0 && count < 100000) {
        int64_t phase = pcc_gc_backend0_full_counter(3);
        int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US);
        pcc_gc_backend0_full_step();
        int64_t delta = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US) - before;
        if (phase > 0 && phase < 10 && delta > peak[phase]) peak[phase] = delta;
        if (pcc_gc_backend0_full_counter(3) == 0) handover = delta;
        count++;
    }
    printf("n %d steps %d completed %lld phase maxima us", n, count,
        (long long)pcc_gc_backend0_full_counter(0));
    for (int p=1; p<10; p++) printf(" %lld", (long long)peak[p]);
    puts("");
    int64_t demoted = pcc_gc_backend0_full_counter(2);
    const int64_t before_reclaim = py_gc_get_count(0);
    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US);
    int lost = pcc_gc_young_log_lost;
    pcc_gc_backend0_young_request = 1;
    int64_t reclaimed = pcc_gc_collect(0);
    pcc_gc_backend0_young_request = 0;
    int64_t confirmation = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US) - before;
    printf("handover %lld us young confirmation %lld us candidates %lld young log overflow %d reclaimed %lld remainder %lld\n",
        (long long)handover, (long long)confirmation, (long long)demoted, lost,
        (long long)reclaimed, (long long)py_gc_get_count(0));
    const int64_t expected = (int64_t)n + 1 + (grouped ? ((int64_t)n + 255) / 256 : 0);
    return pcc_gc_backend0_full_counter(0) != 1 || demoted != expected
        || before_reclaim != expected || reclaimed != expected || py_gc_get_count(0) != 0;
}
