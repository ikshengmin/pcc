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
