"""Rust semantic aggregate identity; deliberately not a C layout alias."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TupleType:
    elements: tuple[object, ...]

    def __str__(self) -> str:
        return "(" + ", ".join(str(item) for item in self.elements) + ",)"


@dataclass(frozen=True)
class SharedReference:
    referent: object

    def __str__(self) -> str:
        return "&" + str(self.referent)


@dataclass(frozen=True)
class TypeApplication:
    """Nominal application, resolved within one declaration-owning checker."""
    name: str
    arguments: tuple[object, ...]

    def __str__(self) -> str:
        return self.name + "<" + ", ".join(map(str, self.arguments)) + ">"


@dataclass(frozen=True)
class TypeParameter:
    """A declaration-bound type variable, never a runtime value or IR type."""
    owner: str
    index: int
    name: str

    def __str__(self) -> str:
        return self.name
