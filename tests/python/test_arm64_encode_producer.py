"""Scalar producer encoding boundaries (Arm ARM scalar instruction families).

Text encoding is a CI-only compatibility oracle, never the production route.
Literal words independently pin register fields, opcodes and width boundaries.
"""
import pytest

from pcc.backend.arm64_encode import EncodeError, assemble_text
from pcc.backend.arm64_encode_producer import (
    encode_producer_atomic,
    encode_producer_system,
    encode_producer_indirect_branch,
    encode_producer_pair,
    encode_producer_fp_immediate,
    encode_producer_vector,
    encode_producer_immediate,
    encode_producer_registers,
)


@pytest.mark.parametrize("args, text, word", [
    (("add", "x9", "x10", "x11"), "add x9, x10, x11", 0x8B0B0149),
    (("add", "x9", "x10", "x11", "", 3), "add x9, x10, x11, lsl #3", 0x8B0B0D49),
    (("add", "x9", "x10", "x11", "", 63), "add x9, x10, x11, lsl #63", 0x8B0BFD49),
    (("sub", "w9", "w10", "w11", "", 31), "sub w9, w10, w11, lsl #31", 0x4B0B7D49),
    (("neg", "x9", "x10"), "neg x9, x10", 0xCB0A03E9),
    (("mul", "w9", "w10", "w11"), "mul w9, w10, w11", 0x1B0B7D49),
    (("sdiv", "x9", "x10", "x11"), "sdiv x9, x10, x11", 0x9ACB0D49),
    (("udiv", "w9", "w10", "w11"), "udiv w9, w10, w11", 0x1ACB0949),
    (("madd", "x9", "x10", "x11", "x12"), "madd x9, x10, x11, x12", 0x9B0B3149),
    (("msub", "w9", "w10", "w11", "w12"), "msub w9, w10, w11, w12", 0x1B0BB149),
    (("sxtb", "x9", "w10"), "sxtb x9, w10", 0x93401D49),
    (("sxth", "w9", "w10"), "sxth w9, w10", 0x13003D49),
    (("sxtw", "x9", "w10"), "sxtw x9, w10", 0x93407D49),
    (("and", "x9", "x10", "x11"), "and x9, x10, x11", 0x8A0B0149),
    (("orr", "w9", "w10", "w11"), "orr w9, w10, w11", 0x2A0B0149),
    (("eor", "x9", "x10", "x11"), "eor x9, x10, x11", 0xCA0B0149),
    (("lslv", "x9", "x10", "x11"), "lslv x9, x10, x11", 0x9ACB2149),
    (("lsrv", "w9", "w10", "w11"), "lsrv w9, w10, w11", 0x1ACB2549),
    (("asrv", "x9", "x10", "x11"), "asrv x9, x10, x11", 0x9ACB2949),
    (("csel", "x9", "x10", "x11", "le"), "csel x9, x10, x11, le", 0x9A8BD149),
    (("fadd", "d9", "d10", "d11"), "fadd d9, d10, d11", 0x1E6B2949),
    (("fsub", "s9", "s10", "s11"), "fsub s9, s10, s11", 0x1E2B3949),
    (("fmul", "d9", "d10", "d11"), "fmul d9, d10, d11", 0x1E6B0949),
    (("fdiv", "s9", "s10", "s11"), "fdiv s9, s10, s11", 0x1E2B1949),
    (("fneg", "d9", "d10"), "fneg d9, d10", 0x1E614149),
    (("fcmp", "d9", "d10"), "fcmp d9, d10", 0x1E6A2120),
    (("fcsel", "s9", "s10", "s11", "eq"), "fcsel s9, s10, s11, eq", 0x1E2B0D49),
    (("fmov", "s9", "w10"), "fmov s9, w10", 0x1E270149),
    (("fmov", "x9", "d10"), "fmov x9, d10", 0x9E660149),
    (("fcvt", "d9", "s10"), "fcvt d9, s10", 0x1E22C149),
    (("fcvt", "s9", "d10"), "fcvt s9, d10", 0x1E624149),
    (("scvtf", "s9", "x10"), "scvtf s9, x10", 0x9E220149),
    (("ucvtf", "d9", "w10"), "ucvtf d9, w10", 0x1E630149),
    (("fcvtzs", "w9", "s10"), "fcvtzs w9, s10", 0x1E380149),
    (("fcvtzu", "x9", "d10"), "fcvtzu x9, d10", 0x9E790149),
])
def test_register_literals_and_text_reference(args, text, word):
    assert encode_producer_registers(*args) == word
    assert assemble_text("  " + text + "\n").code == word.to_bytes(4, "little")


@pytest.mark.parametrize("args, text, word", [
    (("mov", "x9", "", -1), "mov x9, #-1", 0x92800009),
    (("mov", "w9", "", 65535), "mov w9, #65535", 0x529FFFE9),
    (("and", "x9", "x10", 1), "and x9, x10, #1", 0x92400149),
    (("lsl", "x9", "x10", 63), "lsl x9, x10, #63", 0xD3410149),
    (("lsr", "w9", "w10", 31), "lsr w9, w10, #31", 0x531F7D49),
    (("asr", "x9", "x10", 0), "asr x9, x10, #0", 0x9340FD49),
])
def test_immediate_literals_and_text_reference(args, text, word):
    assert encode_producer_immediate(*args) == word
    assert assemble_text("  " + text + "\n").code == word.to_bytes(4, "little")


@pytest.mark.parametrize("args", [
    ("add", "w0", "w1", "w2", "", 32),
    ("sub", "x0", "x1", "x2", "", -1),
    ("add", "x0", "w1", "x2"),
    ("mul", "x0", "x1", " SP "),
    ("sxtw", "w0", "w1"), ("sxtw", "x0", "x1"),
    ("sxtb", "w0", "x1"), ("sxth", "w0", "x1"),
    ("madd", "w0", "w1", "w2", "w3"),
    ("msub", "x0", "x1", "x2", "w3"),
    ("msub", "x0", "x1", "x2", "SP"),
    ("neg", "x0", "x1", "x2"),
    ("fneg", "d0", "d1", "d2"),
    ("fcmp", "d0", "d1", "", "eq"),
    ("fadd", "d0", "d1", "s2"),
    ("fmov", "d0", "w1"), ("fmov", "d0", "SP"),
    ("scvtf", "d0", "sp"), ("fcvtzu", "wsp", "s1"),
    ("fcvt", "s0", "s1"),
    ("csel", "x0", "x1", "x2", "invalid"),
    ("and", "x0", "x1", "x2", "", 1),
    ("mul", "x31", "x1", "x2"),
    ("unknown", "x0", "x1", "x2"),
])
def test_register_invalid_forms_fail_closed(args):
    with pytest.raises(EncodeError):
        encode_producer_registers(*args)


@pytest.mark.parametrize("args", [
    ("mov", "x0", "x1", 1), ("mov", "SP", "", 0),
    ("mov", "x0", "", 0x12345678),
    ("and", "x0", "x1", 0), ("and", "w0", "w1", -1),
    ("lsl", "w0", "w1", 32), ("asr", "x0", "x1", 64),
    ("lsr", "x0", "x1", -1), ("lsl", "x0", "w1", 1),
    ("and", "x0", " SP ", 1), ("unknown", "x0", "x1", 1),
])
def test_immediate_invalid_forms_fail_closed(args):
    with pytest.raises(EncodeError):
        encode_producer_immediate(*args)


@pytest.mark.parametrize("mnemonic, destination, source", [
    ("sxtb", "x0", "x1"), ("sxth", "x30", "xzr"),
    ("fmov", "d31", "d0"), ("fmov", "s0", "s31"),
    ("fmov", "wzr", "s31"), ("fmov", "d31", "xzr"),
    ("scvtf", "d31", "xzr"), ("ucvtf", "s31", "wzr"),
    ("fcvtzs", "xzr", "d31"), ("fcvtzu", "wzr", "s31"),
])
def test_alias_and_register_extremes_match_text(mnemonic, destination, source):
    # x,x sign-extension spellings are the existing producer compatibility
    # domain. They encode the same low-register field as the ISA's w source.
    word = encode_producer_registers(mnemonic, destination, source)
    text = "  " + mnemonic + " " + destination + ", " + source + "\n"
    assert assemble_text(text).code == word.to_bytes(4, "little")


@pytest.mark.parametrize("mnemonic, value", [
    ("mov", 0), ("mov", 0x8000000000000000),
    ("and", 0x8000000000000000), ("and", 0xFFFFFFFFFFFFFFFE),
])
def test_high_bit_immediate_patterns_remain_exact(mnemonic, value):
    left = "" if mnemonic == "mov" else "x1"
    word = encode_producer_immediate(mnemonic, "x0", left, value)
    text = "  " + mnemonic + " x0, " + (left + ", " if left else "") + "#" + hex(value) + "\n"
    assert assemble_text(text).code == word.to_bytes(4, "little")


@pytest.mark.parametrize("args, word", [
    (("clz", "x9", "x10"), 0xDAC01149),
    (("rbit", "w9", "w10"), 0x5AC00149),
    (("rev", "x9", "x10"), 0xDAC00D49),
    (("rev16", "w9", "w10"), 0x5AC00549),
    (("smulh", "x9", "x10", "x11"), 0x9B4B7D49),
    (("umulh", "x9", "x10", "x11"), 0x9BCB7D49),
    (("smull", "x9", "w10", "w11"), 0x9B2B7D49),
    (("umull", "x9", "w10", "w11"), 0x9BAB7D49),
    (("uxtw", "x9", "w10"), 0xD3407D49),
    (("mvn", "x9", "x10"), 0xAA2A03E9),
    (("adds", "x9", "x10", "x11"), 0xAB0B0149),
    (("subs", "w9", "w10", "w11"), 0x6B0B0149),
    (("tst", "x9", "x10"), 0xEA0A013F),
    (("cneg", "x9", "x10", "eq"), 0xDA8A1549),
    (("csinv", "w9", "w10", "w11", "ne"), 0x5A8B1149),
    (("fabs", "d9", "d10"), 0x1E60C149),
    (("fsqrt", "s9", "s10"), 0x1E21C149),
    (("frintn", "d9", "d10"), 0x1E644149),
    (("frintp", "s9", "s10"), 0x1E24C149),
    (("frintm", "d9", "d10"), 0x1E654149),
    (("frintz", "s9", "s10"), 0x1E25C149),
])
def test_extended_scalar_literals(args, word):
    assert encode_producer_registers(*args) == word
    text = "  " + args[0] + " " + ", ".join(args[1:]) + "\n"
    assert assemble_text(text).code == word.to_bytes(4, "little")


@pytest.mark.parametrize("encoder,args,text,word", [
    (encode_producer_atomic, ("casal", "x9", "sp", "x10"), "casal x9, x10, [sp]", 0xC8E9FFEA),
    (encode_producer_atomic, ("ldaddalb", "w9", "x11", "w10"), "ldaddalb w9, w10, [x11]", 0x38E9016A),
    (encode_producer_atomic, ("ldaxr", "x9", "x10"), "ldaxr x9, [x10]", 0xC85FFD49),
    (encode_producer_atomic, ("stlxrh", "w9", "x11", "w10"), "stlxrh w9, w10, [x11]", 0x4809FD6A),
    (encode_producer_system, ("mrs", "x9", "nzcv"), "mrs x9, nzcv", 0xD53B4209),
    (encode_producer_system, ("msr", "x9", "tpidr_el0"), "msr tpidr_el0, x9", 0xD51BD049),
    (encode_producer_indirect_branch, ("blr", "x12"), "blr x12", 0xD63F0180),
    (encode_producer_pair, ("stp", "x9", "x10", "sp", -512), "stp x9, x10, [sp, #-512]", 0xA9202BE9),
    (encode_producer_pair, ("ldp", "w9", "w10", "x11", 252), "ldp w9, w10, [x11, #252]", 0x295FA969),
    (encode_producer_fp_immediate, ("d9", 0x70), "fmov d9, #1.0", 0x1E6E1009),
    (encode_producer_fp_immediate, ("s31", 0), "fmov s31, #2.0", 0x1E20101F),
    (encode_producer_vector, ("cnt", 31, 0), "cnt v31.8b, v0.8b", 0x0E20581F),
    (encode_producer_vector, ("addv", 0, 31), "addv b0, v31.8b", 0x0E31BBE0),
    (encode_producer_vector, ("umov", 30, 31), "umov w30, v31.b[0]", 0x0E013FFE),
])
def test_new_helpers_literals_and_reference(encoder, args, text, word):
    assert encoder(*args) == word
    assert assemble_text("  " + text + "\n").code == word.to_bytes(4, "little")


@pytest.mark.parametrize("operation", ["ldadd", "ldclr", "ldeor", "ldset", "swp", "cas"])
@pytest.mark.parametrize("ordering", ["", "a", "l", "al"])
@pytest.mark.parametrize("suffix,registers", [("b", "w"), ("h", "w"), ("", "w"), ("", "x")])
def test_all_lse_opcode_ordering_size_combinations(operation, ordering, suffix, registers):
    mnemonic = operation + ordering + suffix
    first, second = registers + "9", registers + "10"
    word = encode_producer_atomic(mnemonic, first, "sp", second)
    assert assemble_text("  " + mnemonic + " " + first + ", " + second + ", [sp]\n").code == word.to_bytes(4, "little")


@pytest.mark.parametrize("encoder,args", [
    (encode_producer_registers, ("smull", "x0", "x1", "w2")),
    (encode_producer_registers, ("umulh", "w0", "w1", "w2")),
    (encode_producer_registers, ("uxtw", "x0", "x1")),
    (encode_producer_registers, ("cneg", "x0", "x1", "bad")),
    (encode_producer_registers, ("fabs", "d0", "s1")),
    (encode_producer_registers, ("orr", "w0", "w1", "w2", "", 32)),
    (encode_producer_atomic, ("casab", "x0", "sp", "x1")),
    (encode_producer_atomic, ("ldadd", "w0", "sp", "x1")),
    (encode_producer_atomic, ("ldxr", "x0", "xzr")),
    (encode_producer_atomic, ("ldxr", "x0", "w1")),
    (encode_producer_atomic, ("ldxr", "sp", "x1")),
    (encode_producer_atomic, ("ldxr", "x0", "x1", "x2")),
    (encode_producer_atomic, ("stlxr", "x0", "x1", "x2")),
    (encode_producer_atomic, ("stxrb", "w0", "x1", "x2")),
    (encode_producer_atomic, ("ldaddla", "w0", "x1", "w2")),
    (encode_producer_system, ("mrs", "sp", "nzcv")),
    (encode_producer_system, ("mrs", "w0", "nzcv")),
    (encode_producer_system, ("msr", "x0", "unknown")),
    (encode_producer_indirect_branch, ("blr", "w0")),
    (encode_producer_indirect_branch, ("br", "sp")),
    (encode_producer_pair, ("stp", "x0", "w1", "sp", 0)),
    (encode_producer_pair, ("stp", "x0", "x1", "sp", 512)),
    (encode_producer_pair, ("ldp", "w0", "w1", "sp", -260)),
    (encode_producer_pair, ("stp", "x0", "x1", "sp", 4)),
    (encode_producer_fp_immediate, ("d0", 1)),
    (encode_producer_fp_immediate, ("x0", 0)),
    (encode_producer_vector, ("cnt", 32, 0)),
    (encode_producer_vector, ("umov", 31, 0)),
    (encode_producer_vector, ("movi", 0, 1)),
    (encode_producer_vector, ("unknown", 0, 0)),
])
def test_new_helpers_reject_invalid_shapes(encoder, args):
    with pytest.raises(EncodeError):
        encoder(*args)


def test_movi_and_br_literal_words_without_text_oracle():
    # No old text-encoder branch exists. Arm MOVI: Q=1, op=0, cmode=1110,
    # imm8=0, Rd in bits4:0 => 0x4f00e400 | Rd. BR: 0xd61f0000 | Rn<<5.
    assert encode_producer_vector("movi", 0) == 0x4F00E400
    assert encode_producer_vector("movi", 31) == 0x4F00E41F
    assert encode_producer_indirect_branch("br", "x0") == 0xD61F0000
    assert encode_producer_indirect_branch("br", "x30") == 0xD61F03C0


def test_shifted_orr_literal_and_reference():
    assert encode_producer_registers("orr", "x9", "x10", "x11", "", 63) == 0xAA0BFD49
    assert assemble_text("  orr x9, x10, x11, lsl #63\n").code == bytes.fromhex("49fd0baa")


@pytest.mark.parametrize("mnemonic", [
    "ldar", "ldxr", "ldaxr", "stlr", "ldarb", "ldarh", "ldxrb", "ldxrh",
    "ldaxrb", "ldaxrh", "stlrb", "stlrh", "stxr", "stlxr", "stxrb", "stxrh", "stlxrb", "stlxrh",
])
@pytest.mark.parametrize("wide", [False, True])
def test_exclusive_and_acquire_release_domains(mnemonic, wide):
    narrow = mnemonic.endswith(("b", "h"))
    register = "x10" if wide else "w10"
    store_exclusive = mnemonic.startswith(("stxr", "stlxr"))
    args = (mnemonic, "w9", "sp", register) if store_exclusive else (mnemonic, register, "sp")
    if narrow and wide:
        with pytest.raises(EncodeError):
            encode_producer_atomic(*args)
        return
    word = encode_producer_atomic(*args)
    text = "  " + mnemonic + " " + ("w9, " if store_exclusive else "") + register + ", [sp]\n"
    assert assemble_text(text).code == word.to_bytes(4, "little")
