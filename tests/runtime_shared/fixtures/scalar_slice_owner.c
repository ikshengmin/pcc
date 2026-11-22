/* Shared backing lifetime only, not Go source lowering or growth. */
#include "pcc_shared_managed.h"

extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_pointer_is_managed(void *);
extern int64_t pcc_gc_scheduler_root_count(void);

#ifdef PCC_TEST_GO_SCALAR_ADAPTER
extern int32_t scalar_slice_alloc_words(uint64_t count, pcc_rt_managed_handle **out);
#else
/* Equivalent to a repeated unsigned target word with no reference slots. */
static const pcc_rt_managed_descriptor_v1 words = {1, 0, 8, 8, 0, 0};
static int32_t scalar_slice_alloc_words(uint64_t count, pcc_rt_managed_handle **out) {
    return pcc_rt_managed_alloc(&words, count, out);
}
#endif

/* The allocated payload cannot be represented by a returned stack address. */
static int hand_off(uint64_t n, pcc_rt_managed_handle **caller) {
    pcc_rt_managed_handle *producer = 0;
    uint64_t *payload;
    uint64_t i;
    if (scalar_slice_alloc_words(n, &producer)) return 1;
    payload = (uint64_t *)pcc_rt_managed_data(producer);
    for (i = 0; i < n; i++) {
        if (payload[i] != 0) return 2;
        payload[i] = UINT64_MAX - i;
    }
    if (pcc_rt_managed_clone(producer, caller)) return 3;
    pcc_rt_managed_drop(producer);
    return 0;
}

int main(void) {
    pcc_rt_managed_handle *empty = 0, *owner = 0, *unchanged;
    uint64_t *payload;
    void *object;
    int64_t baseline = pcc_gc_scheduler_root_count();
    if (pcc_gc_backend()) {
        if (scalar_slice_alloc_words(0, &empty) != PCC_RT_MANAGED_UNSUPPORTED) return 10;
        if (empty || pcc_gc_scheduler_root_count() != baseline) return 11;
        return 0;
    }
    if (hand_off(0, &empty) || hand_off(3, &owner)) return 12;
    if (!empty || !owner || pcc_rt_managed_identity_equal(empty, 0)) return 13;
    if (pcc_gc_scheduler_root_count() != baseline + 2) return 14;
    if (pcc_rt_managed_payload_size(empty) != 0 ||
        pcc_rt_managed_element_count(owner) != 3 ||
        pcc_rt_managed_payload_size(owner) != 24) return 15;
    unchanged = empty;
    if (pcc_rt_managed_ref_load(owner, 0, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT) return 16;
    if (unchanged != empty) return 17;
    unchanged = owner;
    if (scalar_slice_alloc_words(UINT64_MAX, &unchanged) != PCC_RT_MANAGED_OVERFLOW ||
        unchanged != owner || pcc_gc_scheduler_root_count() != baseline + 2) return 18;
    if (pcc_rt_managed_collect() < 0) return 19;
    payload = (uint64_t *)pcc_rt_managed_data(owner);
    if (payload[0] != UINT64_MAX || payload[1] != UINT64_MAX - 1 ||
        payload[2] != UINT64_MAX - 2) return 20;
    /* Trusted test introspection only; application code uses the handle API. */
    object = *((void **)owner);
    if (!pcc_gc_pointer_is_managed(object)) return 21;
    pcc_rt_managed_drop(owner);
    if (pcc_gc_pointer_is_managed(object)) return 22;
    pcc_rt_managed_drop(empty);
    if (pcc_gc_scheduler_root_count() != baseline) return 23;
    return 0;
}
