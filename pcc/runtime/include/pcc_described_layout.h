#ifndef PCC_DESCRIBED_LAYOUT_H
#define PCC_DESCRIBED_LAYOUT_H

#include <stdint.h>

/* #13 geometry ABI, not yet a published managed-object tag/allocation API.
 * Descriptor is immutable, compiler-owned data with process lifetime. Its
 * five int64 words are version, fixed prefix extent, repeated element stride,
 * fixed reference count and repeated reference count. Sorted int64 reference
 * offsets follow (fixed offsets from object start, repeated from element).
 * The owner provides a readable, 8-byte aligned descriptor and exact byte
 * count. These internal helpers never accept arbitrary untrusted pointers,
 * allocate, retain, publish or walk a registered GC object. */
#define PCC_DESCRIBED_DESCRIPTOR_VERSION 1
#define PCC_DESCRIBED_DESCRIPTOR_HEADER_BYTES 40
#define PCC_DESCRIBED_OBJECT_DESCRIPTOR_OFFSET 16
#define PCC_DESCRIBED_OBJECT_REPEAT_COUNT_OFFSET 24
#define PCC_DESCRIBED_OBJECT_FIELDS_OFFSET 32

/* Logical object extent (including existing 16-byte header), number of
 * owned reference slots, or offset for an ordinal. Return -1 for invalid
 * geometry/ordinal. Repeated count is the backing allocation's capacity,
 * not a mutable view length; a fixed layout has stride and repeat count 0. */
int64_t pcc_gc_described_extent(const int64_t *descriptor, int64_t descriptor_bytes, int64_t repeat_count);
int64_t pcc_gc_described_slot_count(const int64_t *descriptor, int64_t descriptor_bytes, int64_t repeat_count);
int64_t pcc_gc_described_slot_offset(const int64_t *descriptor, int64_t descriptor_bytes, int64_t repeat_count, int64_t ordinal);

/* Initializer requires fresh, zeroed private backing with exact allocation
 * extent; owner and descriptor must be readable/aligned for their declared
 * extents. Never call on an already published object. Returns 1 on binding,
 * 0 on invalid geometry/state without changing owner bytes. */
int64_t pcc_gc_described_initialize_private(void *owner, int64_t allocation_extent,
    const int64_t *descriptor, int64_t descriptor_bytes, int64_t repeat_count);

/* Visit already bound immutable geometry. The owner and descriptor remain
 * live during the callback; callback receives only the exact owned slots
 * (role=1) and must obey its owner protocol: collector scans cannot park;
 * terminal deallocation owns untracked nonmoving bytes until callback return.
 * state[0] is next ordinal or -1, state[1] is count examined. No recursive
 * validation of the descriptor, no payload scalar visits. Not a frontend
 * frontend allocation or managed GC tag admission by this raw walker. */
typedef void (*PccDescribedSlotVisitor)(void *slot, int64_t role, void *context);
int64_t pcc_gc_described_visit_slots_slice(void *owner, int64_t cursor,
    int64_t limit, PccDescribedSlotVisitor visitor, void *context, int64_t *state);

/* Managed first-gate constructor. Output is a registered, initialized empty
 * owning root in the admitted caller; no reference is returned in a register.
 * The compiler-owned immutable descriptor must live at least as long as the
 * resulting object. Object-family cells and raw mappings use their respective
 * exact provenance/publication owners; malformed/overflowing geometry and
 * allocation/index failure publish neither root nor new object.
 * Returns 1 on success, 0 on invalid input or allocation failure. */
int64_t pcc_gc_described_alloc_into_root(void *output,const int64_t *descriptor,
    int64_t descriptor_bytes,int64_t repeat_count);

#endif
