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


@dataclass(frozen=True)
class FixedByteView:
    span: FixedStackByteSpan
    offset: object
    length: object


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


def fixed_byte_view(builder, span):
    if not isinstance(span, FixedStackByteSpan) or span.function is not builder.function:
        raise ValueError("fixed byte view requires its owning function")
    i64 = ir.IntType(64)
    return FixedByteView(span, ir.Constant(i64, 0), ir.Constant(i64, span.extent.length))


def _check_view_operands(builder, view, failure, *values):
    if (not isinstance(view, FixedByteView) or view.span.function is not builder.function
            or getattr(failure, "function", None) is not builder.function
            or builder.block is failure or builder.block.is_terminated):
        raise ValueError("fixed byte view and failure edge must belong to this function")
    if any(value.type != ir.IntType(64) for value in (view.offset, view.length, *values)):
        raise ValueError("fixed byte view bounds require i64 values")


def _after_bounds_check(builder, condition, failure):
    checked = builder.function.append_basic_block("fixed_byte.checked")
    builder.cbranch(condition, checked, failure)
    builder.position_at_end(checked)


def slice_fixed_bytes(builder, view, low, high, failure):
    """Take a subview of already written bytes, not a capacity extension."""
    _check_view_operands(builder, view, failure, low, high)
    ordered = builder.icmp_unsigned("<=", low, high)
    within = builder.icmp_unsigned("<=", high, view.length)
    _after_bounds_check(builder, builder.and_(ordered, within), failure)
    return FixedByteView(view.span, builder.add(view.offset, low), builder.sub(high, low))


def read_fixed_byte(builder, view, index, failure):
    _check_view_operands(builder, view, failure, index)
    _after_bounds_check(builder, builder.icmp_unsigned("<", index, view.length), failure)
    offset = builder.add(view.offset, index)
    slot = builder.gep(view.span.storage, (ir.Constant(ir.IntType(32), 0), offset), inbounds=True)
    return builder.load(slot)


def scan_fixed_byte_prefix(builder, view, delimiter, failure, *, while_equal):
    """Return the length of the initial run satisfying a scalar comparison."""
    _check_view_operands(builder, view, failure)
    if type(delimiter) is not int or not 0 <= delimiter <= 255 or type(while_equal) is not bool:
        raise ValueError("fixed byte scan requires a byte and a definite comparison")
    i8, i64 = ir.IntType(8), ir.IntType(64)
    before = builder.block
    loop = builder.function.append_basic_block("fixed_byte.scan")
    inspect = builder.function.append_basic_block("fixed_byte.scan_read")
    increment = builder.function.append_basic_block("fixed_byte.scan_step")
    done = builder.function.append_basic_block("fixed_byte.scan_done")
    builder.branch(loop)
    builder.position_at_end(loop)
    index = builder.phi(i64)
    index.add_incoming(ir.Constant(i64, 0), before)
    builder.cbranch(builder.icmp_unsigned("<", index, view.length), inspect, done)
    builder.position_at_end(inspect)
    byte = read_fixed_byte(builder, view, index, failure)
    keep = builder.icmp_unsigned("==" if while_equal else "!=", byte, ir.Constant(i8, delimiter))
    builder.cbranch(keep, increment, done)
    builder.position_at_end(increment)
    next_index = builder.add(index, ir.Constant(i64, 1))
    builder.branch(loop)
    index.add_incoming(next_index, increment)
    builder.position_at_end(done)
    return index
