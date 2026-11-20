"""The host arena can copy a complete run without a transient second list."""
import struct
import tracemalloc

import pytest

from pcc.backend.self_backend_value_arena import CompilerIntArena


def test_full_host_run_only_allocates_its_destination():
    source, target = CompilerIntArena(), CompilerIntArena()
    count = 32768
    source.extend_list([17] * count)
    tracing = tracemalloc.is_tracing()
    try:
        if not tracing:
            tracemalloc.start()
        tracemalloc.reset_peak()
        target.extend_from(source, 0, count)
        retained, peak = tracemalloc.get_traced_memory()
        # A whole temporary slice retains one extra reference per scalar
        # until list.extend returns; allow half such a slice for test noise.
        assert peak - retained < count * struct.calcsize("P") // 2
        assert len(target) == count and target._values == source._values
        assert target._values is not source._values
    finally:
        if not tracing:
            tracemalloc.stop()
        target.close()
        source.close()


@pytest.mark.parametrize("same_owner", [False, True])
def test_full_host_run_extends_aliased_storage_once(same_owner):
    source = CompilerIntArena()
    source.extend_list([1 << 90, -3, 42])
    target = source if same_owner else CompilerIntArena()
    storage = source._values
    if not same_owner:
        target._values, target._length = storage, len(storage)
    try:
        target.extend_from(source, 0, len(source))
        assert target._values is storage
        assert storage == [1 << 90, -3, 42] * 2
        assert len(target) == 6
    finally:
        target.close()
        if not same_owner:
            source.close()


def test_full_host_run_keeps_custom_source_slice():
    class CustomValues(list):
        def __getitem__(self, key):
            assert key == slice(0, 2)
            return [19, 23]

    source, target = CompilerIntArena(), CompilerIntArena()
    try:
        source._values, source._length = CustomValues([1, 2]), 2
        target.extend_from(source, 0, 2)
        assert target._values == [19, 23]
    finally:
        target.close()
        source.close()


def test_full_host_run_gives_custom_destination_the_original_snapshot():
    source, target = CompilerIntArena(), CompilerIntArena()

    class CustomTarget(list):
        def extend(self, values):
            source._values[0] = -1
            super().extend(values)

    try:
        source.extend_list([3, 5])
        target._values = CustomTarget()
        target.extend_from(source, 0, 2)
        assert target._values == [3, 5] and source._values == [-1, 5]
    finally:
        target.close()
        source.close()


def test_full_host_run_keeps_custom_count_addition_and_partial_ranges():
    calls = []

    class Count(int):
        def __radd__(self, other):
            calls.append(other)
            return other + int(self)

    source, target = CompilerIntArena(), CompilerIntArena()
    try:
        source.extend_list([11, 12, 13])
        target.extend_from(source, 0, Count(3))
        assert calls == [0, 0, 0]  # bounds, slice end, destination length
        target.extend_from(source, 1, 1)
        target.extend_from(source, 0, 0)
        assert target._values == [11, 12, 13, 12]
    finally:
        target.close()
        source.close()
