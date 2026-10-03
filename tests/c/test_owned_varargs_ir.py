"""Execute SysV variadic GP-register and overflow arguments with owned ELF."""

import platform
import subprocess
import sys
from unittest.mock import patch

import pytest

from pcc.backend.elf_x86_64 import link_static_executable
from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.x86_64_asm_driver import assemble_file


_HOST = sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
_native = pytest.mark.pcc_gate(unavailable=None if _HOST else "needs Linux x86_64 execution")
_START = """.intel_syntax noprefix
.text
.globl _start
_start:
  call probe
  mov edi, eax
  mov eax, 60
  syscall
"""


@_native
@pytest.mark.parametrize("count", [1, 5, 6, 12])
def test_owned_sysv_varargs_crosses_register_save_area_into_stack(tmp_path, count):
    lines = [
        'target triple = "x86_64-unknown-linux-gnu"',
        'declare void @llvm.va_start.p0(ptr)',
        'declare void @llvm.va_end.p0(ptr)',
        'define i32 @collect(i32 %tag, ...) {',
        'entry:',
        '  %ap = alloca { i32, i32, ptr, ptr }, align 8',
        '  call void @llvm.va_start.p0(ptr %ap)',
    ]
    previous = "0"
    for index in range(count):
        lines.append(f"  %value{index} = va_arg ptr %ap, i32")
        lines.append(f"  %sum{index} = add i32 {previous}, %value{index}")
        previous = f"%sum{index}"
    lines.extend(['  call void @llvm.va_end.p0(ptr %ap)',
                  f'  ret i32 {previous}', '}', 'define i32 @probe() {', 'entry:'])
    arguments = ", ".join("i32 " + str(value) for value in range(1, count + 1))
    expected = count * (count + 1) // 2
    lines.extend([
        f"  %actual = call i32 (i32, ...) @collect(i32 0, {arguments})",
        f"  %difference = sub i32 %actual, {expected}",
        "  ret i32 %difference", "}",
    ])
    ir = "\n".join(lines) + "\n"
    (tmp_path / "program.ll").write_text(ir, encoding="utf-8")
    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        assembly = emit_x86_64_linux_asm(ir)
        (tmp_path / "program.s").write_text(assembly, encoding="utf-8")
        image = link_static_executable([assemble_file(assembly), assemble_file(_START)])
    executable = tmp_path / "program"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
