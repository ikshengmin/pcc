"""Top-tested loops are laid out with the header after the latch.

LLVM's MachineBlockPlacement rotates a loop so that its latch falls through
into the header and the header's conditional branch returns to the body:
one taken branch per iteration instead of the header's not-taken exit test
plus the latch's ``b header``. The self backend emitted blocks in IR order,
so every iteration of ``head: cmp; b.ge exit; body: ...; b head`` paid both
(the rc-base step record executed 7927 such back-edge ``b`` per 600000
instructions; docs/status.md, item 3 P0 notes). The layout places the
loop's blocks other than the header first, then the header, then the
header's exit; blocks between the header and the latch that are outside the
loop move before the loop. pcc-Python loops poll for a thread safepoint
on the backedge (``br %need, %slow, %head`` with ``slow: call; br %head``):
the polling block is the latch that falls into the header, and the slow
block moves before the loop. The entry block never moves; a loop
whose header is its latch, or whose header has no single exit, keeps the
IR order. Every layout runs natively.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @sum(ptr %p, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %body ]
  %acc = phi i64 [ 0, %entry ], [ %acc2, %body ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  %v = load i64, ptr %a, align 8
  %acc2 = add i64 %acc, %v
  %next = add i64 %i, 1
  br label %head
done:
  ret i64 %acc
}
define i64 @find(ptr %p, i64 %n, i64 %value) {
entry:
  %want = and i64 %value, 255
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %step ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %missing
body:
  %a = getelementptr i8, ptr %p, i64 %i
  %b = load i8, ptr %a, align 1
  %w = zext i8 %b to i64
  %hit = icmp eq i64 %w, %want
  br i1 %hit, label %found, label %step
missing:
  ret i64 -1
found:
  ret i64 %i
step:
  %next = add i64 %i, 1
  br label %head
}
define i64 @nested(i64 %n, i64 %m) {
entry:
  br label %outer
outer:
  %i = phi i64 [ 0, %entry ], [ %i2, %outer_latch ]
  %t = phi i64 [ 0, %entry ], [ %t3, %outer_latch ]
  %oc = icmp slt i64 %i, %n
  br i1 %oc, label %inner_pre, label %out
inner_pre:
  br label %inner
inner:
  %j = phi i64 [ 0, %inner_pre ], [ %j2, %inner_body ]
  %t2 = phi i64 [ %t, %inner_pre ], [ %t4, %inner_body ]
  %ic = icmp slt i64 %j, %m
  br i1 %ic, label %inner_body, label %outer_latch
inner_body:
  %prod = mul i64 %i, %j
  %t4 = add i64 %t2, %prod
  %j2 = add i64 %j, 1
  br label %inner
outer_latch:
  %t3 = add i64 %t2, 1
  %i2 = add i64 %i, 1
  br label %outer
out:
  ret i64 %t
}
define i64 @bottom_tested(i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i2, %loop ]
  %s = phi i64 [ 0, %entry ], [ %s2, %loop ]
  %s2 = add i64 %s, %i
  %i2 = add i64 %i, 1
  %c = icmp slt i64 %i2, %n
  br i1 %c, label %loop, label %exit
exit:
  ret i64 %s2
}
define i64 @exit_first(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %i2, %latch ]
  %c = icmp sge i64 %i, %n
  br i1 %c, label %done, label %latch
done:
  ret i64 %i
latch:
  %i2 = add i64 %i, 2
  br label %head
}
@stop = global i32 0
@polls = global i64 0
define void @slow_poll() {
entry:
  %n = load i64, ptr @polls, align 8
  %n2 = add i64 %n, 1
  store i64 %n2, ptr @polls, align 8
  store i32 0, ptr @stop, align 4
  ret void
}
define i64 @polled(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %i2, %latch ], [ %i2, %slow ]
  %s = phi i64 [ 0, %entry ], [ %s2, %latch ], [ %s2, %slow ]
  %c = icmp slt i64 %i, %n
  br i1 %c, label %body, label %done
body:
  %s2 = add i64 %s, %i
  %odd = and i64 %i, 3
  %ask = icmp eq i64 %odd, 2
  br i1 %ask, label %request, label %latch
request:
  store i32 1, ptr @stop, align 4
  br label %latch
latch:
  %i2 = add i64 %i, 1
  %flag = load atomic i32, ptr @stop monotonic, align 4
  %need = icmp ne i32 %flag, 0
  br i1 %need, label %slow, label %head
slow:
  call void @slow_poll()
  br label %head
done:
  ret i64 %s
}
define i32 @main() {
entry:
  %words = alloca [6 x i64], align 8
  %w0 = getelementptr [6 x i64], ptr %words, i64 0, i64 0
  store i64 3, ptr %w0, align 8
  %w1 = getelementptr [6 x i64], ptr %words, i64 0, i64 1
  store i64 5, ptr %w1, align 8
  %w2 = getelementptr [6 x i64], ptr %words, i64 0, i64 2
  store i64 7, ptr %w2, align 8
  %w3 = getelementptr [6 x i64], ptr %words, i64 0, i64 3
  store i64 11, ptr %w3, align 8
  %w4 = getelementptr [6 x i64], ptr %words, i64 0, i64 4
  store i64 13, ptr %w4, align 8
  %w5 = getelementptr [6 x i64], ptr %words, i64 0, i64 5
  store i64 17, ptr %w5, align 8
  %s6 = call i64 @sum(ptr %words, i64 6)
  %s0 = call i64 @sum(ptr %words, i64 0)
  %f7 = call i64 @find(ptr %words, i64 48, i64 7)
  %f9 = call i64 @find(ptr %words, i64 48, i64 9)
  %f0 = call i64 @find(ptr %words, i64 0, i64 3)
  %n34 = call i64 @nested(i64 3, i64 4)
  %n00 = call i64 @nested(i64 0, i64 4)
  %n30 = call i64 @nested(i64 3, i64 0)
  %b5 = call i64 @bottom_tested(i64 5)
  %e7 = call i64 @exit_first(i64 7)
  %p10 = call i64 @polled(i64 10)
  %pc = load i64, ptr @polls, align 8
  %c1 = icmp eq i64 %s6, 56
  %c2 = icmp eq i64 %s0, 0
  %c3 = icmp eq i64 %f7, 16
  %c4 = icmp eq i64 %f9, -1
  %c5 = icmp eq i64 %f0, -1
  %c6 = icmp eq i64 %n34, 21
  %c7 = icmp eq i64 %n00, 0
  %c8 = icmp eq i64 %n30, 3
  %c9 = icmp eq i64 %b5, 10
  %c10 = icmp eq i64 %e7, 8
  %a1 = and i1 %c1, %c2
  %a2 = and i1 %a1, %c3
  %a3 = and i1 %a2, %c4
  %a4 = and i1 %a3, %c5
  %a5 = and i1 %a4, %c6
  %a6 = and i1 %a5, %c7
  %a7 = and i1 %a6, %c8
  %a8 = and i1 %a7, %c9
  %a9 = and i1 %a8, %c10
  %c11 = icmp eq i64 %p10, 45
  %c12 = icmp eq i64 %pc, 2
  %a10 = and i1 %a9, %c11
  %a11 = and i1 %a10, %c12
  %status = select i1 %a11, i32 0, i32 71
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> list[str]:
    text = re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _position(lines: list[str], label: str) -> int:
    return lines.index(label + ":")


def _rotated_by(lines: list[str], name: str, header: str, first_in_loop: str, latch: str) -> None:
    head = "L_" + name + "_" + header
    body = "L_" + name + "_" + first_in_loop
    assert _position(lines, body) < _position(lines, "L_" + name + "_" + latch) < _position(lines, head), lines
    # The latch's last instruction is its conditional branch to the slow
    # block, and the header label follows.
    between = lines[_position(lines, "L_" + name + "_" + latch):_position(lines, head)]
    assert not any(line.startswith("b ") for line in between), lines
    assert any(re.fullmatch(r"(b\.\w+|cbn?z|tbn?z) (.*, )?" + body, line) for line in lines), lines


def _rotated(lines: list[str], name: str, header: str, first_in_loop: str) -> None:
    head = "L_" + name + "_" + header
    body = "L_" + name + "_" + first_in_loop
    # The loop's other blocks come first and the header follows them.
    assert _position(lines, body) < _position(lines, head), lines
    # The header returns to the body with its one conditional branch.
    back = [line for line in lines if re.fullmatch(r"(b\.\w+|cbn?z|tbn?z) (.*, )?" + body, line)]
    assert back, lines
    # Nothing jumps back to the header from inside the loop: the latch falls
    # through, and only the entry edge branches to it.
    jumps = [index for index, line in enumerate(lines) if line == "b " + head]
    assert len(jumps) == 1 and jumps[0] < _position(lines, body), lines


def test_top_tested_loops_rotate(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    _rotated(_body(assembly, "sum"), "sum", "head", "body")
    _rotated(_body(assembly, "find"), "find", "head", "body")
    nested = _body(assembly, "nested")
    _rotated(nested, "nested", "inner", "inner_body")
    _rotated(nested, "nested", "outer", "inner_pre")
    # The exit test falls out of the loop in IR order; rotated, the latch
    # still falls into the header and the header branches back to it.
    first = _body(assembly, "exit_first")
    _rotated(first, "exit_first", "head", "latch")
    assert _position(first, "L_exit_first_head") < _position(first, "L_exit_first_done"), first
    # The polling latch falls into the header, the header into its exit;
    # the slow poll moves before the loop, out of the iteration's path.
    polled = _body(assembly, "polled")
    _rotated_by(polled, "polled", "head", "body", "latch")
    assert _position(polled, "L_polled_slow") < _position(polled, "L_polled_body") < _position(polled, "L_polled_head") < _position(polled, "L_polled_done"), polled
    # find: the latch "step" falls into the header; the early return "found"
    # is not in the loop and is placed before it.
    find = _body(assembly, "find")
    assert _position(find, "L_find_found") < _position(find, "L_find_body"), find
    assert _position(find, "L_find_step") + 1 < len(find)
    after_step = find[_position(find, "L_find_step"):_position(find, "L_find_head")]
    assert not any(line.startswith("b ") for line in after_step), find


def test_other_loops_keep_their_order(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    # A bottom-tested loop already branches back once per iteration.
    bottom = _body(assembly, "bottom_tested")
    assert _position(bottom, "L_bottom_tested_loop") < _position(bottom, "L_bottom_tested_exit"), bottom
    # The unoptimized path keeps IR order.
    plain = _body(emit_aarch64_darwin_asm(SOURCE, optimize=False), "sum")
    assert _position(plain, "L_sum_head") < _position(plain, "L_sum_body"), plain


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
def test_rotated_loops_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "loops"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
