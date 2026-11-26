"""The optimized route copies each direct instruction into the module as integers.

The isel appends every instruction to a per-function direct owner through
append_header, which validates its operands; the optimized sink then copies
it into the module arenas. That copy rebuilt one record object per operand
and walked the operand grammar again: 12% of a Stage1 codegen worker
(array_core). On the CPython host _capture_direct_host now copies the
integers, re-interning symbol IDs (append_copied_semantic_instruction);
PCC_DEBUG_AARCH64_VERIFY_EACH, native arenas and every error keep the
checked copy. Objects are byte-identical (array_core, hoist_predicates).
"""
from __future__ import annotations

import pytest

from pcc.backend import self_backend_aarch64_darwin as darwin
from pcc.backend import self_backend_aarch64_semantics as semantic
from pcc.backend.arm64_encode import (
    EMITTED_INSTRUCTION_MOVE, EMITTED_INSTRUCTION_SCALAR, STRUCTURED_FIXUP_BRANCH26, EncodeError,
)
from pcc.backend.self_backend_aarch64_fragments import AArch64EmissionFragments
from pcc.backend.self_backend_value_arena import CompilerInt4


def register(name):
    return CompilerInt4(semantic.TOKEN_REGISTER, semantic.semantic_register_id(name), 0, 0)


def instructions(symbol):
    return [
        (CompilerInt4(0xAA0103E0, EMITTED_INSTRUCTION_MOVE, 0, -1), "mov", [register("x0"), register("x1")]),
        (CompilerInt4(0x14000000, EMITTED_INSTRUCTION_SCALAR, STRUCTURED_FIXUP_BRANCH26, symbol), "b",
         [CompilerInt4(semantic.TOKEN_SYMBOL, symbol, semantic.SYMBOL_PLAIN, 0)]),
        (CompilerInt4(0xAA0203E1, EMITTED_INSTRUCTION_MOVE, 0, -1), "mov", [register("x1"), register("x2")]),
    ]


def arenas(fragments, semantics, span):
    fragments.start_cursor(span)
    ids = []
    record_id = fragments.next_record_id()
    while record_id >= 0:
        ids.append(record_id)
        record_id = fragments.next_record_id()
    return (list(fragments.records._values), list(semantics.headers._values),
            list(semantics.tokens._values), ids)


def build(copied):
    fragments = AArch64EmissionFragments()
    semantics = semantic.AArch64InstructionSemantics()
    fragments.symbol_names.append("L_target")
    span = fragments.new_fragment()
    for encoded, opcode, tokens in instructions(0):
        if copied:
            values = [part for token in tokens for part in (token.first, token.second, token.third, token.fourth)]
            semantic.append_copied_semantic_instruction(
                fragments, semantics, span, encoded, semantic.semantic_opcode_id(opcode),
                semantic.FORM_CANONICAL, values)
        else:
            semantic.append_semantic_instruction(
                fragments, semantics, span, encoded, semantic.semantic_opcode_id(opcode),
                semantic.FORM_CANONICAL, tokens)
    return fragments, semantics, span


def test_copied_instruction_equals_the_checked_append():
    checked = build(False)
    copied = build(True)
    try:
        assert arenas(*copied) == arenas(*checked)
        copied[1].require_complete(copied[0].records, len(copied[0].symbol_names))
    finally:
        for fragments, semantics, _span in (checked, copied):
            semantics.close()
            fragments.close()


@pytest.mark.parametrize("case", ["symbol_token", "opcode", "relocation", "root_commit"])
def test_copied_instruction_rejects_and_rolls_back(monkeypatch, case):
    fragments, semantics, span = build(True)
    try:
        before = arenas(fragments, semantics, span)
        encoded, opcode, tokens = instructions(0)[1]
        values = [semantic.TOKEN_SYMBOL, 0, semantic.SYMBOL_PLAIN, 0]
        opcode_id = semantic.semantic_opcode_id(opcode)
        expected = EncodeError
        if case == "symbol_token":
            values[1] = len(fragments.symbol_names)
        elif case == "opcode":
            opcode_id = 0
        elif case == "relocation":
            encoded = CompilerInt4(encoded.first, encoded.second, 7, 0)
        else:
            def fail(destination, record_id):
                raise MemoryError("before root commit")
            monkeypatch.setattr(fragments.spans, "append", fail)
            expected = MemoryError
        with pytest.raises(expected):
            semantic.append_copied_semantic_instruction(
                fragments, semantics, span, encoded, opcode_id, semantic.FORM_CANONICAL, values)
        monkeypatch.undo()
        assert arenas(fragments, semantics, span) == before
    finally:
        semantics.close()
        fragments.close()


def capture(monkeypatch, host):
    """Feed one function's isel output through the optimized sink."""
    from pcc.backend import self_backend_aarch64_darwin_mem as producer
    from pcc.backend.self_backend_aarch64_darwin_tlv import tlv_resolver_lines
    from pcc.backend.self_backend_aarch64_fragments import EMISSION_DIRECTIVE_TEXT_SECTION

    original = darwin._NativeAArch64Emission._capture_direct_host
    taken = []
    def observed(self, line):
        copied = original(self, line) if host else False
        taken.append(copied)
        return copied
    monkeypatch.setattr(darwin._NativeAArch64Emission, "_capture_direct_host", observed)
    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = darwin._NativeAArch64Emission(optimize=True)
        sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
        sink.append_label("_f")
        sink.extend([
            producer.emitted_movewide_instruction_line("movz", "x9", 0),
            producer.emitted_memory_instruction_line("stur", "x9", "x29", -8),
            producer.emitted_cset_line("w11", "eq"),
            producer.emitted_branch_line("cbnz", "L_done", "w11"),
        ])
        sink.append_label("L_done")
        sink.append(producer.emitted_indirect_branch_line("blr", "x10"))
        sink.extend(tlv_resolver_lines("_tls", "x9", 31, emitted=True))
        sink.append(producer.emitted_move_register_line("x0", "x9"))
        sink.append(producer.emitted_branch_line("b", "L_done"))
        sink.append(producer.emitted_fixed_instruction_line("ret"))
        sink.release_captured_function()
        fragments = sink.fragments
        return (arenas(fragments, fragments.semantics, sink.module_span),
                list(fragments.symbol_names), taken)
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()
        monkeypatch.undo()


def test_sink_host_copy_equals_the_checked_copy(monkeypatch):
    host_arenas, host_symbols, host_taken = capture(monkeypatch, True)
    checked_arenas, checked_symbols, checked_taken = capture(monkeypatch, False)
    assert host_taken and all(host_taken)
    assert checked_taken and not any(checked_taken)
    assert host_arenas == checked_arenas
    assert host_symbols == checked_symbols
    assert any(token == semantic.TOKEN_SYMBOL for token in host_arenas[2][0::4])


def test_verify_each_keeps_the_checked_copy(monkeypatch):
    monkeypatch.setenv("PCC_DEBUG_AARCH64_VERIFY_EACH", "1")
    _arenas, _symbols, taken = capture(monkeypatch, True)
    assert taken and not any(taken)
