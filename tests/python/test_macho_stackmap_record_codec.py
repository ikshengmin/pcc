"""Host-only fixed-field replay: exact bytes, work admission and error order."""

import struct

import pytest

from pcc.backend import macho_host_stackmap as spool
from pcc.backend import macho_host_stackmap_index as packed
from pcc.backend import precise_stackmap as wire
from tests.python.test_macho_stackmap_consumption import _payload
from tests.python.test_macho_windowed_prepare import _budget


class _ScalarRange:
    """The preceding production field reads, retained as a small oracle."""

    @staticmethod
    def unpack_from(blob, cursor):
        count = blob[cursor + 20] | (blob[cursor + 21] << 8)
        index = (blob[cursor + 28] | (blob[cursor + 29] << 8)
                 | (blob[cursor + 30] << 16) | (blob[cursor + 31] << 24))
        return count, index


class _ScalarIndex:
    @staticmethod
    def pack_into(blob, cursor, destination):
        blob[cursor:cursor + 4] = destination.to_bytes(4, "little")


@pytest.mark.parametrize("offset", (0, 1, 7, spool._BLOCK_BYTES - wire.RECORD_SIZE))
@pytest.mark.parametrize("count,index", (
    (0, 0), (1, 255), (256, 65536), (0xFFFF, 0xFFFFFFFF), (0x102, 0x01020304),
))
def test_fixed_codecs_preserve_unsigned_fields_and_every_other_byte(offset, count, index):
    # Nonzero skipped fields and unaligned starts must not change decoding.
    blob = bytearray(b"z" * (offset + wire.RECORD_SIZE + 7))
    struct.pack_into("<H", blob, offset + 20, count)
    struct.pack_into("<I", blob, offset + 28, index)
    expected = blob[:]
    assert spool._RECORD_ROOT_RANGE.size == wire.RECORD_SIZE
    assert spool._RECORD_ROOT_RANGE.unpack_from(blob, offset) == (count, index)
    assert spool._RECORD_ROOT_RANGE.unpack_from(blob, offset) == _ScalarRange.unpack_from(blob, offset)
    destination = 0xFFFFFFFF - index
    _ScalarIndex.pack_into(expected, offset + 28, destination)
    length = len(blob)
    spool._LOCATION_INDEX.pack_into(blob, offset + 28, destination)
    assert blob == expected and len(blob) == length


def _trace(payloads, runtime, single):
    budget = _budget(100_000_000)
    budget.retained_bytes = 137
    checks = []
    check = budget.check_peak

    def observe(raw, *, records=0, entries=0, label="stack-map"):
        checks.append((raw, records, entries, label, budget.retained_bytes))
        return check(raw, records=records, entries=entries, label=label)

    budget.check_peak = observe
    owned = list(payloads)
    result = spool.merge_owned_stack_map_payloads(
        owned, compact_budget=budget, single_buffer=single, runtime_table=runtime,
    )
    return result, checks, budget.record_visits, budget.retained_bytes, owned


@pytest.mark.parametrize("runtime", (False, True))
@pytest.mark.parametrize("single", (False, True))
@pytest.mark.parametrize("arch", (wire.ARCH_AARCH64, wire.ARCH_X86_64))
def test_replay_matches_scalar_bytes_offsets_admission_and_actual_index_work(monkeypatch, runtime, single, arch):
    # Cross two record windows, reuse ranges, include empties and reorder IDs.
    payloads = (_payload((9,), arch=arch, records=4097),
                _payload((2,), arch=arch, records=4))
    work = []
    original = packed._IndexWork.__init__

    def remember(self, budget):
        original(self, budget)
        work.append(self)

    monkeypatch.setattr(packed._IndexWork, "__init__", remember)
    actual = _trace(payloads, runtime, single)
    with monkeypatch.context() as legacy:
        legacy.setattr(spool, "_RECORD_ROOT_RANGE", _ScalarRange)
        legacy.setattr(spool, "_LOCATION_INDEX", _ScalarIndex)
        expected = _trace(payloads, runtime, single)
    assert actual == expected
    assert actual[2:] == (4101, 137, [None, None])
    assert len(work) == 2
    assert tuple(getattr(work[0], field) for field in packed._IndexWork.__slots__) == tuple(
        getattr(work[1], field) for field in packed._IndexWork.__slots__)
    eager = wire.merge_stack_map_payloads(payloads, compact_budget=_budget(100_000_000))
    assert actual[0] == (wire.transcode_runtime_table(*eager) if runtime else eager)


def test_production_replay_uses_one_codec_read_and_in_place_write_per_record(monkeypatch):
    read, write = spool._RECORD_ROOT_RANGE, spool._LOCATION_INDEX
    reads, writes = [], []

    class Read:
        @staticmethod
        def unpack_from(blob, cursor):
            assert type(blob) is bytearray
            reads.append((len(blob), cursor))
            return read.unpack_from(blob, cursor)

    class Write:
        @staticmethod
        def pack_into(blob, cursor, destination):
            assert type(blob) is bytearray
            writes.append((len(blob), cursor))
            return write.pack_into(blob, cursor, destination)

    monkeypatch.setattr(spool, "_RECORD_ROOT_RANGE", Read)
    monkeypatch.setattr(spool, "_LOCATION_INDEX", Write)
    _trace([_payload((1,), records=4097)], False, True)
    assert len(reads) == len(writes) == 4097
    assert writes == [(size, cursor + 28) for size, cursor in reads]
    assert [size for size, cursor in reads if cursor == 0] == [65536, 65536, 32]


@pytest.mark.parametrize("remaining", range(wire.RECORD_SIZE))
def test_truncated_record_fails_before_codec_or_record_work(monkeypatch, remaining):
    class Forbidden:
        @staticmethod
        def unpack_from(*args):
            pytest.fail("record codec ran before complete structural validation")

    monkeypatch.setattr(spool, "_RECORD_ROOT_RANGE", Forbidden)
    original = _payload((1,), records=1, empty_roots=True)
    bad = original[:wire.HEADER_SIZE + wire.FUNCTION_SIZE + remaining]
    owned, budget = [bad], _budget()
    with pytest.raises(wire.PreciseStackMapError, match="truncated safepoint record"):
        spool.merge_owned_stack_map_payloads(owned, compact_budget=budget, single_buffer=True)
    assert owned == [bad] and budget.record_visits == budget.retained_bytes == 0


@pytest.mark.parametrize("count,index", ((1, 0x80000000), (0xFFFF, 0), (0, 2)))
def test_invalid_unsigned_range_keeps_original_error_and_input_owner(count, index):
    bad = bytearray(_payload((1,), records=2))
    start = wire.HEADER_SIZE + wire.FUNCTION_SIZE
    struct.pack_into("<H", bad, start + 20, count)
    struct.pack_into("<I", bad, start + 28, index)
    bad = bytes(bad)
    owned, budget = [bad], _budget()
    with pytest.raises(wire.PreciseStackMapError, match="record names locations outside its table"):
        spool.merge_owned_stack_map_payloads(owned, compact_budget=budget, single_buffer=True)
    assert owned == [bad] and budget.record_visits == 2 and budget.retained_bytes == 0
