/* GC0 graph proof: opaque scalar payload, genuine owner edges, root identity,
 * actual cycle reclamation and failed-request ownership. No mock allocator. */
#include "pcc_shared_managed.h"
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_current_thread_id(void);
extern int64_t pcc_thread_registered(void);
extern int64_t pcc_gc_pointer_is_managed(void *);
extern int64_t pcc_gc_scheduler_root_count(void);

static const pcc_rt_managed_descriptor_v1 node_desc = {1, 16, 2, 16, 1, 0};
static const pcc_rt_managed_descriptor_v1 leaf_desc = {1, 8, 2, 2, 0, 0};
static const pcc_rt_managed_descriptor_v1 bad_desc = {1, 0, 0, 3, 0, 0};
static const pcc_rt_managed_descriptor_v1 huge_desc = {1, 0, 2, 8, 0, 0};
static const pcc_rt_managed_descriptor_v1 array_desc = {1, 0, 8, 8, 0, 1};
static const pcc_rt_managed_descriptor_v1 oom_desc = {1, 1073741824, 0, 8, 0, 0};

int main(void) {
    pcc_rt_managed_handle *a = 0, *b = 0, *copy = 0, *loaded = 0;
    pcc_rt_managed_handle *leaf = 0, *unchanged = 0;
    pcc_rt_managed_handle *shadow = 0, *array = 0, *edgeleaf = 0;
    unsigned char *data;
    void *a_object, *b_object, *leaf_object;
    void *shadow_object, *array_object, *edgeleaf_object;
    uint64_t i;
    int64_t roots_before, roots_live;
    if (pcc_current_thread_id() <= 0 || !pcc_thread_registered()) return 70;
    if (pcc_gc_backend() != 0) {
        unchanged = (pcc_rt_managed_handle *)(uint64_t)123;
        if (pcc_rt_managed_alloc(&node_desc, 4, &unchanged) != PCC_RT_MANAGED_UNSUPPORTED) return 71;
        if ((uint64_t)unchanged != 123) return 72;
        if (pcc_rt_managed_collect() != -PCC_RT_MANAGED_UNSUPPORTED) return 73;
        return 0;
    }
    roots_before = pcc_gc_scheduler_root_count();
    if (pcc_rt_managed_alloc(&node_desc, 4, &a)) return 1;
    if (pcc_rt_managed_alloc(&node_desc, 7, &b)) return 2;
    if (pcc_rt_managed_alloc(&leaf_desc, 5, &leaf)) return 3;
    if (pcc_gc_scheduler_root_count() != roots_before + 3) return 54;
    if (pcc_rt_managed_payload_size(a) != 24 || pcc_rt_managed_element_count(a) != 4) return 4;
    if (pcc_rt_managed_descriptor(a) != &node_desc) return 5;
    data = (unsigned char *)pcc_rt_managed_data(a);
    if (((uint64_t)data & 15) != 0) return 6;
    for (i=0; i<24; i++) if (data[i] != 0) return 7;
    for (i=0; i<24; i++) data[i] = 255;
    /* A genuine aligned object address, not a tagged immediate: this otherwise
     * unreachable self-cycle must NOT become a root through scalar bytes. */
    if (pcc_rt_managed_alloc(&node_desc, 0, &shadow)) return 31;
    if (pcc_rt_managed_ref_store(shadow, 0, shadow)) return 32;
    shadow_object = *((void **)shadow);
    *((void **)data) = shadow_object;
    pcc_rt_managed_drop(shadow); shadow = 0;
    if (pcc_rt_managed_collect() != 1) return 33;
    if (pcc_gc_pointer_is_managed(shadow_object)) return 34;
    if (*((void **)pcc_rt_managed_data(a)) != shadow_object) return 35;

    /* Repeated reference shape is fixed_refs + count * element_refs. A
     * same-value store must not leak a retain; replacement by NULL retires the
     * old sole owner immediately, and nullable load returns a null handle. */
    if (pcc_rt_managed_alloc(&array_desc, 3, &array)) return 36;
    if (pcc_rt_managed_payload_size(array) != 24) return 37;
    if (pcc_rt_managed_alloc(&leaf_desc, 1, &edgeleaf)) return 38;
    edgeleaf_object = *((void **)edgeleaf);
    if (pcc_rt_managed_ref_store(array, 2, edgeleaf)) return 39;
    if (pcc_rt_managed_ref_store(array, 2, edgeleaf)) return 40;
    pcc_rt_managed_drop(edgeleaf); edgeleaf = 0;
    if (!pcc_gc_pointer_is_managed(edgeleaf_object)) return 41;
    if (pcc_rt_managed_ref_store(array, 2, 0)) return 42;
    if (pcc_gc_pointer_is_managed(edgeleaf_object)) return 43;
    if (pcc_rt_managed_ref_load(array, 2, &loaded) || loaded) return 44;
    if (pcc_rt_managed_ref_store(array, 3, 0) != PCC_RT_MANAGED_BAD_LAYOUT) return 45;
    if (pcc_rt_managed_ref_store(array, 0, array)) return 46;
    array_object = *((void **)array);
    pcc_rt_managed_drop(array); array = 0;
    if (pcc_rt_managed_collect() != 1) return 47;
    if (pcc_gc_pointer_is_managed(array_object)) return 48;
    if (pcc_rt_managed_ref_load(a, 0, &loaded) || loaded) return 8;
    roots_live = pcc_gc_scheduler_root_count();
    if (pcc_rt_managed_clone(a, &copy)) return 9;
    if (pcc_gc_scheduler_root_count() != roots_live + 1) return 55;
    if (copy == a || !pcc_rt_managed_identity_equal(a, copy)) return 10;
    if (pcc_rt_managed_identity_equal(a, b)) return 11;
    if (pcc_rt_managed_ref_store(a, 0, b)) return 12;
    if (pcc_rt_managed_ref_store(b, 0, a)) return 13;
    if (pcc_rt_managed_ref_load(a, 0, &loaded)) return 14;
    if (!pcc_rt_managed_identity_equal(loaded, b)) return 15;
    /* Test-only introspection observes exact internal object provenance. The
     * application API never returns or compares this private envelope. */
    a_object = *((void **)a);
    b_object = *((void **)b);
    leaf_object = *((void **)leaf);
    pcc_rt_managed_drop(a); a = 0;
    pcc_rt_managed_drop(b); b = 0;
    pcc_rt_managed_drop(loaded); loaded = 0;
    if (pcc_rt_managed_collect() != 0) return 16;
    if (!pcc_gc_pointer_is_managed(a_object) || !pcc_gc_pointer_is_managed(b_object)) return 17;
    data = (unsigned char *)pcc_rt_managed_data(copy);
    if (*((void **)data) != shadow_object) return 49;
    for (i=8; i<24; i++) if (data[i] != 255) return 18;
    unchanged = copy;
    if (pcc_rt_managed_alloc(&bad_desc, 0, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT) return 19;
    if (unchanged != copy) return 20;
    if (pcc_rt_managed_alloc(&huge_desc, UINT64_MAX, &unchanged) != PCC_RT_MANAGED_OVERFLOW) return 21;
    if (unchanged != copy) return 22;
    if (pcc_rt_managed_alloc(&array_desc, 134217729, &unchanged) != PCC_RT_MANAGED_OVERFLOW) return 50;
    if (unchanged != copy) return 51;
    /* Native observer limits address space to 64 MiB; this checked 1 GiB request
     * must fail in the genuine allocator and roll back its registered handle. */
    roots_live = pcc_gc_scheduler_root_count();
    if (pcc_rt_managed_alloc(&oom_desc, 0, &unchanged) != PCC_RT_MANAGED_OOM) return 52;
    if (unchanged != copy) return 53;
    if (pcc_gc_scheduler_root_count() != roots_live) return 56;
    if (pcc_rt_managed_ref_store(copy, 1, leaf) != PCC_RT_MANAGED_BAD_LAYOUT) return 23;
    if (pcc_rt_managed_ref_load(copy, 1, &unchanged) != PCC_RT_MANAGED_BAD_LAYOUT) return 24;
    if (unchanged != copy) return 25;
    pcc_rt_managed_drop(copy); copy = 0;
    if (pcc_rt_managed_collect() != 2) return 26;
    if (pcc_gc_pointer_is_managed(a_object) || pcc_gc_pointer_is_managed(b_object)) return 27;
    pcc_rt_managed_drop(leaf); leaf = 0;
    if (pcc_gc_pointer_is_managed(leaf_object)) return 28;
    if (pcc_rt_managed_clone(0, &loaded) || loaded) return 29;
    if (!pcc_rt_managed_identity_equal(0, 0)) return 30;
    pcc_rt_managed_drop(0);
    if (pcc_gc_scheduler_root_count() != roots_before) return 57;
    return 0;
}
