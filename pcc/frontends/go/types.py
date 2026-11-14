"""Go scalar type identities and target data-model facts.

The currently admitted x86_64/arm64 targets have 64-bit int, uint and uintptr.
Fixed-width types never depend on a host C data model; byte/rune are aliases,
while int, int64 and uintptr remain distinct named Go types.
"""

from dataclasses import dataclass
from pcc.frontends.go.records import (
    DefinedType, InterfaceMethod, InterfaceType, MethodSignature, StructType, underlying,
)


class PredeclaredError(DefinedType):
    def __deepcopy__(self, memo):
        # Generic syntax snapshots must preserve the universe declaration.
        return self


ERROR_TYPE = PredeclaredError("error", "", InterfaceType((
    InterfaceMethod("Error", "", MethodSignature((), "string")),
)))
ANY_TYPE = InterfaceType(())


def is_error_interface(dtype):
    return underlying(dtype) == ERROR_TYPE.underlying


@dataclass(frozen=True)
class PointerType:
    element: object

    def __str__(self):
        return "*" + str(self.element)


@dataclass(frozen=True)
class SliceType:
    """Signature identity only. No slice value or backing-store ABI is admitted."""
    element: object

    def __str__(self):
        return "[]" + str(self.element)


@dataclass(frozen=True)
class ArrayType:
    count: int
    element: object

    def __str__(self):
        return f"[{self.count}]{self.element}"


@dataclass(frozen=True)
class Results:
    """A function's multiple values, never a first-class Go tuple type."""
    elements: tuple

    def __str__(self):
        return "(" + ", ".join(str(item) for item in self.elements) + ")"


def result_types(dtype):
    return dtype.elements if isinstance(dtype, Results) else () if dtype == "void" else (dtype,)


def identical_ignoring_tags(left, right):
    """Conversion-only structural identity; nested named types stay nominal.

    Assignment, method matching, inference and instance identity deliberately
    keep ordinary equality, including every decoded struct tag. Only explicit
    conversions use this recursive relation after stripping their outer names.
    """
    if left is right:
        return True
    if isinstance(left, DefinedType) or isinstance(right, DefinedType):
        return False
    if type(left) is not type(right):
        return False
    if isinstance(left, StructType):
        return len(left.fields) == len(right.fields) and all(
            a.name == b.name and a.package == b.package and a.embedded == b.embedded
            and identical_ignoring_tags(a.type, b.type)
            for a, b in zip(left.fields, right.fields))
    if isinstance(left, ArrayType):
        return left.count == right.count and identical_ignoring_tags(left.element, right.element)
    if isinstance(left, (PointerType, SliceType)):
        return identical_ignoring_tags(left.element, right.element)
    if isinstance(left, Results):
        return len(left.elements) == len(right.elements) and all(
            identical_ignoring_tags(a, b) for a, b in zip(left.elements, right.elements))
    if isinstance(left, InterfaceType):
        return len(left.methods) == len(right.methods) and all(
            a.name == b.name and a.package == b.package
            and a.signature.variadic == b.signature.variadic
            and len(a.signature.parameters) == len(b.signature.parameters)
            and all(identical_ignoring_tags(x, y)
                    for x, y in zip(a.signature.parameters, b.signature.parameters))
            and identical_ignoring_tags(a.signature.result, b.signature.result)
            for a, b in zip(left.methods, right.methods))
    return left == right


def same_underlying_ignoring_tags(left, right):
    return identical_ignoring_tags(underlying(left), underlying(right))


INTEGER_TYPES = {
    "int8": (8, True), "int16": (16, True), "int32": (32, True), "int64": (64, True),
    "uint8": (8, False), "uint16": (16, False), "uint32": (32, False), "uint64": (64, False),
    "int": (64, True), "uint": (64, False), "uintptr": (64, False),
}
# IEEE 754 binary32/binary64: (significand bits incl. the implicit one,
# minimum normal exponent, maximum exponent), https://go.dev/ref/spec#Numeric_types
FLOAT_TYPES = {"float32": (24, -126, 127), "float64": (53, -1022, 1023)}
TYPE_NAMES = {name: name for name in INTEGER_TYPES}
TYPE_NAMES.update({name: name for name in FLOAT_TYPES})
TYPE_NAMES.update({"bool": "bool", "byte": "uint8", "rune": "int32", "string": "string"})
TYPE_NAMES["error"] = ERROR_TYPE
TYPE_NAMES["any"] = ANY_TYPE
TYPES = frozenset(TYPE_NAMES.values())


def is_string(dtype):
    return underlying(dtype) in ("string", "untyped string")


def is_untyped_integer(dtype):
    return dtype in ("untyped int", "untyped rune")


def integer_bits(dtype):
    dtype = underlying(dtype)
    if is_untyped_integer(dtype):
        return 32 if dtype == "untyped rune" else 64
    return INTEGER_TYPES[dtype][0]


def integer_signed(dtype):
    dtype = underlying(dtype)
    return True if is_untyped_integer(dtype) else INTEGER_TYPES[dtype][1]


def integer_bounds(dtype):
    bits, signed = INTEGER_TYPES[underlying(dtype)]
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)


def is_integer(dtype):
    dtype = underlying(dtype)
    return isinstance(dtype, str) and (is_untyped_integer(dtype) or dtype in INTEGER_TYPES)


def is_float(dtype):
    dtype = underlying(dtype)
    return isinstance(dtype, str) and (dtype == "untyped float" or dtype in FLOAT_TYPES)


def float_bits(dtype):
    dtype = underlying(dtype)
    return 64 if dtype == "untyped float" else 32 if dtype == "float32" else 64


def is_numeric(dtype):
    return is_integer(dtype) or is_float(dtype)


def round_float(value, dtype):
    """Round an exact constant to dtype's IEEE format, half to even.

    Returns the rounded exact value, or None when it overflows the format
    (https://go.dev/ref/spec#Representability). Subnormals keep the minimum
    exponent; a Go constant has no negative zero.
    """
    from fractions import Fraction
    precision, minimum, maximum = FLOAT_TYPES[underlying(dtype)]
    if value == 0:
        return Fraction(0)
    negative = value < 0
    numerator, denominator = abs(value.numerator), value.denominator
    exponent = numerator.bit_length() - denominator.bit_length()
    if numerator * (1 << max(0, -exponent)) < denominator * (1 << max(0, exponent)):
        exponent -= 1
    shift = max(exponent, minimum) - (precision - 1)
    if shift >= 0:
        denominator <<= shift
    else:
        numerator <<= -shift
    quotient, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or 2 * remainder == denominator and quotient & 1:
        quotient += 1
    if quotient.bit_length() + shift > maximum + 1:
        return None
    result = Fraction(quotient) * (Fraction(2) ** shift)
    return -result if negative else result


def supported_target(triple):
    """Admit only known 64-bit word/pointer ABIs, not an architecture prefix.

    An x86_64 or aarch64 spelling alone does not establish a 64-bit pointer
    model (x32 and ILP32 exist). The shared matchers establish the owned OS
    route; this frontend also checks the environment component explicitly.
    """
    from pcc.backend.self_backend_target_match import (
        is_aarch64_darwin_triple, is_aarch64_linux_triple, is_x86_64_linux_triple,
    )

    if not isinstance(triple, str):
        return False
    parts = triple.lower().split("-")
    if is_aarch64_darwin_triple(triple):
        return len(parts) == 3
    if is_x86_64_linux_triple(triple) or is_aarch64_linux_triple(triple):
        return len(parts) != 4 or parts[3] in ("gnu", "musl")
    return False
