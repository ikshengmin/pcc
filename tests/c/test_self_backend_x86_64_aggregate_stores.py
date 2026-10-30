"""Execute literal stores using the owned x86 emitter, assembler and ELF linker.

These are host-driven, runtime-free machine-code regressions, not native-pcc1
or managed-runtime parity qualification.
"""

import builtins
import platform
import subprocess
import sys
from unittest.mock import patch

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.owned_elf_link import link_inputs
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.target_objects import encode_assembly_object


TARGET = "x86_64-unknown-linux-gnu"
NATIVE_X86 = pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux"
    and platform.machine().lower() in ("x86_64", "amd64")
)

# The same minimal Linux syscall scaffold used by the owned-ELF native tests.
STARTUP = """.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
  call main
  mov edi, eax
  mov eax, 60
  syscall
.size _start, .-_start
"""

LITERALS = [
    pytest.param(
        "<4 x i32>", "<i32 1, i32 -3, i32 5, i32 -7>",
        bytes.fromhex("01000000 fdffffff 05000000 f9ffffff"),
        id="signed-vector",
    ),
    pytest.param(
        "[15 x i8]", "[" + ", ".join("i8 " + str(i) for i in range(1, 16)) + "]",
        bytes(range(1, 16)), id="all-store-chunk-widths",
    ),
    pytest.param(
        "%Record", "{i8 18, i32 305419896, i16 22136}",
        bytes.fromhex("12000000 78563412 78560000"), id="named-padded-struct",
    ),
    pytest.param(
        "<4 x float>", "<float -0.0, float 1.5, float -2.0, float 3.25>",
        bytes.fromhex("00000080 0000c03f 000000c0 00005040"), id="float-bits",
    ),
    pytest.param(
        "<2 x ptr>", "<ptr null, ptr inttoptr (i64 1234605616436508552 to ptr)>",
        bytes.fromhex("0000000000000000 8877665544332211"), id="pointer-bits",
    ),
]


def _literal_store_program(value_type, literal, expected, destination):
    declarations = "%Record = type {i8, i32, i16}\n"
    setup = ""
    if destination == "alloca":
        setup = f"  %storage = alloca {value_type}\n"
        pointer = "%storage"
    elif destination == "global":
        declarations += f"@storage = global {value_type} zeroinitializer\n"
        pointer = "@storage"
    else:
        declarations += f"@storage = global [2 x {value_type}] zeroinitializer\n"
        gep = f"getelementptr ([2 x {value_type}], ptr @storage, i64 0, i64 1)"
        if destination == "constant-gep":
            pointer = gep
        else:
            assert destination == "ssa-pointer"
            setup = f"  %storage = getelementptr [2 x {value_type}], ptr @storage, i64 0, i64 1\n"
            pointer = "%storage"
    lines = [
        declarations, "define i32 @main() {", "entry:", setup,
        f"  store {value_type} {literal}, ptr {pointer}",
    ]
    for index, byte in enumerate(expected):
        lines.extend([
            f"  %p{index} = getelementptr i8, ptr {pointer}, i64 {index}",
            f"  %b{index} = load i8, ptr %p{index}",
            f"  %c{index} = icmp eq i8 %b{index}, {byte}",
        ])
        if index:
            previous = "%c0" if index == 1 else f"%ok{index - 1}"
            lines.append(f"  %ok{index} = and i1 {previous}, %c{index}")
    lines.extend([
        f"  %status = select i1 %ok{len(expected) - 1}, i32 0, i32 91",
        "  ret i32 %status", "}",
    ])
    return "\n".join(lines)


def _run_owned_program(ir, tmp_path):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert name != "llvmlite" and not name.startswith("llvmlite."), name
        return original_import(name, *args, **kwargs)

    def forbidden_process(*args, **kwargs):
        raise AssertionError("owned aggregate-store build started an external process")

    binary = tmp_path / "aggregate-store"
    with patch("builtins.__import__", checked_import), patch("subprocess.Popen", forbidden_process):
        assembly = emit_self_asm(f'target triple = "{TARGET}"\n' + ir)
        (tmp_path / "program.ll").write_text(ir)
        (tmp_path / "program.s").write_text(assembly)
        program = tmp_path / "program.o"
        startup = tmp_path / "startup.o"
        program.write_bytes(encode_assembly_object(assembly, TARGET))
        startup.write_bytes(encode_assembly_object(STARTUP, TARGET))
        link_inputs(target=TARGET, output=str(binary), objects=[str(startup), str(program)])
    completed = subprocess.run([str(binary)], capture_output=True, timeout=5)
    assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"", b"")


@NATIVE_X86
@pytest.mark.parametrize("destination", ["alloca", "global", "constant-gep", "ssa-pointer"])
@pytest.mark.parametrize("value_type,literal,expected", LITERALS)
def test_owned_literal_store_preserves_every_byte(tmp_path, value_type, literal, expected, destination):
    _run_owned_program(_literal_store_program(value_type, literal, expected, destination), tmp_path)


def test_symbolic_pointer_literal_store_remains_fail_closed():
    ir = f'''target triple = "{TARGET}"
@value = global i32 17
define i32 @main() {{
entry:
  %slot = alloca <2 x ptr>
  store <2 x ptr> <ptr @value, ptr null>, ptr %slot
  ret i32 0
}}
'''
    with pytest.raises(BackendUnavailable, match="pointer aggregate literal value not translated yet"):
        emit_self_asm(ir)
