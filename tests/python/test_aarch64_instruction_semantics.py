"""Packed operand provenance contracts; not optimized-route acceptance."""

import pytest

from pcc.backend import self_backend_aarch64_semantics as semantic
from pcc.backend.arm64_encode import EMITTED_INSTRUCTION_MOVE, EncodeError
from pcc.backend.self_backend_aarch64_fragments import AArch64EmissionFragments
from pcc.backend.self_backend_value_arena import CompilerInt4, CompilerIntArena


def test_register_provenance_preserves_width_alias_and_stack_zero_spelling():
    names = ("x0", "w0", "x29", "fp", "x30", "lr", "sp", "wsp", "xzr", "wzr", "s0", "d0", "q0", "q31")
    ids = [semantic.semantic_register_id(name) for name in names]
    assert len(set(ids)) == len(names)
    assert [semantic.semantic_register_spelling(index) for index in ids] == list(names)
    for bad in ("x31", "w31", "x01", "q32", "x-1", "v0", "X0", "x0, x1"):
        with pytest.raises(EncodeError):
            semantic.semantic_register_id(bad)


def test_immediate_provenance_retains_radix_sign_and_unsigned64():
    for value in (0, -1, 255, -(1 << 63), (1 << 63) - 1, 1 << 63, (1 << 64) - 1, -((1 << 64) - 1)):
        for radix in (10, 16):
            token = semantic.semantic_immediate_token(value, radix, semantic.IMMEDIATE_HASH)
            assert semantic.semantic_immediate_value(token) == value
            assert semantic.semantic_immediate_radix(token) == radix
            assert semantic.semantic_immediate_flags(token) & semantic.IMMEDIATE_HASH
            assert all(-(1 << 63) <= part < 1 << 63 for part in (token.second, token.third, token.fourth))
    negative_zero = semantic.semantic_immediate_token(0, 10, semantic.IMMEDIATE_HASH | semantic.IMMEDIATE_NEGATIVE_ZERO)
    assert semantic.semantic_immediate_flags(negative_zero) & semantic.IMMEDIATE_NEGATIVE_ZERO
    with pytest.raises(EncodeError):
        semantic.semantic_immediate_token(1 << 64)
    with pytest.raises(EncodeError):
        semantic.semantic_immediate_token(1, 10, semantic.IMMEDIATE_NEGATIVE_ZERO)


def test_opcode_and_condition_aliases_are_not_reconstructed_from_words():
    for first, second in (("mov", "orr"), ("cmp", "subs"), ("cset", "csinc"), ("b.cs", "b.hs"), ("b.cc", "b.lo")):
        assert semantic.semantic_opcode_id(first) != semantic.semantic_opcode_id(second)
    assert semantic.CONDITION_NAMES.index("cs") != semantic.CONDITION_NAMES.index("hs")
    with pytest.raises(EncodeError, match="unsupported"):
        semantic.semantic_opcode_id("unknown")


def test_header_and_operand_bounds_fail_closed_for_missing_or_corrupt_tags():
    owner = semantic.AArch64InstructionSemantics()
    try:
        with pytest.raises(EncodeError, match="missing"):
            owner.require_header(0)
        owner.append_unproven()
        with pytest.raises(EncodeError, match="unproven"):
            owner.require_header(0)
        owner.append_token(semantic.TOKEN_REGISTER, semantic.semantic_register_id("x0"))
        owner.append_header(semantic.semantic_opcode_id("ret"), semantic.FORM_CANONICAL, 0, 1)
        assert owner.operand(1, 0).second == semantic.semantic_register_id("x0")
        with pytest.raises(EncodeError, match="index"):
            owner.operand(1, 1)
        owner.tokens.set(1, 31)
        with pytest.raises(EncodeError, match="operand"):
            owner.require_header(1)
    finally:
        owner.close()


def test_address_shape_and_explicit_shift_remain_provenance():
    owner = semantic.AArch64InstructionSemantics()
    try:
        start = owner.token_count()
        owner.append_token(semantic.TOKEN_REGISTER, semantic.semantic_register_id("x0"))
        owner.append_token(semantic.TOKEN_ADDRESS, semantic.ADDRESS_REGISTER, 3)
        owner.append_token(semantic.TOKEN_REGISTER, semantic.semantic_register_id("sp"))
        owner.append_token(semantic.TOKEN_REGISTER, semantic.semantic_register_id("w2"))
        owner.append_token(semantic.TOKEN_SHIFT, semantic.SHIFT_NAMES.index("sxtw"), 0, 1)
        owner.append_header(semantic.semantic_opcode_id("ldr"), semantic.FORM_CANONICAL, start, 5)
        assert owner.operand(0, 4).fourth == 1
        owner.tokens.set(1 * 4 + 2, 4)
        with pytest.raises(EncodeError):
            owner.require_header(0)
        owner.reset()
        owner.append_token(semantic.TOKEN_ADDRESS, semantic.ADDRESS_OFFSET, 2)
        owner.append_token(semantic.TOKEN_REGISTER, semantic.semantic_register_id("w0"))
        owner.append_token(semantic.TOKEN_IMMEDIATE, 0, 10, semantic.IMMEDIATE_HASH)
        with pytest.raises(EncodeError, match="base"):
            owner.append_header(semantic.semantic_opcode_id("ldr"), semantic.FORM_CANONICAL, 0, 3)
        assert len(owner.headers) == 0
    finally:
        owner.close()


def test_symbol_liveness_keeps_original_lexical_register_tokens():
    x9 = semantic.semantic_register_id("x9")
    assert semantic.semantic_symbol_uses_register("x9.part", x9)
    assert not semantic.semantic_symbol_uses_register("L_x9", x9)
    assert not semantic.semantic_symbol_uses_register("w9", x9)
    assert semantic.semantic_symbol_uses_register("w9", semantic.semantic_register_id("w9"))
    assert not semantic.semantic_symbol_uses_register("lr", semantic.semantic_register_id("x30"))
    assert semantic.semantic_symbol_uses_register("lr", semantic.semantic_register_id("lr"))


def test_record_alignment_requires_all_instruction_tags_and_valid_symbols():
    owner = semantic.AArch64InstructionSemantics()
    records = CompilerIntArena()
    try:
        records.append4(0xD65F03C0, 4, 0, -1)
        with pytest.raises(EncodeError, match="record IDs"):
            owner.require_complete(records, 0)
        owner.append_unproven()
        with pytest.raises(EncodeError, match="unproven"):
            owner.require_complete(records, 0)
        owner.reset()
        owner.append_token(semantic.TOKEN_SYMBOL, 0, semantic.SYMBOL_PLAIN, 0)
        owner.append_header(semantic.semantic_opcode_id("b"), semantic.FORM_CANONICAL, 0, 1)
        with pytest.raises(EncodeError, match="symbol"):
            owner.require_complete(records, 0)
    finally:
        records.close()
        owner.close()


def test_replacement_publication_aligns_records_and_rolls_back_before_root_commit(monkeypatch):
    fragments = AArch64EmissionFragments()
    semantics = semantic.AArch64InstructionSemantics()
    try:
        span = fragments.new_fragment()
        encoded = CompilerInt4(0xAA0103E0, EMITTED_INSTRUCTION_MOVE, 0, -1)
        tokens = [
            CompilerInt4(semantic.TOKEN_REGISTER, semantic.semantic_register_id("x0"), 0, 0),
            CompilerInt4(semantic.TOKEN_REGISTER, semantic.semantic_register_id("x1"), 0, 0),
        ]
        assert semantic.append_semantic_instruction(fragments, semantics, span, encoded,
            semantic.semantic_opcode_id("mov"), semantic.FORM_CANONICAL, tokens) == 0
        semantics.require_complete(fragments.records, 0)
        before = (len(fragments.records), len(semantics.headers), len(semantics.tokens))

        def fail_before_commit(destination, record_id):
            assert len(fragments.records) == before[0] + 4
            raise MemoryError("before root commit")

        with monkeypatch.context() as patch:
            patch.setattr(fragments.spans, "append", fail_before_commit)
            with pytest.raises(MemoryError, match="before root commit"):
                semantic.append_semantic_instruction(fragments, semantics, span, encoded,
                    semantic.semantic_opcode_id("mov"), semantic.FORM_CANONICAL, tokens)
        assert (len(fragments.records), len(semantics.headers), len(semantics.tokens)) == before
        fragments.start_cursor(span)
        assert fragments.next_record_id() == 0
        assert fragments.next_record_id() == -1
        semantics.require_complete(fragments.records, 0)
    finally:
        semantics.close()
        fragments.close()


def test_semantic_scope_reset_and_close_do_not_retain_operand_storage():
    owner = semantic.AArch64InstructionSemantics()
    owner.append_token(semantic.TOKEN_FLOAT_ZERO, 0)
    owner.append_header(semantic.semantic_opcode_id("fcmp"), semantic.FORM_CANONICAL, 0, 1)
    owner.reset()
    assert len(owner.headers) == len(owner.tokens) == 0
    with pytest.raises(EncodeError, match="missing"):
        owner.require_header(0)
    owner.close()
    owner.close()
    with pytest.raises(RuntimeError, match="closed"):
        owner.append_unproven()


def test_semantic_encoder_matches_owned_words_without_instruction_text(monkeypatch):
    from pcc.backend import arm64_encode as encoder
    from pcc.backend.self_backend_aarch64_semantic_encode import encode_semantic_instruction

    def reg(name):
        return CompilerInt4(semantic.TOKEN_REGISTER, semantic.semantic_register_id(name), 0, 0)

    def imm(value):
        return semantic.semantic_immediate_token(value)

    def address(mode, children):
        return CompilerInt4(semantic.TOKEN_ADDRESS, mode, children, 0)

    cases = [
        ("mov", [reg("x0"), reg("x1")], "  mov x0, x1"),
        ("sub", [reg("sp"), reg("sp"), imm(48)], "  sub sp, sp, #48"),
        ("str", [reg("x0"), address(semantic.ADDRESS_BASE, 1), reg("sp")], "  str x0, [sp]"),
        ("ldr", [reg("x17"), address(semantic.ADDRESS_OFFSET, 2), reg("sp"), imm(32)], "  ldr x17, [sp, #32]"),
        ("ldp", [reg("x1"), reg("x2"), address(semantic.ADDRESS_OFFSET, 2), reg("x29"), imm(-16)], "  ldp x1, x2, [x29, #-16]"),
        ("stp", [reg("x29"), reg("x30"), address(semantic.ADDRESS_PREINDEX, 2), reg("sp"), imm(-16)], "  stp x29, x30, [sp, #-16]!"),
        ("mrs", [reg("x17"), CompilerInt4(semantic.TOKEN_SYSTEM, 0, 0, 0)], "  mrs x17, nzcv"),
        ("msr", [CompilerInt4(semantic.TOKEN_SYSTEM, 0, 0, 0), reg("x17")], "  msr nzcv, x17"),
        ("blr", [reg("x16")], "  blr x16"),
        ("uxtw", [reg("x11"), reg("w11")], "  uxtw x11, w11"),
        ("csel", [reg("x9"), reg("x1"), reg("x2"), CompilerInt4(semantic.TOKEN_CONDITION, semantic.CONDITION_NAMES.index("hs"), 0, 0)], "  csel x9, x1, x2, hs"),
        ("csinv", [reg("x9"), reg("x1"), reg("x2"), CompilerInt4(semantic.TOKEN_CONDITION, semantic.CONDITION_NAMES.index("lo"), 0, 0)], "  csinv x9, x1, x2, lo"),
        ("fcsel", [reg("d9"), reg("d1"), reg("d2"), CompilerInt4(semantic.TOKEN_CONDITION, semantic.CONDITION_NAMES.index("eq"), 0, 0)], "  fcsel d9, d1, d2, eq"),
        ("fmov", [reg("d9"), CompilerInt4(semantic.TOKEN_FLOAT_IMMEDIATE, 0x70, 0, semantic.IMMEDIATE_HASH)], "  fmov d9, #1.0"),
    ]
    expected = [encoder.assemble_text(source).code for _, _, source in cases]

    def forbidden(*args, **kwargs):
        raise AssertionError("semantic replacement used instruction text")

    monkeypatch.setattr(encoder, "_encode_one", forbidden)
    monkeypatch.setattr(encoder, "assemble_text_lines", forbidden)
    for (opcode, operands, _), code in zip(cases, expected):
        record = encode_semantic_instruction(semantic.semantic_opcode_id(opcode), operands)
        assert record.first.to_bytes(4, "little") == code
        assert record.third == 0 and record.fourth == -1
    branch = encode_semantic_instruction(semantic.semantic_opcode_id("b.ne"), [
        CompilerInt4(semantic.TOKEN_SYMBOL, 37, semantic.SYMBOL_PLAIN, 0),
    ])
    assert (branch.first, branch.third, branch.fourth) == (0x54000001, -19, 37)


def test_complete_events_keep_empty_positions_and_directive_boundaries():
    from pcc.backend.self_backend_aarch64_fragments import (
        EMISSION_RECORD_EMPTY, EMISSION_RECORD_DIRECTIVE, EMISSION_DIRECTIVE_P2ALIGN,
    )
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        span = owner.new_fragment()
        owner.append_branch(span, "b", "L_next")
        semantic.append_semantic_event(owner, owner.semantics, span,
            CompilerInt4(0, EMISSION_RECORD_EMPTY, 0, -1))
        owner.append_label(span, "L_next")
        owner.semantics.require_complete(owner.records, len(owner.symbol_names))
        result = owner.drop_fallthrough_branches(span)
        owner.start_cursor(result)
        assert owner.next_record_id() == 1
        assert owner.next_record_id() == 2
        assert owner.next_record_id() == -1
        blocked = owner.new_fragment()
        owner.append_branch(blocked, "b", "L_blocked")
        semantic.append_semantic_event(owner, owner.semantics, blocked,
            CompilerInt4(EMISSION_DIRECTIVE_P2ALIGN, EMISSION_RECORD_DIRECTIVE, 2, -1))
        owner.append_label(blocked, "L_blocked")
        result = owner.drop_fallthrough_branches(blocked)
        owner.start_cursor(result)
        assert [owner.next_record_id(), owner.next_record_id(), owner.next_record_id()] == [3, 4, 5]
    finally:
        owner.close()


def test_metadata_adapter_cannot_admit_instruction_or_code_section_events():
    from pcc.backend import BackendUnavailable
    from pcc.backend import self_backend_aarch64_darwin_mem as producer
    from pcc.backend.self_backend_aarch64_darwin import _NativeAArch64Emission
    from pcc.backend.self_backend_aarch64_fragments import EMISSION_DIRECTIVE_TEXT_SECTION

    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = _NativeAArch64Emission(optimize=True)
        for lines in (
            ["  ret"], [".inst 0xd65f03c0"], [".word 0xd65f03c0"],
            [".section __TEXT,__text,regular,pure_instructions"],
            [".section\t__TEXT,\t__text,regular,pure_instructions"],
            ["  .quad 1\n  ret"],
        ):
            with pytest.raises(BackendUnavailable):
                sink.extend_metadata(lines)
        assert len(sink.fragments.records) == 0
        sink.extend_metadata([".section __DATA,__data", "_data:", "  .long 1\n  .long 2"])
        # The multiline initializer retains ONE original module event.
        assert len(sink.fragments.records) == 12
        sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
        with pytest.raises(BackendUnavailable, match="code event stream"):
            sink.extend_metadata(["  .quad 1"])
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()


@pytest.mark.parametrize(
    ("entry", "token", "rejected_part"),
    [
        ("  ret", "ret", "  ret"),
        (".inst 0xd65f03c0", ".inst", ".inst 0xd65f03c0"),
        (".word 0xd65f03c0", ".word", ".word 0xd65f03c0"),
        (".unsupported 7", ".unsupported", ".unsupported 7"),
        ("  .quad 1\n\n\tret", "ret", "\tret"),
        ("\n  .long 1\r\n  .unknown\t7", ".unknown", "  .unknown\t7"),
    ],
)
def test_metadata_rejection_reports_the_part_without_capturing_the_event(
    entry, token, rejected_part,
):
    from pcc.backend import BackendUnavailable
    from pcc.backend import self_backend_aarch64_darwin_mem as producer
    from pcc.backend.self_backend_aarch64_darwin import _NativeAArch64Emission

    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = _NativeAArch64Emission(optimize=True)
        section = ".section __DATA,__data"
        with pytest.raises(BackendUnavailable) as caught:
            sink.extend_metadata([section, "_data:", entry])
        assert str(caught.value) == (
            "instruction or unsupported directive in metadata adapter: "
            f"token_prefix={token!r}, "
            f"line_prefix={rejected_part!r}"
        )
        # Earlier entries remain, but no part of the rejected initializer
        # acquires a metadata index or an event, including valid .quad/.long.
        assert sink.metadata_lines == [section]
        assert sink.fragments.symbol_names == ["_data"]
        assert len(sink.fragments.records) == 8
        assert sink.direct_index == 0
        assert len(sink.direct_records) == 0
        assert sink.metadata_prefix_open
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()


@pytest.mark.parametrize("character", ["x", "\x00", "\U0010ffff"])
def test_metadata_rejection_bounds_and_escapes_untrusted_prefixes(character):
    from pcc.backend import BackendUnavailable
    from pcc.backend import self_backend_aarch64_darwin_mem as producer
    from pcc.backend.self_backend_aarch64_darwin import _NativeAArch64Emission

    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = _NativeAArch64Emission(optimize=True)
        # The same exception protocol carries this through the codegen worker.
        # Neither a huge token nor terminal escapes may flood its retained log.
        token = ".unknown\x1b[31m" + character * 400
        part = "\t" + token + " DO_NOT_RETAIN_THE_TAIL"
        with pytest.raises(BackendUnavailable) as caught:
            sink.extend_metadata(["", "  .long 1\n" + part])
        message = str(caught.value)
        assert message == (
            "instruction or unsupported directive in metadata adapter: "
            f"token_prefix={token[:64]!r}, line_prefix={part[:160]!r}"
        )
        assert "\\x1b" in message and "\\t" in message
        assert "\x1b" not in message and "\t" not in message
        assert "\n" not in message and "\r" not in message and "\x00" not in message
        assert "DO_NOT_RETAIN_THE_TAIL" not in message
        assert len(message.encode("utf-8")) < 2500
        assert sink.metadata_lines == []
        # Only the preceding empty entry was captured.
        assert len(sink.fragments.records) == 4
        assert sink.direct_index == 0
        assert len(sink.direct_records) == 0
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()
