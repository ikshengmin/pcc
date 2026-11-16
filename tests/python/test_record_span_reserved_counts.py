"""Concat nodes carry child IDs; span headers alone carry sequence lengths."""
import random
from pcc.backend.self_backend_value_arena import CompilerIntArena, CompilerRecordSpanArena


def test_concat_reserved_slots_do_not_retain_duplicate_lengths():
    arena = CompilerRecordSpanArena()
    try:
        left = arena.new_span()
        right = arena.new_span()
        for value in range(300):
            arena.append(left, value)
        arena.append(right, 400)
        arena.extend(left, right)
        for index in range(len(arena.nodes) // 3):
            node = arena.nodes.get3_unchecked(index)
            assert node.third == (1 if node.first == -1 else 0)
        assert arena.length(left) == 301
        assert arena.diagnostic_values(left) == list(range(300)) + [400]
    finally:
        arena.close()


def test_existing_concat_payload_is_not_traversal_authority():
    arena = CompilerRecordSpanArena()
    try:
        span = arena.new_span()
        for value in range(400):
            arena.append(span, value)
        for index in range(len(arena.nodes) // 3):
            node = arena.nodes.get3_unchecked(index)
            if node.first >= 0:
                arena.nodes.set_unchecked(index * 3 + 2, -(index + 1))
        assert arena.length(span) == 400
        assert arena.diagnostic_values(span) == list(range(400))
    finally:
        arena.close()


def test_reserved_concat_counts_preserve_randomized_snapshots_and_cursors():
    rng = random.Random(1407)
    arena = CompilerRecordSpanArena()
    spans = [arena.new_span() for _ in range(6)]
    expected = [[] for _ in spans]
    cursors = []
    try:
        for step in range(200):
            target = rng.randrange(len(spans))
            source = rng.randrange(len(spans))
            if step % 3 and len(expected[target]) + len(expected[source]) < 2000:
                arena.extend(spans[target], spans[source])
                expected[target] += expected[source][:]
            else:
                arena.append(spans[target], step)
                expected[target].append(step)
            if step % 19 == 0:
                cursor = CompilerIntArena()
                arena.start_cursor(spans[target], cursor)
                cursors.append((cursor, expected[target][:]))
        for span, values in zip(spans, expected):
            assert arena.length(span) == len(values)
            assert arena.diagnostic_values(span) == values
        for cursor, values in cursors:
            got = []
            value = arena.next_record(cursor)
            while value >= 0:
                got.append(value)
                value = arena.next_record(cursor)
            assert got == values
    finally:
        for cursor, _ in cursors:
            cursor.close()
        arena.close()
