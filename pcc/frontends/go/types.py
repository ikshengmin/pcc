"""Go scalar type identities and target data-model facts.

The currently admitted x86_64/arm64 targets have 64-bit int, uint and uintptr.
Fixed-width types never depend on a host C data model; byte/rune are aliases,
while int, int64 and uintptr remain distinct named Go types.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArrayType:
    count: int
    element: str

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


INTEGER_TYPES = {
    "int8": (8, True), "int16": (16, True), "int32": (32, True), "int64": (64, True),
    "uint8": (8, False), "uint16": (16, False), "uint32": (32, False), "uint64": (64, False),
    "int": (64, True), "uint": (64, False), "uintptr": (64, False),
}
TYPE_NAMES = {name: name for name in INTEGER_TYPES}
TYPE_NAMES.update({"bool": "bool", "byte": "uint8", "rune": "int32", "string": "string"})
TYPES = frozenset(TYPE_NAMES.values())


def is_string(dtype):
    return dtype in ("string", "untyped string")


def integer_bits(dtype):
    return 64 if dtype == "untyped int" else INTEGER_TYPES[dtype][0]


def integer_signed(dtype):
    return True if dtype == "untyped int" else INTEGER_TYPES[dtype][1]


def integer_bounds(dtype):
    bits, signed = INTEGER_TYPES[dtype]
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)


def is_integer(dtype):
    return isinstance(dtype, str) and (dtype == "untyped int" or dtype in INTEGER_TYPES)


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
