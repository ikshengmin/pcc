"""x86-64: computed values land in their register homes without a scratch copy.

The instruction selector computes a load, extension or address into r10/r11
and copies it to the value's register. retarget_scratch_definitions writes
the copy's destination directly when the definition is a full 64-bit write
that reads its source first (mov, movzx, movsx, movsxd, lea; Intel SDM
Vol. 2) and nothing reads the scratch register again before it is
rewritten in the same block; labels, jumps and unmodeled instructions keep
the copy. A getelementptr with constant indices is one lea of its total
byte offset. Real-machine execution is the docker differential in
tests/integration/test_self_backend_x86_64_linux.py.
"""

import re

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


def test_constant_gep_is_one_lea_and_loads_land_in_place():
    asm = emit_x86_64_linux_asm(_PROGRAM)
    body = asm.split("field:", 1)[1]
    # %pair is 24 bytes: element 1, field 2 (offset 16), element 3 = 46.
    assert re.search(r"^  lea r11, \[\w+ \+ 46\]$", body, re.M), body
    assert not re.search(r"^  lea r11, \[r11 \+ \d+\]$", body, re.M), body
    assert not re.search(r"^  mov r(?:bx|12|13|14|15), r1[01]$", body, re.M), body
