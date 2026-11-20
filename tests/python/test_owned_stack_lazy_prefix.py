"""Mode 2 needs a materialized output only after a rewrite or damaged count."""
import pytest

from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from tests.python import test_aarch64_owned_stack as stack
from tests.python import test_aarch64_typed_peepholes as oracle


def fixture(mode, case, prefix=0, repeat=1, suffix=0):
    fragments, semantics, base, _ = oracle._fixture(stack.items(mode, case))
    fragments.semantics = semantics
    original = oracle._ids(fragments, base)
    span = fragments.new_fragment()
    fragments.spans.append_id_list(
        span, [original[-1]] * prefix + original * repeat + [original[-1]] * suffix,
    )
    return fragments, semantics, span


def test_mode2_unchanged_span_never_populates_dead_scratch(monkeypatch):
    f, s, span = fixture(2, "offset", prefix=19, repeat=5, suffix=1)
    before = oracle._ids(f, span)
    lengths = []
    close = typed._Pass.close

    def observe(p):
        lengths.append(len(p.output))
        return close(p)

    monkeypatch.setattr(typed._Pass, "close", observe)
    try:
        out = typed._run_owned_stack(f, s, span, 2)
        assert lengths == [0]
        assert out != span
        f.spans.append(span, before[0])
        assert oracle._ids(f, out) == before
    finally:
        f.close()


@pytest.mark.parametrize("case", ["basic", "byte", "same_x9"])
@pytest.mark.parametrize("prefix", [0, 31])
def test_mode2_first_rewrite_preserves_prefix_repeats_suffix_and_words(case, prefix):
    results = []
    for run in (typed._run, typed._run_owned_stack):
        f, s, span = fixture(2, case, prefix=prefix, repeat=2, suffix=3)
        try:
            before = oracle._ids(f, span)
            out = run(f, s, span, 2)
            ids = oracle._ids(f, out)
            assert ids != before
            assert oracle._ids(f, span) == before
            results.append((ids, [tuple((v.first, v.second, v.third, v.fourth))
                                  for v in (f.records.get4_unchecked(r) for r in ids)],
                            list(s.headers._values), list(s.tokens._values)))
        finally:
            f.close()
    assert results[0] == results[1]


@pytest.mark.parametrize("stored_length", [-1, 0, 99, 0x80000000])
def test_mode2_bad_root_length_rebuilds_from_actual_ids(stored_length):
    f, s, span = fixture(2, "offset", prefix=3, repeat=2)
    before = oracle._ids(f, span)
    root = f.spans.spans.get2_unchecked(span.first)
    f.spans.spans.set2_unchecked(span.first * 2, root.first, stored_length)
    try:
        out = typed._run_owned_stack(f, s, span, 2)
        assert oracle._ids(f, out) == before
        assert f.spans.length(out) == len(before)
        assert f.spans.is_checked_run(out)
        assert f.spans.length(span) == stored_length
    finally:
        f.close()


def test_mode2_flushes_original_prefix_before_first_producer_failure(monkeypatch):
    f, s, span = fixture(2, "basic", prefix=17)
    before = oracle._ids(f, span)
    primary = MemoryError("injected producer failure")
    scratch = []

    def fail(p, *args):
        scratch.append((p.output, p.ids))
        assert p.output._values == before[:17]
        raise primary

    monkeypatch.setattr(typed._Pass, "new_move", fail)
    try:
        with pytest.raises(MemoryError) as caught:
            typed._run_owned_stack(f, s, span, 2)
        assert caught.value is primary
        assert scratch[0][0]._closed and scratch[0][1]._closed
        assert oracle._ids(f, span) == before
    finally:
        f.close()

def test_mode2_replays_a_long_prefix_with_bounded_scratch(monkeypatch):
    from pcc.backend.self_backend_value_arena import CompilerIntArena

    f, s, span = fixture(2, "basic", prefix=4099)
    before = oracle._ids(f, span)
    contexts, copies = [], []
    init, extend = typed._Pass.__init__, CompilerIntArena.extend_from

    def initialize(p, *args):
        init(p, *args)
        contexts.append(p)

    def observe(target, source, start, count):
        if contexts and target is contexts[0].output and source is contexts[0].ids:
            copies.append((start, count))
        return extend(target, source, start, count)

    monkeypatch.setattr(typed._Pass, "__init__", initialize)
    monkeypatch.setattr(CompilerIntArena, "extend_from", observe)
    try:
        out = typed._run_owned_stack(f, s, span, 2)
        cursor = 0
        for start, count in copies:
            assert start == cursor and 0 < count <= 1024
            cursor += count
        assert cursor == 4099
        assert oracle._ids(f, out)[:4099] == before[:4099]
    finally:
        f.close()
