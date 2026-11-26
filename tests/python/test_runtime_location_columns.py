"""Runtime stack-map locations convert a byte column at a time on the host.

emit_runtime_table turns each version 2 LOCATION (16 bytes) into a version
3 word: a managed frame-relative slot's negative offset, bit 0 set when the
slot is owned. _convert_locations_by_column checks every fixed field and
builds the words with bytes slicing; any other location falls back to
_runtime_location_word, which reports it. Both must write the same words.
"""
from __future__ import annotations

import random

import pytest

from pcc.backend import macho_host_runtime_stackmap as runtime
from pcc.backend import precise_stackmap as wire


def _valid(rng, arch):
    register = 29 if arch == wire.ARCH_AARCH64 else 6
    flags = rng.choice((wire.LOCATION_MANAGED, wire.LOCATION_MANAGED | wire.LOCATION_OWNED))
    offset = -8 * rng.randrange(1, 1 << 20)
    return (wire.LOCATION_STACK_INDIRECT, flags, 8, register, wire.NO_BASE, offset, 8)


def _words(locations, arch):
    return b"".join(wire._RUNTIME_LOCATION.pack(wire._runtime_location_word(location, arch))
                    for location in locations)


@pytest.mark.parametrize("arch", sorted(wire.ARCH_NAMES))
@pytest.mark.parametrize("count", [1, 7, 256, 4096])
def test_column_conversion_writes_the_per_location_words(arch, count):
    rng = random.Random(count + arch)
    locations = [_valid(rng, arch) for _ in range(count)]
    raw = b"".join(wire._LOCATION.pack(*location) for location in locations)
    converted = bytearray(count * 4 + 12)
    assert runtime._convert_locations_by_column(raw, count, arch, converted)
    assert bytes(converted[:count * 4]) == _words(locations, arch)


@pytest.mark.parametrize("field,value", [
    # kind, flags, size, register, base index, offset, extent
    (0, 2), (1, 0), (1, wire.LOCATION_OWNED), (2, 4), (3, 5), (4, 0), (5, 3), (5, 8), (6, 4),
])
def test_any_other_location_takes_the_reporting_conversion(field, value):
    arch = wire.ARCH_AARCH64
    rng = random.Random(field)
    locations = [_valid(rng, arch) for _ in range(9)]
    bad = list(locations[4])
    bad[field] = value
    locations[4] = tuple(bad)
    raw = b"".join(wire._LOCATION.pack(*location) for location in locations)
    converted = bytearray(9 * 4)
    assert not runtime._convert_locations_by_column(raw, 9, arch, converted)
    assert converted == bytearray(9 * 4)
    with pytest.raises(wire.PreciseStackMapError):
        _words(locations, arch)


def test_misaligned_offset_and_patched_converter_take_the_loop(monkeypatch):
    arch = wire.ARCH_AARCH64
    location = _valid(random.Random(3), arch)
    misaligned = location[:5] + (location[5] + 4,) + location[6:]
    raw = wire._LOCATION.pack(*misaligned)
    assert not runtime._convert_locations_by_column(raw, 1, arch, bytearray(4))
    monkeypatch.setattr(wire, "_runtime_location_word", lambda fields, arch: 0)
    assert not runtime._convert_locations_by_column(wire._LOCATION.pack(*location), 1, arch, bytearray(4))
