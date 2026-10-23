"""Small host-only regression fixtures for emitted stack-map admission.

Hand-constructed wire-format inputs contain eight functions and 32 records.
They exercise host serialization and admission without invoking a compiler,
executable linker, runtime collector, or native code.
"""

import io
import struct
import sys

import pytest

from pcc.backend import macho_host_stackmap as spool
from pcc.backend import precise_stackmap as wire


_RETAINED_BYTES = 32 * 1024
_FUNCTION_IDS = (8, 2, 7, 1, 6, 3, 5, 4)


def _budget(limit=1_000_000):
    word = struct.calcsize("P")
    quantum = 2 * word

    def rounded(size):
        return ((size + quantum - 1) // quantum) * quantum

    integer = rounded(sys.getsizeof((1 << 64) - 1))
    budget = wire.CompactStackMapBudget(
        limit, 256, set_header_bytes=rounded(sys.getsizeof(set())),
        set_entry_bytes=2 * word, integer_bytes=integer,
        metadata_entry_bytes=(64 * word + rounded(sys.getsizeof((0, 0, 0)))
                              + 3 * integer + rounded(sys.getsizeof(b""))),
        allocation_quantum=quantum,
    )
    # Keep the tiny caller-owned fixtures and byte oracles charged throughout.
    budget.retained_bytes = _RETAINED_BYTES
    return budget


def _payloads(arch=wire.ARCH_AARCH64):
    register = 29 if arch == wire.ARCH_AARCH64 else 6

    def slot(offset, owned=False):
        flags = wire.LOCATION_MANAGED | (wire.LOCATION_OWNED if owned else 0)
        return wire.StackMapLocation(
            wire.LOCATION_STACK_INDIRECT, flags, register=register, offset=offset,
        )

    shared = (slot(-8), slot(-16, owned=True))
    payloads = []
    for fid in _FUNCTION_IDS:
        singleton = (slot(-24 - 8 * (fid % 2), owned=True),)
        function = wire.FunctionStackMap(
            function_id=fid, function_address=4096 * fid,
            code_size=32, frame_size=64,
            records=(
                wire.SafepointRecord(fid * 16 + 1, 0, wire.SAFEPOINT_ENTRY, ()),
                wire.SafepointRecord(fid * 16 + 2, 4, wire.SAFEPOINT_CALL, shared),
                wire.SafepointRecord(
                    fid * 16 + 3, 8, wire.SAFEPOINT_EXCEPTION, shared,
                    flags=wire.RECORD_HAS_EXCEPTION_EDGE, exceptional_offset=24,
                ),
                wire.SafepointRecord(
                    fid * 16 + 4, 12, wire.SAFEPOINT_CONTINUATION, singleton,
                    flags=wire.RECORD_SUSPENDED, continuation_id=7,
                ),
            ),
        )
        payloads.append(wire.encode_stack_map(wire.PreciseStackMap(arch, (function,))))
    return tuple(payloads)


def _estimate(budget, raw, *, records=0, entries=0):
    result = raw + budget.retained_bytes + entries * budget.metadata_entry_bytes
    return result + (budget._set_bytes(records) if records else 0)


def _merge(payloads, *, runtime=True, single=True, limit=1_000_000):
    budget = _budget(limit)
    checks = []
    check = budget.check_peak

    def tracked(raw, *, records=0, entries=0, label="stack-map"):
        checks.append((label, raw, _estimate(budget, raw, records=records, entries=entries)))
        return check(raw, records=records, entries=entries, label=label)

    budget.check_peak = tracked
    owned = [bytes(memoryview(payload)) for payload in payloads]
    result = spool.merge_owned_stack_map_payloads(
        owned, compact_budget=budget, single_buffer=single, runtime_table=runtime,
    )
    assert owned == [None] * len(payloads)
    assert budget.retained_bytes == _RETAINED_BYTES
    return result, checks, budget


@pytest.mark.parametrize("arch", [wire.ARCH_AARCH64, wire.ARCH_X86_64])
@pytest.mark.parametrize("single", [False, True])
def test_runtime_output_bound_preserves_small_format_bytes_and_offsets(arch, single):
    payloads = _payloads(arch)
    assert len(payloads) == 8 and sum(map(len, payloads)) < 2048
    v2, offsets = wire.merge_stack_map_payloads(payloads, compact_budget=_budget())
    expected = wire.transcode_runtime_table(v2, offsets)
    actual, checks, budget = _merge(payloads, single=single)
    assert actual == expected
    assert actual[1] == tuple((fid, 40 + index * 32 + 8)
                             for index, fid in enumerate(range(1, 9)))
    # Eight functions, 32 records, empty + three nonempty live sets,
    # four physical slots, and two rare records per function.
    assert wire._RUNTIME_HEADER.unpack_from(actual[0])[4:] == (8, 32, 4, 4, 16, 0, 0)
    assert len(actual[0]) == 40 + 8 * 32 + 32 * 8 + 4 * 8 + 4 * 4 + 16 * 16
    wire.validate_runtime_table(actual[0], expected_arch=arch)
    assert budget.record_visits == 32
    bounds = [raw for label, raw, _ in checks if label == "spool output bound"]
    # Preflight conservatively reserves a rare row for all 32 records,
    # not just the 16 rare rows this fixture actually emits.
    assert bounds[-1] == spool._FIXED_BYTES + 40 + 8 * 32 + 32 * 24 + 4 * 8 + 4 * 4


@pytest.mark.parametrize("single", [False, True])
def test_runtime_output_bound_tight_limit_keeps_v2_and_one_byte_refusals(single):
    payloads = _payloads()
    expected, checks, _ = _merge(payloads, single=single)
    limit = max(estimate for _, _, estimate in checks)
    bound = [estimate for label, _, estimate in checks if label == "spool output bound"][-1]
    assert limit == bound
    actual, _, budget = _merge(payloads, single=single, limit=limit)
    assert actual == expected and budget.limit_bytes == limit
    with pytest.raises(wire.PreciseStackMapError, match="spool output bound"):
        _merge(payloads, runtime=False, single=single, limit=limit)
    with pytest.raises(wire.PreciseStackMapError, match="spool output bound"):
        _merge(payloads, single=single, limit=limit - 1)


def test_runtime_output_bound_keeps_final_actual_size_admission(monkeypatch):
    from pcc.backend import macho_host_runtime_stackmap as runtime

    budget = _budget()
    check = budget.check_peak
    refused = []

    def final_boundary(raw, *, records=0, entries=0, label="stack-map"):
        if label == "runtime spool materialize":
            estimate = _estimate(budget, raw, records=records, entries=entries)
            assert estimate < budget.limit_bytes
            # Lower only this test budget at the final allocation boundary.
            # The real check still receives the actual allocation estimate.
            budget.limit_bytes = estimate - 1
            refused.append(estimate)
        return check(raw, records=records, entries=entries, label=label)

    def forbidden(*args, **kwargs):
        pytest.fail("allocated a final backing before its size was admitted")

    budget.check_peak = final_boundary
    monkeypatch.setattr(runtime.io, "BytesIO", forbidden)
    with pytest.raises(wire.PreciseStackMapError, match="runtime spool materialize"):
        spool.merge_owned_stack_map_payloads(
            list(_payloads()), compact_budget=budget,
            single_buffer=True, runtime_table=True,
        )
    assert len(refused) == 1
    assert "label=runtime spool materialize" in budget.last_rejection
    assert budget.retained_bytes == _RETAINED_BYTES


def test_runtime_output_bound_keeps_cumulative_intermediate_write_limit(monkeypatch):
    payloads = _payloads()
    v2, _ = wire.merge_stack_map_payloads(payloads, compact_budget=_budget())
    intermediate_bytes = len(v2) - wire.HEADER_SIZE
    initialize = spool._SpoolWork.__init__
    observed = []

    def limited(work, budget):
        initialize(work, budget)
        # Admit the v2 intermediate exactly; the first v3 write must refuse.
        work.write_limit = intermediate_bytes
        observed.append(work)

    monkeypatch.setattr(spool._SpoolWork, "__init__", limited)
    with pytest.raises(wire.PreciseStackMapError, match="spool write work budget exceeded"):
        _merge(payloads)
    assert len(observed) == 1
    assert observed[0].written == intermediate_bytes
    assert observed[0].write_limit == intermediate_bytes


def test_runtime_output_bound_keeps_read_limit_before_io():
    work = spool._SpoolWork(_budget())
    work.read_limit = 3
    source = io.BytesIO(b"1234")
    with memoryview(bytearray(4)) as target:
        with pytest.raises(wire.PreciseStackMapError, match="spool read work budget exceeded"):
            work.read_exact(source, target)
    assert source.tell() == 0 and work.read == 0
