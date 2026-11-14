"""Reuse an indexed call's target decision without changing emitted output."""

from __future__ import annotations

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_aarch64_darwin_calls as calls
from pcc.backend import self_backend_aarch64_darwin_mem as memory
from pcc.backend.arm64_encode import EMITTED_INSTRUCTION_SCALAR
from pcc.backend.self_backend_aarch64_darwin_abi import aggregate_returned_indirect
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_prepare import prepare_module_for_target


_TARGETS = (
    "aarch64-unknown-linux-gnu",
    "arm64-unknown-linux-musl",
    "arm64-apple-darwin23.6.0",
)
_CASES = (
    ("fixed", 0), ("fixed", 12), ("fp", 8), ("fp", 9),
    ("vararg", 12), ("mixed", 12), ("register-align", 12),
    ("stack-align", 12), ("indirect", 12),
)


def _source(target, shape, count=12):
    args = [("i64", str(index + 1)) for index in range(count)]
    if shape == "fp":
        args = [("double", "0.000000e+00")] * count
    elif shape == "mixed":
        args += [("double", "0.000000e+00")] * 10
    elif shape == "register-align":
        args = args[:7] + [("[1 x i128]", "zeroinitializer"), ("i64", "99")]
    elif shape == "stack-align":
        args = args[:9] + [("[1 x i128]", "zeroinitializer"), ("i64", "99")]
    signature = ", ".join(arg_type for arg_type, _value in args)
    actuals = ", ".join(arg_type + " " + value for arg_type, value in args)
    if shape == "vararg":
        signature = "i64, ..."
        invocation = "void (i64, ...) @sink"
    else:
        invocation = "void " + ("%callee" if shape == "indirect" else "@sink")
    declaration = "" if shape == "indirect" else "declare void @sink(" + signature + ")\n"
    parameters = "ptr %callee" if shape == "indirect" else ""
    return (
        'target triple = "' + target + '"\n' + declaration
        + "define void @caller(" + parameters + ") {\nentry:\n"
        + "  call " + invocation + "(" + actuals + ")\n  ret void\n}\n"
    )


def _emit(source, *, indexed, capture=False, fault=None):
    prepared = prepare_module_for_target(
        source, aggregate_returned_indirect=aggregate_returned_indirect,
    )
    assert len(prepared.functions) == 1
    function = prepared.functions[0]
    kernel = get_indexed_function_kernel(function)
    capturing = False
    try:
        memory.require_direct_instruction_capture_idle()
        # Corrupt a real prepared call only to exercise the emitter's existing
        # early guards, without replacing the verifier or relaxing assertions.
        if fault == "intrinsic":
            header = kernel.call_header(0)
            kernel.call_texts[header.second] = "llvm.trap"
        elif fault == "vararg-overflow":
            header = kernel.call_header(0)
            span = kernel.call_span(0)
            kernel.call_scalars.set_unchecked(2, header.third | 2)
            kernel.call_scalars.set_unchecked(5, span.first + 1)
        if capture:
            memory.begin_direct_instruction_capture()
            capturing = True
        if indexed:
            lines = calls.emit_call_instruction_indexed(
                function, kernel, 0, prepared.module_symbols,
            )
        else:
            # This independently retained path still queries the target where
            # it chooses register layout and stack placement.
            lines = calls.emit_call_instruction(
                function, *kernel.diagnostic_call_data(0),
                module_symbols=prepared.module_symbols,
            )
        if not capture:
            return lines
        records = memory.borrow_direct_instruction_records()
        return (
            lines,
            [records.get_unchecked(index) for index in range(len(records))],
            list(memory.borrow_direct_instruction_symbol_names()),
        )
    finally:
        if capturing:
            memory.end_direct_instruction_capture()
        kernel.close_native_tables()


@pytest.mark.parametrize("target", _TARGETS)
@pytest.mark.parametrize("shape,count", _CASES)
@pytest.mark.parametrize("capture", (False, True), ids=("assembly", "records"))
def test_indexed_call_target_reuse_preserves_legacy_output(target, shape, count, capture):
    source = _source(target, shape, count)
    assert _emit(source, indexed=True, capture=capture) == _emit(
        source, indexed=False, capture=capture,
    )


@pytest.mark.parametrize("shape,count,expected_queries", (
    ("fixed", 0, 0), ("fp", 8, 0), ("fp", 9, 1),
    ("fixed", 1, 1), ("fixed", 8, 1), ("fixed", 9, 1),
    ("fixed", 32, 1), ("vararg", 12, 1),
))
@pytest.mark.parametrize("target", (_TARGETS[0], _TARGETS[2]))
def test_indexed_call_classifies_target_only_when_needed(
    monkeypatch, shape, count, expected_queries, target,
):
    classify = calls.is_aarch64_linux_triple
    queries = []

    def counted(triple):
        queries.append(triple)
        return classify(triple)

    source = _source(target, shape, count)
    expected = _emit(source, indexed=False)
    with monkeypatch.context() as patch:
        # The symbol mangler's independent imported classifier is not replaced.
        patch.setattr(calls, "is_aarch64_linux_triple", counted)
        actual = _emit(source, indexed=True)
    assert actual == expected
    assert queries == [target] * expected_queries


@pytest.mark.parametrize("fault,message", (
    ("intrinsic", "indexed regular-call lowering received an LLVM intrinsic"),
    ("vararg-overflow", "fixed args exceed actual args"),
))
@pytest.mark.parametrize("capture", (False, True))
def test_indexed_call_errors_precede_target_classification(monkeypatch, fault, message, capture):
    def forbidden(_triple):
        pytest.fail("target classification ran before an existing call error")

    monkeypatch.setattr(calls, "is_aarch64_linux_triple", forbidden)
    with pytest.raises(BackendUnavailable, match=message):
        _emit(_source(_TARGETS[0], "fixed"), indexed=True, capture=capture, fault=fault)
    assert not memory.direct_instruction_capture_active()


def test_indexed_call_target_decision_is_not_retained_between_calls():
    sequence = (_TARGETS[0], _TARGETS[2], _TARGETS[1], _TARGETS[2], _TARGETS[0])
    outputs = [_emit(_source(target, "vararg"), indexed=True) for target in sequence]
    assert outputs[0] == outputs[2] == outputs[4]
    assert outputs[1] == outputs[3]
    assert outputs[0] != outputs[1]
    # Twelve i64 arguments: Linux spills four, Darwin spills eleven variadic
    # arguments. The resulting 16-byte-aligned outgoing frames must differ.
    assert "  sub sp, sp, #32" in outputs[0]
    assert "  sub sp, sp, #96" in outputs[1]
    assert "  bl sink" in outputs[0]
    assert "  bl _sink" in outputs[1]


def test_indexed_call_preserves_linux_stack_alignment():
    linux = _emit(_source(_TARGETS[0], "stack-align"), indexed=True)
    darwin = _emit(_source(_TARGETS[2], "stack-align"), indexed=True)
    # Both platforms retain an aggregate's natural 16-byte stack alignment.
    # Darwin's compact scalar slots do not waive alignment of the next value.
    assert "  sub sp, sp, #48" in linux
    assert "  add x14, sp, #32" in linux
    assert "  sub sp, sp, #48" in darwin
    assert "  add x14, sp, #32" in darwin


@pytest.mark.parametrize("target", (_TARGETS[0], _TARGETS[2]))
@pytest.mark.parametrize("shape", ("vararg", "stack-align"))
def test_call_target_reuse_matches_assembly_and_native_sections(target, shape):
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.self_backend_aarch64_darwin import (
        emit_aarch64_darwin_indexed_module,
        emit_aarch64_darwin_indexed_transport,
    )
    from pcc.backend.self_backend_parse import parse_self_backend_module

    source = _source(target, shape)
    assembly = emit_aarch64_darwin_indexed_module(
        parse_self_backend_module(source), optimize=False,
    )
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(source), optimize=False, structured_instructions=True,
    )
    assert transport.native_finalized
    assert transport.fallback_instruction_count == 0
    # Complete sections include instruction words, symbols, relocations and
    # stackmaps, not merely the human-readable call spelling.
    assert transport.assemble_sections() == assemble_file(assembly)


def _stack_layout_source(target, types, fixed_count=None):
    values = ["0.000000e+00" if ty in ("float", "double") else
              "null" if ty == "ptr" else "zeroinitializer" if ty.startswith(("{", "[")) else "1"
              for ty in types]
    signature = ", ".join(types if fixed_count is None else types[:fixed_count] + ["..."])
    actuals = ", ".join(ty + " " + value for ty, value in zip(types, values))
    callee = "void @sink" if fixed_count is None else "void (" + signature + ") @sink"
    return ('target triple = "' + target + '"\ndeclare void @sink(' + signature + ')\n'
            + 'define void @caller() {\nentry:\n  call ' + callee + '(' + actuals + ')\n'
            + '  ret void\n}\n')


def _stack_address_offsets(output, *, capture):
    if not capture:
        return [int(line.split("#")[1]) for line in output
                if line.startswith("  add x14, sp, #")]
    # Direct mode returns placeholders in the line stream. Inspect its packed
    # instruction records instead: ADD X14, SP, #imm12 has imm12 in bits 21:10.
    # Retain the opcode, width, shift and register fields when selecting words.
    _lines, records, _symbols = output
    assert len(records) % 4 == 0
    offsets = []
    for index in range(0, len(records), 4):
        word, family, fixup, symbol = records[index:index + 4]
        if word & 0xFFC003FF == 0x910003EE:
            assert (family, fixup, symbol) == (EMITTED_INSTRUCTION_SCALAR, 0, -1)
            offsets.append((word >> 10) & 0xFFF)
    return offsets


def test_stack_address_offsets_read_words_not_placeholder_text():
    # Fixed ISA words keep the probe independent of the producer being checked.
    words = [
        0x910013EE,  # add x14, sp, #4
        0x910023EE,  # add x14, sp, #8
        0x910033EE,  # add x14, sp, #12
        0xD10023EE,  # sub x14, sp, #8
        0x110023EE,  # add w14, wsp, #8
        0x914023EE,  # add x14, sp, #8, lsl #12
        0x910023ED,  # add x13, sp, #8
        0x910023CE,  # add x14, x30, #8
    ]
    records = [field for word in words
               for field in (word, EMITTED_INSTRUCTION_SCALAR, 0, -1)]
    lines = [memory.DIRECT_INSTRUCTION_PLACEHOLDER] * len(words)
    actual = (lines, records, [])
    assert _stack_address_offsets(actual, capture=True) == [4, 8, 12]
    # A changed word must change the observed layout even though all text is
    # unchanged. The fixed-layout test below would reject this scalar sequence.
    records[0] = 0x910023EE
    assert _stack_address_offsets(actual, capture=True) == [8, 8, 12]


@pytest.mark.parametrize("metadata", [
    (0, 0, -1),
    (EMITTED_INSTRUCTION_SCALAR, 1, -1),
    (EMITTED_INSTRUCTION_SCALAR, 0, 0),
])
def test_stack_address_offsets_reject_non_scalar_or_relocated_records(metadata):
    actual = ([memory.DIRECT_INSTRUCTION_PLACEHOLDER], [0x910013EE, *metadata], [])
    with pytest.raises(AssertionError):
        _stack_address_offsets(actual, capture=True)


@pytest.mark.parametrize("target", (_TARGETS[0], _TARGETS[2]))
@pytest.mark.parametrize("capture", (False, True), ids=("assembly", "records"))
@pytest.mark.parametrize("types,offsets", [
    (["float"] * 12, ([0, 8, 16, 24], [0, 4, 8, 12])),
    (["i64"] * 8 + ["float"] * 8 + ["i8", "i16", "float", "double", "i32", "ptr"],
     ([0, 8, 16, 24, 32, 40], [0, 2, 4, 8, 16, 24])),
    (["float"] * 9 + ["{float, float, float}", "float"],
     ([0, 8, 24], [0, 4, 16])),
    (["i64"] * 8 + ["i8", "{i8, i8, i8}", "i32", "{i64, i64, i64}"],
     ([0, 8, 16, 24], [0, 8, 16, 24])),
])
def test_fixed_stack_layout_matches_both_emitters(target, capture, types, offsets):
    source = _stack_layout_source(target, types)
    actual = _emit(source, indexed=True, capture=capture)
    assert actual == _emit(source, indexed=False, capture=capture)
    actual_offsets = _stack_address_offsets(actual, capture=capture)
    expected = offsets[0 if target == _TARGETS[0] else 1]
    for offset in expected[1:]:
        assert offset in actual_offsets
    if all(not ty.startswith(("{", "[")) for ty in types):
        assert actual_offsets == expected[1:]


@pytest.mark.parametrize("fixed_count,vararg_offset", [(9, 8), (10, 8), (11, 16)])
@pytest.mark.parametrize("indexed", (False, True))
def test_darwin_call_aligns_varargs_after_compact_fixed_tail(fixed_count, vararg_offset, indexed):
    # Named i32 values beyond x7 are packed; unnamed values keep 8-byte slots.
    source = _stack_layout_source(_TARGETS[2], ["i32"] * fixed_count + ["i64", "i64"], fixed_count)
    lines = _emit(source, indexed=indexed)
    for offset in (vararg_offset, vararg_offset + 8):
        assert "  add x14, sp, #" + str(offset) in lines
    if fixed_count >= 10:
        assert "  add x14, sp, #4" in lines
