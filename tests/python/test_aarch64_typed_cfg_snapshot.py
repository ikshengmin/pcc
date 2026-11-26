"""Captured CFG output must not alias mutable source nodes or roots.

Public proof and owner callbacks may mutate the source after record capture.
The returned root must preserve the algorithm's captured output sequence.
"""
import pytest

from pcc.backend import self_backend_aarch64_typed_cfg as cfg
from pcc.backend.arm64_encode import EncodeError
from pcc.backend.self_backend_aarch64_fragments import AArch64EmissionFragments
from pcc.backend.self_backend_value_arena import CompilerIntArena, CompilerRecordSpanArena


@pytest.fixture
def verify_each(monkeypatch):
    """Arenas changed after append are caught on read only under the
    debug switch; the default validates each instruction once."""
    monkeypatch.setenv("PCC_DEBUG_AARCH64_VERIFY_EACH", "1")


PASSES = ("thread", "fold", "labels")


def invoke(kind, owner, span, extra=()):
    if kind == "thread":
        return cfg.thread_trampoline_branches(owner, owner.semantics, span, extra)
    if kind == "fold":
        return cfg.fold_cond_branch_to_fallthrough(owner, owner.semantics, span, extra)
    return cfg.drop_unreferenced_empty_local_labels(owner, owner.semantics, span, extra)


def ids(owner, span):
    cursor = CompilerIntArena()
    try:
        owner.spans.start_cursor(span, cursor)
        result = []
        while True:
            rid = owner.spans.next_record(cursor)
            if rid < 0:
                return result
            result.append(rid)
    finally:
        cursor.close()


def fixture(owner_class=AArch64EmissionFragments, span_class=None):
    owner = owner_class(capture_semantics=True)
    if span_class is not None:
        owner.spans.close()
        owner.spans = span_class()
    span = owner.new_fragment()
    owner.append_label(span, "L_a")
    owner.append_branch(span, "b", "L_target")
    owner.append_label(span, "L_target")
    owner.append_nop(span)
    return owner, span


@pytest.mark.parametrize("kind", PASSES)
def test_unchanged_output_has_independent_rebuilt_root(kind):
    owner, span = fixture()
    try:
        expected = ids(owner, span)
        nodes = len(owner.spans.nodes)
        result = invoke(kind, owner, span)
        assert result != span
        assert ids(owner, result) == expected
        assert len(owner.spans.nodes) > nodes  # public mutable callbacks require a rebuild
        assert owner.spans._root(result) != owner.spans._root(span)
    finally:
        owner.close()


@pytest.mark.parametrize("kind", PASSES)
def test_snapshot_roots_and_preexisting_cursor_are_independent(kind):
    owner, span = fixture()
    cursor = CompilerIntArena()
    try:
        expected = ids(owner, span)
        owner.spans.start_cursor(span, cursor)
        result = invoke(kind, owner, span)
        owner.spans.append(span, expected[0])
        assert ids(owner, result) == expected
        owner.extend_fragment(result, result)
        assert ids(owner, result) == expected * 2
        assert ids(owner, span) == expected + expected[:1]
        owner.spans.append(result, expected[-1])
        assert ids(owner, span) == expected + expected[:1]
        observed = []
        while True:
            value = owner.spans.next_record(cursor)
            if value < 0:
                break
            observed.append(value)
        assert observed == expected
    finally:
        cursor.close()
        owner.close()


@pytest.mark.parametrize("kind", PASSES)
def test_corrupt_stored_count_uses_rebuilt_output(kind):
    owner, span = fixture()
    try:
        expected = ids(owner, span)
        root = owner.spans._root(span)
        owner.spans.spans.set2_unchecked(span.first * 2, root.first, root.second + 7)
        result = invoke(kind, owner, span)
        assert ids(owner, result) == expected
        assert owner.spans.length(result) == len(expected)
    finally:
        owner.close()


class Once:
    """External proof iterable, with a legitimate caller-visible side effect."""
    def __init__(self, action):
        self.action = action
        self.called = False

    def __iter__(self):
        if not self.called:
            self.called = True
            self.action()
        return iter(())


@pytest.mark.parametrize("kind", PASSES)
@pytest.mark.parametrize("mutation", ("append", "same_length", "reset", "replace_owner"))
def test_external_proof_mutation_keeps_captured_record_sequence(kind, mutation):
    owner, span = fixture()
    retired = []
    try:
        expected = ids(owner, span)
        original_root = owner.spans._root(span)
        def mutate():
            if mutation == "append":
                owner.spans.append(span, expected[-1])
            elif mutation == "same_length":
                other = owner.new_fragment()
                for rid in reversed(expected):
                    owner.spans.append(other, rid)
                replacement = owner.spans._root(other)
                owner.spans.spans.set2_unchecked(span.first * 2, replacement.first, replacement.second)
            elif mutation == "reset":
                owner.spans.reset()
            else:
                previous = owner.spans
                replacement = CompilerRecordSpanArena()
                new_span = replacement.new_span()
                for rid in reversed(expected):
                    replacement.append(new_span, rid)
                assert new_span == span
                assert replacement.generation == previous.generation
                assert replacement._root(new_span) == original_root
                retired.append(previous)
                owner.spans = replacement
        proof = Once(mutate)
        result = invoke(kind, owner, span, proof)
        assert proof.called
        assert ids(owner, result) == expected
        assert owner.spans.length(result) == len(expected)
    finally:
        owner.close()
        for arena in retired:
            arena.close()


@pytest.mark.parametrize("kind", PASSES)
@pytest.mark.usefixtures("verify_each")
def test_external_proof_late_record_corruption_is_not_hidden(kind):
    owner, span = fixture()
    try:
        expected = ids(owner, span)
        proof = Once(lambda: owner.records.set(expected[-1] * 4 + 1, -999))
        with pytest.raises(EncodeError, match="event family"):
            invoke(kind, owner, span, proof)
        assert proof.called
    finally:
        owner.close()


class FragmentSubclass(AArch64EmissionFragments):
    pass


class SpanSubclass(CompilerRecordSpanArena):
    pass


class FragmentProxy:
    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, name):
        if name == "extend_fragment":
            raise AttributeError(name)
        return getattr(self.owner, name)


@pytest.mark.parametrize("kind", PASSES)
@pytest.mark.parametrize("variant", ("fragment_subclass", "span_subclass", "proxy"))
def test_generic_owners_retain_rebuild_fallback(kind, variant):
    owner, span = fixture(
        FragmentSubclass if variant == "fragment_subclass" else AArch64EmissionFragments,
        SpanSubclass if variant == "span_subclass" else None,
    )
    try:
        expected = ids(owner, span)
        nodes = len(owner.spans.nodes)
        target = FragmentProxy(owner) if variant == "proxy" else owner
        result = invoke(kind, target, span)
        assert ids(owner, result) == expected
        assert result != span
        assert len(owner.spans.nodes) > nodes
    finally:
        owner.close()


def test_real_label_rewrite_is_not_treated_as_unchanged():
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        span = owner.new_fragment()
        owner.append_label(span, "L_unused")
        owner.append_label(span, "L_next")
        owner.append_nop(span)
        expected = ids(owner, span)
        result = invoke("labels", owner, span)
        assert ids(owner, result) == expected[1:]
        assert ids(owner, span) == expected
    finally:
        owner.close()


def test_real_trampoline_retarget_is_not_treated_as_unchanged():
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        span = owner.new_fragment()
        owner.append_branch(span, "b", "L_a")
        owner.append_label(span, "L_a")
        owner.append_branch(span, "b", "L_target")
        owner.append_label(span, "L_target")
        owner.append_nop(span)
        before = ids(owner, span)
        result = invoke("thread", owner, span)
        assert ids(owner, result) != before
        assert ids(owner, span) == before
        from pcc.backend.self_backend_aarch64_typed_pass_context import TypedPassContext
        ctx = TypedPassContext(owner, owner.semantics)
        assert ctx.operands(ids(owner, result)[0])[-1][1] == owner.symbol_ids["L_target"]
    finally:
        owner.close()


def test_real_conditional_fold_is_not_treated_as_unchanged():
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        span = owner.new_fragment()
        owner.append_label(span, "L_source")
        owner.append_branch(span, "b.eq", "L_success")
        owner.append_branch(span, "b", "L_error")
        owner.append_label(span, "L_success")
        owner.append_nop(span)
        owner.append_label(span, "L_error")
        owner.append_nop(span)
        before = ids(owner, span)
        edges = [(owner.symbol_ids[name] for name in ("L_source", "L_error", "L_success"))]
        edges = [tuple(edge) for edge in edges]
        result = invoke("fold", owner, span, edges)
        after = ids(owner, result)
        assert len(after) == len(before) - 1
        assert ids(owner, span) == before
        from pcc.backend.self_backend_aarch64_typed_pass_context import TypedPassContext
        ctx = TypedPassContext(owner, owner.semantics)
        assert ctx.opcode(after[1]) == "b.ne"
        assert ctx.operands(after[1])[-1][1] == owner.symbol_ids["L_error"]
    finally:
        owner.close()


@pytest.mark.parametrize("kind", PASSES)
def test_proof_in_place_leaf_mutation_keeps_captured_output(kind):
    owner, span = fixture()
    try:
        expected = ids(owner, span)
        original_root = owner.spans._root(span)
        proof = Once(lambda: owner.spans.nodes.set(1, expected[-1]))
        result = invoke(kind, owner, span, proof)
        assert proof.called
        assert owner.spans._root(span) == original_root
        assert ids(owner, span) != expected
        assert ids(owner, result) == expected
    finally:
        owner.close()


@pytest.mark.parametrize("kind", PASSES)
def test_output_root_creation_callback_keeps_captured_output(kind):
    owner, span = fixture()
    original_new = owner.new_fragment
    calls = 0
    try:
        expected = ids(owner, span)

        def new_fragment():
            nonlocal calls
            calls += 1
            result = original_new()
            if calls == 2:
                owner.spans.append(span, expected[0])
            return result

        owner.new_fragment = new_fragment
        result = invoke(kind, owner, span)
        assert calls == 2
        assert ids(owner, span) == expected + expected[:1]
        assert ids(owner, result) == expected
    finally:
        owner.new_fragment = original_new
        owner.close()
