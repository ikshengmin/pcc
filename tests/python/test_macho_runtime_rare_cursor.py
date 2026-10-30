"""Finite host runtime-table parity and rare-row decode-work regressions.

These exercise the complete validator on real wire bytes, not a Stage1
build, native execution, GC behavior, or an elapsed-time benchmark.
"""

from collections import Counter
import struct
import sys

import pytest

from pcc.backend import (
    macho_host_runtime_validation as host,
    macho_host_validation as boundary,
    precise_stackmap as wire,
)


def _budget():
    word = struct.calcsize("P")
    return wire.CompactStackMapBudget(
        8_000_000, 100_000, set_header_bytes=sys.getsizeof(set()),
        set_entry_bytes=2 * word, integer_bytes=sys.getsizeof((1 << 64) - 1),
        metadata_entry_bytes=64 * word + sys.getsizeof((0, 0, 0))
        + 3 * sys.getsizeof((1 << 64) - 1) + sys.getsizeof(b""),
        allocation_quantum=2 * word,
    )


def _payload(record_count=32, rare_indices=(), *, split=None):
    lengths = (record_count,) if split is None else (split, record_count - split)
    functions = []
    records = []
    first = 0
    for ordinal, count in enumerate(lengths):
        functions.append(wire._RUNTIME_FUNCTION.pack(
            ordinal + 1, 0x1000 + ordinal * 0x1000, count + 2, 16, first, count,
        ))
        records.extend(wire._RUNTIME_RECORD.pack(pc + 1, 0) for pc in range(count))
        first += count
    rare = [wire._RUNTIME_RARE.pack(
        index, wire.SAFEPOINT_CALL, 0, 0, wire.NO_OFFSET, 0,
    ) for index in rare_indices]
    return b"".join((
        wire._RUNTIME_HEADER.pack(
            wire.MAGIC, wire.RUNTIME_TABLE_VERSION, wire.ARCH_AARCH64,
            wire.POINTER_SIZE, len(functions), record_count, 1, 1, len(rare), 0, 0,
        ),
        b"".join(functions), b"".join(records),
        wire._RUNTIME_LIVE_SET.pack(0, 1), wire._RUNTIME_LOCATION.pack(-8),
        b"".join(rare),
    ))


def _result(validator, payload):
    try:
        validator(payload, expected_arch=wire.ARCH_AARCH64, final_image=True)
    except wire.PreciseStackMapError as error:
        return type(error), str(error)
    return None


def _host(payload, **kwargs):
    return host.validate_runtime_table(payload, compact_budget=_budget(), **kwargs)


@pytest.mark.parametrize("record_count,rare_indices,split", (
    (32, (), None),
    (32, (0,), None),
    (32, (31,), None),
    (32, tuple(range(32)), None),
    (4096, (2, 2047, 4095), None),
    (32, (0, 15, 16, 31), 16),
))
def test_runtime_rare_cursor_preserves_complete_validation(
    record_count, rare_indices, split,
):
    payload = _payload(record_count, rare_indices, split=split)
    assert _result(wire.validate_runtime_table, payload) is None
    assert _result(_host, payload) is None


@pytest.mark.parametrize("record_count,rare_indices,split", (
    (32, (), None),
    (32, (0,), None),
    (32, (31,), None),
    (32, tuple(range(32)), None),
    (4096, (2, 2047, 4095), None),
    (32, (0, 15, 16, 31), 16),
))
def test_runtime_rare_cursor_decodes_each_row_twice(
    monkeypatch, record_count, rare_indices, split,
):
    payload = _payload(record_count, rare_indices, split=split)
    original = wire._RUNTIME_RARE
    reads = Counter()

    class CountedRare:
        size = original.size

        def unpack_from(self, data, offset=0):
            reads[offset] += 1
            return original.unpack_from(data, offset)

    monkeypatch.setattr(wire, "_RUNTIME_RARE", CountedRare())
    # Directly execute this private algorithm: production deliberately falls
    # back when its codec is instrumented. One full validation pass and one
    # cursor read per rare row are required, independent of ordinary records.
    assert _result(_host, payload) is None
    assert len(reads) == len(rare_indices)
    assert sum(reads.values()) == 2 * len(rare_indices)
    assert all(count == 2 for count in reads.values())


def _invalid_payload(case):
    payload = bytearray(_payload(32, (4, 31)))
    function = wire._RUNTIME_HEADER.size
    records = function + wire._RUNTIME_FUNCTION.size
    rare = len(payload) - 2 * wire._RUNTIME_RARE.size
    row = list(wire._RUNTIME_RARE.unpack_from(payload, rare))
    if case == "duplicate-rare":
        struct.pack_into("<I", payload, rare + wire._RUNTIME_RARE.size, row[0])
    elif case == "rare-out-of-range":
        struct.pack_into("<I", payload, rare + wire._RUNTIME_RARE.size, 32)
    elif case in ("reserved", "rare-before-bad-record", "rare-before-bad-function"):
        row[3] = 1
    elif case == "unknown-kind":
        row[1] = 255
    elif case == "unknown-flags":
        row[2] = 255
    elif case == "exception-outside-function":
        row[2] = wire.RECORD_HAS_EXCEPTION_EDGE
        row[4] = 34
    elif case == "bad-record-before-exception":
        row[0] = 0
        row[2] = wire.RECORD_HAS_EXCEPTION_EDGE
        row[4] = 34
    elif case == "unowned-records":
        fields = list(wire._RUNTIME_FUNCTION.unpack_from(payload, function))
        fields[5] -= 1
        wire._RUNTIME_FUNCTION.pack_into(payload, function, *fields)
    else:
        raise AssertionError(case)
    wire._RUNTIME_RARE.pack_into(payload, rare, *row)
    if case in ("rare-before-bad-record", "bad-record-before-exception"):
        wire._RUNTIME_RECORD.pack_into(payload, records, 34, 0)
    if case == "rare-before-bad-function":
        struct.pack_into("<Q", payload, function, 0)
    return bytes(payload)


@pytest.mark.parametrize("case", (
    "duplicate-rare", "rare-out-of-range", "reserved", "unknown-kind",
    "unknown-flags", "exception-outside-function", "unowned-records",
    "rare-before-bad-record", "rare-before-bad-function",
    "bad-record-before-exception",
))
def test_runtime_rare_cursor_preserves_errors_and_priority(case):
    payload = _invalid_payload(case)
    expected = _result(wire.validate_runtime_table, payload)
    assert expected is not None
    assert _result(_host, payload) == expected


def test_runtime_validation_boundary_rejects_mutable_payload():
    with pytest.raises(wire.PreciseStackMapError, match="must be immutable bytes"):
        boundary.validate_stack_map_payload(
            bytearray(_payload(32, (31,))), compact_budget=_budget(),
        )


def test_runtime_validation_boundary_selects_guarded_host_route():
    payload = _payload(32, (0, 31))
    budget = _budget()
    # This is an actual eligibility check, not a forced dispatch or a skip on
    # unsupported/instrumented hosts. Run it with the supported plain CPython.
    assert host.available(payload, budget)
    assert boundary.validate_stack_map_payload(
        payload, expected_arch=wire.ARCH_AARCH64, final_image=True,
        compact_budget=budget,
    ) is None


def test_runtime_validation_profile_hook_keeps_generic_fallback(monkeypatch):
    payload = _payload(32, (31,))
    expected = _result(wire.validate_runtime_table, payload)
    budget = _budget()
    # Establish the eligibility change before replacing the optimized entry.
    assert host.available(payload, budget)

    def forbidden(*args, **kwargs):
        raise AssertionError("instrumented input used the private host route")

    previous = sys.getprofile()
    try:
        sys.setprofile(lambda frame, event, arg: None)
        assert not host.available(payload, budget)
        monkeypatch.setattr(host, "validate_runtime_table", forbidden)
        actual = _result(
            lambda value, **kwargs: boundary.validate_stack_map_payload(
                value, compact_budget=_budget(), **kwargs,
            ), payload,
        )
    finally:
        sys.setprofile(previous)
    assert actual == expected
