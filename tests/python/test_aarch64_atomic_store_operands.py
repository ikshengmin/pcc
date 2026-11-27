"""Atomic stores use their operands' registers, and zero as ZR.

The atomic store emitter built every address in x9 and every value in x10
before ``stlr``: a lock release was ``mov x9, x1; mov w10, w2;
stlr w10, [x9]`` where LLVM emits ``stlr wzr, [x1]``. Register-allocated
operands are now used where they are, and a constant zero value is the
zero register. The program stores and reads every width back.
"""
import re
import subprocess

import pytest

from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@w = global i32 7, align 4
@x = global i64 7, align 8
@b = global i8 7, align 1
define void @release_zero32(ptr %p) {
entry:
  store atomic i32 0, ptr %p release, align 4
  ret void
}
define void @release_value64(ptr %p, i64 %v) {
entry:
  store atomic i64 %v, ptr %p release, align 8
  ret void
}
define void @relaxed_zero64(ptr %p) {
entry:
  store atomic i64 0, ptr %p monotonic, align 8
  ret void
}
define void @release_zero8(ptr %p) {
entry:
  store atomic i8 0, ptr %p release, align 1
  ret void
}
define i32 @main() {
entry:
  call void @release_zero32(ptr @w)
  call void @release_value64(ptr @x, i64 -9)
  call void @release_zero8(ptr @b)
  %w = load i32, ptr @w
  %x = load i64, ptr @x
  %b = load i8, ptr @b
  call void @relaxed_zero64(ptr @x)
  %z = load i64, ptr @x
  %ok_w = icmp eq i32 %w, 0
  %ok_x = icmp eq i64 %x, -9
  %ok_b = icmp eq i8 %b, 0
  %ok_z = icmp eq i64 %z, 0
  %a = and i1 %ok_w, %ok_x
  %c = and i1 %ok_b, %ok_z
  %all = and i1 %a, %c
  %status = select i1 %all, i32 0, i32 67
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_atomic_stores_take_operand_registers_and_zero(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    expected = {
        "release_zero32": r"stlr wzr, \[x0\]",
        "release_value64": r"stlr x1, \[x0\]",
        "relaxed_zero64": r"str xzr, \[x0\]",
        "release_zero8": r"stlrb wzr, \[x0\]",
    }
    for name, pattern in expected.items():
        body = _body(assembly, name)
        assert re.search(pattern, body), (name, body)
        assert not re.search(r"\bmov [wx](9|10),", body), (name, body)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_atomic_stores_run(tmp_path, monkeypatch, registers):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "stores"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
