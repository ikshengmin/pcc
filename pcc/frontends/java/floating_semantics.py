"""Java binary32/binary64 constant semantics (JLS 3.10.2, 5.1, 15.4).

Round exact ratios directly to the declared format, once. In particular a
decimal float literal and a long-to-float conversion must not first round to
binary64. Host floating arithmetic is not the Java constant evaluator.
"""
import re

from pcc.backend.wide_float import decode_float_bits, encode_ratio
from pcc.ir.support._float_bits import (
    _bits_to_float32,
    _bits_to_float64,
    _float64_to_bits,
)

from .integer_semantics import wrap_integer


DECIMAL_DIGITS = r"[0-9](?:[0-9_]*[0-9])?"
HEX_DIGITS = r"[0-9a-fA-F](?:[0-9a-fA-F_]*[0-9a-fA-F])?"
EXPONENT = r"[eE][+-]?" + DECIMAL_DIGITS
FLOAT_LITERAL = re.compile(
    r"(?:0[xX](?:" + HEX_DIGITS + r"\.?|(?:" + HEX_DIGITS + r")?\." + HEX_DIGITS
    + r")[pP][+-]?" + DECIMAL_DIGITS + r"[fFdD]?|"
    + DECIMAL_DIGITS + r"\.(?:" + DECIMAL_DIGITS + r")?(?:" + EXPONENT + r")?[fFdD]?|"
    + r"\." + DECIMAL_DIGITS + r"(?:" + EXPONENT + r")?[fFdD]?|"
    + DECIMAL_DIGITS + EXPONENT + r"[fFdD]?|" + DECIMAL_DIGITS + r"[fFdD])"
)


INFINITY = 1e309
NAN = INFINITY - INFINITY


def negative(value):
    return bool(_float64_to_bits(value) >> 63)


def infinite(value):
    return value == INFINITY or value == -INFINITY


def finite_ratio(value):
    sign, numerator, denominator, special = decode_float_bits(_float64_to_bits(value), 64)
    assert not special
    return -numerator if sign else numerator, denominator


def rounded_ratio(numerator, denominator, dtype, *, negative_zero=False):
    """Reuse owned integer-only rounding and the IR's exact bit transport.

    No fractions/decimal module, host decimal parser, or host floating
    arithmetic is an intermediate rounding owner. These existing helpers are
    also on the owned object emitter's dependency path.
    """
    width = 32 if dtype == "float" else 64
    bits = encode_ratio(numerator, denominator, width, negative_zero and numerator == 0)
    return _bits_to_float32(bits) if width == 32 else _bits_to_float64(bits)


def floating_literal(node):
    text = node.value.replace("_", "").lower()
    dtype = "float" if text[-1] == "f" else "double"
    if text[-1] in "fd":
        text = text[:-1]
    # Keep exact-ratio allocation bounded independently of adversarial exponents.
    if len(text) > 4096:
        node.token.error("floating literal exceeds the initial precision budget",
                         code="java.unsupported.source-size-limit")
    hexadecimal = text.startswith("0x")
    pieces = text.split("p" if hexadecimal else "e")
    significand = pieces[0][2:] if hexadecimal else pieces[0]
    digits = significand.replace(".", "")
    coefficient = int(digits, 16 if hexadecimal else 10)
    if not coefficient:
        return dtype, 0.0
    exponent_text = pieces[1] if len(pieces) == 2 else "0"
    # Leading exponent zeroes are valid and must not change the range decision.
    exponent_digits = exponent_text.lstrip("+-").lstrip("0")
    exponent = (int(exponent_text) if len(exponent_digits) <= 6 else
                -1_000_000 if exponent_text.startswith("-") else 1_000_000)
    fraction_digits = len(significand.split(".")[1]) if "." in significand else 0
    exponent -= fraction_digits * (4 if hexadecimal else 1)
    # These conservative bounds are outside both Java formats even with the
    # maximum admitted significand; avoid allocating giant powers of 2 or 10.
    if exponent > 20000 or exponent < -20000:
        node.token.error("floating literal rounds to infinity or zero")
    radix = 2 if hexadecimal else 10
    numerator, denominator = ((coefficient * radix ** exponent, 1) if exponent >= 0
                              else (coefficient, radix ** -exponent))
    value = rounded_ratio(numerator, denominator, dtype)
    if value == 0 or infinite(value):
        node.token.error("nonzero floating literal rounds to infinity or zero")
    return dtype, value


def converted_constant(value, source, target):
    """Primitive numeric conversion, including Java's two-stage FP narrowing."""
    if value is None or source == target:
        return value
    if target in {"float", "double"}:
        if source in {"float", "double"}:
            if value != value or infinite(value):
                return value
            numerator, denominator = finite_ratio(value)
            return rounded_ratio(numerator, denominator, target, negative_zero=negative(value))
        return rounded_ratio(value, 1, target)
    if source in {"float", "double"}:
        width = 64 if target == "long" else 32
        minimum, maximum = -(1 << (width - 1)), (1 << (width - 1)) - 1
        value = (0 if value != value else minimum if value <= minimum
                 else maximum if value >= maximum else int(value))
    bits = {"byte": 8, "short": 16, "char": 16, "int": 32, "long": 64}[target]
    return value & 0xffff if target == "char" else wrap_integer(value, bits)


def floating_binary(operation, left, right, dtype):
    """Already-promoted operands; no remainder approximation or contraction."""
    if operation in {"==", "!=", "<", "<=", ">", ">="}:
        return {"==": lambda: left == right, "!=": lambda: left != right,
                "<": lambda: left < right, "<=": lambda: left <= right,
                ">": lambda: left > right, ">=": lambda: left >= right}[operation]()
    if left != left or right != right:
        return NAN
    if operation == "-":
        return floating_binary("+", left, -right, dtype)
    negative_result = negative(left) != negative(right)
    if operation == "+":
        if infinite(left) or infinite(right):
            return NAN if left == -right else left if infinite(left) else right
        a, b = finite_ratio(left)
        c, d = finite_ratio(right)
        numerator, denominator = a * d + c * b, b * d
        negative_zero = left == right == 0 and negative(left) and negative(right)
    elif operation == "*":
        if infinite(left) or infinite(right):
            return NAN if left == 0 or right == 0 else -INFINITY if negative_result else INFINITY
        a, b = finite_ratio(left)
        c, d = finite_ratio(right)
        numerator, denominator, negative_zero = a * c, b * d, negative_result
    elif operation == "/":
        if (left == 0 and right == 0) or (infinite(left) and infinite(right)):
            return NAN
        if right == 0 or infinite(left):
            return -INFINITY if negative_result else INFINITY
        if infinite(right):
            return -0.0 if negative_result else 0.0
        a, b = finite_ratio(left)
        c, d = finite_ratio(right)
        numerator, denominator, negative_zero = a * d, b * c, negative_result
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
    else:
        raise AssertionError("unsupported Java floating operation " + operation)
    return rounded_ratio(numerator, denominator, dtype, negative_zero=negative_zero)
