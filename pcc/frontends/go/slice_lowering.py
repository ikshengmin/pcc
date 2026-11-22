"""Isolated owned lowering for nonescaping, fixed stack byte backing."""
from dataclasses import dataclass, replace

from pcc.frontends.go.managed_objects import BYTE_BACKING, FixedByteExtent
from pcc.frontends.go.slice_proof import append_within_extent
from pcc.ir.compat import ir


MAX_FIXED_BYTE_STACK_CAPACITY = 4096


@dataclass(frozen=True)
class FixedStackByteSpan:
    function: object
    storage: object
    extent: FixedByteExtent


def allocate_fixed_byte_span(builder, extent):
    """Reserve this explicit capacity in one function, not a Go slice ABI."""
    if (not isinstance(extent, FixedByteExtent) or getattr(extent.backing, "identity", None) != BYTE_BACKING
            or type(extent.length) is not int or type(extent.capacity) is not int
            or not 0 <= extent.length <= extent.capacity <= MAX_FIXED_BYTE_STACK_CAPACITY
            or extent.capacity == 0 or builder.function is None
            or builder.block is not builder.function.entry_basic_block):
        raise ValueError("fixed byte backing requires bounded capacity in its function entry")
    backing_type = ir.ArrayType(ir.IntType(8), extent.capacity)
    slot = builder.alloca(backing_type, name="fixed_byte_backing")
    builder.store(ir.Constant(backing_type, None), slot)
    return FixedStackByteSpan(builder.function, slot, extent)


def append_fixed_byte_words(builder, span, summary, words):
    """Emit only in-bounds writes, retaining the exact same allocated slot."""
    if not isinstance(span, FixedStackByteSpan) or span.function is not builder.function:
        raise ValueError("fixed byte backing cannot cross its function lifetime")
    words = tuple(words)
    result = append_within_extent(span.extent, summary, len(words))
    i8, i32, i64 = ir.IntType(8), ir.IntType(32), ir.IntType(64)
    if any(word.type != i32 for word in words):
        raise ValueError("fixed byte append requires i32 words")
    if any(type(shift) is not int or not 0 <= shift < 32 for shift in summary.shifts):
        raise ValueError("fixed byte append requires valid uint32 shifts")
    if getattr(span.storage.type, "pointee", None) != ir.ArrayType(i8, span.extent.capacity):
        raise ValueError("fixed byte backing capacity does not match its allocation")
    offset = span.extent.length
    for word in words:
        for shift in summary.shifts:
            value = builder.lshr(word, ir.Constant(i32, shift)) if shift else word
            slot = builder.gep(span.storage, (ir.Constant(i32, 0), ir.Constant(i64, offset)), inbounds=True)
            builder.store(builder.trunc(value, i8), slot)
            offset += 1
    return replace(span, extent=result)
