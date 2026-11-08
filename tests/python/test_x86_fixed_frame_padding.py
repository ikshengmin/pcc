"""Unowned fixed-frame bytes start zero without weakening the native-root ABI.

Specification: docs/project-intent.md, machine roots and ordinary C semantics.
These tests do not treat removed/promoted SSA slots as padding or claim that
initialization of entry-stale bytes removes dead values written later.
"""
from __future__ import annotations

import platform
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_x86_frame as frame
from pcc.backend.self_backend_dispatch import emit_self_asm


TARGETS = ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc")


class Layout:
    """Independent finite storage spans, including aliases and odd widths."""
    slot_scalars = [8, 0, 16, 1, 48, 2]
    value_names = ["buffer"]
    hidden_sret_slot_id = 0

    def slot_offset(self, slot):
        return self.slot_scalars[slot * 2]

    def slot_type_id(self, slot):
        return self.slot_scalars[slot * 2 + 1]

    def type_layout(self, type_id):
        return SimpleNamespace(first=(8, 1, 24)[type_id])

    def alloca_offset(self, value):
        return 80

    def alloca_type_id(self, value):
        return 3

    def type_span(self, type_id):
        return SimpleNamespace(third=25)


def test_padding_keeps_every_nominal_slot_and_whole_addressable_alloca(monkeypatch):
    monkeypatch.setattr(frame, "get_indexed_function_kernel", lambda function: Layout())
    function = SimpleNamespace(frame_size=96)
    # SSA [0,8), [15,16), [24,48); alloca [55,80); sret aliases [0,8).
    assert frame.fixed_frame_padding(function, None) == [
        (8, 15), (16, 24), (48, 55), (80, 96),
    ]
    root = SimpleNamespace(offset=-88)
    reload = SimpleNamespace(source_offset=-96, destination_offset=-104)
    record = SimpleNamespace(locations=(root,), reloads=(reload,))
    plan = SimpleNamespace(packed_records=None, records=(record,))
    assert frame.fixed_frame_padding(SimpleNamespace(frame_size=112), plan) == [
        (8, 15), (16, 24), (48, 55), (104, 112),
    ]


def test_packed_maps_keep_root_and_both_reload_endpoints(monkeypatch):
    from pcc.backend.self_backend_precise_stackmaps import (
        PackedPlannedSafepoints, PlannedManagedReload, PlannedRootLocation,
    )
    monkeypatch.setattr(frame, "get_indexed_function_kernel", lambda function: Layout())
    packed = PackedPlannedSafepoints(1)
    packed.append(1, ".Lsite", 0, (PlannedRootLocation(-88, True),), 0, "", 0,
                  (PlannedManagedReload(-96, -104),))
    try:
        plan = SimpleNamespace(packed_records=packed, records=())
        assert frame.fixed_frame_padding(SimpleNamespace(frame_size=112), plan) == [
            (8, 15), (16, 24), (48, 55), (104, 112),
        ]
    finally:
        packed.close()


def test_hidden_sret_storage_is_kept_without_an_ordinary_value_slot(monkeypatch):
    class SretOnly:
        slot_scalars = []
        value_names = []
        hidden_sret_slot_id = 0

        def slot_offset(self, slot):
            return 8

    monkeypatch.setattr(frame, "get_indexed_function_kernel", lambda function: SretOnly())
    assert frame.fixed_frame_padding(SimpleNamespace(frame_size=16), None) == [(8, 16)]


def test_invalid_ownership_span_fails_before_emitting_zeroes(monkeypatch):
    monkeypatch.setattr(frame, "get_indexed_function_kernel", lambda function: Layout())
    with pytest.raises(BackendUnavailable, match="invalid x86 fixed frame span"):
        frame.fixed_frame_padding(SimpleNamespace(frame_size=64), None)


def test_partial_word_initializers_cover_only_the_padding_bytes():
    padding = [(8, 15), (16, 24), (48, 55)]
    writes = set()
    used_widths = set()
    widths = {"BYTE": 1, "WORD": 2, "DWORD": 4, "QWORD": 8}
    for line in frame.emit_fixed_frame_padding(padding):
        match = re.fullmatch(r"  mov (BYTE|WORD|DWORD|QWORD) PTR \[rbp - ([0-9]+)\], 0", line)
        assert match, line
        high = int(match[2])
        used_widths.add(match[1])
        span = set(range(high - widths[match[1]], high))
        assert writes.isdisjoint(span)
        writes.update(span)
    assert writes == {byte for low, high in padding for byte in range(low, high)}
    assert used_widths == set(widths)


def _source(target, variadic=False):
    return f'''target triple = "{target}"
declare ptr @llvm.frameaddress.p0(i32)
declare i64 @inspect_padding(ptr, ptr)
define i64 @probe(i64 %a, i64 %b, i64 %c, i64 %d, i64 %e, i64 %f, i64 %g, i64 %h{", ..." if variadic else ""}) {{
entry:
  %buffer = alloca [25 x i8], align 8
  %last = getelementptr i8, ptr %buffer, i64 24
  store i8 7, ptr %buffer
  store i8 9, ptr %last
  %fp = call ptr @llvm.frameaddress.p0(i32 0)
  %status = call i64 @inspect_padding(ptr %fp, ptr %buffer)
  %s0 = add i64 %a, %b
  %s1 = add i64 %s0, %c
  %s2 = add i64 %s1, %d
  %s3 = add i64 %s2, %e
  %s4 = add i64 %s3, %f
  %s5 = add i64 %s4, %g
  %s6 = add i64 %s5, %h
  %result = add i64 %s6, %status
  ret i64 %result
}}
'''


def _emitted_layout(monkeypatch, target, variadic=False, registers=True):
    seen = []
    original = frame.fixed_frame_padding

    def capture(function, plan):
        padding = original(function, plan)
        seen.append((function.frame_size, function.platform_frame_extra, padding))
        return padding

    with monkeypatch.context() as scoped:
        scoped.setattr(frame, "fixed_frame_padding", capture)
        if not registers:
            from pcc.backend import self_backend_x86_64_linux as backend
            scoped.setattr(backend, "keep_frame_slots_in_registers", lambda lines, *args, **kwargs: lines)
        assembly = emit_self_asm(_source(target, variadic), target)
    assert len(seen) == 1
    return assembly, seen[0]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("variadic", (False, True))
@pytest.mark.parametrize("registers", (False, True))
def test_final_assembly_retains_zeroes_and_all_abi_input_saves(monkeypatch, target, variadic, registers):
    assembly, (local, extra, padding) = _emitted_layout(monkeypatch, target, variadic, registers)
    assert padding
    first_call = assembly.index("  call inspect_padding")
    for line in frame.emit_fixed_frame_padding(padding):
        assert line in assembly[:first_call]
    if "windows" in target:
        from pcc.backend.self_backend_win64_abi import MXCSR_OFFSET, PLATFORM_AREA_SIZE
        base = extra - PLATFORM_AREA_SIZE
        assert f"  mov QWORD PTR [rsp + {base + MXCSR_OFFSET}], 0" in assembly[:first_call]
        for register, offset in (("rbx", 0), ("rsi", 8), ("rdi", 16)):
            assert f"  mov QWORD PTR [rsp + {base + offset}], {register}" in assembly
            assert f"  mov {register}, QWORD PTR [rsp + {base + offset}]" in assembly
        for register, offset in (("xmm10", 32), ("xmm11", 48)):
            assert f"  movdqu XMMWORD PTR [rsp + {base + offset}], {register}" in assembly
            assert f"  movdqu {register}, XMMWORD PTR [rsp + {base + offset}]" in assembly
        assert "  .seh_endprologue" in assembly
        if variadic:
            for index, register in enumerate(("rcx", "rdx", "r8", "r9")):
                assert f"  mov QWORD PTR [rbp + {16 + index * 8}], {register}" in assembly
        # The seventh/eighth inputs remain above this function's frame.
        assert "[rbp + 64]" in assembly and "[rbp + 72]" in assembly
    else:
        saved = 176 if variadic else 0
        assert f"  mov QWORD PTR [rsp + {saved}], rbx" in assembly
        assert f"  mov rbx, QWORD PTR [rsp + {saved}]" in assembly
        assert f"  mov QWORD PTR [rsp + {saved + 8}], 0" in assembly[:first_call]
        if variadic:
            for index, register in enumerate(("rdi", "rsi", "rdx", "rcx", "r8", "r9")):
                assert f"  mov QWORD PTR [rsp + {index * 8}], {register}" in assembly
            assert "  movdqu XMMWORD PTR [rsp + 160], xmm7" in assembly
        assert "[rbp + 16]" in assembly and "[rbp + 24]" in assembly
    assert all(0 <= low < high <= local for low, high in padding)


def test_sysv_process_entry_keeps_original_stack_register_live():
    source = '''target triple = "x86_64-unknown-linux-gnu"
declare void @consume_initial_stack(ptr)
define void @_start(ptr %initial) {
entry:
  call void @consume_initial_stack(ptr %initial)
  ret void
}
'''
    assembly = emit_self_asm(source, TARGETS[0])
    save = assembly.index("  mov r11, rsp")
    zero = assembly.index("  mov QWORD PTR [rsp + 8], 0")
    call = assembly.index("  call consume_initial_stack")
    assert save < zero < call
    # Initializers themselves use no register scratch and cannot overwrite
    # the raw initial-stack pointer before argument lowering consumes it.
    consumed = False
    for line in assembly[zero:call].splitlines():
        if re.search(r", r11$", line):
            consumed = True
            break
        assert not re.match(r"  (?:xor|mov) r11,", line), line
    assert consumed


@pytest.mark.parametrize("target", TARGETS)
def test_aggregate_result_and_hidden_sret_remain_physical_storage(monkeypatch, target):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    source = f'''target triple = "{target}"
declare void @consume_buffer(ptr)
define {{i64, i64, i64}} @aggregate(i64 %argument) {{
entry:
  %buffer = alloca [25 x i8], align 8
  call void @consume_buffer(ptr %buffer)
  %first = insertvalue {{i64, i64, i64}} zeroinitializer, i64 %argument, 0
  %second = insertvalue {{i64, i64, i64}} %first, i64 19, 1
  %third = insertvalue {{i64, i64, i64}} %second, i64 23, 2
  ret {{i64, i64, i64}} %third
}}
'''
    original = frame.fixed_frame_padding
    seen = []

    def capture(function, plan):
        padding = original(function, plan)
        kernel = get_indexed_function_kernel(function)
        assert kernel.hidden_sret_slot_id >= 0
        sret = kernel.slot_offset(kernel.hidden_sret_slot_id)
        buffer = kernel.alloca_offset(kernel.value_id("buffer"))
        assert buffer >= 25
        for low, high in padding:
            assert high <= sret - 8 or low >= sret
            assert high <= buffer - 25 or low >= buffer
        seen.append(padding)
        return padding

    monkeypatch.setattr(frame, "fixed_frame_padding", capture)
    assembly = emit_self_asm(source, target)
    assert len(seen) == 1
    for line in frame.emit_fixed_frame_padding(seen[0]):
        assert line in assembly


@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
)
@pytest.mark.parametrize("variadic", (False, True))
@pytest.mark.parametrize("registers", (False, True))
def test_owned_linux_poisoned_frame_preserves_live_storage_and_native_abi(
    tmp_path, monkeypatch, variadic, registers,
):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    assembly, (local, extra, padding) = _emitted_layout(
        monkeypatch, TARGETS[0], variadic, registers,
    )
    # The poisoner writes only ordinary mapped process-stack storage before
    # the callee enters. This fixture has no runtime archive or external cc.
    assert local + extra + 64 < 2048
    assert padding
    checks = []
    for low, high in padding:
        for offset in range(low + 1, high + 1):
            checks.extend([
                f"  movzx eax, BYTE PTR [rdi - {offset}]",
                "  cmp eax, 0", "  jne .Lbad_padding",
            ])
    mxcsr_distance = local + extra - (184 if variadic else 8)
    checks.extend([
        f"  mov rax, QWORD PTR [rdi - {mxcsr_distance}]",
        "  cmp rax, 0", "  jne .Lbad_padding",
    ])
    observer = '''.intel_syntax noprefix
.text
.globl inspect_padding
.type inspect_padding, @function
inspect_padding:
''' + "\n".join(checks) + '''
  movzx eax, BYTE PTR [rsi]
  cmp eax, 7
  jne .Lbad_padding
  movzx eax, BYTE PTR [rsi + 24]
  cmp eax, 9
  jne .Lbad_padding
  xor eax, eax
  ret
.Lbad_padding:
  mov eax, 1000
  ret
.size inspect_padding, .-inspect_padding
'''
    startup = '''.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
  mov rcx, 256
  lea rdx, [rsp - 2048]
  mov rax, 81985529216486895
.Lpoison:
  mov QWORD PTR [rdx], rax
  add rdx, 8
  sub rcx, 1
  jne .Lpoison
  mov rbx, 257
  mov r12, 258
  mov r13, 259
  mov r14, 260
  mov r15, 261
  sub rsp, 16
  mov QWORD PTR [rsp], 7
  mov QWORD PTR [rsp + 8], 8
  mov rdi, 1
  mov rsi, 2
  mov rdx, 3
  mov rcx, 4
  mov r8, 5
  mov r9, 6
  xor eax, eax
  call probe
  add rsp, 16
  cmp rax, 36
  jne .Lbad_abi
  cmp rbx, 257
  jne .Lbad_abi
  cmp r12, 258
  jne .Lbad_abi
  cmp r13, 259
  jne .Lbad_abi
  cmp r14, 260
  jne .Lbad_abi
  cmp r15, 261
  jne .Lbad_abi
  xor edi, edi
  jmp .Lexit
.Lbad_abi:
  mov edi, 1
.Lexit:
  mov eax, 60
  syscall
.size _start, .-_start
'''
    image = link_static_executable([
        assemble_file(startup), assemble_file(assembly), assemble_file(observer),
    ])
    executable = tmp_path / "frame-padding"
    executable.write_bytes(image)
    executable.chmod(0o755)
    completed = subprocess.run([str(executable)], capture_output=True, timeout=10)
    assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"", b"")
