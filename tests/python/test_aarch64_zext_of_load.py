"""Zero-extending a loaded i8/i16/i32 to i64 costs nothing.

LDRB, LDRH and LDR (32-bit), their unscaled and acquire forms, all clear
the destination's upper bits, so ``zext`` of such a load result is the
loaded register (LLVM selects no instruction). The backend emitted
``mov w4, w4`` after every ``ldrb w4`` in the byte loops of memchr, memcmp
and ``py_obj_eq_value`` (about 3.1k per 600000 stepped instructions; docs
/status.md, item 3 P0 notes). A zext of an argument or a call result keeps
``mov w, w``: their upper 32 bits are unspecified (AAPCS64), which the
program checks with a callee that truncates 0x100000005. Because the
zext holds the load's bits, the allocator gives both one register even
while the narrow value stays live (``both``), so no copy remains.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@flag = global i32 7
define i64 @byte(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %b = load i8, ptr %a, align 1
  %w = zext i8 %b to i64
  %s = add i64 %w, %i
  ret i64 %s
}
define i64 @half(ptr %p) {
entry:
  %b = load i16, ptr %p, align 2
  %w = zext i16 %b to i64
  %s = add i64 %w, 1
  ret i64 %s
}
define i64 @word(ptr %p) {
entry:
  %b = load i32, ptr %p, align 4
  %w = zext i32 %b to i64
  %s = add i64 %w, 1
  ret i64 %s
}
define i64 @atomic_word() {
entry:
  %b = load atomic i32, ptr @flag acquire, align 4
  %w = zext i32 %b to i64
  %s = add i64 %w, 1
  ret i64 %s
}
define i32 @narrow(i64 %x) {
entry:
  %t = trunc i64 %x to i32
  ret i32 %t
}
define i64 @from_call(i64 %x) {
entry:
  %r = call i32 @narrow(i64 %x)
  %w = zext i32 %r to i64
  ret i64 %w
}
define i64 @from_argument(i32 %a) {
entry:
  %w = zext i32 %a to i64
  %s = add i64 %w, 1
  ret i64 %s
}
define i64 @both(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %b = load i8, ptr %a, align 1
  %w = zext i8 %b to i64
  %c = icmp eq i8 %b, 0
  %s = select i1 %c, i64 100, i64 %w
  %t = add i64 %s, %w
  ret i64 %t
}
define i32 @main() {
entry:
  %buf = alloca [8 x i8], align 8
  store i64 -1, ptr %buf, align 8
  %b0 = getelementptr [8 x i8], ptr %buf, i64 0, i64 2
  store i8 200, ptr %b0, align 1
  %v1 = call i64 @byte(ptr %buf, i64 2)
  %v2 = call i64 @half(ptr %buf)
  %v3 = call i64 @word(ptr %buf)
  %v4 = call i64 @atomic_word()
  %v5 = call i64 @from_call(i64 4294967301)
  %v6 = call i64 @from_argument(i32 -1)
  %v7 = call i64 @both(ptr %buf, i64 2)
  %c1 = icmp eq i64 %v1, 202
  %c2 = icmp eq i64 %v2, 65536
  %c3 = icmp eq i64 %v3, 4291362816
  %c4 = icmp eq i64 %v4, 8
  %c5 = icmp eq i64 %v5, 5
  %c6 = icmp eq i64 %v6, 4294967296
  %c7 = icmp eq i64 %v7, 400
  %a1 = and i1 %c1, %c2
  %a2 = and i1 %a1, %c3
  %a3 = and i1 %a2, %c4
  %a4 = and i1 %a3, %c5
  %a5 = and i1 %a4, %c6
  %a6 = and i1 %a5, %c7
  %status = select i1 %a6, i32 0, i32 89
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_zext_of_a_load_emits_nothing(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name in ("byte", "half", "word", "atomic_word"):
        body = _body(assembly, name)
        assert re.search(r"\b(ldrb|ldrh|ldr|ldar) w\d+, \[", body), (name, body)
        assert not re.search(r"\bmov w(\d+), w\1\b", body), (name, body)
    both = _body(assembly, "both")
    loaded = re.search(r"\bldrb w(\d+), ", both).group(1)
    # The zext's use (the final add) reads the loaded register itself.
    assert re.search(r"\badd x\d+, x\d+, x" + loaded + r"$", both, re.M), both
    for name in ("from_call", "from_argument"):
        body = _body(assembly, name)
        assert re.search(r"\bmov w\d+, w\d+\b", body), (name, body)


@pytest.mark.parametrize("registers", ("0", "1"))
def test_typed_and_text_emission_agree(monkeypatch, registers):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    expected = assemble_file(emit_aarch64_darwin_asm(SOURCE, optimize=True))
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    assert transport.assemble_sections() == expected


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_zext_programs_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "zext"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
