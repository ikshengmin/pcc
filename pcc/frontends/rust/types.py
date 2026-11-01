"""Rust semantic aggregate identity; deliberately not a C layout alias."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TupleType:
    elements: tuple[object, ...]

    def __str__(self) -> str:
        return "(" + ", ".join(str(item) for item in self.elements) + ",)"


@dataclass(frozen=True)
class SharedReference:
    """Reference identity; historical class name also covers explicit &mut."""
    referent: object
    mutable: bool = False
    lifetime: str | None = field(default=None, compare=False)

    def __str__(self) -> str:
        return "&" + ((self.lifetime + " ") if self.lifetime else "") + ("mut " if self.mutable else "") + str(self.referent)


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


INTEGER_TYPES = frozenset({'i32', 'i64', 'u32', 'u64', 'isize', 'usize'})


def integer_bits(ty, pointer_bits=None):
    """Target-sized integers retain distinct semantic names, not u64 aliases."""
    if ty in {'isize', 'usize'}:
        if pointer_bits != 64:
            raise ValueError('Rust target-sized integers require a qualified 64-bit pointer contract')
        return pointer_bits
    return int(ty[1:])


@dataclass(frozen=True, eq=False)
class ArrayLength:
    """Unresolved source constant; normalized before semantic instance keys."""
    expression: object

    def __str__(self):
        return '<constant expression>'


@dataclass(frozen=True)
class FixedArrayType:
    element: object
    length: int | ArrayLength

    def __str__(self):
        return '[' + str(self.element) + '; ' + str(self.length) + ']'
