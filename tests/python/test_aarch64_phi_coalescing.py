"""Phi coalescing in the self AArch64 register allocator.

A phi and an incoming value whose intervals do not overlap share one
register group, so the edge copy between them moves nothing
(``_phi_copy_schedule`` skips a copy with equal source and destination).
Register copies were 19.1% of the self backend's excess executed
instructions over clang (docs/status.md, P0 exact ranking); phi edge copies
are a large part of them.

Overlapping values (a phi swap cycle, a loop value still used after the
loop) must stay apart: the programs below compute known results natively.
"""
import subprocess

import pytest

from pcc.backend import self_backend_aarch64_darwin_regalloc as regalloc
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_asm,
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_parse import parse_self_backend_module


def test_groups_merge_only_disjoint_intervals():
    intervals = [(0, 4, 1, False), (4, 9, 2, True), (3, 8, 3, False), (10, 12, 4, False)]
    groups, members = regalloc._merge_alias_groups(intervals, [], [(1, 2), (2, 3), (4, 2)])
    by_root = {root: (start, end, needs) for start, end, root, needs in groups}
    # 1 ends where 2 starts: one group, which needs callee-saved for both.
    assert sorted(members[1]) == [1, 2, 4]
    assert by_root[1] == (0, 12, True)
    # 3 overlaps 1 and 2: it keeps its own register.
    assert members[3] == [3] and by_root[3] == (3, 8, False)


def test_merge_respects_existing_alias_members():
    # 5 aliases 6 (same bits, overlapping); 7 overlaps 6, so it cannot join.
    intervals = [(0, 5, 5, False), (2, 8, 6, False), (6, 9, 7, False)]
    groups, members = regalloc._merge_alias_groups(intervals, [(6, 5)], [(7, 5)])
    assert sorted(members[5]) == [5, 6] and members[7] == [7]


def test_oversized_groups_are_not_checked_pairwise(monkeypatch):
    monkeypatch.setattr(regalloc, "_COALESCE_CHECK_LIMIT", 1)
    intervals = [(0, 1, 1, False), (1, 2, 2, False), (2, 3, 3, False)]
    groups, members = regalloc._merge_alias_groups(intervals, [], [(1, 2), (2, 3)])
    # {1, 2} then {1, 2} x {3} is 2 > 1 pairs: refused.
    assert sorted(members[1]) == [1, 2] and members[3] == [3]


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @f(i64 %x) {
entry:
  %d = shl i64 %x, 1
  %r = add i64 %d, 1
  ret i64 %r
}
define i64 @merge(i64 %a, i64 %b) {
entry:
  %c = icmp slt i64 %a, %b
  br i1 %c, label %then, label %else
then:
  %x = call i64 @f(i64 %a)
  br label %join
else:
  %y = call i64 @f(i64 %b)
  br label %join
join:
  %r = phi i64 [ %x, %then ], [ %y, %else ]
  %s = call i64 @f(i64 %r)
  %t = add i64 %s, %r
  ret i64 %t
}
define i64 @loop(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %head ]
  %acc = phi i64 [ 0, %entry ], [ %acc2, %head ]
  %v = call i64 @f(i64 %i)
  %acc2 = add i64 %acc, %v
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, %n
  br i1 %done, label %exit, label %head
exit:
  ret i64 %acc2
}
define i64 @swap(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %head ]
  %a = phi i64 [ 1, %entry ], [ %b, %head ]
  %b = phi i64 [ 2, %entry ], [ %a, %head ]
  %v = call i64 @f(i64 %a)
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, %n
  br i1 %done, label %exit, label %head
exit:
  %w = mul i64 %a, 10
  %z = add i64 %w, %b
  ret i64 %z
}
define i64 @after(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %head ]
  %v = call i64 @f(i64 %i)
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, %n
  br i1 %done, label %exit, label %head
exit:
  %k = mul i64 %i, 100
  %z = add i64 %k, %next
  ret i64 %z
}
define i32 @main() {
entry:
  %m1 = call i64 @merge(i64 1, i64 5)
  %m2 = call i64 @merge(i64 9, i64 5)
  %l = call i64 @loop(i64 5)
  %s3 = call i64 @swap(i64 3)
  %s4 = call i64 @swap(i64 4)
  %a5 = call i64 @after(i64 5)
  %ok1 = icmp eq i64 %m1, 10
  %ok2 = icmp eq i64 %m2, 34
  %ok3 = icmp eq i64 %l, 25
  %ok4 = icmp eq i64 %s3, 12
  %ok5 = icmp eq i64 %s4, 21
  %ok6 = icmp eq i64 %a5, 405
  %a = and i1 %ok1, %ok2
  %b = and i1 %a, %ok3
  %c = and i1 %b, %ok4
  %d = and i1 %c, %ok5
  %e = and i1 %d, %ok6
  %status = select i1 %e, i32 0, i32 43
  ret i32 %status
}
'''


def _body(assembly, name):
    lines = assembly.split("\n")
    start = lines.index("_" + name + ":")
    end = start + 1
    while end < len(lines) and not lines[end].startswith(("_", ".p2align", ".section")):
        end += 1
    return lines[start:end]


def test_loop_backedge_moves_nothing_and_merge_keeps_one_result_copy():
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    loop = _body(assembly, "loop")
    branch = next(i for i, line in enumerate(loop) if line.startswith("  b.eq L_loop_exit"))
    assert loop[branch + 1] == "  b L_loop_head", loop
    merge = _body(assembly, "merge")
    for label in ("L_merge_then:", "L_merge_else:"):
        block = merge[merge.index(label):]
        call = block.index("  bl _f")
        after = []
        for line in block[call + 1:]:
            if line == "" or line.startswith("  b ") or (line.endswith(":") and not line.startswith("L_pcc_smap")):
                break
            after.append(line)
        # The call result goes straight into the phi's register.
        moves = [line for line in after if line.startswith("  mov ")]
        assert len(moves) == 1 and moves[0].endswith(", x0"), block


def test_native_bytes_equal_the_assembled_text():
    assembly = emit_aarch64_darwin_indexed_module(parse_self_backend_module(SOURCE), optimize=True)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(SOURCE), optimize=True, structured_instructions=True,
    )
    assert transport.assemble_sections() == assemble_file(assembly)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("optimize", (False, True))
def test_phi_programs_execute(tmp_path, optimize):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(SOURCE), optimize=optimize, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "phi"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
