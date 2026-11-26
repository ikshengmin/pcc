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
from pcc.unsafe import load_i64, ptr_is_null

__pcc_freestanding__ = True

_DESC_VERSION = 1
_DESC_HEADER_BYTES = 40
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
