#include "pcc_shared_managed.h"
extern int32_t copy_string_bytes(const void *, uint64_t, pcc_rt_managed_handle **);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

static int produce(pcc_rt_managed_handle **out) {
    unsigned char bytes[8] = {77, 65, 0, 255, 195, 169, 90, 88};
    int result = copy_string_bytes(bytes + 1, 6, out);
    volatile unsigned char *dead = bytes;
    uint64_t i;
    for (i = 0; i < 8; i++) dead[i] = 0;
    return result;
}

int main(void) {
    pcc_rt_managed_handle *result = 0, *empty = 0;
    pcc_rt_managed_handle *unchanged = (pcc_rt_managed_handle *)(uint64_t)123;
    const unsigned char *data;
    int64_t roots = pcc_gc_scheduler_root_count();
    unsigned char short_input = 1;
    if (copy_string_bytes(0, 1, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT ||
        copy_string_bytes(&short_input, 49, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT ||
        copy_string_bytes(0, 0, 0) != PCC_RT_MANAGED_BAD_LAYOUT ||
        (uint64_t)unchanged != 123) return 1;
    if (pcc_gc_backend()) {
        if (produce(&unchanged) != PCC_RT_MANAGED_UNSUPPORTED ||
            (uint64_t)unchanged != 123 || pcc_gc_scheduler_root_count() != roots) return 2;
        return 0;
    }
    if (produce(&result) || copy_string_bytes(0, 0, &empty)) return 3;
    if (!result || !empty || pcc_rt_managed_payload_size(empty) ||
        pcc_gc_scheduler_root_count() != roots + 2) return 4;
    if (pcc_rt_managed_collect() < 0) return 5;
    if (pcc_rt_managed_payload_size(result) != 6) return 6;
    data = (const unsigned char *)pcc_rt_managed_data(result);
    if (data[0] != 65 || data[1] || data[2] != 255 || data[3] != 195 ||
        data[4] != 169 || data[5] != 90) return 7;
    if (write(1, data, 6) != 6) return 8;
    pcc_rt_managed_drop(result); pcc_rt_managed_drop(empty);
    if (pcc_gc_scheduler_root_count() != roots) return 9;
    return 0;
}
