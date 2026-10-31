"""Java fixed-width integer semantics, independent of source/class decoding.

These emit ordinary public IR values. No JVM operand stack or bytecode format
is exposed to the source frontend; target object/link ownership is unchanged.
"""
from pcc.ir.compat import ir


def wrap_integer(value, width):
    bits = value & ((1 << width) - 1)
    return bits if bits < (1 << (width - 1)) else bits - (1 << width)


def wrap_i32(value):
    return wrap_integer(value, 32)


def emit_binary(builder, operation, left, right):
    if operation in ("shl", "ashr", "lshr"):
        width = left.type.width
        if right.type.width > width:
            right = builder.trunc(right, left.type)
        elif right.type.width < width:
            right = builder.zext(right, left.type)
        right = builder.and_(right, ir.Constant(left.type, width - 1))
    emitter = getattr(builder, {"and": "and_", "or": "or_"}.get(operation, operation))
    # No nsw/nuw flags: Java integral arithmetic wraps at its promoted width.
    return emitter(left, right)


def emit_narrow(builder, operation, value):
    narrow = builder.trunc(value, ir.IntType(8 if operation == "i2b" else 16))
    extend = builder.zext if operation == "i2c" else builder.sext
    return extend(narrow, ir.IntType(32))
