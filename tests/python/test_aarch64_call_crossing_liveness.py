"""A value needs a callee-saved register only where it is live across a call.

The function-wide allocator gives each value one linear interval from its
definition to its last use. Any call inside that interval forced x19-x28,
even a call in a block the value is not live in: in
``pcc_container_unlock`` the thread-state address and word are used after a
cold ``abort`` block laid out between their definition and their use, so
both took callee-saved registers and every call saved and restored x19/x20.
LLVM's live intervals have holes there. A call strictly inside the interval
now counts only if the value is live after it (used later in that block or
live out of it); calls at the interval's ends keep their rule. A fused
``madd`` reads its multiply operands at the consumer, after any call between
the two. The programs execute natively under both allocators.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@state = global i64 5
@calls = global i64 0
define void @cold() {
entry:
  %n = load i64, ptr @calls, align 8
  %n2 = add i64 %n, 100
  store i64 %n2, ptr @calls, align 8
  ret void
}
define void @note() {
entry:
  %n = load i64, ptr @calls, align 8
  %n2 = add i64 %n, 1
  store i64 %n2, ptr @calls, align 8
  ret void
}
define i64 @hole(ptr %p, i64 %k) {
entry:
  %v = load i64, ptr %p, align 8
  %bad = icmp eq i64 %v, 0
  br i1 %bad, label %fail, label %ok
fail:
  call void @cold()
  ret i64 -1
ok:
  %w = add i64 %v, %k
  store i64 %w, ptr %p, align 8
  ret i64 %w
}
define i64 @across(ptr %p, i64 %k) {
entry:
  %v = load i64, ptr %p, align 8
  call void @note()
  %w = add i64 %v, %k
  store i64 %w, ptr %p, align 8
  ret i64 %w
}
define i64 @loop_across(ptr %p, i64 %n) {
entry:
  %base = load i64, ptr %p, align 8
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %i2, %body ]
  %s = phi i64 [ 0, %entry ], [ %s2, %body ]
  %c = icmp slt i64 %i, %n
  br i1 %c, label %body, label %done
body:
  call void @note()
  %s2 = add i64 %s, %base
  %i2 = add i64 %i, 1
  br label %head
done:
  ret i64 %s
}
define i64 @madd_across(i64 %a, i64 %b, i64 %c) {
entry:
  %m = mul i64 %a, %b
  call void @note()
  %r = add i64 %m, %c
  ret i64 %r
}
define i32 @main() {
entry:
  %h1 = call i64 @hole(ptr @state, i64 3)
  %z = alloca i64, align 8
  store i64 0, ptr %z, align 8
  %h2 = call i64 @hole(ptr %z, i64 3)
  %a1 = call i64 @across(ptr @state, i64 2)
  %l1 = call i64 @loop_across(ptr @state, i64 3)
  %md = call i64 @madd_across(i64 3, i64 4, i64 5)
  %calls = load i64, ptr @calls, align 8
  %c1 = icmp eq i64 %h1, 8
  %c2 = icmp eq i64 %h2, -1
  %c3 = icmp eq i64 %a1, 10
  %c4 = icmp eq i64 %l1, 30
  %c5 = icmp eq i64 %calls, 105
  %c6 = icmp eq i64 %md, 17
  %b1 = and i1 %c1, %c2
  %b2 = and i1 %b1, %c3
  %b3 = and i1 %b2, %c4
  %b4 = and i1 %b3, %c5
  %b5 = and i1 %b4, %c6
  %status = select i1 %b5, i32 0, i32 83
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_callee_saved_registers_only_for_values_live_across_calls(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    # %v and %k are dead in the cold block: no callee-saved register.
    hole = _body(assembly, "hole")
    assert not re.search(r"\bx(19|2[0-8])\b", hole), hole
    # Live across the call: callee-saved, saved and restored.
    for name in ("across", "loop_across"):
        body = _body(assembly, name)
        assert re.search(r"\bstp x19, ", body), (name, body)


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
def test_call_crossing_programs_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "crossing"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
