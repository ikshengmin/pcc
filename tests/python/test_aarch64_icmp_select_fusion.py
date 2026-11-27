"""An integer compare feeding only the next instruction's select uses its flags.

``%c = icmp P T %a, %b`` immediately followed by ``select i1 %c, ...`` (the
compare's only use) materialized the i1 (``cset``, a byte mask, a copy) and
compared it with zero again before the ``csel``: ten instructions for
``pcc_gc_load_ptr``'s NULL-slot guard in every loop that reads an item
(docs/status.md, item 3: the size loop of ``str.join`` ran 45 instructions
per item against about 15 under LLVM's code generation). LLVM selects
``cmp a, b`` + ``csel d, t, f, P``. The compare now sets the flags where it
stands and the select reads them: nothing between the two sets flags (the
select's operands are moved or loaded). A compare with another use, one
with an instruction between, and an i1 select keep the value path. The
program executes every form natively under both allocators.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@cell = global i64 7, align 8
@other = global i64 9, align 8
define ptr @null_guard(ptr %slot) {
entry:
  %null = icmp eq ptr %slot, null
  %safe = select i1 %null, ptr @other, ptr %slot
  ret ptr %safe
}
define i64 @smax(i64 %a, i64 %b) {
entry:
  %c = icmp sgt i64 %a, %b
  %r = select i1 %c, i64 %a, i64 %b
  ret i64 %r
}
define i32 @umin32(i32 %a, i32 %b) {
entry:
  %c = icmp ult i32 %a, %b
  %r = select i1 %c, i32 %a, i32 %b
  ret i32 %r
}
define i64 @slt_const(i64 %a) {
entry:
  %c = icmp slt i64 %a, 100
  %r = select i1 %c, i64 1, i64 2
  ret i64 %r
}
define i16 @narrow(i16 %a, i16 %b) {
entry:
  %c = icmp slt i16 %a, %b
  %r = select i1 %c, i16 %a, i16 %b
  ret i16 %r
}
define i64 @loop_guard(ptr %items, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [0, %entry], [%next, %body]
  %sum = phi i64 [0, %entry], [%total, %body]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %slot = getelementptr i64, ptr %items, i64 %i
  %isnull = icmp eq ptr %slot, null
  %safe = select i1 %isnull, ptr @other, ptr %slot
  %v = load i64, ptr %safe, align 8
  %total = add i64 %sum, %v
  %next = add i64 %i, 1
  br label %head
done:
  ret i64 %sum
}
define i64 @shared(i64 %a, i64 %b) {
entry:
  %c = icmp eq i64 %a, %b
  %r = select i1 %c, i64 10, i64 20
  %z = zext i1 %c to i64
  %s = add i64 %r, %z
  ret i64 %s
}
define i64 @apart(i64 %a, i64 %b) {
entry:
  %c = icmp ne i64 %a, %b
  %m = mul i64 %a, %b
  %r = select i1 %c, i64 %m, i64 5
  ret i64 %r
}
define i1 @boolsel(i64 %a, i1 %x, i1 %y) {
entry:
  %c = icmp ugt i64 %a, 3
  %r = select i1 %c, i1 %x, i1 %y
  ret i1 %r
}
define i32 @main() {
entry:
  %words = alloca [3 x i64], align 8
  %w0 = getelementptr [3 x i64], ptr %words, i64 0, i64 0
  store i64 100, ptr %w0, align 8
  %w1 = getelementptr [3 x i64], ptr %words, i64 0, i64 1
  store i64 20, ptr %w1, align 8
  %w2 = getelementptr [3 x i64], ptr %words, i64 0, i64 2
  store i64 3, ptr %w2, align 8
  %g0 = call ptr @null_guard(ptr null)
  %g1 = call ptr @null_guard(ptr @cell)
  %ok_g0 = icmp eq ptr %g0, @other
  %ok_g1 = icmp eq ptr %g1, @cell
  %m0 = call i64 @smax(i64 3, i64 -5)
  %m1 = call i64 @smax(i64 -7, i64 2)
  %u0 = call i32 @umin32(i32 1, i32 -1)
  %u1 = call i32 @umin32(i32 -2, i32 5)
  %s0 = call i64 @slt_const(i64 -1000)
  %s1 = call i64 @slt_const(i64 100)
  %n0 = call i16 @narrow(i16 -3, i16 2)
  %n1 = call i16 @narrow(i16 30000, i16 -30000)
  %l0 = call i64 @loop_guard(ptr %w0, i64 3)
  %h0 = call i64 @shared(i64 4, i64 4)
  %h1 = call i64 @shared(i64 4, i64 5)
  %a0 = call i64 @apart(i64 3, i64 4)
  %a1 = call i64 @apart(i64 6, i64 6)
  %b0 = call i1 @boolsel(i64 9, i1 1, i1 0)
  %b1 = call i1 @boolsel(i64 2, i1 1, i1 0)
  %c1 = icmp eq i64 %m0, 3
  %c2 = icmp eq i64 %m1, 2
  %c3 = icmp eq i32 %u0, 1
  %c4 = icmp eq i32 %u1, 5
  %c5 = icmp eq i64 %s0, 1
  %c6 = icmp eq i64 %s1, 2
  %c7 = icmp eq i16 %n0, -3
  %c8 = icmp eq i16 %n1, -30000
  %c9 = icmp eq i64 %l0, 123
  %c10 = icmp eq i64 %h0, 11
  %c11 = icmp eq i64 %h1, 20
  %c12 = icmp eq i64 %a0, 12
  %c13 = icmp eq i64 %a1, 5
  %c14 = xor i1 %b1, 1
  %r1 = and i1 %ok_g0, %ok_g1
  %r2 = and i1 %r1, %c1
  %r3 = and i1 %r2, %c2
  %r4 = and i1 %r3, %c3
  %r5 = and i1 %r4, %c4
  %r6 = and i1 %r5, %c5
  %r7 = and i1 %r6, %c6
  %r8 = and i1 %r7, %c7
  %r9 = and i1 %r8, %c8
  %r10 = and i1 %r9, %c9
  %r11 = and i1 %r10, %c10
  %r12 = and i1 %r11, %c11
  %r13 = and i1 %r12, %c12
  %r14 = and i1 %r13, %c13
  %r15 = and i1 %r14, %b0
  %r16 = and i1 %r15, %c14
  %status = select i1 %r16, i32 0, i32 81
  ret i32 %status
}
'''

FUSED = {
    "null_guard": "eq",
    "smax": "gt",
    "umin32": "lo",
    "slt_const": "lt",
    "narrow": "lt",
    "loop_guard": "eq",
}


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_a_compare_feeding_only_the_next_select_sets_its_flags(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name, condition in FUSED.items():
        body = _body(assembly, name)
        assert re.search(r"\bf?csel [wx]\d+, [wx]\d+, [wx]\d+, " + condition + r"\b", body), (name, body)
        assert not re.search(r"\bcset\b", body), (name, body)
        # Nothing between the compare and the select sets the flags again.
        select = body.index("csel ")
        between = body[body.rindex("cmp ", 0, select):select].split("\n", 1)[1]
        assert not re.search(r"\b(cmp|cmn|tst|adds|subs|ands|ccmp|fcmp)\b", between), (name, body)
    for name in ("shared", "apart"):
        assert re.search(r"\bcset\b", _body(assembly, name)), name


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
def test_fused_and_kept_selects_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "icmp_select"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
