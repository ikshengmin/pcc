#include "pcc_shared_managed.h"
extern int32_t scalar_slice_seed(uint64_t, pcc_rt_managed_handle **);
extern int32_t scalar_slice_append_within(pcc_rt_managed_handle *, uint64_t, uint64_t);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

static const pcc_rt_managed_descriptor_v1 unrelated = {1, 0, 8, 8, 0, 0};

int main(void) {
    pcc_rt_managed_handle *owner = 0, *other = 0, *alias = 0;
    uint64_t *data;
    uint64_t observed[5];
    int64_t before = pcc_gc_scheduler_root_count();
    if (scalar_slice_append_within(0, 0, 17) != PCC_RT_MANAGED_BAD_LAYOUT) return 1;
    if (pcc_gc_backend()) {
        if (scalar_slice_seed(4, &owner) != PCC_RT_MANAGED_UNSUPPORTED ||
            owner || pcc_gc_scheduler_root_count() != before) return 2;
        return 0;
    }
    if (pcc_rt_managed_alloc(&unrelated, 4, &other)) return 3;
    if (scalar_slice_append_within(other, 0, 17) != PCC_RT_MANAGED_BAD_LAYOUT ||
        *((uint64_t *)pcc_rt_managed_data(other))) return 4;
    pcc_rt_managed_drop(other);
    if (scalar_slice_seed(4, &owner) || pcc_rt_managed_clone(owner, &alias)) return 5;
    data = (uint64_t *)pcc_rt_managed_data(owner);
    data[0] = 3; data[1] = 5;
    if (scalar_slice_append_within(owner, 4, 17) != PCC_RT_MANAGED_BAD_LAYOUT ||
        scalar_slice_append_within(owner, UINT64_MAX, 17) != PCC_RT_MANAGED_BAD_LAYOUT ||
        data[2] || data[3]) return 6;
    if (scalar_slice_append_within(owner, 2, UINT64_MAX)) return 7;
    if (!pcc_rt_managed_identity_equal(owner, alias) ||
        pcc_gc_scheduler_root_count() != before + 2) return 8;
    if (pcc_rt_managed_collect() < 0) return 9;
    data = (uint64_t *)pcc_rt_managed_data(alias);
    if (data[0] != 3 || data[1] != 5 || data[2] != UINT64_MAX || data[3]) return 10;
    observed[0] = data[0]; observed[1] = data[1]; observed[2] = data[2]; observed[3] = data[3];
    pcc_rt_managed_drop(owner);
    if (pcc_gc_scheduler_root_count() != before + 1) return 11;
    if (scalar_slice_append_within(alias, 3, 11)) return 12;
    observed[4] = ((uint64_t *)pcc_rt_managed_data(alias))[3];
    if (observed[4] != 11) return 13;
    pcc_rt_managed_drop(alias);
    if (pcc_gc_scheduler_root_count() != before) return 14;
    if (write(1, observed, sizeof(observed)) != sizeof(observed)) return 15;
    return 0;
}
