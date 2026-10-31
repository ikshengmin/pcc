"""Structural place identities, not pointer-alias or language ownership proofs.

Adapters supply stable root identities and projection identities. A field and
an index are distinct projections even if their labels compare equal. An index
identifies an element selected by the adapter; this module does not resolve
dynamic indices, dereferences, aliases, storage lifetimes or overlapping layouts.
"""
from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    identity: Hashable


@dataclass(frozen=True)
class Index:
    identity: Hashable


Path = tuple[Field | Index, ...]


def prefix(left: Path, right: Path) -> bool:
    """Whether left is an identical or enclosing structural projection path."""
    return right[:len(left)] == left


@dataclass(frozen=True)
class Place:
    root: Hashable
    path: Path = ()

    def project(self, projection: Field | Index) -> Place:
        return Place(self.root, self.path + (projection,))

    def contains(self, other: Place) -> bool:
        return self.root == other.root and prefix(self.path, other.path)

    def overlaps(self, other: Place) -> bool:
        """Structural ancestor overlap only; False does not prove non-aliasing."""
        return self.contains(other) or other.contains(self)
