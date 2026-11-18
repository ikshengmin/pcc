"""Compiler-private integer spans: sequence semantics without object slots."""
from __future__ import annotations

import pytest
from pathlib import Path
import subprocess
import os

from pcc.backend.self_backend_value_arena import CompilerInt2, CompilerIntArena, CompilerRecordSpanArena


def test_nonnegative_radix_sort_preserves_machine_key_order_on_host():
    keys = [5, (1 << 54) - 1, 0, 5, 1 << 40, 255, 256, 1]
    arena = CompilerIntArena()
    try:
        for key in keys:
            arena.append(key)
        arena.sort_nonnegative_radix()
        assert [arena.get_unchecked(index) for index in range(len(arena))] == sorted(keys)
        arena.append(-1)
        with pytest.raises(ValueError, match="nonnegative"):
            arena.sort_nonnegative_radix()
    finally:
        arena.close()


def test_record_spans_snapshot_append_extend_and_self_extend():
    arena = CompilerRecordSpanArena()
    try:
        left = arena.new_span()
        right = arena.new_span()
        arena.append(left, 1)
        arena.append(right, 2)
        arena.extend(left, right)
        arena.append(right, 3)
        assert arena.diagnostic_values(left) == [1, 2]
        assert arena.diagnostic_values(right) == [2, 3]
        arena.extend(left, left)
        assert arena.diagnostic_values(left) == [1, 2, 1, 2]
        assert arena.length(left) == 4
    finally:
        arena.close()


def test_record_spans_iterate_deep_concatenation_without_recursion():
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        for index in range(2000):
            arena.append(span, index)
        assert arena.diagnostic_values(span) == list(range(2000))
        assert arena.projection_count == 1
    finally:
        arena.close()


def test_record_spans_reject_stale_handles_and_release_arenas():
    arena = CompilerRecordSpanArena()
    old = arena.new_span()
    arena.append(old, 4)
    arena.reset()
    current = arena.new_span()
    arena.append(current, 5)
    with pytest.raises(RuntimeError, match="stale"):
        arena.length(old)
    assert arena.diagnostic_values(current) == [5]
    arena.close()
    with pytest.raises(RuntimeError, match="closed"):
        arena.new_span()


def test_record_spans_reject_negative_record_ids_before_mutation():
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        with pytest.raises(ValueError, match="nonnegative"):
            arena.append(span, -1)
        assert arena.length(span) == 0
    finally:
        arena.close()


def test_record_span_cursor_snapshots_and_rejects_reset_without_projection():
    arena = CompilerRecordSpanArena()
    cursor = CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append(span, 7)
        arena.start_cursor(span, cursor)
        arena.append(span, 8)
        assert arena.next_record(cursor) == 7
        assert arena.next_record(cursor) == -1
        assert arena.projection_count == 0
        arena.start_cursor(span, cursor)
        arena.reset()
        with pytest.raises(RuntimeError, match="stale"):
            arena.next_record(cursor)
    finally:
        cursor.close()
        arena.close()


def test_record_span_virtual_growth_is_checked_before_overflow():
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        arena.append(span, 1)
        for _ in range(30):
            arena.extend(span, span)
        assert arena.length(span) == 1 << 30
        with pytest.raises(OverflowError, match="length limit"):
            arena.extend(span, span)
        assert arena.length(span) == 1 << 30
    finally:
        arena.close()


@pytest.mark.parametrize("index", [-1, 1000])
def test_record_span_invalid_indexes_do_not_change_published_span(index):
    arena = CompilerRecordSpanArena()
    try:
        valid = arena.new_span()
        arena.append(valid, 4)
        with pytest.raises(IndexError, match="out of range"):
            arena.append(CompilerInt2(index, valid.second), 9)
        assert arena.diagnostic_values(valid) == [4]
    finally:
        arena.close()


@pytest.mark.parametrize("first,second,third", [(0, 0, 2), (-1, -3, 1), (-1, 2, 3)])
def test_record_span_corrupt_nodes_fail_closed(first, second, third):
    arena = CompilerRecordSpanArena()
    cursor = CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append(span, 7)
        arena.nodes.set3_unchecked(0, first, second, third)
        arena.start_cursor(span, cursor)
        with pytest.raises(RuntimeError, match="acyclic|leaf is invalid"):
            arena.next_record(cursor)
    finally:
        cursor.close()
        arena.close()


def test_record_span_partial_construction_closes_first_arena(monkeypatch):
    from pcc.backend import self_backend_value_arena as module
    allocated = []
    def allocate():
        if allocated:
            raise MemoryError("second arena allocation")
        first = CompilerIntArena()
        allocated.append(first)
        return first
    monkeypatch.setattr(module, "CompilerIntArena", allocate)
    with pytest.raises(MemoryError, match="second arena allocation"):
        CompilerRecordSpanArena()
    with pytest.raises(RuntimeError, match="closed"):
        allocated[0].diagnostic_values()


def test_record_span_diagnostic_failure_closes_its_cursor(monkeypatch):
    from pcc.backend import self_backend_value_arena as module
    arena = CompilerRecordSpanArena()
    cursor = CompilerIntArena()
    monkeypatch.setattr(module, "CompilerIntArena", lambda: cursor)
    try:
        with pytest.raises(IndexError):
            arena.diagnostic_values(CompilerInt2(1000, arena.generation))
        with pytest.raises(RuntimeError, match="closed"):
            cursor.diagnostic_values()
    finally:
        arena.close()


def test_record_span_independent_cursors_keep_their_snapshots():
    arena = CompilerRecordSpanArena()
    first, second = CompilerIntArena(), CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append(span, 1)
        arena.start_cursor(span, first)
        arena.append(span, 2)
        arena.start_cursor(span, second)
        assert arena.next_record(first) == arena.next_record(second) == 1
        assert arena.next_record(first) == -1
        assert arena.next_record(second) == 2
        assert arena.next_record(second) == -1
    finally:
        first.close()
        second.close()
        arena.close()


def _cursor_values(arena, span):
    cursor = CompilerIntArena()
    try:
        arena.start_cursor(span, cursor)
        values = []
        value = arena.next_record(cursor)
        while value >= 0:
            values.append(value)
            value = arena.next_record(cursor)
        return values
    finally:
        cursor.close()


def _copied_values(arena, span):
    out = CompilerIntArena()
    try:
        arena.copy_ids(span, out)
        return [out.get_unchecked(index) for index in range(len(out))]
    finally:
        out.close()


def test_int_arena_bulk_extension_minimum_and_self_extension():
    source, target = CompilerIntArena(), CompilerIntArena()
    try:
        for value in (4, -2, 9, 7):
            source.append(value)
        target.append(1)
        target.extend_from(source, 1, 2)
        target.extend_list([5, 6])
        target.extend_from(target, 0, 3)
        assert target.diagnostic_values() == [1, -2, 9, 5, 6, 1, -2, 9]
        assert target.min_from(2, 4) == 1
        assert target.min_from(0, 8) == -2
        for start, count in ((-1, 1), (3, 2), (0, -1)):
            with pytest.raises(IndexError, match="out of range"):
                target.extend_from(source, start, count)
        with pytest.raises(IndexError, match="out of range"):
            target.min_from(0, 0)
        assert len(target) == 8
    finally:
        source.close()
        target.close()


def test_record_span_runs_keep_sequence_semantics_with_one_node_per_run():
    arena = CompilerRecordSpanArena()
    ids = CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append(span, 1)
        for value in (2, 3, 4):
            ids.append(value)
        nodes = len(arena.nodes)
        arena.append_ids(span, ids)
        assert len(arena.nodes) - nodes == 6  # one run triple, one concat triple
        ids.clear()
        ids.append(99)
        assert _cursor_values(arena, span) == [1, 2, 3, 4]
        snapshot = arena.new_span()
        arena.extend(snapshot, span)
        arena.append_id_list(span, [5, 6])
        arena.append(span, 7)
        arena.extend(span, span)
        expected = [1, 2, 3, 4, 5, 6, 7] * 2
        assert _cursor_values(arena, span) == expected
        assert _copied_values(arena, span) == expected
        assert arena.diagnostic_values(span) == expected
        assert arena.length(span) == len(expected)
        assert _cursor_values(arena, snapshot) == [1, 2, 3, 4]
        alone = arena.new_span()
        arena.append_id_list(alone, [8])
        arena.append_id_list(alone, [])
        assert _cursor_values(arena, alone) == _copied_values(arena, alone) == [8]
        assert arena.length(alone) == 1
    finally:
        ids.close()
        arena.close()


def test_record_span_run_cursors_are_independent_snapshots():
    arena = CompilerRecordSpanArena()
    first, second = CompilerIntArena(), CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append_id_list(span, [10, 11, 12])
        arena.start_cursor(span, first)
        assert arena.next_record(first) == 10
        arena.append_id_list(span, [13])
        arena.start_cursor(span, second)
        assert [arena.next_record(second) for _ in range(5)] == [10, 11, 12, 13, -1]
        assert [arena.next_record(first) for _ in range(3)] == [11, 12, -1]
        arena.start_cursor(span, first)
        arena.reset()
        assert len(arena.runs) == 0
        with pytest.raises(RuntimeError, match="stale"):
            arena.next_record(first)
    finally:
        first.close()
        second.close()
        arena.close()


@pytest.mark.parametrize("records", [[-1], [3, -4, 5]])
def test_record_span_runs_reject_negative_ids_before_publication(records):
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        arena.append_id_list(span, [1])
        nodes, runs = len(arena.nodes), len(arena.runs)
        with pytest.raises(ValueError, match="nonnegative"):
            arena.append_id_list(span, records)
        assert (len(arena.nodes), len(arena.runs)) == (nodes, runs)
        assert arena.length(span) == 1
        assert _cursor_values(arena, span) == [1]
    finally:
        arena.close()


def test_record_span_run_length_is_checked_before_copying():
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        arena.append(span, 1)
        for _ in range(29):
            arena.extend(span, span)
            arena.append(span, 1)
        arena.extend(span, span)
        assert arena.length(span) == 0x7FFFFFFE
        runs = len(arena.runs)
        with pytest.raises(OverflowError, match="length limit"):
            arena.append_id_list(span, [2, 3])
        assert len(arena.runs) == runs
    finally:
        arena.close()


@pytest.mark.parametrize("start,count", [(-1, 2), (0, 0), (1, 3)])
def test_record_span_corrupt_runs_fail_closed(start, count):
    arena = CompilerRecordSpanArena()
    cursor = CompilerIntArena()
    try:
        span = arena.new_span()
        arena.append_id_list(span, [7, 8])
        arena.nodes.set3_unchecked(0, -2, start, count)
        arena.start_cursor(span, cursor)
        with pytest.raises(RuntimeError, match="run is invalid"):
            arena.next_record(cursor)
        with pytest.raises(RuntimeError, match="run is invalid"):
            _copied_values(arena, span)
    finally:
        cursor.close()
        arena.close()


def test_record_span_checked_mark_requires_one_whole_checked_run():
    arena = CompilerRecordSpanArena()
    ids = CompilerIntArena()
    try:
        ids.append(3)
        ids.append(4)
        plain, checked, empty = arena.new_span(), arena.new_span(), arena.new_span()
        arena.append_ids(plain, ids)
        arena.append_ids(checked, ids, True)
        assert not arena.is_checked_run(plain)
        assert not arena.is_checked_run(empty)
        assert arena.is_checked_run(checked)
        snapshot = arena.new_span()
        arena.extend(snapshot, checked)
        assert arena.is_checked_run(snapshot)
        assert _cursor_values(arena, checked) == _copied_values(arena, checked) == [3, 4]
        arena.append(checked, 5)
        assert not arena.is_checked_run(checked)
        assert arena.is_checked_run(snapshot)
        root = arena.spans.get2_unchecked(snapshot.first)
        arena.spans.set2_unchecked(snapshot.first * 2, root.first, 7)
        assert not arena.is_checked_run(snapshot)
    finally:
        ids.close()
        arena.close()


def test_record_span_partial_construction_closes_earlier_arenas(monkeypatch):
    from pcc.backend import self_backend_value_arena as module
    allocated = []
    def allocate():
        if len(allocated) == 2:
            raise MemoryError("third arena allocation")
        arena = CompilerIntArena()
        allocated.append(arena)
        return arena
    monkeypatch.setattr(module, "CompilerIntArena", allocate)
    with pytest.raises(MemoryError, match="third arena allocation"):
        CompilerRecordSpanArena()
    for arena in allocated:
        with pytest.raises(RuntimeError, match="closed"):
            arena.diagnostic_values()


def test_record_span_native_self_backend_executes_aggregate_handles(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi

    repo = Path(__file__).resolve().parents[2]
    consumer = tmp_path / "span_canary.py"
    consumer.write_text("""
from pcc.backend.self_backend_value_arena import CompilerInt2, CompilerIntArena, CompilerRecordSpanArena

def run() -> None:
    pool = CompilerRecordSpanArena()
    cursor = CompilerIntArena()
    assert pool.nodes.uses_native_storage
    assert pool.spans.uses_native_storage
    assert cursor.uses_native_storage
    left: CompilerInt2 = pool.new_span()
    right: CompilerInt2 = pool.new_span()
    pool.append(left, 7)
    pool.append(right, 8)
    pool.extend(left, right)
    pool.append(right, 9)
    pool.extend(left, left)
    pool.start_cursor(left, cursor)
    total = 0
    count = 0
    value = pool.next_record(cursor)
    while value >= 0:
        total += value
        count += 1
        value = pool.next_record(cursor)
    print(total)
    print(count)
    print(pool.projection_count)
    assert pool.runs.uses_native_storage
    ids = CompilerIntArena()
    assert ids.uses_native_storage
    ids.append(3)
    ids.append(4)
    ids.append(5)
    ids.extend_from(ids, 0, 2)
    run: CompilerInt2 = pool.new_span()
    pool.append(run, 1)
    pool.append_ids(run, ids)
    pool.append_id_list(run, [6, 7])
    pool.extend(run, run)
    copied = CompilerIntArena()
    pool.copy_ids(run, copied)
    pool.start_cursor(run, cursor)
    replayed = 0
    index = 0
    value = pool.next_record(cursor)
    while value >= 0:
        assert value == copied.get_unchecked(index)
        replayed += value * (index + 1)
        index += 1
        value = pool.next_record(cursor)
    print(index)
    print(len(copied))
    print(replayed)
    print(ids.min_from(0, len(ids)))
    copied.close()
    ids.close()
    sorter = CompilerIntArena()
    for key in (5, (1 << 54) - 1, 0, 5, 1 << 40, 255, 256, 1):
        sorter.append(key)
    assert sorter.uses_native_storage
    sorter.sort_nonnegative_radix()
    for index, expected in enumerate((0, 1, 5, 5, 255, 256, 1 << 40, (1 << 54) - 1)):
        assert sorter.get_unchecked(index) == expected
    print("radix-ok")
    sorter.close()
    cursor.close()
    pool.close()

run()
""".lstrip())
    output = tmp_path / "span_canary"
    compile_python_multi(
        [str(consumer), str(repo / "pcc/backend/self_backend_value_arena.py"),
         str(repo / "pcc/unsafe/__init__.py")],
        str(output), entry_module="pcc.backend.span_canary",
        module_names=["pcc.backend.span_canary", "pcc.backend.self_backend_value_arena", "pcc.unsafe"],
        ir_scaffold_mode="on", backend="self",
        recursive_stdlib=False, target_triple="arm64-apple-darwin23.6.0",
    )
    for backend in range(5):
        result = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                     PCC_GC_REFCOUNT_PROVENANCE_PROBE="2", PATH="/nonexistent"),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == "30\n4\n0\n16\n16\n616\n3\nradix-ok\n"
