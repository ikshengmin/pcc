#include "pcc_shared_managed.h"
extern int32_t scalar_slice_seed(uint64_t, pcc_rt_managed_handle **);
extern int32_t scalar_slice_append_state(pcc_rt_managed_handle **, uint64_t *, uint64_t);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int64_t write(int, const void *, uint64_t);

int main(void) {
    pcc_rt_managed_handle *owner = 0, *old = 0;
    uint64_t length = 1, i, size, cap, previous;
    uint64_t observed[9];
    uint64_t *data;
    int64_t roots = pcc_gc_scheduler_root_count();
    if (scalar_slice_append_state(&owner, &length, 3) != PCC_RT_MANAGED_BAD_LAYOUT ||
        owner || length != 1) return 1;
    if (scalar_slice_append_state(&owner, (uint64_t *)&owner, 3) != PCC_RT_MANAGED_BAD_LAYOUT ||
        scalar_slice_append_state(0, &length, 3) != PCC_RT_MANAGED_BAD_LAYOUT || owner) return 2;
    length = 0;
    if (pcc_gc_backend()) {
        if (scalar_slice_append_state(&owner, &length, 3) != PCC_RT_MANAGED_UNSUPPORTED ||
            owner || length || pcc_gc_scheduler_root_count() != roots) return 3;
        return 0;
    }
    for (i = 0; i < 9; i++) {
        cap = pcc_rt_managed_element_count(owner);
        previous = (uint64_t)owner;
        if (scalar_slice_append_state(&owner, &length, (0x8000000000000000ULL | i))) return 4;
        if (!owner || length != i + 1) return 5;
        size = pcc_rt_managed_element_count(owner);
        if (size < length || (i < cap && previous != (uint64_t)owner) ||
            (i == cap && previous == (uint64_t)owner)) return 6;
        if (i == 0 && pcc_rt_managed_clone(owner, &old)) return 7;
        if (pcc_gc_scheduler_root_count() != roots + 2) return 8;
        if (pcc_rt_managed_collect() < 0) return 9;
    }
    if (pcc_rt_managed_identity_equal(owner, old) ||
        *((uint64_t *)pcc_rt_managed_data(old)) != 0x8000000000000000ULL) return 10;
    data = (uint64_t *)pcc_rt_managed_data(owner);
    for (i = 0; i < 9; i++) observed[i] = data[i];
    pcc_rt_managed_drop(owner); owner = 0;
    pcc_rt_managed_drop(old);
    if (pcc_gc_scheduler_root_count() != roots) return 11;
    if (scalar_slice_seed(4194304, &owner)) return 12;
    length = 4194304;
    previous = (uint64_t)owner;
    *((uint64_t *)pcc_rt_managed_data(owner)) = 17;
    if (scalar_slice_append_state(&owner, &length, 29) != PCC_RT_MANAGED_OOM ||
        (uint64_t)owner != previous || length != 4194304 ||
        *((uint64_t *)pcc_rt_managed_data(owner)) != 17 ||
        pcc_gc_scheduler_root_count() != roots + 1) return 13;
    pcc_rt_managed_drop(owner);
    if (pcc_gc_scheduler_root_count() != roots) return 14;
    if (write(1, observed, sizeof(observed)) != sizeof(observed)) return 15;
    return 0;
}
