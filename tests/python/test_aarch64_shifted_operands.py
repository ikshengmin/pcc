"""A shift by a constant folds into the next ALU instruction's second operand.

``%s = shl/lshr/ashr iN %y, C`` whose only use is the directly following
``add``/``sub``/``and``/``or``/``xor`` of the same 32- or 64-bit type
emitted the shift and then the operation. LLVM's AArch64 selection takes
the shifted-register form instead (``eor w8, w8, w0, lsr #13``,
``add x8, x9, x8, lsl #7``): the shift emits nothing. ``sub`` folds only
its subtrahend. A second use, an instruction between the two, a constant
other operand, and a consumer fused into ``madd`` or ``tst`` keep the
shift. Encodings are clang 20's; the program
executes every form natively.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.arm64_encode import assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


ENCODINGS = {
    "  add x0, x1, x2, lsr #13": 0x8B423420,
    "  eor w8, w8, w0, lsr #13": 0x4A403508,
    "  eor x1, x1, x2, lsr #13": 0xCA423421,
    "  sub x0, x1, x2, asr #3": 0xCB820C20,
    "  and x3, x4, x5, lsl #2": 0x8A050883,
    "  orr w1, w2, w3, asr #31": 0x2A837C41,
    "  add x8, x9, x8, lsl #7": 0x8B081D28,
    "  sub w0, w1, w2, lsl #5": 0x4B021420,
    "  add w3, w4, w5, asr #1": 0x0B850483,
    "  orr x19, x20, x21, lsr #63": 0xAA55FE93,
}


def test_shifted_register_forms_encode_like_the_reference_assembler():
    for text, word in ENCODINGS.items():
        assert int.from_bytes(assemble_text(text).code[:4], "little") == word, text


def test_shifted_register_records_reencode_to_the_same_words():
    from pcc.backend import self_backend_aarch64_darwin_mem as mem
    from pcc.backend import self_backend_aarch64_semantics as sem
    from pcc.backend.self_backend_aarch64_semantic_encode import encode_semantic_instruction

    for text, word in ENCODINGS.items():
        mnemonic, d, n, m, kind, amount = re.fullmatch(
            r"  (\w+) (\w+), (\w+), (\w+), (\w+) #(\d+)", text).groups()
        shift_kind = sem.SHIFT_NAMES.index(kind)
        assert mem.emitted_scalar_register_line(
            mnemonic, d, n, m, shift=int(amount), shift_kind=shift_kind) == text
        mem.begin_direct_instruction_capture(capture_semantics=True)
        try:
            line = mem.emitted_scalar_register_line(
                mnemonic, d, n, m, shift=int(amount), shift_kind=shift_kind)
            assert line == mem.DIRECT_INSTRUCTION_PLACEHOLDER
            records = mem.borrow_direct_instruction_records()
            owner = mem.borrow_direct_instruction_semantics()
            assert records.get4_unchecked(0).first == word, text
            header = owner.require_header(0)
            tokens = [owner.operand(0, i) for i in range(header.fourth)]
            assert encode_semantic_instruction(header.first, tokens).first == word, text
        finally:
            mem.end_direct_instruction_capture()


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @hash(i64 %x) {
entry:
  %a = lshr i64 %x, 4
  %b = lshr i64 %x, 13
  %c = xor i64 %a, %b
  %d = and i64 %c, 63
  ret i64 %d
}
define i64 @add_shl(i64 %x, i64 %y) {
entry:
  %s = shl i64 %y, 3
  %r = add i64 %x, %s
  ret i64 %r
}
define i64 @add_commuted(i64 %x, i64 %y) {
entry:
  %s = shl i64 %y, 7
  %r = add i64 %s, %x
  ret i64 %r
}
define i64 @sub_ashr(i64 %x, i64 %y) {
entry:
  %s = ashr i64 %y, 2
  %r = sub i64 %x, %s
  ret i64 %r
}
define i32 @or_lshr32(i32 %x, i32 %y) {
entry:
  %s = lshr i32 %y, 5
  %r = or i32 %x, %s
  ret i32 %r
}
define i32 @and_ashr32(i32 %x, i32 %y) {
entry:
  %s = ashr i32 %y, 3
  %r = and i32 %s, %x
  ret i32 %r
}
define i64 @sub_minuend(i64 %x, i64 %y) {
entry:
  %s = shl i64 %y, 2
  %r = sub i64 %s, %x
  ret i64 %r
}
define i64 @shared(i64 %x, i64 %y) {
entry:
  %s = shl i64 %y, 2
  %r = add i64 %x, %s
  %t = mul i64 %r, %s
  ret i64 %t
}
define i64 @constant_other(i64 %y) {
entry:
  %s = shl i64 %y, 3
  %r = add i64 %s, 5
  ret i64 %r
}
define i64 @into_madd(i64 %a, i64 %b, i64 %c) {
entry:
  %m = mul i64 %a, %b
  %s = shl i64 %c, 2
  %r = add i64 %m, %s
  ret i64 %r
}
define i64 @into_tst(i64 %x) {
entry:
  %s = shl i64 %x, 2
  %a = and i64 %s, 8
  %c = icmp eq i64 %a, 0
  br i1 %c, label %yes, label %no
yes:
  ret i64 11
no:
  ret i64 22
}
define i32 @main() {
entry:
  %h = call i64 @hash(i64 74565)
  %a1 = call i64 @add_shl(i64 5, i64 3)
  %a2 = call i64 @add_commuted(i64 1, i64 2)
  %s1 = call i64 @sub_ashr(i64 100, i64 -40)
  %o1 = call i32 @or_lshr32(i32 1, i32 -1)
  %n1 = call i32 @and_ashr32(i32 255, i32 -1024)
  %m1 = call i64 @sub_minuend(i64 1, i64 10)
  %sh = call i64 @shared(i64 1, i64 2)
  %co = call i64 @constant_other(i64 4)
  %md = call i64 @into_madd(i64 3, i64 4, i64 5)
  %t1 = call i64 @into_tst(i64 2)
  %t2 = call i64 @into_tst(i64 1)
  %c1 = icmp eq i64 %h, 61
  %c2 = icmp eq i64 %a1, 29
  %c3 = icmp eq i64 %a2, 257
  %c4 = icmp eq i64 %s1, 110
  %c5 = icmp eq i32 %o1, 134217727
  %c6 = icmp eq i32 %n1, 128
  %c7 = icmp eq i64 %m1, 39
  %c8 = icmp eq i64 %sh, 72
  %c9 = icmp eq i64 %co, 37
  %c10 = icmp eq i64 %md, 32
  %c11 = icmp eq i64 %t1, 22
  %c12 = icmp eq i64 %t2, 11
  %b1 = and i1 %c1, %c2
  %b2 = and i1 %b1, %c3
  %b3 = and i1 %b2, %c4
  %b4 = and i1 %b3, %c5
  %b5 = and i1 %b4, %c6
  %b6 = and i1 %b5, %c7
  %b7 = and i1 %b6, %c8
  %b8 = and i1 %b7, %c9
  %b9 = and i1 %b8, %c10
  %b10 = and i1 %b9, %c11
  %b11 = and i1 %b10, %c12
  %status = select i1 %b11, i32 0, i32 67
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


FOLDED = {
    "hash": r"eor x\d+, x\d+, x\d+, lsr #13",
    "add_shl": r"add x\d+, x\d+, x\d+, lsl #3",
    "add_commuted": r"add x\d+, x\d+, x\d+, lsl #7",
    "sub_ashr": r"sub x\d+, x\d+, x\d+, asr #2",
    "or_lshr32": r"orr w\d+, w\d+, w\d+, lsr #5",
    "and_ashr32": r"and w\d+, w\d+, w\d+, asr #3",
}


def test_single_use_shifts_fold_into_the_next_operation(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name, pattern in FOLDED.items():
        body = _body(assembly, name)
        assert re.search(r"\b" + pattern, body), (name, body)
    # Only the shift adjacent to the xor folds; the other stays.
    hash_body = _body(assembly, "hash")
    assert len(re.findall(r"\blsr x\d+, x\d+, #", hash_body)) == 1, hash_body
    for name in ("add_shl", "add_commuted", "sub_ashr", "or_lshr32", "and_ashr32"):
        body = _body(assembly, name)
        assert not re.search(r"\b(lsl|lsr|asr) [wx]\d+, [wx]\d+, #", body), (name, body)
    for name in ("sub_minuend", "shared", "constant_other", "into_madd", "into_tst"):
        body = _body(assembly, name)
        assert re.search(r"\blsl x\d+, x\d+, #2|\blsl x\d+, x\d+, #3", body), (name, body)
        assert not re.search(r"\b(add|sub|and|orr|eor) [wx]\d+, [wx]\d+, [wx]\d+, (lsl|lsr|asr) #", body), (name, body)


@pytest.mark.parametrize("registers", ("0", "1"))
def test_typed_and_text_emission_agree(monkeypatch, registers):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    expected = assemble_file(emit_aarch64_darwin_asm(SOURCE, optimize=True))
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    assert transport.assemble_sections() == expected


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_shifted_operands_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "shifts"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
