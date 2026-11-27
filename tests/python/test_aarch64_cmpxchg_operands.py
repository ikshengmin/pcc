"""LSE compare-exchange uses operand registers and immediates.

The 32/64-bit ``cas`` lowering built the expectation in x10 and the new
value in x11, then copied the expectation into the result register:
``mov w10, w1; mov w11, w2; mov w1, w10; casa w1, w11, [x22];
cmp w1, w10``. CAS only reads Rt, so a new value already in a register
other than the result's (or zero, as ZR) is Rt itself; a small constant
expectation seeds Rs directly and is compared as an immediate; an
allocated expectation is copied once and compared where it is. The
program takes the success and failure paths of each shape.
"""
import re
import subprocess

import pytest

from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@lock = global i32 0, align 4
@word = global i64 5, align 8
define i32 @acquire_lock(ptr %p) {
entry:
  %r = cmpxchg ptr %p, i32 0, i32 1 acquire monotonic
  %old = extractvalue { i32, i1 } %r, 0
  ret i32 %old
}
define i64 @swap_values(ptr %p, i64 %e, i64 %d) {
entry:
  %r = cmpxchg ptr %p, i64 %e, i64 %d acq_rel monotonic
  %ok = extractvalue { i64, i1 } %r, 1
  %z = zext i1 %ok to i64
  ret i64 %z
}
define i64 @clear_if(ptr %p, i64 %e) {
entry:
  %r = cmpxchg ptr %p, i64 %e, i64 0 seq_cst seq_cst
  %old = extractvalue { i64, i1 } %r, 0
  ret i64 %old
}
define i32 @main() {
entry:
  %a = call i32 @acquire_lock(ptr @lock)
  %b = call i32 @acquire_lock(ptr @lock)
  %held = load i32, ptr @lock
  %c = call i64 @swap_values(ptr @word, i64 5, i64 9)
  %d = call i64 @swap_values(ptr @word, i64 5, i64 11)
  %w1 = load i64, ptr @word
  %e = call i64 @clear_if(ptr @word, i64 3)
  %f = call i64 @clear_if(ptr @word, i64 9)
  %w2 = load i64, ptr @word
  %k1 = icmp eq i32 %a, 0
  %k2 = icmp eq i32 %b, 1
  %k3 = icmp eq i32 %held, 1
  %k4 = icmp eq i64 %c, 1
  %k5 = icmp eq i64 %d, 0
  %k6 = icmp eq i64 %w1, 9
  %k7 = icmp eq i64 %e, 9
  %k8 = icmp eq i64 %f, 9
  %k9 = icmp eq i64 %w2, 0
  %m1 = and i1 %k1, %k2
  %m2 = and i1 %k3, %k4
  %m3 = and i1 %k5, %k6
  %m4 = and i1 %k7, %k8
  %m5 = and i1 %m1, %m2
  %m6 = and i1 %m3, %m4
  %m7 = and i1 %m5, %m6
  %all = and i1 %m7, %k9
  %status = select i1 %all, i32 0, i32 71
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_compare_exchange_takes_operands_in_place(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    lock = _body(assembly, "acquire_lock")
    # The result returns in x0, so the address moves to x9 first.
    assert re.search(r"\bcasa w0, w11, \[x9\]", lock) and re.search(r"\bcmp w0, #0\b", lock), lock
    assert not re.search(r"\bmov w10,", lock), lock
    values = _body(assembly, "swap_values")
    cas = re.search(r"\bcasal x(\d+), x2, \[x0\]", values)
    assert cas, values
    assert re.search(r"\bmov x" + cas.group(1) + r", x1\b", values), values
    assert re.search(r"\bcmp x" + cas.group(1) + r", x1\b", values), values
    clear = _body(assembly, "clear_if")
    assert re.search(r"\bcasal x0, xzr, \[x9\]", clear), clear
    for body in (lock, values, clear):
        assert not re.search(r"\bmov [wx]1[01], ", body), body


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_compare_exchange_paths_run(tmp_path, monkeypatch, registers):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "cas"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
