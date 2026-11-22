"""Model a size-class slab cursor without an object index.

A single mutator owns the bump cursor until stopped-world handoff. Published
tail is a release/acquire boundary in the eventual runtime, not in this
serial model. A cursor never represents a lifetime outside its own epoch.
"""
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Cursor:
    epoch: int
    published_end: int
    next_cell: int = 0


class StaleCursor(Exception):
    pass


class NurserySlab:
    def __init__(self, base, cell_size, capacity):
        assert cell_size > 0 and capacity > 0
        self.base = base
        self.cell_size = cell_size
        self.capacity = capacity
        self.epoch = 0
        self.published = 0
        self.reserved = False
        self.young_bits = 0
        self.old_bits = 0

    def reserve(self):
        assert not self.reserved
        if self.published == self.capacity:
            return None
        self.reserved = True
        return self.base + self.published * self.cell_size

    def publish(self, address):
        assert self.reserved and address == self.base + self.published * self.cell_size
        self.young_bits |= 1 << self.published
        self.published += 1
        self.reserved = False

    def snapshot(self):
        return Cursor(self.epoch, self.published)

    def next_young(self, cursor):
        if cursor.epoch != self.epoch:
            raise StaleCursor
        mask = ((1 << cursor.published_end) - 1) ^ ((1 << cursor.next_cell) - 1)
        remaining = self.young_bits & mask
        if not remaining:
            return None
        first_bit = remaining & -remaining
        cell = first_bit.bit_length() - 1
        return (self.base + cell * self.cell_size,
                Cursor(cursor.epoch, cursor.published_end, cell + 1))

    def young_exact(self, address):
        offset = address - self.base
        if offset < 0 or offset % self.cell_size != 0:
            return False
        cell = offset // self.cell_size
        return cell < self.published and bool(self.young_bits & (1 << cell))

    def retire_young(self, address, *, retained_as_old=False):
        assert self.young_exact(address)
        bit = 1 << ((address - self.base) // self.cell_size)
        self.young_bits &= ~bit
        if retained_as_old:
            self.old_bits |= bit

    def retire_old(self, address):
        assert (address - self.base) % self.cell_size == 0
        bit = 1 << ((address - self.base) // self.cell_size)
        assert self.old_bits & bit
        self.old_bits &= ~bit

    def reset(self):
        if self.reserved or self.young_bits or self.old_bits:
            return False
        self.epoch += 1
        self.published = 0
        return True


def enumerate_young(slab, cursor):
    seen = []
    while (next_entry := slab.next_young(cursor)) is not None:
        address, cursor = next_entry
        seen.append(address)
    return seen


def allocate(slab):
    address = slab.reserve()
    assert address is not None
    slab.publish(address)
    return address


def test_span_uses_published_tail_not_reserved_or_new_tail():
    slab = NurserySlab(0x1000, 32, 4)
    first = allocate(slab)
    pending = slab.reserve()
    cursor = slab.snapshot()
    assert not slab.young_exact(pending)
    assert not slab.young_exact(first + 1)
    assert not slab.reset()
    slab.publish(pending)
    assert enumerate_young(slab, cursor) == [first]
    assert enumerate_young(slab, slab.snapshot()) == [first, pending]


@pytest.mark.parametrize("discard", ("dead", "promoted"))
def test_cursor_skips_holes_without_reusing_an_in_place_old_object(discard):
    slab = NurserySlab(0x1000, 32, 3)
    a, b, c = (allocate(slab) for _ in range(3))
    assert slab.reserve() is None
    cursor = slab.snapshot()
    slab.retire_young(b, retained_as_old=discard == "promoted")
    assert not slab.young_exact(b)
    assert enumerate_young(slab, cursor) == [a, c]
    slab.retire_young(a)
    slab.retire_young(c)
    if discard == "promoted":
        assert not slab.reset()
        slab.retire_old(b)
    assert slab.reset()


def test_stale_cursor_cannot_validate_a_reused_address():
    slab = NurserySlab(0x1000, 32, 1)
    first = allocate(slab)
    cursor = slab.snapshot()
    slab.retire_young(first)
    assert slab.reset()
    assert allocate(slab) == first
    assert slab.young_exact(first)  # current raw provenance is not identity
    with pytest.raises(StaleCursor):
        enumerate_young(slab, cursor)
    assert enumerate_young(slab, slab.snapshot()) == [first]
