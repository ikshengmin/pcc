"""Windows x64 target entry over the shared owned x86 selector."""

from .self_backend_x86_64_linux import _emit_x86_64_module


def emit_x86_64_windows_asm(ir_text: str) -> str:
    return _emit_x86_64_module(ir_text, windows=True)


def stack_probe_assembly() -> str:
    # Preserve argument registers and RAX. Touch every guard page without
    # moving the caller's stack pointer; the caller makes one unwind-described
    # allocation after returning. The helper is a leaf and changes no
    # nonvolatile registers.
    return """.intel_syntax noprefix
.text
.globl __pcc_chkstk
.type __pcc_chkstk, @function
__pcc_chkstk:
  mov r10, rsp
  add r10, 8
  mov r11, rax
.Lpcc_probe_loop:
  cmp r11, 4096
  jb .Lpcc_probe_tail
  sub r10, 4096
  test BYTE PTR [r10], al
  sub r11, 4096
  jmp .Lpcc_probe_loop
.Lpcc_probe_tail:
  sub r10, r11
  test BYTE PTR [r10], al
  ret
.size __pcc_chkstk, .-__pcc_chkstk
"""
