"""Go nominal identity and value-record types, independent of IR layout.

A defined type gets declaration identity, not identity from its representation.
Aliases reuse the resolved type object. No runtime class/GC layout is introduced.
"""
from dataclasses import dataclass


@dataclass(eq=False)
class DefinedType:
    name: str
    package: str
    underlying: object = None

    def __str__(self):
        return self.package + "." + self.name if self.package else self.name


@dataclass(frozen=True)
class Field:
    name: str
    type: object
    package: str = ""


@dataclass(frozen=True)
class StructType:
    fields: tuple

    def __str__(self):
        return "struct{" + "; ".join(field.name + " " + str(field.type) for field in self.fields) + "}"


def underlying(dtype):
    return dtype.underlying if isinstance(dtype, DefinedType) else dtype


def is_named(dtype):
    return isinstance(dtype, DefinedType) or isinstance(dtype, str) and not dtype.startswith("untyped ")


def same_underlying(left, right):
    return underlying(left) == underlying(right)
