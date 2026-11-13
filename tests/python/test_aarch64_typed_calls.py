"""Call-producer boundaries: Arm ARM instruction forms and AAPCS64 saves.

These component tests isolate materialization. Whole-route native execution,
varargs, tail calls and all-five-GC integration remain separate requirements.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.backend import self_backend_aarch64_darwin_calls as calls
from pcc.backend import self_backend_aarch64_darwin_mem as mem
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.arm64_encode import assemble_text
from pcc.backend.self_backend_ir import TypeDesc


def _check_capture(emit, expected, *, inspect=None, literal_words=None):
    assert emit() == expected
    if literal_words is None:
        reference = assemble_text("\n".join(expected) + "\n").code
    else:
        reference = b"".join(
            literal_words[i].to_bytes(4, "little") if i in literal_words
            else assemble_text(line + "\n").code
            for i, line in enumerate(expected)
        )
    mem.begin_direct_instruction_capture(capture_semantics=True)
    try:
        result = emit()
        records = mem.borrow_direct_instruction_records()
        owner = mem.borrow_direct_instruction_semantics()
        assert len(records) == 4 * len(expected)
        assert len(owner.headers) == 4 * len(expected)
        assert all(line in (mem.DIRECT_INSTRUCTION_PLACEHOLDER,
                            mem.DIRECT_CALL_INSTRUCTION_PLACEHOLDER,
                            mem.DIRECT_INDIRECT_CALL_INSTRUCTION_PLACEHOLDER)
                   for line in result)
        assert len(result) == len(expected)
        assert b"".join(records.get4_unchecked(i).first.to_bytes(4, "little")
                        for i in range(len(expected))) == reference
        assert [sem.OPCODE_NAMES[owner.require_header(i).first]
                for i in range(len(expected))] == [line.split()[0] for line in expected]
        if inspect is not None:
            inspect(owner, result)
    finally:
        mem.end_direct_instruction_capture()


@pytest.fixture
def isolated_operands(monkeypatch):
    monkeypatch.setattr(calls, "materialize_pointer", lambda *a, **kw: [])
    monkeypatch.setattr(calls, "materialize_value", lambda *a, **kw: [])
    monkeypatch.setattr(calls, "store_value_regs_to_value_slot", lambda *a, **kw: [])
    monkeypatch.setattr(calls, "parsed_function_has_value_slot", lambda *a: True)
    return SimpleNamespace(name="typed_calls", aarch64_dynamic_stack=True)


@pytest.mark.parametrize("size", [32, 64, 128])
@pytest.mark.parametrize("zero", [False, True])
def test_simd_block_call_instructions(isolated_operands, size, zero):
    expected = ["  movi v0.16b, #0"] if zero else []
    for offset in range(0, size, 16):
        suffix = "" if offset == 0 else f", #{offset}"
        if not zero:
            expected.append(f"  ldr q0, [x10{suffix}]")
        expected.append(f"  str q0, [x9{suffix}]")
    def emit():
        if zero:
            return calls._emit_aligned_simd_block_zero(isolated_operands, "%dst", size, None)
        return calls._emit_aligned_simd_block_copy(isolated_operands, "%dst", "%src", size, None)
    def inspect(owner, result):
        first = 1 if zero else 0
        assert owner.operand(first, 1).second == sem.ADDRESS_BASE
        assert owner.operand(first + (1 if zero else 2), 1).second == sem.ADDRESS_OFFSET
    # Canonical text parsing does not support MOVI. Pin that segment to its
    # Arm ISA word; retain text-oracle comparison for every load/store word.
    _check_capture(emit, expected, inspect=inspect,
                   literal_words={0: 0x4F00E400} if zero else None)


@pytest.mark.parametrize("alignment", [16, 32, 4096])
def test_dynamic_alloca_exact_hex_and_sp(isolated_operands, alignment):
    args = ((TypeDesc("int", 64), "%size"), (TypeDesc("int", 64), str(alignment)))
    expected = ["  add x9, x9, #15", "  and x9, x9, #0xfffffffffffffff0",
                "  mov x10, sp", "  sub x10, x10, x9"]
    if alignment > 16:
        expected.append("  and x10, x10, #" + hex((1 << 64) - alignment))
    expected.append("  mov sp, x10")
    def inspect(owner, result):
        assert sem.semantic_immediate_radix(owner.operand(1, 2)) == 16
        assert owner.operand(2, 1).second == sem.REGISTER_SP
        assert owner.operand(len(expected) - 1, 0).second == sem.REGISTER_SP
    _check_capture(lambda: calls.emit_dynamic_alloca_call(isolated_operands, None, args, None),
                   expected, inspect=inspect)


@pytest.mark.parametrize("width", [32, 64])
def test_copysign_selected_width_only(isolated_operands, width):
    typ = TypeDesc("fp", width)
    gp, fp = ("w", "s") if width == 32 else ("x", "d")
    expected = [f"  fmov {gp}11, {fp}9", f"  fmov {gp}12, {fp}10",
                f"  and {gp}11, {gp}11, #{hex((1 << (width - 1)) - 1)}",
                f"  and {gp}12, {gp}12, #{hex(1 << (width - 1))}",
                f"  orr {gp}11, {gp}11, {gp}12", f"  fmov {fp}11, {gp}11"]
    _check_capture(lambda: calls.emit_copysign_intrinsic_call(
        isolated_operands, "%result", typ, "llvm.copysign", ((typ, "%a"), (typ, "%b")), None),
        expected)


def test_gc_register_capture_explicit_zero_offset(isolated_operands):
    typ = TypeDesc("ptr", 64)
    expected = [f"  stp x{first}, x{first + 1}, [x9, #{(first - 19) * 8}]"
                for first in range(19, 31, 2)]
    expected += ["  mov x10, sp", "  stp x10, xzr, [x9, #96]",
                 "  stp xzr, xzr, [x9, #112]"]
    def inspect(owner, result):
        assert owner.operand(0, 2).second == sem.ADDRESS_OFFSET
        assert owner.operand(0, 4).second == 0
        assert owner.operand(7, 1).second == sem.REGISTER_XZR
    _check_capture(lambda: calls.emit_call_instruction(
        isolated_operands, None, typ, "llvm.pcc.capture.gc.registers", False,
        ((typ, "%buffer"),), 1, False, module_symbols=SimpleNamespace()),
        expected, inspect=inspect)


def test_trap_capture_and_bad_operand_cleanup(isolated_operands):
    from pcc.backend import BackendUnavailable
    typ = TypeDesc("void", 0)
    def emit():
        return calls.emit_call_instruction(isolated_operands, None, typ,
            "llvm.trap", False, (), 0, False, module_symbols=SimpleNamespace())
    _check_capture(emit, ["  brk #0"])
    mem.begin_direct_instruction_capture(capture_semantics=True)
    try:
        with pytest.raises(BackendUnavailable):
            calls.emit_call_instruction(isolated_operands, None, typ, "llvm.trap", False,
                ((TypeDesc("int", 64), "1"),), 1, False, module_symbols=SimpleNamespace())
        assert len(mem.borrow_direct_instruction_records()) == 0
        assert len(mem.borrow_direct_instruction_semantics().headers) == 0
    finally:
        mem.end_direct_instruction_capture()
    _check_capture(emit, ["  brk #0"])


def test_calls_has_no_raw_instruction_construction():
    tree = ast.parse(Path(calls.__file__).read_text())
    raw = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("  ") and value.strip() and "\n" not in value:
                raw.append((node.lineno, value))
    assert raw == []


@pytest.mark.parametrize("triple", ["arm64-apple-darwin", "aarch64-unknown-linux-gnu", "aarch64-pc-windows-msvc"])
@pytest.mark.parametrize("vararg", [False, True])
def test_indirect_call_marker_across_targets(isolated_operands, monkeypatch, triple, vararg):
    monkeypatch.setattr(calls, "get_indexed_function_kernel", lambda func: None)
    symbols = SimpleNamespace(target_triple=triple)
    def inspect(owner, result):
        assert result == [mem.DIRECT_INDIRECT_CALL_INSTRUCTION_PLACEHOLDER]
        assert owner.operand(0, 0).second == 12
    _check_capture(lambda: calls.emit_call_instruction(
        isolated_operands, None, TypeDesc("void"), "%callee", True, (), 0, vararg,
        module_symbols=symbols), ["  blr x12"], inspect=inspect)


@pytest.mark.parametrize("width", [32, 64])
@pytest.mark.parametrize("operation, opcode", [
    ("fabs", "fabs"), ("sqrt", "fsqrt"), ("floor", "frintm"),
    ("ceil", "frintp"), ("trunc", "frintz"), ("rint", "frintn"),
])
def test_fp_unary_calls_exact_opcode(isolated_operands, width, operation, opcode):
    typ = TypeDesc("fp", width)
    prefix = "s" if width == 32 else "d"
    emit = getattr(calls, "emit_" + operation + "_intrinsic_call")
    _check_capture(lambda: emit(isolated_operands, "%result", typ,
        "llvm." + operation, ((typ, "%value"),), None),
        [f"  {opcode} {prefix}11, {prefix}9"])


@pytest.mark.parametrize("width", [32, 64])
def test_abs_call_preserves_condition_alias(isolated_operands, width):
    typ = TypeDesc("int", width)
    prefix = "w" if width == 32 else "x"
    def inspect(owner, result):
        token = owner.operand(1, 2)
        assert token.first == sem.TOKEN_CONDITION
        assert sem.CONDITION_NAMES[token.second] == "mi"
    _check_capture(lambda: calls.emit_abs_intrinsic_call(
        isolated_operands, "%result", typ, "llvm.abs",
        ((typ, "%value"), (TypeDesc("int", 1), "false")), None),
        [f"  cmp {prefix}9, #0", f"  cneg {prefix}10, {prefix}9, mi"], inspect=inspect)


@pytest.mark.parametrize("signed", [False, True])
def test_i32_overflow_multiply_captures_uxtw(isolated_operands, monkeypatch, signed):
    from pcc.backend import arm64_encode

    typ = TypeDesc("int", 32)
    result_type = TypeDesc("struct", fields=(typ, TypeDesc("int", 1)))
    if signed:
        expected = ["  smull x11, w9, w10", "  sxtw x13, w11",
                    "  cmp x11, x13", "  cset w12, ne"]
        producer = calls.emit_smul_overflow_intrinsic_call
        callee = "llvm.smul.with.overflow.i32"
    else:
        expected = ["  umull x11, w9, w10", "  lsr x12, x11, #32",
                    "  cmp x12, #0", "  cset w12, ne"]
        producer = calls.emit_umul_overflow_intrinsic_call
        callee = "llvm.umul.with.overflow.i32"
    expected += ["  uxtw x11, w11", "  orr x11, x11, x12, lsl #32"]

    def forbidden(*args, **kwargs):
        raise AssertionError("active overflow producer entered instruction-text encoding")

    def emit():
        if mem.direct_instruction_capture_active():
            # The inactive text oracle has already run. Forbid instruction-line
            # parsing for the actual producer, not for the compatibility oracle.
            monkeypatch.setattr(arm64_encode, "_encode_one", forbidden)
            monkeypatch.setattr(arm64_encode, "_split_operands", forbidden)
            monkeypatch.setattr(arm64_encode, "append_emitted_instruction_record", forbidden)
        return producer(isolated_operands, "%result", result_type, callee,
                        ((typ, "%lhs"), (typ, "%rhs")), None)

    def inspect(owner, result):
        assert sem.OPCODE_NAMES[owner.require_header(4).first] == "uxtw"
        assert owner.require_header(4).fourth == 2
        assert owner.operand(4, 0).first == sem.TOKEN_REGISTER
        assert owner.operand(4, 0).second == sem.REGISTER_X + 11
        assert owner.operand(4, 1).first == sem.TOKEN_REGISTER
        assert owner.operand(4, 1).second == sem.REGISTER_W + 11
        assert result == [mem.DIRECT_INSTRUCTION_PLACEHOLDER] * 6

    _check_capture(emit, expected, inspect=inspect)
