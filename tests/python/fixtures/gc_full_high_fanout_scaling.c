/* n [grouped]: native automatic-full step timing, excluding the initial aging collection. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_step(void);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
int main(int argc, char **argv) {
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
    py_gc_collect();
    py_gc_enable();
    pcc_gc_backend0_full_set_slice_us(200);
    printf("tracked %lld before stw max %lld\n", (long long)py_gc_get_count(0),
        (long long)pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_MAX_US));
    if (pcc_gc_backend0_full_begin() == 0) return 4;
    int64_t peak[10] = {0};
    int count = 0;
    while (pcc_gc_backend0_full_counter(3) != 0 && count < 100000) {
        int64_t phase = pcc_gc_backend0_full_counter(3);
        int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US);
        pcc_gc_backend0_full_step();
        int64_t delta = pcc_gc_telemetry(PCC_GC_COUNTER_COLLECTION_STW_SUM_US) - before;
        if (phase > 0 && phase < 10 && delta > peak[phase]) peak[phase] = delta;
        count++;
    }
    printf("n %d steps %d completed %lld phase maxima us", n, count,
        (long long)pcc_gc_backend0_full_counter(0));
    for (int p=1; p<10; p++) printf(" %lld", (long long)peak[p]);
    puts("");
    return pcc_gc_backend0_full_counter(0) != 1;
}
