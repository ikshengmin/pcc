"""Arena boundary coverage; no test is a native-route qualification by itself."""
import pytest

from pcc.backend.arm64_encode import (
    EncodeError, EMITTED_INSTRUCTION_SCALAR, STRUCTURED_FIXUP_BRANCH26,
    intern_emitted_symbol,
)
from pcc.backend.self_backend_aarch64_fragments import (
    AArch64EmissionFragments, EMISSION_RECORD_LABEL, EMISSION_RECORD_EMPTY,
)
from pcc.backend.self_backend_aarch64_semantics import (
    AArch64InstructionSemantics, FORM_CANONICAL, TOKEN_SYMBOL,
    append_semantic_event, append_semantic_instruction, semantic_opcode_id,
)
from pcc.backend.self_backend_aarch64_typed_pass_context import TypedPassContext
from pcc.backend.self_backend_aarch64_typed_cfg import drop_fallthrough_uncond_branches
from pcc.backend.self_backend_value_arena import CompilerInt4


@pytest.fixture
def verify_each(monkeypatch):
    """Arenas changed after append are caught on read only under the
    debug switch; the default validates each instruction once."""
    monkeypatch.setenv("PCC_DEBUG_AARCH64_VERIFY_EACH", "1")


def test_arena_fallthrough_reuses_existing_pass_and_retains_empty_ids():
    fragments = AArch64EmissionFragments()
    semantics = AArch64InstructionSemantics()
    try:
        span = fragments.new_fragment()
        target = intern_emitted_symbol("L_target", fragments.symbol_ids, fragments.symbol_names)
        append_semantic_instruction(fragments, semantics, span,
            CompilerInt4(0x14000000, EMITTED_INSTRUCTION_SCALAR, STRUCTURED_FIXUP_BRANCH26, target),
            semantic_opcode_id("b"), FORM_CANONICAL, [CompilerInt4(TOKEN_SYMBOL, target, 0, 0)])
        blank = append_semantic_event(fragments, semantics, span, CompilerInt4(0, EMISSION_RECORD_EMPTY, 0, -1))
        label = append_semantic_event(fragments, semantics, span, CompilerInt4(target, EMISSION_RECORD_LABEL, 0, -1))
        result = drop_fallthrough_uncond_branches(fragments, semantics, span)
        ctx = TypedPassContext(fragments, semantics)
        assert ctx.read_span(result) == [blank, label]
    finally:
        semantics.close()
        fragments.close()


def test_arena_rejects_missing_instruction_provenance():
    fragments = AArch64EmissionFragments()
    semantics = AArch64InstructionSemantics()
    try:
        fragments.append_nop(fragments.new_fragment())
        with pytest.raises(EncodeError, match="provenance"):
            TypedPassContext(fragments, semantics)
    finally:
        semantics.close()
        fragments.close()


@pytest.mark.parametrize("event", [CompilerInt4(1, -3, 0, -1), CompilerInt4(0, -99, 0, -1), CompilerInt4(2, -2, 0, -1)])
@pytest.mark.usefixtures("verify_each")
def test_arena_rejects_unknown_or_malformed_events(event):
    fragments = AArch64EmissionFragments()
    semantics = AArch64InstructionSemantics()
    try:
        span = fragments.new_fragment()
        fragments._append_record(span, event)
        semantics.append_unproven()
        with pytest.raises(EncodeError, match="event"):
            ctx = TypedPassContext(fragments, semantics)
            ctx.read_span(span)
    finally:
        semantics.close()
        fragments.close()


def test_replacement_encoder_publishes_matching_branch_and_pair_provenance():
    from pcc.backend.self_backend_aarch64_semantics import (
        TOKEN_REGISTER, TOKEN_ADDRESS, ADDRESS_BASE, REGISTER_SP,
    )
    fragments = AArch64EmissionFragments()
    semantics = AArch64InstructionSemantics()
    try:
        target = intern_emitted_symbol("L_target", fragments.symbol_ids, fragments.symbol_names)
        ctx = TypedPassContext(fragments, semantics)
        branch = ctx.emit("b", [(TOKEN_SYMBOL, target, 0, 0)])
        assert ctx.record(branch).first == 0x14000000
        assert ctx.record(branch).third == STRUCTURED_FIXUP_BRANCH26
        assert ctx.record(branch).fourth == target
        assert ctx.opcode(branch) == "b"
        pair_tokens = [(TOKEN_REGISTER, 9, 0, 0), (TOKEN_REGISTER, 10, 0, 0),
                       (TOKEN_ADDRESS, ADDRESS_BASE, 1, 0), (TOKEN_REGISTER, REGISTER_SP, 0, 0)]
        pair = ctx.emit("stp", pair_tokens)
        assert ctx.record(pair).first == 0xA9002BE9
        assert ctx.record(pair).third == 0
        assert ctx.record(pair).fourth == -1
        assert ctx.opcode(pair) == "stp"
        assert ctx.operands(pair) == pair_tokens
        semantics.require_complete(fragments.records, len(fragments.symbol_names))
    finally:
        semantics.close()
        fragments.close()


@pytest.mark.parametrize("invalid", [-1, 1000])
def test_host_span_snapshot_still_rejects_invalid_record_id(invalid):
    fragments = AArch64EmissionFragments(capture_semantics=True)
    try:
        span = fragments.new_fragment()
        fragments.append_nop(span)
        valid = fragments.new_fragment()
        fragments.spans.append_id_list(valid, [0])
        original = fragments.spans.runs._length - 1
        # Deliberately corrupt the copied run after publication; the host
        # fast path must not treat a valid span tree as a valid ID stream.
        fragments.spans.runs.set_unchecked(original, invalid)
        ctx = TypedPassContext(fragments, fragments.semantics)
        with pytest.raises(EncodeError, match="record ID is invalid"):
            ctx.read_span(valid)
    finally:
        fragments.close()


@pytest.mark.parametrize("checked", [False, True])
def test_exact_single_run_copies_one_snapshot_without_source_alias(monkeypatch, checked):
    from pcc.backend import self_backend_aarch64_typed_pass_context as pc
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    monkeypatch.delenv("PCC_DEBUG_AARCH64_VERIFY_EACH", raising=False)
    fragments = AArch64EmissionFragments(capture_semantics=True)
    source_ids = CompilerIntArena()
    try:
        seed = fragments.new_fragment()
        for _ in range(4):
            fragments.append_nop(seed)
        source = fragments.new_fragment()
        for value in (0, 1, 3, 2):
            source_ids.append(value)
        fragments.spans.append_ids(source, source_ids, checked=checked)
        original_run_values = list(fragments.spans.runs._values)
        ctx = TypedPassContext(fragments, fragments.semantics)
        seen = []
        original = pc._copy_exact_host_span_slice
        def observe(context, span, ids):
            before = ids._values
            result = original(context, span, ids)
            assert result is not before and result is not fragments.spans.runs._values
            assert ids._values is before and ids._values == []
            assert ids._length == 0
            seen.append(result)
            return result
        monkeypatch.setattr(pc, "_copy_exact_host_span_slice", observe)
        result = ctx.read_span(source)
        assert result is seen[0] and result == [0, 1, 3, 2]
        assert result is not fragments.spans.runs._values
        result.append(0)
        assert fragments.spans.runs._values == original_run_values
    finally:
        source_ids.close()
        fragments.close()


@pytest.mark.parametrize("observer", ["copy", "extend", "close"])
def test_custom_copy_lifetime_still_clears_observed_temporary(monkeypatch, observer):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    from pcc.backend import self_backend_aarch64_typed_pass_context as pc
    monkeypatch.delenv("PCC_DEBUG_AARCH64_VERIFY_EACH", raising=False)
    fragments = AArch64EmissionFragments(capture_semantics=True)
    try:
        seed = fragments.new_fragment()
        fragments.append_nop(seed)
        fragments.append_nop(seed)
        source = fragments.new_fragment()
        fragments.spans.append_id_list(source, [0, 1])
        ctx = TypedPassContext(fragments, fragments.semantics)
        observed = []
        if observer == "copy":
            original = fragments.copy_record_ids
            def copy(span, out):
                original(span, out)
                observed.append(out._values)
            monkeypatch.setattr(fragments, "copy_record_ids", copy)
        elif observer == "extend":
            original = CompilerIntArena.extend_from
            def extend(out, source, start, count):
                original(out, source, start, count)
                observed.append(out._values)
            monkeypatch.setattr(CompilerIntArena, "extend_from", extend)
        else:
            original = CompilerIntArena.close
            def close(arena):
                if arena._length == 2:
                    observed.append(arena._values)
                original(arena)
            monkeypatch.setattr(CompilerIntArena, "close", close)
        proof = pc._copy_exact_host_span_slice
        def check(context, span, ids):
            result = proof(context, span, ids)
            assert result is None
            return result
        monkeypatch.setattr(pc, "_copy_exact_host_span_slice", check)
        assert ctx.read_span(source) == [0, 1]
        assert observed and all(values == [] for values in observed)
    finally:
        fragments.close()


def test_custom_id_comparison_and_concat_keep_snapshot_oracle(monkeypatch):
    from pcc.backend import self_backend_aarch64_typed_pass_context as pc
    monkeypatch.delenv("PCC_DEBUG_AARCH64_VERIFY_EACH", raising=False)
    fragments = AArch64EmissionFragments(capture_semantics=True)
    try:
        seed = fragments.new_fragment()
        fragments.append_nop(seed)
        fragments.append_nop(seed)
        source = fragments.new_fragment()
        fragments.spans.append_id_list(source, [0, 1])
        calls = []
        class ObservedID(int):
            def __lt__(self, other):
                calls.append("lt")
                return super().__lt__(other)
            def __ge__(self, other):
                calls.append("ge")
                return super().__ge__(other)
        fragments.spans.runs._values[-2] = ObservedID(0)
        ctx = TypedPassContext(fragments, fragments.semantics)
        proof = pc._copy_exact_host_span_slice
        def check(context, span, ids):
            result = proof(context, span, ids)
            assert result is None
            assert calls == []
            return result
        monkeypatch.setattr(pc, "_copy_exact_host_span_slice", check)
        assert ctx.read_span(source) == [0, 1]
        assert calls == ["lt", "ge"]
        calls.clear()
        fragments.spans.append(source, 1)  # concat, not a single run
        assert ctx.read_span(source) == [0, 1, 1]
        assert calls == ["lt", "ge"]
    finally:
        fragments.close()
