"""Java fixed-width integer semantics, independent of source/class decoding.

These emit ordinary public IR values. No JVM operand stack or bytecode format
is exposed to the source frontend; target object/link ownership is unchanged.
"""
from pcc.ir.compat import ir


def wrap_i32(value):
    bits = value & 0xffffffff
    return bits if bits < 0x80000000 else bits - 0x100000000


def emit_binary(builder, operation, left, right):
    if operation in ("shl", "ashr", "lshr"):
        right = builder.and_(right, ir.Constant(ir.IntType(32), 31))
    emitter = getattr(builder, {"and": "and_", "or": "or_"}.get(operation, operation))
    # No nsw/nuw flags: signed Java int arithmetic wraps modulo 2**32.
    return emitter(left, right)


def emit_narrow(builder, operation, value):
    narrow = builder.trunc(value, ir.IntType(8 if operation == "i2b" else 16))
    extend = builder.zext if operation == "i2c" else builder.sext
    return extend(narrow, ir.IntType(32))
