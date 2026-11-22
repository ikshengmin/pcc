#include "pcc_shared_managed.h"
extern int32_t scalar_slice_source_loop(uint32_t, pcc_rt_managed_handle **, uint64_t *);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

int main(void) {
    const uint32_t inputs[3] = {0, 1, 10};
    uint64_t output[14], pos = 0, length = 666, i, j;
    pcc_rt_managed_handle *owner = 0;
    const uint64_t *payload;
    int64_t before = pcc_gc_scheduler_root_count();
    if (scalar_slice_source_loop(1, 0, &length) != PCC_RT_MANAGED_BAD_LAYOUT ||
        length != 666) return 1;
    if (scalar_slice_source_loop(1, &owner, (uint64_t *)&owner) != PCC_RT_MANAGED_BAD_LAYOUT ||
        owner) return 2;
    if (pcc_gc_backend()) {
        if (scalar_slice_source_loop(1, &owner, &length) != PCC_RT_MANAGED_UNSUPPORTED ||
            owner || length != 666 || pcc_gc_scheduler_root_count() != before) return 3;
        return 0;
    }
    for (i = 0; i < 3; i++) {
        if (scalar_slice_source_loop(inputs[i], &owner, &length)) return 4;
        if (length != inputs[i] || (!owner) != (inputs[i] == 0)) return 5;
        if (pcc_gc_scheduler_root_count() != before + (inputs[i] != 0)) return 6;
        if (pcc_rt_managed_collect() < 0) return 7;
        output[pos] = length;
        pos++;
        payload = (const uint64_t *)pcc_rt_managed_data(owner);
        for (j = 0; j < length; j++) {
            if (payload[j] != j) return 8;
            output[pos] = payload[j];
            pos++;
        }
        pcc_rt_managed_drop(owner); owner = 0;
        if (pcc_gc_scheduler_root_count() != before) return 9;
    }
    if (pos != 14 || write(1, output, sizeof(output)) != sizeof(output)) return 10;
    return 0;
}
