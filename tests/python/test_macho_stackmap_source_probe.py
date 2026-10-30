"""Host source lookup: first-slot loop elision with unchanged work and reads."""

import builtins

import pytest

from pcc.backend import macho_host_stackmap_index as packed
from pcc.backend import precise_stackmap as wire
from tests.python.test_macho_stackmap_consumption import _payload
from tests.python.test_macho_stackmap_index import _tables
from tests.python.test_macho_stackmap_record_codec import _trace


def _previous_find(self, key):
    """Exact preceding lookup body, including every work check and row read."""
    self.work.begin()
    if self.buffer is None:
        return -1, -1
    capacity = len(self.buffer) // packed._SOURCE.size
    mask = capacity - 1
    slot = packed._mix64(key) & mask
    for depth in range(1, capacity + 1):
        self.work.probe(depth)
        existing, destination = packed._SOURCE.unpack_from(self.buffer, slot * packed._SOURCE.size)
        if existing == 0:
            return -1, slot
        if existing == key:
            return destination, slot
        slot = (slot + 1) & mask
    raise wire.PreciseStackMapError("stack-map source index has no empty slot")


def _state(source):
    return (source.used, None if source.buffer is None else bytes(source.buffer),
            tuple(getattr(source.work, field) for field in packed._IndexWork.__slots__))


def _outcome(find, source, key):
    try:
        result = find(source, key)
    except Exception as error:
        result = type(error), str(error)
    return result, _state(source)


@pytest.mark.parametrize("key", (1, 3, 0xFFFFFFFFFFFF))
@pytest.mark.parametrize("present", (False, True))
def test_first_slot_reads_current_mapping_without_a_collision_iterator(monkeypatch, key, present):
    root, source, work, _checks = _tables()
    source.buffer = bytearray(8 * packed._SOURCE.size)
    slot = packed._mix64(key) & 7
    if present:
        packed._SOURCE.pack_into(source.buffer, slot * packed._SOURCE.size, key, 0xFFFFFFFF)
    ranges = []

    def observe(*args):
        ranges.append(args)
        return builtins.range(*args)

    monkeypatch.setattr(packed, "range", observe, raising=False)
    assert source.find(key) == ((0xFFFFFFFF if present else -1), slot)
    assert ranges == []
    assert (work.operations, work.steps, work.max_probe) == (1, 1, 1)
    # Mutation between calls must be observed, not a memoized hit or miss.
    packed._SOURCE.pack_into(source.buffer, slot * packed._SOURCE.size, key, 17)
    assert source.find(key) == (17, slot)
    assert ranges == []
    source.close()
    assert source.find(key) == (-1, -1)
    assert (work.operations, work.steps, work.max_probe) == (3, 2, 1)
    root.close()


@pytest.mark.parametrize("size", (0, 1, 15, 16, 32, 128))
@pytest.mark.parametrize("filled", (False, True))
@pytest.mark.parametrize("key", (1, 9, 0xFFFFFFFFFFFF))
def test_every_slot_and_empty_capacity_matches_preimage(monkeypatch, size, filled, key):
    # A forced terminal bucket exercises wraparound and full-table refusal.
    # Short/empty buffers are defensive private-state controls, not inputs.
    monkeypatch.setattr(packed, "_mix64", lambda value: 7)
    outcomes = []
    for find in (_previous_find, packed._SourceIndex.find):
        root, source, _work, _checks = _tables()
        source.buffer = bytearray(size)
        capacity = size // packed._SOURCE.size
        if filled:
            for slot in range(capacity):
                packed._SOURCE.pack_into(source.buffer, slot * packed._SOURCE.size,
                                         slot + 1, 0xFFFFFFFF - slot)
            source.used = capacity
        outcomes.append(_outcome(find, source, key))
        source.close()
        root.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("failure", ("none", "operation", "probe"))
@pytest.mark.parametrize("available", range(10))
def test_work_refusal_keeps_exact_read_probe_order(monkeypatch, failure, available):
    original_source = packed._SOURCE
    original_probe = packed._IndexWork.probe
    original_begin = packed._IndexWork.begin
    outcomes = []
    events = []

    class ObservedSource:
        size = original_source.size

        @staticmethod
        def unpack_from(buffer, offset):
            events.append(("read", offset))
            return original_source.unpack_from(buffer, offset)

    def begin(work, count=1):
        events.append(("begin", count, work.operations, work.steps))
        return original_begin(work, count)

    def probe(work, depth):
        events.append(("probe", depth, work.operations, work.steps))
        return original_probe(work, depth)

    def mix(key):
        events.append(("hash", key))
        return 7

    monkeypatch.setattr(packed, "_SOURCE", ObservedSource)
    monkeypatch.setattr(packed, "_mix64", mix)
    monkeypatch.setattr(packed._IndexWork, "begin", begin)
    monkeypatch.setattr(packed._IndexWork, "probe", probe)
    for find in (_previous_find, packed._SourceIndex.find):
        root, source, work, _checks = _tables()
        source.buffer = bytearray(8 * original_source.size)
        for slot in range(8):
            original_source.pack_into(source.buffer, slot * original_source.size, slot + 1, slot)
        source.used = 8
        if failure == "operation":
            work.operation_limit = work.operations + available
        if failure == "probe":
            work.steps = 16 * (work.operations + 2) - available
        events.clear()
        outcomes.append((_outcome(find, source, 65537), list(events)))
        source.close()
        root.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("collisions", (False, True))
def test_real_insert_growth_and_repeated_lookup_match_preimage(monkeypatch, collisions):
    if collisions:
        monkeypatch.setattr(packed, "_mix64", lambda value: 7)
    outcomes = []
    for find in (_previous_find, packed._SourceIndex.find):
        root, source, _work, checks = _tables()
        rows = []
        keys = [(number << 16) | count for number in range(40)
                for count in (1, 3, 65535)]
        keys.extend(((1 << 31) << 16 | 3, 0xFFFFFFFFFFFF))
        for number, key in enumerate(keys):
            result, state = _outcome(find, source, key)
            rows.append((result, state))
            if result[0] is wire.PreciseStackMapError:
                break
            destination = 0xFFFFFFFF - number
            source.append(key, destination, result[1])
            assert find(source, key)[0] == destination
            assert find(source, keys[0])[0] == 0xFFFFFFFF
            rows.append(_state(source))
        outcomes.append((rows, checks, _state(source)))
        source.close()
        root.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("depth", (1, 2, 3))
def test_probe_callback_replacement_uses_current_buffer(monkeypatch, depth):
    original_probe = packed._IndexWork.probe
    monkeypatch.setattr(packed, "_mix64", lambda value: 7)
    outcomes = []
    for find in (_previous_find, packed._SourceIndex.find):
        root, source, work, _checks = _tables()
        source.buffer = bytearray(8 * packed._SOURCE.size)
        for offset, key in ((7, 1), (0, 2), (1, 3)):
            packed._SOURCE.pack_into(source.buffer, offset * packed._SOURCE.size, key, key)
        replacement = source.buffer[:]
        slot = (7 + depth - 1) & 7
        packed._SOURCE.pack_into(replacement, slot * packed._SOURCE.size, 65537, 42)

        def probe(current_work, current_depth):
            original_probe(current_work, current_depth)
            if current_depth == depth:
                source.buffer = replacement

        with monkeypatch.context() as callback:
            callback.setattr(packed._IndexWork, "probe", probe)
            assert find(source, 65537) == (42, slot)
        outcomes.append(_state(source))
        assert work.steps == depth
        source.close()
        root.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("runtime", (False, True))
@pytest.mark.parametrize("single", (False, True))
@pytest.mark.parametrize("arch", (wire.ARCH_AARCH64, wire.ARCH_X86_64))
def test_merge_matches_preimage_bytes_offsets_admission_and_index_work(monkeypatch, runtime, single, arch):
    payloads = (_payload((9,), arch=arch, records=4097),
                _payload((2,), arch=arch, records=4))
    works = []
    original = packed._IndexWork.__init__

    def remember(self, budget):
        original(self, budget)
        works.append(self)

    monkeypatch.setattr(packed._IndexWork, "__init__", remember)
    actual = _trace(payloads, runtime, single)
    with monkeypatch.context() as previous:
        previous.setattr(packed._SourceIndex, "find", _previous_find)
        expected = _trace(payloads, runtime, single)
    assert actual == expected
    assert len(works) == 2
    assert tuple(getattr(works[0], field) for field in packed._IndexWork.__slots__) == tuple(
        getattr(works[1], field) for field in packed._IndexWork.__slots__)
    eager = wire.merge_stack_map_payloads(payloads)
    assert actual[0] == (wire.transcode_runtime_table(*eager) if runtime else eager)
