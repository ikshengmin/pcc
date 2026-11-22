"""System-assembler differentials for the owned x86_64 emitter encoder."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.backend.x86_64_encode import X86EncodeError, encode_instruction


_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_X86_LINUX = (
    os.sys.platform.startswith("linux")
    and platform.machine().lower() in {"x86_64", "amd64", "x64"}
)
_GATE = None if (_CC and _IS_X86_LINUX) else "needs cc and Linux x86_64"
_X86_GATE = pytest.mark.pcc_gate(unavailable=_GATE)


@pytest.mark.parametrize("instruction, expected", [
    ("mov BYTE PTR [r11], 255", "41c603ff"),
    ("mov WORD PTR [r11], 4660", "6641c7033412"),
    ("mov DWORD PTR [r11], 24", "41c70318000000"),
    ("mov QWORD PTR [r11], -1", "49c703ffffffff"),
])
def test_memory_immediate_mov_encoding(instruction, expected):
    encoded = encode_instruction(instruction, pc=0, labels={}, section_name=".text")
    assert encoded.code.hex() == expected
    assert not encoded.relocations


def test_memory_immediate_mov_rip_relocation_accounts_for_immediate():
    encoded = encode_instruction("mov DWORD PTR value[rip], 24", pc=0, labels={}, section_name=".text")
    assert encoded.code.hex() == "c7050000000018000000"
    assert len(encoded.relocations) == 1
    relocation = encoded.relocations[0]
    assert relocation.offset == 2 and relocation.symbol == "value" and relocation.addend == -8


def test_memory_immediate_mov_rejects_unencodable_64bit_constant():
    with pytest.raises(X86EncodeError, match="immediate"):
        encode_instruction("mov QWORD PTR [r11], 2147483648", pc=0, labels={}, section_name=".text")


def test_cpuid_takes_no_explicit_operands():
    bare = encode_instruction("cpuid", pc=37, labels={}, section_name=".text")
    assert bare.code == bytes.fromhex("0fa2") and not bare.relocations
    with pytest.raises(X86EncodeError, match="cpuid takes no operands"):
        encode_instruction("cpuid eax", pc=0, labels={}, section_name=".text")


def test_xgetbv_takes_no_explicit_operands():
    bare = encode_instruction("xgetbv", pc=91, labels={}, section_name=".text")
    assert bare.code == bytes.fromhex("0f01d0") and not bare.relocations
    with pytest.raises(X86EncodeError, match="xgetbv takes no operands"):
        encode_instruction("xgetbv ecx", pc=0, labels={}, section_name=".text")


def _execute_owned_and_system_assembly(assembly, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    owned, reference, source = (tmp_path / name for name in ("owned", "reference", "same.s"))
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, "Popen", lambda *_a, **_kw: pytest.fail("owned path invoked a tool"))
        owned.write_bytes(link_static_executable([assemble_file(assembly)], entry="_start"))
    owned.chmod(0o700)
    source.write_text(assembly, encoding="utf-8")
    subprocess.run([_CC, "-nostdlib", "-static", "-o", str(reference), str(source)],
                   check=True, capture_output=True, timeout=30)
    got = subprocess.run([str(owned)], check=True, capture_output=True, timeout=5)
    want = subprocess.run([str(reference)], check=True, capture_output=True, timeout=5)
    assert got.stdout == want.stdout and got.stderr == want.stderr == b""
    return got.stdout


@pytest.mark.integration
@_X86_GATE
def test_cpuid_native_owned_registers_match_system_oracle(tmp_path, monkeypatch):
    assembly = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 16
  mov rbx, 0x1122334455667788
  mov r8, rsp
  xor edi, edi
  xor esi, esi
  call probe
  mov rax, 0x1122334455667788
  cmp rbx, rax
  jne fail
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 16
  syscall
  cmp rax, 16
  jne fail
  xor edi, edi
  jmp exit
fail:
  mov edi, 1
exit:
  mov eax, 60
  syscall
probe:
  push rbx
  mov eax, edi
  mov ecx, esi
  cpuid
  mov DWORD PTR [r8], eax
  mov DWORD PTR [r8 + 4], ebx
  mov DWORD PTR [r8 + 8], ecx
  mov DWORD PTR [r8 + 12], edx
  pop rbx
  ret
"""
    assert len(_execute_owned_and_system_assembly(assembly, tmp_path, monkeypatch)) == 16


@pytest.mark.integration
@_X86_GATE
@pytest.mark.parametrize("disable", (False, True))
def test_xgetbv_native_matches_system_oracle_only_under_osxsave(tmp_path, monkeypatch, disable):
    forced_clear = "xor ecx, ecx" if disable else ""
    assembly = f""".intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 16
  mov QWORD PTR [rsp], 0
  mov QWORD PTR [rsp + 8], 0
  mov eax, 1
  xor ecx, ecx
  cpuid
  {forced_clear}
  mov r9d, 0x08000000
  test ecx, r9d
  je output
  xor ecx, ecx
  xgetbv
  mov DWORD PTR [rsp], eax
  mov DWORD PTR [rsp + 4], edx
  mov QWORD PTR [rsp + 8], 1
output:
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 16
  syscall
  mov eax, 60
  xor edi, edi
  syscall
"""
    raw = _execute_owned_and_system_assembly(assembly, tmp_path, monkeypatch)
    assert len(raw) == 16
    enabled, = raw[8:9]
    assert enabled in (0, 1)
    assert not disable or enabled == 0
    assert enabled or raw[:8] == bytes(8)
    (tmp_path / "executed-xgetbv.txt").write_text(str(enabled) + "\n")


def _system_encoding(tmp_path: Path, instruction: str):
    asm_path = tmp_path / "oracle.s"
    obj_path = tmp_path / "oracle.o"
    asm_path.write_text(
        ".intel_syntax noprefix\n"
        ".text\n"
        ".globl probe\n"
        ".type probe, @function\n"
        "probe:\n"
        f"  {instruction}\n"
        ".size probe, .-probe\n"
        '.section .note.GNU-stack,"",@progbits\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [_CC, "-c", str(asm_path), "-o", str(obj_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    obj = parse_relocatable(obj_path.read_bytes())
    text_index = next(
        index for index, section in enumerate(obj.sections)
        if section.name == ".text"
    )
    section = obj.sections[text_index]
    relocations = tuple(
        (
            relocation.offset,
            relocation.type,
            relocation.addend,
            obj.symbols[relocation.symbol_index].name,
        )
        for relocation in section.relocations
    )
    return section.data, relocations


_EMITTER_SHAPES = (
    "push rbp",
    "pop rbp",
    "mov rbp, rsp",
    "mov r10b, r11b",
    "mov r10w, r11w",
    "mov r10d, r11d",
    "mov r10, 0x1122334455667788",
    "mov r10d, 0x81234567",
    "mov r10b, 37",
    "mov r10, QWORD PTR [rbp - 24]",
    "mov QWORD PTR [rsp + 16], r9",
    "mov r10, QWORD PTR [r12]",
    "mov QWORD PTR [r13], r10",
    "movzx r10, r11b",
    "movzx r10d, WORD PTR [r11 + 6]",
    "movsx r10, r10w",
    "movsxd r10, r10d",
    "lea r11, [r11 + r10*8]",
    "lea r10, [rbp - 32]",
    "add r10, r11",
    "sub r10d, r11d",
    "and rsp, -16",
    "or r10, r11",
    "or al, bl",
    "and al, bl",
    "xor eax, eax",
    "cmp r10, -1",
    "test r10, r10",
    "imul r10, r11",
    "imul r10, r11, 257",
    "neg r10",
    "shl r10, 9",
    "shr r10, cl",
    "sar r10, 63",
    "div r10",
    "idiv r10",
    "cdq",
    "cqo",
    "sete al",
    "setne r10b",
    "setp bl",
    "cmovl rax, r10",
    "cmova rax, r10",
    "xchg QWORD PTR [r11], r10",
    "lock xadd QWORD PTR [r11], r10",
    "lock cmpxchg DWORD PTR [r11], r10d",
    "movss xmm10, DWORD PTR [rbp - 8]",
    "movss DWORD PTR [r11], xmm10",
    "movsd xmm10, xmm11",
    "movd xmm10, r10d",
    "movd r10d, xmm10",
    "movq xmm10, r10",
    "movq r10, xmm10",
    "xorps xmm10, xmm11",
    "xorpd xmm10, xmm11",
    "addss xmm10, xmm11",
    "addsd xmm10, xmm11",
    "subss xmm10, xmm11",
    "subsd xmm10, xmm11",
    "mulss xmm10, xmm11",
    "mulsd xmm10, xmm11",
    "divss xmm10, xmm11",
    "divsd xmm10, xmm11",
    "sqrtss xmm10, xmm10",
    "sqrtsd xmm10, xmm10",
    "ucomiss xmm10, xmm11",
    "ucomisd xmm10, xmm11",
    "cvtsi2ss xmm10, r10",
    "cvtsi2sd xmm10, r10d",
    "cvttss2si r10, xmm10",
    "cvttsd2si r10d, xmm10",
    "cvtsd2ss xmm10, xmm10",
    "cvtss2sd xmm10, xmm10",
    "add r10, QWORD PTR fs:0",
    "lea r10, external_data[rip]",
    "mov r10, QWORD PTR external_tls@gottpoff[rip]",
    "call external_function",
    "jne external_target",
    "syscall",
    "mfence",
    "cpuid",
    "xgetbv",
    "ret",
    "ud2",
)


@_X86_GATE
@pytest.mark.parametrize("instruction", _EMITTER_SHAPES)
def test_owned_encoder_matches_system_assembler_per_emitted_shape(
    tmp_path: Path,
    instruction: str,
) -> None:
    system_code, system_relocations = _system_encoding(tmp_path, instruction)
    encoded = encode_instruction(
        instruction,
        pc=0,
        labels={},
        section_name=".text",
    )
    owned_relocations = tuple(
        (
            relocation.offset,
            relocation.type,
            relocation.addend,
            relocation.symbol,
        )
        for relocation in encoded.relocations
    )
    assert encoded.code == system_code
    assert owned_relocations == system_relocations


@pytest.mark.parametrize(
    "instruction",
    (
        "mov rax, [no_base*4]",
        "mov ah, r10b",
        "mov QWORD PTR [rsp + 32], xmm10",
        "mov xmm10, DWORD PTR [rbp + 48]",
        "vzeroupper",
        "jmp 7",
        "lock ret",
        "mov r10b, 256",
        "add r10, 0x100000000",
        "movsd xmm10, DWORD PTR [r11]",
        "xorps xmm10, DWORD PTR [r11]",
    ),
)
def test_owned_encoder_rejects_unowned_shapes(instruction: str) -> None:
    with pytest.raises(X86EncodeError):
        encode_instruction(
            instruction,
            pc=0,
            labels={},
            section_name=".text",
        )
