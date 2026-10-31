"""AArch64 constants and addresses take the short instruction forms.

Constants (Arm ARM C6.2 MOVN/MOVZ/MOVK): a value with more 0xFFFF chunks
than nonzero chunks is built with MOVN, as assemblers select ``mov #imm``;
only when the register is exactly the constant's width, because MOVN sets
every bit outside its chunk (an i32 in an X register keeps a zero upper
half). Expected words were cross-checked once with llvm-mc.

Addresses: a getelementptr with constant indices is one add/sub of its
total byte offset, and one 64-bit register index over a power-of-two element
is one add with a shifted register, both between the allocated homes of the
base, index and result. The program is emitted by the production object
path, linked by pcc's owned linker and executed.
"""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.arm64_encode import assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.backend.self_backend_aarch64_darwin_regs import emit_const_to_reg_bits


@pytest.mark.parametrize("instruction, encoding", [
    ("movn x0, #0", "00008092"),
    ("movn x0, #1", "20008092"),
    ("movn w0, #0", "00008012"),
    ("movn x9, #1, lsl #16", "2900a092"),
    ("movn w9, #0, lsl #16", "0900a012"),
])
def test_movn_words_match_reference(instruction, encoding):
    assert assemble_text(instruction).code == bytes.fromhex(encoding)


@pytest.mark.parametrize("bits, reg, value, expected", [
    (64, "x0", -1, ["  movn x0, #0"]),
    (64, "x0", -2, ["  movn x0, #1, lsl #0"]),
    (64, "x0", -65536, ["  movn x0, #65535, lsl #0"]),
    (64, "x9", -0x12340001, ["  movn x9, #4660, lsl #16"]),
    (64, "x9", -0x12340002, ["  movn x9, #1, lsl #0", "  movk x9, #60875, lsl #16"]),
    (32, "w0", -1, ["  movn w0, #0"]),
    (32, "w0", -16, ["  movn w0, #15, lsl #0"]),
    # Not the register width: the zeroing MOVZ form keeps the upper half.
    (32, "x0", -1, ["  movz x0, #65535, lsl #0", "  movk x0, #65535, lsl #16"]),
    (64, "x0", 0, ["  movz x0, #0"]),
    (64, "x0", 0x12345678, ["  movz x0, #22136, lsl #0", "  movk x0, #4660, lsl #16"]),
    (64, "x0", -0x100000000, ["  movz x0, #65535, lsl #32", "  movk x0, #65535, lsl #48"]),
])
def test_constant_sequence_choice(bits, reg, value, expected):
    assert emit_const_to_reg_bits(bits, reg, value) == expected


_PROGRAM = '''target triple = "arm64-apple-darwin"
%pair = type { i32, i64, [4 x i16] }

define i64 @minus_one() {
entry:
  ret i64 -1
}

define i32 @minus_sixteen() {
entry:
  ret i32 -16
}

define i64 @field(ptr %record) {
entry:
  %slot = getelementptr %pair, ptr %record, i64 1, i32 2, i64 3
  %value = load i16, ptr %slot, align 2
  %wide = zext i16 %value to i64
  %head = getelementptr %pair, ptr %record, i64 0, i32 1
  %first = load i64, ptr %head, align 8
  %sum = add i64 %wide, %first
  ret i64 %sum
}

define i64 @element(ptr %base, i64 %index) {
entry:
  %slot = getelementptr i64, ptr %base, i64 %index
  %value = load i64, ptr %slot, align 8
  %next = add i64 %index, 1
  %slot2 = getelementptr i64, ptr %base, i64 %next
  %value2 = load i64, ptr %slot2, align 8
  %sum = add i64 %value, %value2
  ret i64 %sum
}

define i32 @main() {
entry:
  %records = alloca [2 x %pair], align 8
  %head = getelementptr %pair, ptr %records, i64 0, i32 1
  store i64 1000, ptr %head, align 8
  %tail = getelementptr %pair, ptr %records, i64 1, i32 2, i64 3
  store i16 -2, ptr %tail, align 2
  %f = call i64 @field(ptr %records)
  %ok1 = icmp eq i64 %f, 66534
  %array = alloca [8 x i64], align 8
  %a3 = getelementptr [8 x i64], ptr %array, i64 0, i64 3
  store i64 40, ptr %a3, align 8
  %a4 = getelementptr [8 x i64], ptr %array, i64 0, i64 4
  store i64 2, ptr %a4, align 8
  %e = call i64 @element(ptr %array, i64 3)
  %ok2 = icmp eq i64 %e, 42
  %m1 = call i64 @minus_one()
  %ok3 = icmp eq i64 %m1, -1
  %m16 = call i32 @minus_sixteen()
  %ok4 = icmp eq i32 %m16, -16
  %ok12 = and i1 %ok1, %ok2
  %ok34 = and i1 %ok3, %ok4
  %all = and i1 %ok12, %ok34
  br i1 %all, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 3
}
'''


def _body(text, name):
    return text.split("_" + name + ":", 1)[1].split("\n_", 1)[0]


@pytest.mark.parametrize("optimize", [False, True])
def test_short_forms_are_emitted(optimize):
    asm = emit_aarch64_darwin_asm(_PROGRAM, optimize=optimize)
    assert re.search(r"^  movn x0, #0$", _body(asm, "minus_one"), re.M), _body(asm, "minus_one")
    assert re.search(r"^  movn w0, #15, lsl #0$", _body(asm, "minus_sixteen"), re.M)
    field = _body(asm, "field")
    # %pair is 24 bytes (i32, pad, i64 at 8, [4 x i16] at 16); element 1,
    # field 2, element 3 is 24 + 16 + 6.
    assert re.search(r"^  add x\d+, x\d+, #46$", field, re.M), field
    assert re.search(r"^  add x\d+, x\d+, #8$", field, re.M), field
    element = _body(asm, "element")
    assert re.search(r"^  add x\d+, x\d+, x\d+, lsl #3$", element, re.M), element


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")
def test_short_forms_program_runs(tmp_path):
    data = emit_owned_object(_PROGRAM, "arm64-apple-darwin")
    executable = tmp_path / "short-forms"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
