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
TYPE_NAMES = {name: name for name in INTEGER_TYPES}
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
