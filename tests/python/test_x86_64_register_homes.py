"""x86-64: computed values land in their register homes without a scratch copy.

The instruction selector computes a load, extension or address into r10/r11
and copies it to the value's register. retarget_scratch_definitions writes
the copy's destination directly when the definition is a full 64-bit write
that reads its source first (mov, movzx, movsx, movsxd, lea; Intel SDM
Vol. 2) and nothing reads the scratch register again before it is
rewritten in the same block; labels, jumps and unmodeled instructions keep
the copy. A getelementptr with constant indices is one lea of its total
byte offset while its source is live. Last reads must retain the GC
pointer-retirement side effect. Both forms execute as owned native images.
"""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.x86_64_register_copies import retarget_scratch_definitions


@pytest.mark.parametrize("lines, expected", [
    (["  mov r10, QWORD PTR [r11]", "  mov rbx, r10", "  mov r10, r12", "  ret"],
     ["  mov rbx, QWORD PTR [r11]", "  mov r10, r12", "  ret"]),
    (["  movzx r10, WORD PTR [r11]", "  mov r12, r10", "  ret"],
     ["  movzx r12, WORD PTR [r11]", "  ret"]),
    (["  lea r11, [rbx + rcx*8]", "  mov rbx, r11", "  xor r11, r11", "  ret"],
     ["  lea rbx, [rbx + rcx*8]", "  xor r11, r11", "  ret"]),
    (["  movsxd r10, DWORD PTR [rdi]", "  mov rax, r10", "  ret"],
     ["  movsxd rax, DWORD PTR [rdi]", "  ret"]),
])
def test_scratch_definition_is_retargeted(lines, expected):
    assert retarget_scratch_definitions(lines) == expected


@pytest.mark.parametrize("lines", [
    # The scratch register is read again.
    ["  mov r10, QWORD PTR [r11]", "  mov rbx, r10", "  add r12, r10", "  ret"],
    # A label or a jump ends the block: conservatively live.
    ["  mov r10, QWORD PTR [r11]", "  mov rbx, r10", ".Lnext:", "  mov r10, 1"],
    ["  mov r10, QWORD PTR [r11]", "  mov rbx, r10", "  jmp .Lnext"],
    # A narrower copy or a frame register is not a full retarget.
    ["  mov r10, QWORD PTR [r11]", "  mov ebx, r10d", "  ret"],
    ["  mov r10, QWORD PTR [r11]", "  mov rsp, r10", "  ret"],
    # Not one of the pure definitions (add reads its destination).
    ["  add r10, rbx", "  mov rcx, r10", "  ret"],
    # A partial write keeps the copy.
    ["  mov r10w, WORD PTR [r11]", "  mov rbx, r10", "  ret"],
])
def test_scratch_definition_is_kept(lines):
    assert retarget_scratch_definitions(lines) == lines


_PROGRAM = '''target triple = "x86_64-unknown-linux-gnu"
%pair = type { i32, i64, [4 x i16] }
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
'''


# Keep the input pointer live across both GEPs. The last ordinary load
# is the separate lifetime boundary at which the pointer home is retired.
_LIVE_PROGRAM = _PROGRAM.replace(
    "  ret i64 %sum",
    "  %last = load i32, ptr %record, align 4\n"
    "  %lastwide = zext i32 %last to i64\n"
    "  %result = add i64 %sum, %lastwide\n"
    "  ret i64 %result",
)


def test_constant_gep_is_one_lea_and_loads_land_in_place():
    body = emit_x86_64_linux_asm(_LIVE_PROGRAM).split("field:", 1)[1]
    # The live pointer can be forwarded for both GEPs; neither crosses
    # a lifetime marker. Preserve the original optimization assertions
    # over that region, not over the unrelated retirement sequence.
    before_retirement = body.split("  mov r11, rbx\n  mov ebx, 0", 1)[0]
    assert re.findall(r"^  lea r11, \[rbx \+ (\d+)\]$", before_retirement, re.M) == ["46", "8"], body
    assert not re.search(r"^  lea r11, \[r11 \+ \d+\]$", before_retirement, re.M), body
    # The load and extension must land directly in their homes. The
    # later arithmetic result is not a load or extension.
    assert re.search(r"^  movzx r(?:bx|12|13|14|15), WORD PTR ", before_retirement, re.M), body
    assert re.search(r"^  lea r11, \[rbx \+ 8\]\n  mov r(?:bx|12|13|14|15), QWORD PTR \[r11\]$", before_retirement, re.M), body
    assert before_retirement != body, body


def test_constant_gep_last_read_preserves_pointer_retirement():
    body = emit_x86_64_linux_asm(_PROGRAM).split("field:", 1)[1]
    assert re.search(r"^  lea r11, \[rbx \+ 46\]$", body, re.M), body
    assert body.count("  lea r11, ") == 2, body
    # The source must be copied before retirement and cannot be used
    # directly afterwards. The pointer-home clear is a GC side effect.
    assert "  mov r11, rbx\n  mov ebx, 0\n  lea r11, [r11 + 8]\n" in body, body


@pytest.mark.pcc_gate(unavailable=(
    None if sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64")
    else "requires native Linux x86-64 execution"
))
@pytest.mark.parametrize("program, expected", [(_PROGRAM, 42), (_LIVE_PROGRAM, 49)])
def test_constant_gep_executes_owned_image(tmp_path, program, expected):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  lea rdi, .Lrecord[rip]
  call field
  mov rdi, rax
  mov rax, 60
  syscall
.section .rodata
.p2align 3
.Lrecord:
  .long 7
  .long 0
  .quad 13
  .zero 30
  .short 29
"""
    exe = tmp_path / "field"
    exe.write_bytes(link_static_executable([
        assemble_file(start), assemble_file(emit_x86_64_linux_asm(program)),
    ]))
    exe.chmod(0o755)
    result = subprocess.run([str(exe)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (expected, b"", b"")
