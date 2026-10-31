"""Rust semantic aggregate identity; deliberately not a C layout alias."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TupleType:
    elements: tuple[object, ...]

    def __str__(self) -> str:
        return "(" + ", ".join(str(item) for item in self.elements) + ",)"
