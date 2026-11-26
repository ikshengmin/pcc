#ifndef PCC_DESCRIBED_LAYOUT_H
#define PCC_DESCRIBED_LAYOUT_H

#include <stdint.h>

/* #13 geometry ABI, not yet a published managed-object tag/allocation API.
 * Descriptor is immutable, compiler-owned data with process lifetime. Its
 * five int64 words are version, fixed prefix extent, repeated element stride,
 * fixed reference count and repeated reference count. Sorted int64 reference
 * offsets follow (fixed offsets from object start, repeated from element).
 * The owner provides a readable, 8-byte aligned descriptor and exact byte
 * count. These helpers never accept an arbitrary untrusted pointer, allocate,
 * expose raw slot addresses, retain, publish or walk a GC object. */
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

#endif
