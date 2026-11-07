"""Direct ELF global atomics retain width, observations and owned execution."""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_x86_64_linux as x86
from pcc.backend.elf_x86_64 import (
    SHN_UNDEF,
    link_static_executable,
    parse_relocatable,
)
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.target_objects import (
    emit_x86_64_streamed_module,
    encode_assembly_object,
)
from pcc.backend.x86_64_asm_driver import assemble_file
from pcc.ir import ir
from tests.c.test_owned_atomic_scalar_regressions import _forbid_process


TARGET = "x86_64-unknown-linux-gnu"
NATIVE = pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64"
)
START = """.intel_syntax noprefix
.text
.globl _start
_start:
  call main
  mov edi, eax
  mov eax, 60
  syscall
"""


def _readers(monkeypatch, width, ordering):
    # Exercise the real direct-indexed producer as well as the IR-text route.
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    module = ir.Module(name="global-atomic-loads")
    module.triple = TARGET
    word = ir.IntType(width)
    defined = ir.GlobalVariable(module, word, "defined_cell")
    defined.initializer = ir.Constant(word, -5)
    defined.align = width // 8
    external = ir.GlobalVariable(module, word, "external_cell")
    external.linkage = "external"
    for name, cell in (("read_defined", defined), ("read_external", external)):
        function = ir.Function(module, ir.FunctionType(word, []), name)
        builder = ir.IRBuilder(function.append_basic_block("entry"))
        builder.ret(builder.load_atomic(cell, ordering, width // 8, typ=word))
    return module


def _body(assembly, name):
    return assembly.split(name + ":\n", 1)[1].split(".size " + name, 1)[0]


def _assert_reader_symbols(object_bytes):
    symbols = {symbol.name: symbol for symbol in parse_relocatable(object_bytes).symbols}
    assert symbols["external_cell"].section_index == SHN_UNDEF
    assert symbols["defined_cell"].section_index != SHN_UNDEF


def _execute(tmp_path, monkeypatch, objects, startup=START):
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        image = link_static_executable(
            [assemble_file(startup)] + [parse_relocatable(obj) for obj in objects]
        )
    output = tmp_path / "atomic-loads"
    output.write_bytes(image)
    output.chmod(0o755)
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("ordering", ("monotonic", "acquire", "seq_cst"))
def test_global_atomic_load_selection_and_direct_object_parity(monkeypatch, width, ordering):
    module = _readers(monkeypatch, width, ordering)
    text = str(module)
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        # Pin the selector separately from the later register-copy passes.
        with guard.context() as selection:
            selection.setattr(x86, "_POST_SELECTION_PASSES", False)
            assembly = emit_self_asm(text, TARGET)
        memory = {8: "BYTE", 16: "WORD", 32: "DWORD", 64: "QWORD"}[width]
        reg = {8: "r10b", 16: "r10w", 32: "r10d", 64: "r10"}[width]
        for suffix in ("defined", "external"):
            body = _body(assembly, "read_" + suffix)
            assert f"  mov {reg}, {memory} PTR {suffix}_cell[rip]" in body
            assert "  lea " not in body
            assert "mfence" not in body and "lock " not in body
        text_object = encode_assembly_object(emit_self_asm(text, TARGET), TARGET)
        direct_object = emit_x86_64_streamed_module(
            module.direct_indexed_module()
        ).encode_object()
        owned_object = emit_owned_object(text, TARGET)
        for obj in (text_object, direct_object, owned_object):
            _assert_reader_symbols(obj)
        assert direct_object == text_object == owned_object


def _global_harness(width):
    alignment = width // 8
    word = f"i{width}"
    # Compare the complete value, including its high bit, before and after
    # direct writes. The external definition has a canary on each side.
    checks = []
    conditions = []
    for step, value in enumerate((-5, 3, -2)):
        for name in ("defined", "external"):
            checks.append(f"  store {word} {value}, ptr @{name}_cell, align {alignment}")
            checks.append(f"  %{name}{step} = call {word} @read_{name}()")
            condition = f"%ok_{name}{step}"
            checks.append(f"  {condition} = icmp eq {word} %{name}{step}, {value}")
            conditions.append(condition)
    for name, value in (("before", 85), ("after", 42)):
        checks.extend((
            f"  %{name} = load i8, ptr @{name}, align 1",
            f"  %ok_{name} = icmp eq i8 %{name}, {value}",
        ))
        conditions.append("%ok_" + name)
    result = conditions[0]
    for index, condition in enumerate(conditions[1:]):
        checks.append(f"  %all{index} = and i1 {result}, {condition}")
        result = f"%all{index}"
    checks.extend((
        f"  %failed = xor i1 {result}, true",
        "  %status = zext i1 %failed to i32",
        "  ret i32 %status",
    ))
    return f'''target triple = "{TARGET}"
@defined_cell = external global {word}
@external_cell = external global {word}
@before = external global i8
@after = external global i8
declare {word} @read_defined()
declare {word} @read_external()
define i32 @main() {{
entry:
''' + "\n".join(checks) + "\n}\n"


@NATIVE
@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("ordering", ("monotonic", "acquire", "seq_cst"))
def test_global_atomic_loads_execute_with_direct_writes_and_neighbors(
    tmp_path, monkeypatch, width, ordering,
):
    directive = {8: ".byte", 16: ".short", 32: ".long", 64: ".quad"}[width]
    # Neighbor sentinels check that loads leave surrounding storage intact.
    # The separate assembly assertions verify the exact memory access width.
    startup = START + f'''.data
.p2align {(width // 8).bit_length() - 1}
.zero {width // 8 - 1}
.globl before
before:
.byte 85
.globl external_cell
external_cell:
{directive} -5
.globl after
after:
.byte 42
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        readers = _readers(monkeypatch, width, ordering)
        objects = [
            emit_x86_64_streamed_module(readers.direct_indexed_module()).encode_object(),
            emit_owned_object(_global_harness(width), TARGET),
        ]
        _assert_reader_symbols(objects[0])
    _execute(tmp_path, monkeypatch, objects, startup)


POLL = f'''target triple = "{TARGET}"
@first_flag = global i32 0, align 4
@second_flag = global i32 0, align 4
define i32 @poll() {{
entry:
  %first = load atomic i32, ptr @first_flag acquire, align 4
  %second = load atomic i32, ptr @second_flag acquire, align 4
  %first_set = icmp ne i32 %first, 0
  %second_set = icmp ne i32 %second, 0
  %needed = or i1 %first_set, %second_set
  %result = zext i1 %needed to i32
  ret i32 %result
}}
'''


@NATIVE
def test_two_atomic_polls_observe_all_direct_flag_combinations(tmp_path, monkeypatch):
    statements = []
    # Revisit 0/0 after setting both flags to catch stale cached observations.
    for index, (first, second) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 0))):
        statements.extend((
            f"  store i32 {first}, ptr @first_flag, align 4",
            f"  store i32 {second}, ptr @second_flag, align 4",
            f"  %seen{index} = call i32 @poll()",
            f"  %ok{index} = icmp eq i32 %seen{index}, {first | second}",
        ))
    result = "%ok0"
    for index in range(1, 5):
        statements.append(f"  %all{index} = and i1 {result}, %ok{index}")
        result = f"%all{index}"
    statements.extend((f"  %bad = xor i1 {result}, true", "  %status = zext i1 %bad to i32", "  ret i32 %status"))
    text = POLL + "define i32 @main() {\nentry:\n" + "\n".join(statements) + "\n}\n"
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        body = _body(emit_self_asm(text, TARGET), "poll")
        for name in ("first_flag", "second_flag"):
            assert len(re.findall(r"^  mov [^\n]+, DWORD PTR " + name + r"\[rip\]$", body, re.M)) == 1
        assert "  lea " not in body
        obj = emit_owned_object(text, TARGET)
    _execute(tmp_path, monkeypatch, [obj])


@pytest.mark.parametrize("pointer, declaration, arguments, setup, expected", (
    ("@tls_cell", "@tls_cell = thread_local global i64 7, align 8", "", "", "@gottpoff[rip]"),
    ("%pointer", "", "ptr %pointer", "", "[r11]"),
    ("%slot", "@box = global [3 x i64] [i64 85, i64 7, i64 42], align 8", "",
     "  %slot = getelementptr [3 x i64], ptr @box, i64 0, i64 1", "lea r11, box[rip]"),
))
def test_non_direct_atomic_loads_keep_address_materialization(
    monkeypatch, pointer, declaration, arguments, setup, expected,
):
    monkeypatch.setattr(x86, "_POST_SELECTION_PASSES", False)
    text = f'''target triple = "{TARGET}"
{declaration}
define i64 @read_fallback({arguments}) {{
entry:
{setup}
  %value = load atomic i64, ptr {pointer} acquire, align 8
  ret i64 %value
}}
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        body = _body(emit_self_asm(text, TARGET), "read_fallback")
        assert expected in body
        assert "  mov r10, QWORD PTR [r11]" in body
        assert emit_owned_object(text, TARGET)


def test_windows_direct_atomic_load_keeps_absolute_address(monkeypatch):
    monkeypatch.setattr(x86, "_POST_SELECTION_PASSES", False)
    target = "x86_64-pc-windows-msvc"
    text = f'''target triple = "{target}"
@cell = global i64 7, align 8
define i64 @read_global() {{
entry:
  %value = load atomic i64, ptr @cell seq_cst, align 8
  ret i64 %value
}}
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        assembly = emit_self_asm(text, target)
        assert "  movabs r11, OFFSET FLAT:cell" in assembly
        assert "  mov r10, QWORD PTR [r11]" in assembly
        assert emit_owned_object(text, target)


@NATIVE
@pytest.mark.parametrize("width", (8, 16, 32, 64))
def test_pointer_and_ssa_gep_atomic_loads_execute(tmp_path, monkeypatch, width):
    word = f"i{width}"
    alignment = width // 8
    text = f'''target triple = "{TARGET}"
@box = global [3 x {word}] [{word} 85, {word} -5, {word} 42], align {alignment}
define {word} @read_pointer(ptr %address) {{
entry:
  %value = load atomic {word}, ptr %address acquire, align {alignment}
  ret {word} %value
}}
define {word} @read_gep() {{
entry:
  %slot = getelementptr [3 x {word}], ptr @box, i64 0, i64 1
  %value = load atomic {word}, ptr %slot seq_cst, align {alignment}
  ret {word} %value
}}
define i32 @main() {{
entry:
  %address = getelementptr [3 x {word}], ptr @box, i64 0, i64 1
  %old_pointer = call {word} @read_pointer(ptr %address)
  %old_gep = call {word} @read_gep()
  store {word} 7, ptr %address, align {alignment}
  %new_pointer = call {word} @read_pointer(ptr %address)
  %new_gep = call {word} @read_gep()
  %before = load {word}, ptr @box, align {alignment}
  %after = load {word}, ptr getelementptr ([3 x {word}], ptr @box, i64 0, i64 2), align {alignment}
  %ok0 = icmp eq {word} %old_pointer, -5
  %ok1 = icmp eq {word} %old_gep, -5
  %ok2 = icmp eq {word} %new_pointer, 7
  %ok3 = icmp eq {word} %new_gep, 7
  %ok4 = icmp eq {word} %before, 85
  %ok5 = icmp eq {word} %after, 42
  %all1 = and i1 %ok0, %ok1
  %all2 = and i1 %all1, %ok2
  %all3 = and i1 %all2, %ok3
  %all4 = and i1 %all3, %ok4
  %all5 = and i1 %all4, %ok5
  %bad = xor i1 %all5, true
  %status = zext i1 %bad to i32
  ret i32 %status
}}
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        obj = emit_owned_object(text, TARGET)
    _execute(tmp_path, monkeypatch, [obj])


@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("ordering", ("acquire", "seq_cst"))
def test_constant_expression_gep_atomic_load_remains_unsupported(monkeypatch, width, ordering):
    # Atomic text operands currently accept an SSA pointer or direct global,
    # unlike the ordinary load parser. Preserve that explicit boundary.
    word = f"i{width}"
    text = f'''target triple = "{TARGET}"
@box = global [3 x {word}] [{word} 85, {word} -5, {word} 42], align {width // 8}
define {word} @read_gep() {{
entry:
  %value = load atomic {word}, ptr getelementptr ([3 x {word}], ptr @box, i64 0, i64 1) {ordering}, align {width // 8}
  ret {word} %value
}}
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        for emit in (emit_self_asm, emit_owned_object):
            with pytest.raises(BackendUnavailable, match="malformed atomic load"):
                emit(text, TARGET)


@NATIVE
def test_tls_atomic_load_uses_current_thread_pointer(tmp_path, monkeypatch):
    text = f'''target triple = "{TARGET}"
@tls_cell = thread_local global i64 0, align 8
define i64 @read_tls() {{
entry:
  %value = load atomic i64, ptr @tls_cell acquire, align 8
  ret i64 %value
}}
'''
    # One eight-byte TLS symbol has TP offset -8. Supply two distinct
    # variant-II TLS blocks to check address selection, without a runtime
    # archive or external libc. This is not a multithreaded ordering test.
    startup = '''.intel_syntax noprefix
.text
.globl _start
_start:
  lea rsi, first_tp[rip]
  mov eax, 158
  mov edi, 4098
  syscall
  test rax, rax
  jne failed
  call read_tls
  cmp rax, 37
  jne failed
  lea rsi, second_tp[rip]
  mov eax, 158
  mov edi, 4098
  syscall
  test rax, rax
  jne failed
  call read_tls
  cmp rax, 73
  jne failed
  xor edi, edi
  jmp exit
failed:
  mov edi, 1
exit:
  mov eax, 60
  syscall
.data
.p2align 3
.quad 37
first_tp:
.quad first_tp
.quad 73
second_tp:
.quad second_tp
'''
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbid_process)
        obj = emit_owned_object(text, TARGET)
    _execute(tmp_path, monkeypatch, [obj], startup)
