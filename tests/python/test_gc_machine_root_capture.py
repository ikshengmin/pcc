"""GC stack snapshots: AAPCS64 6.1.1 and AMD64 ABI 3.2.1 preserved GPRs."""
from __future__ import annotations

import os
import platform
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.native_object import decode_native_object
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.target_objects import encode_assembly_object
from pcc.frontends.python.pipeline import compile_python
from tests.python.test_darwin_tls_leaf_protection import emit
from tests.python.test_owned_external_tls import _c_ir


TARGETS = ("arm64-apple-darwin", "aarch64-unknown-linux-gnu",
           "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc")
SOURCE = '''from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    capture_gc_registers, frame_address, load_i64, memcpy, null,
    ptr_add, ptr_diff, store_i64,
)
__pcc_freestanding__ = True
@c_abi_export("capture_roots")
def capture_roots(buffer: c_ptr) -> c_ptr:
    stack = capture_gc_registers(buffer)
    if load_i64(buffer, 128) != -37:
        return null()
    size = ptr_diff(frame_address(), stack) + 16
    if size <= 0 or size > 4096:
        return null()
    store_i64(buffer, 128, size)
    memcpy(ptr_add(buffer, 136), stack, size)
    return stack
'''


def capture_ir(tmp_path, target):
    source = tmp_path / "capture.py"
    source.write_text(SOURCE)
    output = tmp_path / "capture.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   python_library=True, backend="self", target_triple=target)
    return output.read_text()


@pytest.mark.parametrize("target", TARGETS)
def test_capture_initializes_every_word_and_preserves_machine_roots(tmp_path, target):
    text = capture_ir(tmp_path, target)
    assert "@llvm.pcc.capture.gc.registers(" in text
    assembly = emit_self_asm(text, target)
    assert "llvm.pcc.capture.gc.registers" not in assembly
    if target.startswith(("arm64", "aarch64")):
        for first in range(19, 31, 2):
            assert f"stp x{first}, x{first + 1}, [x9, #{(first - 19) * 8}]" in assembly
        assert "stp x10, xzr, [x9, #96]" in assembly
        assert "stp xzr, xzr, [x9, #112]" in assembly
    else:
        for index, register in enumerate(("rbx", "rbp", "r12", "r13", "r14", "r15", "rdi", "rsi", "rsp")):
            assert f"mov QWORD PTR [r11 + {index * 8}], {register}" in assembly
        assert "mov QWORD PTR [r11 + 120], 0" in assembly
    assert emit_owned_object(text, target)


def sentinel_trampoline():
    lines = [".section __TEXT,__text,regular,pure_instructions", ".p2align 2",
             ".globl _capture_sentinels", "_capture_sentinels:",
             "  stp x29, x30, [sp, #-96]!", "  mov x29, sp"]
    for first in range(19, 29, 2):
        lines.append(f"  stp x{first}, x{first + 1}, [sp, #{16 + (first - 19) * 8}]")
    for index in range(10):
        lines.append(f"  mov x{19 + index}, #{257 + index}")
    lines.append("  bl _capture_roots")
    for first in range(19, 29, 2):
        lines.append(f"  ldp x{first}, x{first + 1}, [sp, #{16 + (first - 19) * 8}]")
    lines.extend(["  ldp x29, x30, [sp], #96", "  ret"])
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_capture_executes_with_live_callee_registers_and_buffer_canaries(
    tmp_path, monkeypatch, indexed, registers,
):
    from pcc.backend.macho_exec import link_executable

    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    target = TARGETS[0]
    library = emit(capture_ir(tmp_path, target), indexed, tmp_path)
    trampoline = decode_native_object(encode_assembly_object(sentinel_trampoline(), target))
    driver = emit_owned_object(_c_ir('''extern void *capture_sentinels(void *);
int main(void) {
    long words[1026];
    for (int i = 0; i < 1026; ++i) words[i] = -37;
    long stack = (long)capture_sentinels(words + 1);
    if (words[0] != -37 || words[1025] != -37) return 1;
    if (words[17] <= 0 || words[17] > 4096) return 15;
    for (int i = 0; i < 10; ++i) {
        int found = 0;
        for (int j = 1; j < 17; ++j) if (words[j] == 257 + i) found = 1;
        for (int j = 18; j < 18 + words[17] / 8; ++j)
            if (words[j] == 257 + i) found = 1;
        if (!found) return 2 + i;
    }
    if (stack == 0 || (stack & 15) != 0 || stack >= (long)words) return 12;
    if (words[13] != stack) return 13;
    for (int i = 14; i <= 16; ++i) if (words[i] != 0) return 14;
    return 0;
}
''', target), target)
    output = tmp_path / "capture"
    output.write_bytes(link_executable([library, trampoline, driver]))
    output.chmod(0o755)
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=15,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_capture_requires_native_execution():
    from pcc import unsafe

    with pytest.raises(Exception):
        unsafe.capture_gc_registers(None)
    assert "capture_gc_registers" in unsafe.__all__
