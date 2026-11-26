"""Checked descriptor geometry for #13 (not a published GC object type yet).

The compiler owns the immutable, readable, aligned descriptor and its exact
byte extent. Validate fixed/repeated reference offsets before allocation or
any visitor is allowed to use them. Logical extent includes the unchanged
object header; the descriptor pointer and immutable repeat count themselves
are scalar metadata, never managed edges. No allocation nor root publication
is admitted here; the collector's tag dispatch remains unchanged.
"""
from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import call_void_ptr_i64_ptr, load_i64, load_ptr, ptr_add, ptr_is_null, store_i64, store_ptr

__pcc_freestanding__ = True

_DESC_VERSION = 1
_DESC_HEADER_BYTES = 40
_OBJECT_DESCRIPTOR_OFFSET = 16
_OBJECT_REPEAT_COUNT_OFFSET = 24
_OBJECT_FIELDS_OFFSET = 32
_MAX_I64 = 9223372036854775807


@c_abi_export("pcc_gc_described_extent")
def pcc_gc_described_extent(descriptor, descriptor_bytes: i64, repeat_count: i64) -> i64:
    if ptr_is_null(descriptor) != 0 or descriptor_bytes < _DESC_HEADER_BYTES:
        return -1
    if (descriptor_bytes % 8) != 0 or repeat_count < 0:
        return -1
    if load_i64(descriptor, 0) != _DESC_VERSION:
        return -1
    prefix: i64 = load_i64(descriptor, 8)
    stride: i64 = load_i64(descriptor, 16)
    fixed_refs: i64 = load_i64(descriptor, 24)
    element_refs: i64 = load_i64(descriptor, 32)
    if prefix < _OBJECT_FIELDS_OFFSET or stride < 0 or fixed_refs < 0 or element_refs < 0:
        return -1
    available: i64 = (descriptor_bytes - _DESC_HEADER_BYTES) // 8
    if fixed_refs > available or element_refs != available - fixed_refs:
        return -1
    if stride == 0:
        if repeat_count != 0 or element_refs != 0:
            return -1
    elif repeat_count > (_MAX_I64 - prefix) // stride:
        return -1
    # Reference slots require natural 8-byte alignment. Primitive-only
    # repeated backing may use any positive byte stride.
    if element_refs != 0 and ((prefix % 8) != 0 or (stride % 8) != 0):
        return -1
    if fixed_refs > (prefix - _OBJECT_FIELDS_OFFSET) // 8 or element_refs > stride // 8:
        return -1
    previous: i64 = _OBJECT_FIELDS_OFFSET - 8
    index: i64 = 0
    while index < fixed_refs:
        offset: i64 = load_i64(descriptor, _DESC_HEADER_BYTES + index * 8)
        if offset < _OBJECT_FIELDS_OFFSET or offset > prefix - 8 or (offset % 8) != 0 or offset <= previous:
            return -1
        previous = offset
        index = index + 1
    previous = -8
    index = 0
    while index < element_refs:
        offset = load_i64(descriptor, _DESC_HEADER_BYTES + (fixed_refs + index) * 8)
        if offset < 0 or offset > stride - 8 or (offset % 8) != 0 or offset <= previous:
            return -1
        previous = offset
        index = index + 1
    return prefix + stride * repeat_count


@c_abi_export("pcc_gc_described_slot_count")
def pcc_gc_described_slot_count(descriptor, descriptor_bytes: i64, repeat_count: i64) -> i64:
    if pcc_gc_described_extent(descriptor, descriptor_bytes, repeat_count) < 0:
        return -1
    # Geometry bounds every 8-byte slot within the checked extent, so neither
    # this count nor later ordinal arithmetic can overflow.
    return load_i64(descriptor, 24) + load_i64(descriptor, 32) * repeat_count


@c_abi_export("pcc_gc_described_slot_offset")
def pcc_gc_described_slot_offset(descriptor, descriptor_bytes: i64, repeat_count: i64, ordinal: i64) -> i64:
    count: i64 = pcc_gc_described_slot_count(descriptor, descriptor_bytes, repeat_count)
    if ordinal < 0 or ordinal >= count:
        return -1
    fixed_refs: i64 = load_i64(descriptor, 24)
    if ordinal < fixed_refs:
        return load_i64(descriptor, _DESC_HEADER_BYTES + ordinal * 8)
    # An admitted repeated ordinal implies element_refs > 0.
    element_refs: i64 = load_i64(descriptor, 32)
    remaining: i64 = ordinal - fixed_refs
    element: i64 = remaining // element_refs
    ref: i64 = remaining % element_refs
    return (load_i64(descriptor, 8) + element * load_i64(descriptor, 16)
            + load_i64(descriptor, _DESC_HEADER_BYTES + (fixed_refs + ref) * 8))


@c_abi_export("pcc_gc_described_initialize_private")
def pcc_gc_described_initialize_private(owner, allocation_extent: i64, descriptor, descriptor_bytes: i64, repeat_count: i64) -> i64:
    """Bind geometry exactly once, while the zeroed raw owner is private.

    Caller holds the readable allocation and immutable descriptor alive. This
    must finish BEFORE allocator/collector visibility; it never publishes a
    header, root, tag, or reference. No safepoint may split the final stores.
    Rejected inputs leave every byte of the owner unchanged.
    """
    if ptr_is_null(owner) != 0 or allocation_extent < _OBJECT_FIELDS_OFFSET:
        return 0
    if pcc_gc_described_extent(descriptor, descriptor_bytes, repeat_count) != allocation_extent:
        return 0
    if ptr_is_null(load_ptr(owner, _OBJECT_DESCRIPTOR_OFFSET)) == 0 or load_i64(owner, _OBJECT_REPEAT_COUNT_OFFSET) != 0:
        return 0
    store_i64(owner, _OBJECT_REPEAT_COUNT_OFFSET, repeat_count)
    store_ptr(owner, _OBJECT_DESCRIPTOR_OFFSET, descriptor)
    return 1


@c_abi_export("pcc_gc_described_visit_slots_slice")
def pcc_gc_described_visit_slots_slice(owner, cursor: i64, limit: i64, visitor, context, state_out) -> i64:
    """Bounded visit of an already validated immutable layout (not tag dispatch).

    The owner and descriptor must stay live; the descriptor and repeat count
    were bound by initialize_private and cannot subsequently change. This
    trusted-owner helper never validates the descriptor again, scans scalar
    bytes, reads reference contents, retains slot addresses or parks. The
    callback must obey its owner protocol: collector scans cannot park; the
    terminal deallocator owns untracked, nonmoving bytes until callback
    return even if releasing a child runs user code.
    state_out is [next ordinal or -1, number examined], exactly the common
    collector cursor convention. Every visited slot has owned-reference role.
    """
    if ptr_is_null(state_out) != 0:
        return 0
    store_i64(state_out, 0, -1)
    store_i64(state_out, 8, 0)
    if ptr_is_null(owner) != 0 or ptr_is_null(visitor) != 0 or cursor < 0 or limit <= 0:
        return 0
    descriptor = load_ptr(owner, _OBJECT_DESCRIPTOR_OFFSET)
    if ptr_is_null(descriptor) != 0:
        return 0
    repeats: i64 = load_i64(owner, _OBJECT_REPEAT_COUNT_OFFSET)
    prefix: i64 = load_i64(descriptor, 8)
    stride: i64 = load_i64(descriptor, 16)
    fixed_refs: i64 = load_i64(descriptor, 24)
    element_refs: i64 = load_i64(descriptor, 32)
    total: i64 = fixed_refs + element_refs * repeats
    examined: i64 = 0
    while cursor < total and examined < limit:
        offset: i64 = 0
        if cursor < fixed_refs:
            offset = load_i64(descriptor, _DESC_HEADER_BYTES + cursor * 8)
        else:
            remaining: i64 = cursor - fixed_refs
            # A repeated ordinal exists only when element_refs is positive.
            element: i64 = remaining // element_refs
            ref: i64 = remaining % element_refs
            offset = prefix + element * stride + load_i64(descriptor, _DESC_HEADER_BYTES + (fixed_refs + ref) * 8)
        call_void_ptr_i64_ptr(visitor, ptr_add(owner, offset), 1, context)
        cursor = cursor + 1
        examined = examined + 1
    if cursor < total:
        store_i64(state_out, 0, cursor)
    store_i64(state_out, 8, examined)
    return 1
