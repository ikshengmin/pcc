#ifndef PCC_SHARED_MANAGED_H
#define PCC_SHARED_MANAGED_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Experimental opt-in ABI v1, GC0 only. Not in the default runtime archive.
 * The compiler/link caller must explicitly select the managed runtime object.
 * A descriptor is immutable process-lifetime metadata. Per-allocation count
 * computes fixed + count * element sizes with checked arithmetic.
 * Scalars/bytes and managed reference slots are distinct storage regions;
 * never write pointers into scalar payload and expect them to be traced.
 * Handles are opaque owning root cells, NOT object addresses or registry nodes.
 * Calls require a carrier registered with the existing PCC thread runtime.
 * Select GC0 before managed entry; do not race collector selection or other
 * managed mutators with this first serialized lane.
 * Exact live handles, valid descriptor/output storage and caller-serialized
 * handle/field/payload use are preconditions. Stale pointers/ABA are not checked.
 * Output storage must not alias a live handle or managed payload. On failure
 * output is unchanged. Clone/load return NEW owning handles; a null edge or
 * clone(NULL) succeeds with a null output. drop(NULL) does nothing.
 * data is stable only while an owning handle lives (GC0 does not relocate).
 * Do not free/realloc it or expose the private envelope as a Python tuple.
 * Language identity, String immutability/interning, bounds and constructors
 * belong to the frontend. No language finalizer, pinning, or all-five-GC claim.
 */
typedef struct {
    uint64_t version;
    uint64_t fixed_payload_bytes;
    uint64_t element_payload_bytes;
    uint64_t payload_alignment; /* power of two, 1..16 */
    uint64_t fixed_reference_slots;
    uint64_t element_reference_slots;
} pcc_rt_managed_descriptor_v1;
typedef struct pcc_rt_managed_handle pcc_rt_managed_handle;
enum { PCC_RT_MANAGED_OK=0, PCC_RT_MANAGED_OOM=1,
       PCC_RT_MANAGED_OVERFLOW=2, PCC_RT_MANAGED_BAD_LAYOUT=3,
       PCC_RT_MANAGED_UNSUPPORTED=4 };
int32_t pcc_rt_managed_alloc(const pcc_rt_managed_descriptor_v1 *descriptor,
                           uint64_t count, pcc_rt_managed_handle **out);
int32_t pcc_rt_managed_clone(pcc_rt_managed_handle *, pcc_rt_managed_handle **out);
void pcc_rt_managed_drop(pcc_rt_managed_handle *);
void *pcc_rt_managed_data(pcc_rt_managed_handle *);
const pcc_rt_managed_descriptor_v1 *pcc_rt_managed_descriptor(pcc_rt_managed_handle *);
uint64_t pcc_rt_managed_element_count(pcc_rt_managed_handle *);
uint64_t pcc_rt_managed_payload_size(pcc_rt_managed_handle *);
int32_t pcc_rt_managed_identity_equal(pcc_rt_managed_handle *, pcc_rt_managed_handle *);
int32_t pcc_rt_managed_ref_store(pcc_rt_managed_handle *, uint64_t index,
                               pcc_rt_managed_handle *child);
int32_t pcc_rt_managed_ref_load(pcc_rt_managed_handle *, uint64_t index,
                              pcc_rt_managed_handle **out);
/* Actual existing GC0 cycle collection; negative UNSUPPORTED otherwise. */
int64_t pcc_rt_managed_collect(void);
#ifdef __cplusplus
}
#endif
#endif
