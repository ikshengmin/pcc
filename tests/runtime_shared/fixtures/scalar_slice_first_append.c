#include "pcc_shared_managed.h"
extern int32_t scalar_slice_first_append(uint64_t, pcc_rt_managed_handle **);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

int main(void) {
    const uint64_t inputs[3] = {0, 0x8000000000000000ULL, UINT64_MAX};
    uint64_t observed[3];
    int64_t before = pcc_gc_scheduler_root_count();
    uint64_t i;
    if (scalar_slice_first_append(0, 0) != PCC_RT_MANAGED_BAD_LAYOUT) return 1;
    for (i = 0; i < 3; i++) {
        pcc_rt_managed_handle *result = (pcc_rt_managed_handle *)(uint64_t)123;
        int status = scalar_slice_first_append(inputs[i], &result);
        if (pcc_gc_backend()) {
            if (status != PCC_RT_MANAGED_UNSUPPORTED || (uint64_t)result != 123) return 2;
            if (pcc_gc_scheduler_root_count() != before) return 3;
            continue;
        }
        if (status || !result || (uint64_t)result == 123) return 4;
        if (pcc_gc_scheduler_root_count() != before + 1) return 5;
        if (pcc_rt_managed_collect() < 0) return 6;
        if (pcc_rt_managed_element_count(result) != 1 ||
            pcc_rt_managed_payload_size(result) != 8) return 7;
        observed[i] = *((uint64_t *)pcc_rt_managed_data(result));
        if (observed[i] != inputs[i]) return 8;
        pcc_rt_managed_drop(result);
        if (pcc_gc_scheduler_root_count() != before) return 9;
    }
    if (!pcc_gc_backend() && write(1, observed, sizeof(observed)) != sizeof(observed)) return 10;
    return 0;
}
