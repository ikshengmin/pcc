#include "pcc_shared_managed.h"
extern int32_t scalar_slice_seed(uint64_t, pcc_rt_managed_handle **);
extern int32_t scalar_slice_grow(pcc_rt_managed_handle *, uint64_t, pcc_rt_managed_handle **);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

static const pcc_rt_managed_descriptor_v1 unrelated = {1, 0, 8, 8, 0, 0};

int main(void) {
    pcc_rt_managed_handle *old = 0, *grown = 0, *different = 0, *empty = 0, *large = 0;
    pcc_rt_managed_handle *unchanged = (pcc_rt_managed_handle *)(uint64_t)123;
    uint64_t *from, *to;
    uint64_t observed[5];
    int64_t before = pcc_gc_scheduler_root_count();
    if (scalar_slice_grow(0, 1, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT ||
        (uint64_t)unchanged != 123) return 1;
    if (pcc_gc_backend()) {
        if (scalar_slice_seed(2, &unchanged) != PCC_RT_MANAGED_UNSUPPORTED ||
            (uint64_t)unchanged != 123 || pcc_gc_scheduler_root_count() != before) return 2;
        return 0;
    }
    if (pcc_rt_managed_alloc(&unrelated, 2, &different)) return 3;
    if (scalar_slice_grow(different, 2, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT ||
        (uint64_t)unchanged != 123) return 4;
    pcc_rt_managed_drop(different);
    if (scalar_slice_seed(0, &empty) || scalar_slice_grow(empty, UINT64_MAX, &grown)) return 5;
    if (pcc_rt_managed_element_count(grown) != 1 ||
        *((uint64_t *)pcc_rt_managed_data(grown)) != UINT64_MAX) return 6;
    pcc_rt_managed_drop(grown); grown = 0;
    pcc_rt_managed_drop(empty);
    if (scalar_slice_seed(2, &old)) return 7;
    from = (uint64_t *)pcc_rt_managed_data(old);
    from[0] = 3; from[1] = 5;
    if (scalar_slice_grow(old, UINT64_MAX, 0) != PCC_RT_MANAGED_BAD_LAYOUT) return 8;
    if (scalar_slice_grow(old, UINT64_MAX, &grown)) return 9;
    if (pcc_rt_managed_identity_equal(old, grown) ||
        pcc_gc_scheduler_root_count() != before + 2) return 10;
    if (pcc_rt_managed_collect() < 0) return 11;
    to = (uint64_t *)pcc_rt_managed_data(grown);
    from = (uint64_t *)pcc_rt_managed_data(old);
    if (pcc_rt_managed_element_count(old) != 2 || pcc_rt_managed_element_count(grown) != 4 ||
        to[0] != 3 || to[1] != 5 || to[2] != UINT64_MAX || to[3] != 0) return 12;
    to[0] = 7; from[1] = 11;
    observed[0] = from[0]; observed[1] = from[1];
    observed[2] = to[0]; observed[3] = to[1]; observed[4] = to[2];
    pcc_rt_managed_drop(grown); pcc_rt_managed_drop(old);
    if (pcc_gc_scheduler_root_count() != before) return 13;
    /* The process has a 64 MiB address-space limit. A 32 MiB source fits,
       but allocating a second 64 MiB backing cannot succeed. */
    if (scalar_slice_seed(4194304, &large)) return 14;
    from = (uint64_t *)pcc_rt_managed_data(large);
    from[0] = 13;
    if (scalar_slice_grow(large, 17, &unchanged) != PCC_RT_MANAGED_OOM ||
        (uint64_t)unchanged != 123 || pcc_gc_scheduler_root_count() != before + 1 ||
        from[0] != 13) return 15;
    pcc_rt_managed_drop(large);
    if (pcc_gc_scheduler_root_count() != before) return 16;
    if (write(1, observed, sizeof(observed)) != sizeof(observed)) return 17;
    return 0;
}
