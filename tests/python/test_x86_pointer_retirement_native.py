"""Machine-dead pointer cells stop contributing false conservative roots.

This proves only allocator-owned SSA storage. Returned helper frames and
addressable scratch lifetime are separate contracts, not inferred here.
"""
import platform
import subprocess
import sys

import pytest

from pcc.backend.self_backend_dispatch import emit_self_asm


@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
)
def test_native_retirement_preserves_live_interior_argument_and_callee_saves(tmp_path):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    source = '''target triple = "x86_64-unknown-linux-gnu"
declare void @consume(ptr)
declare i64 @observe(ptr)
define i64 @probe(ptr %dead, ptr %interior) {
entry:
  call void @consume(ptr %dead)
  %result = call i64 @observe(ptr %interior)
  ret i64 %result
}
'''
    assembly = emit_self_asm(source, "x86_64-unknown-linux-gnu")
    assert "pcc_retire_" not in assembly
    control = '''.intel_syntax noprefix
.data
.p2align 3
payload:
  .quad 0
  .byte 0, 77
.text
.globl consume
.type consume, @function
consume:
  cmp rdi, 8192
  jne .Lbad
  ret
.size consume, .-consume
.globl observe
.type observe, @function
observe:
  cmp rbx, 8192
  je .Lbad
  cmp r12, 8192
  je .Lbad
  cmp r13, 8192
  je .Lbad
  cmp r14, 8192
  je .Lbad
  cmp r15, 8192
  je .Lbad
  lea rax, payload[rip]
  add rax, 9
  cmp rdi, rax
  jne .Lbad
  movzx eax, BYTE PTR [rdi]
  cmp eax, 77
  jne .Lbad
  mov eax, 123
  ret
.size observe, .-observe
.globl _start
.type _start, @function
_start:
  mov rbx, 257
  mov r12, 258
  mov r13, 259
  mov r14, 260
  mov r15, 261
  mov edi, 8192
  lea rsi, payload[rip]
  add rsi, 9
  call probe
  cmp rax, 123
  jne .Lbad
  cmp rbx, 257
  jne .Lbad
  cmp r12, 258
  jne .Lbad
  cmp r13, 259
  jne .Lbad
  cmp r14, 260
  jne .Lbad
  cmp r15, 261
  jne .Lbad
  xor edi, edi
  jmp .Lexit
.Lbad:
  mov edi, 1
.Lexit:
  mov eax, 60
  syscall
.size _start, .-_start
'''
    image = link_static_executable([assemble_file(control), assemble_file(assembly)])
    executable = tmp_path / "retired-pointer"
    executable.write_bytes(image)
    executable.chmod(0o755)
    completed = subprocess.run([str(executable)], capture_output=True, timeout=10,
                               env={"PATH": ""})
    assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"", b"")


@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
)
def test_native_promoted_i64_homes_clear_poison_without_losing_raw_address(tmp_path):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    # The two i64 inputs have distinct eight-byte local homes. Both actual
    # values must survive observe, including a raw address carried as an int.
    # Observe sees this activation's abandoned homes, not a returned frame.
    source = '''target triple = "x86_64-unknown-linux-gnu"
declare void @observe(i64, i64)
define i64 @probe(i64 %bits, i64 %delta) {
entry:
  call void @observe(i64 %bits, i64 %delta)
  %address = inttoptr i64 %bits to ptr
  %value = load i64, ptr %address
  %result = add i64 %value, %delta
  ret i64 %result
}
'''
    assembly = emit_self_asm(source, "x86_64-unknown-linux-gnu")
    for offset in (8, 16):
        accesses = [line for line in assembly.splitlines() if f"[rbp - {offset}]" in line]
        assert accesses == [f"  mov QWORD PTR [rbp - {offset}], 0"]
    control = '''.intel_syntax noprefix
.data
.p2align 3
payload:
  .quad 77
.text
.globl observe
.type observe, @function
observe:
  mov rax, QWORD PTR [rbp - 8]
  cmp rax, 0
  jne .Lbad
  mov rax, QWORD PTR [rbp - 16]
  cmp rax, 0
  jne .Lbad
  lea rax, payload[rip]
  cmp rdi, rax
  jne .Lbad
  cmp rsi, 5
  jne .Lbad
  xor eax, eax
  xor ecx, ecx
  xor edx, edx
  xor edi, edi
  xor esi, esi
  xor r8d, r8d
  xor r9d, r9d
  xor r10d, r10d
  xor r11d, r11d
  ret
.size observe, .-observe
.globl _start
.type _start, @function
_start:
  mov rbx, 257
  mov r12, 258
  mov r13, 259
  mov r14, 260
  mov r15, 261
  lea rdi, payload[rip]
  mov esi, 5
  mov QWORD PTR [rsp - 24], rdi
  mov QWORD PTR [rsp - 32], rdi
  call probe
  cmp rax, 82
  jne .Lbad
  cmp rbx, 257
  jne .Lbad
  cmp r12, 258
  jne .Lbad
  cmp r13, 259
  jne .Lbad
  cmp r14, 260
  jne .Lbad
  cmp r15, 261
  jne .Lbad
  xor edi, edi
  jmp .Lexit
.Lbad:
  mov edi, 1
.Lexit:
  mov eax, 60
  syscall
.size _start, .-_start
'''
    image = link_static_executable([assemble_file(control), assemble_file(assembly)])
    executable = tmp_path / "promoted-i64-homes"
    executable.write_bytes(image)
    executable.chmod(0o755)
    completed = subprocess.run([str(executable)], capture_output=True, timeout=10,
                               env={"PATH": ""})
    assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"", b"")
