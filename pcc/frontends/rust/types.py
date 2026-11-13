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
class SliceType:
    """`[T]`, parsed so a source can be read past it; slices (unsized
    referents, fat pointers, unsizing) are not implemented, and every use is
    reported at `token` before any other reference boundary."""
    element: object
    token: object = field(default=None, compare=False)

    def __str__(self) -> str:
        return "[" + str(self.element) + "]"


SLICE_GAP = "slice types and array-to-slice unsizing are not implemented"


# `&'static str`: string-literal data. It is a plain Copy value type here:
# literals are its only source and no borrow of a local can produce one, so
# it needs no lifetime analysis. `&str` with any other lifetime is still an
# ordinary reference behind the checked-source boundary.
STATIC_STR = "&'static str"


def is_static_str(ty, *, elided_is_static=False):
    """Whether a parsed type is `&'static str` (or `&str` where elision means 'static)."""
    return (isinstance(ty, SharedReference) and ty.referent == "str" and not ty.mutable
            and (ty.lifetime == "'static" or elided_is_static and ty.lifetime is None))


@dataclass(frozen=True)
class TypeApplication:
    """Nominal application, resolved within one declaration-owning checker.

    `lifetimes` keeps the written lifetime arguments (`Type<'a, T>`) for
    diagnostics only: they are erased from type identity, as lifetimes are
    once an actual rustc check has proved them.
    """
    name: str
    arguments: tuple[object, ...]
    lifetimes: tuple[str, ...] = field(default=(), compare=False)

    def __str__(self) -> str:
        return self.name + "<" + ", ".join([*self.lifetimes, *map(str, self.arguments)]) + ">"


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
