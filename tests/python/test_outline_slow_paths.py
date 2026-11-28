"""outline-slow-paths keeps a function's frameless fast exits and moves the
paths that need a frame into internal functions entered by tail calls.

Structural checks run on the host; the native checks emit the original
and the outlined module with the self AArch64 backend, link them with
pcc's linker and compare what each program computes, so every cut, phi
parameter, widened value and copied rejoin is executed, not only printed.
"""
from __future__ import annotations

import platform
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.ir.optimization.outline_slow_paths import (
    outline_slow_paths_sized,
    outline_slow_paths_text,
)


HEADER = 'target triple = "arm64-apple-darwin"\n'


def _function(text: str, name: str) -> str:
    match = re.search(r"^define [^\n]*@" + re.escape(name) + r"\(.*?^}\n", text, re.S | re.M)
    assert match, text
    return match.group(0)


def _outline(text: str) -> tuple[str, bool]:
    return outline_slow_paths_sized(text, 0)


# A fast null exit, and a slow path that needs stack memory and a call.
SIMPLE = HEADER + '''
@sink = global i64 0, align 8
define void @fill(ptr %buf, ptr %o) {
entry:
  %v = load i64, ptr %o, align 8
  store i64 %v, ptr %buf, align 8
  ret void
}
define i64 @probe(ptr %o, i64 %n) "no-builtins" {
entry:
  %null = icmp eq ptr %o, null
  br i1 %null, label %fast, label %slow
fast:
  %k = load i64, ptr @sink, align 8
  %f = add i64 %k, 7
  ret i64 %f
slow:
  %buf = alloca [16 x i8]
  call void @fill(ptr %buf, ptr %o)
  %v = load i64, ptr %buf, align 8
  %r = add i64 %v, %n
  ret i64 %r
}
'''


def test_the_slow_path_becomes_a_tail_called_internal_function():
    text, changed = _outline(SIMPLE)
    assert changed
    probe = _function(text, "probe")
    assert "alloca" not in probe and "@fill(" not in probe
    assert re.search(r"%slow\.result\.0 = call i64 \(ptr, i64\) @probe\.slow\.0\(ptr %o, i64 %n\)\n"
                     r"  ret i64 %slow\.result\.0", probe), probe
    slow = _function(text, "probe.slow.0")
    assert slow.startswith('define internal i64 @probe.slow.0(ptr %o, i64 %n) noinline "no-builtins" {')
    assert "alloca [16 x i8]" in slow and "call void @fill(ptr %buf, ptr %o)" in slow


def test_the_definition_inliner_does_not_put_a_slow_path_back():
    from pcc.frontends.python.compiled_owned_passes import WIDE_DEFINITION_LIMIT
    from pcc.ir.optimization.inline import inline_module

    # The loop's slow path has no alloca: without noinline the runtime's
    # object-phase inliner put such paths back (pcc_gc_release_known).
    text, _changed = _outline(LOOP)
    inlined, _ = inline_module(text, include_definitions=True,
                               definition_limit=WIDE_DEFINITION_LIMIT)
    probe = _function(inlined, "probe")
    assert "@probe.slow.0(" in probe and "@tick(" not in probe


def test_the_kept_function_has_no_frame():
    text, _changed = _outline(SIMPLE)
    assembly = emit_aarch64_darwin_asm(text, optimize=True)
    body = assembly.split("\n_probe:", 1)[1].split("\n.p2align", 1)[0]
    assert "x29" not in body and "sp" not in body, body
    assert re.search(r"\bb \S*_probe\.slow\.0\n", body), body


# A call in a loop cannot be a slow-path entry: the cut moves to the
# loop's preheader, and the whole loop is outlined.
LOOP = HEADER + '''
@ticks = global i64 0, align 8
define void @tick() {
entry:
  %t = load i64, ptr @ticks, align 8
  %u = add i64 %t, 1
  store i64 %u, ptr @ticks, align 8
  ret void
}
define i64 @probe(ptr %p) {
entry:
  %null = icmp eq ptr %p, null
  br i1 %null, label %done, label %pre
done:
  %z = load i64, ptr @ticks, align 8
  ret i64 %z
pre:
  %start = load i64, ptr %p, align 8
  br label %loop
loop:
  %i = phi i64 [ %start, %pre ], [ %next, %poll ]
  %c = icmp sgt i64 %i, 0
  br i1 %c, label %poll, label %exit
poll:
  call void @tick()
  %next = sub i64 %i, 1
  br label %loop
exit:
  %t = load i64, ptr @ticks, align 8
  ret i64 %t
}
'''


def test_a_call_on_a_cycle_moves_the_cut_before_the_loop():
    text, changed = _outline(LOOP)
    assert changed
    probe = _function(text, "probe")
    assert "loop:" not in probe and "pre:" not in probe
    assert "@probe.slow.0(ptr %p)" in probe
    slow = _function(text, "probe.slow.0")
    assert re.search(r"^pre:\n", slow, re.M) and "loop:" in slow and "call void @tick()" in slow


# The slow-path entry merges two kept edges: its phi becomes a parameter
# each cut passes its own value for, and an i1 crosses widened.
PHI = HEADER + '''
@base = global i64 -1, align 8
define void @work(ptr %p) {
entry:
  store i64 1, ptr %p, align 8
  ret void
}
define i64 @probe(ptr %p, i64 %k) {
entry:
  %small = icmp slt i64 %k, 10
  %null = icmp eq ptr %p, null
  br i1 %null, label %fast, label %check
fast:
  %b0 = load i64, ptr @base, align 8
  ret i64 %b0
check:
  %big = icmp sgt i64 %k, 100
  br i1 %big, label %slow, label %middle
middle:
  %zero = icmp eq i64 %k, 0
  br i1 %zero, label %fast, label %slow
slow:
  %w = phi i64 [ 5, %check ], [ 9, %middle ]
  call void @work(ptr %p)
  %v = load i64, ptr %p, align 8
  %s = select i1 %small, i64 100, i64 200
  %a = add i64 %v, %w
  %b = add i64 %a, %s
  ret i64 %b
}
'''


def test_entry_phis_become_parameters_and_narrow_values_widen():
    text, changed = _outline(PHI)
    assert changed
    probe = _function(text, "probe")
    calls = re.findall(r"call i64 \(ptr, i64, i64\) @probe\.slow\.0\(ptr %p, i64 %slow\.wide\.\d+\.0, i64 (\d+)\)",
                       probe)
    assert sorted(calls) == ["5", "9"], probe
    assert probe.count("zext i1 %small to i64") == 2
    slow = _function(text, "probe.slow.0")
    assert "(ptr %p, i64 %small.wide, i64 %w)" in slow
    assert "%small = trunc i64 %small.wide to i1" in slow
    assert "phi" not in slow


def test_too_many_live_values_leave_the_function_alone():
    params = ", ".join("i64 %a" + str(index) for index in range(9))
    uses = "".join("  %s" + str(index) + " = add i64 %acc" + str(index) + ", %a" + str(index) + "\n"
                   "  %acc" + str(index + 1) + " = add i64 %s" + str(index) + ", 0\n"
                   for index in range(9))
    text = HEADER + '''
declare void @g()
define i64 @probe(ptr %p, ''' + params + ''') {
entry:
  %null = icmp eq ptr %p, null
  br i1 %null, label %fast, label %slow
fast:
  ret i64 0
slow:
  call void @g()
  %acc0 = add i64 0, 0
''' + uses + '''  ret i64 %acc9
}
'''
    assert _outline(text) == (text, False)


def test_no_fast_exit_means_no_outlining():
    text = HEADER + '''
declare void @g()
define void @probe(ptr %p) {
entry:
  %null = icmp eq ptr %p, null
  br i1 %null, label %a, label %b
a:
  call void @g()
  br label %b
b:
  call void @g()
  store i64 1, ptr %p, align 8
  ret void
}
'''
    assert _outline(text) == (text, False)


def test_an_argument_test_alone_is_not_a_fast_exit():
    # free(NULL) returns at once; every real call needs the slow path.
    text = HEADER + '''
declare void @release(ptr)
define void @probe(ptr %p) {
entry:
  %null = icmp eq ptr %p, null
  br i1 %null, label %done, label %work
done:
  ret void
work:
  %buf = alloca [8 x i8]
  call void @release(ptr %buf)
  store i64 0, ptr %p, align 8
  ret void
}
'''
    assert _outline(text) == (text, False)


def test_only_aarch64_modules_are_outlined():
    # The x86-64 backends make no sibling call: an outlined slow path would
    # add a call while the fast path kept its frame.
    for triple in ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"):
        text = SIMPLE.replace("arm64-apple-darwin", triple)
        assert _outline(text) == (text, False)
    linux = SIMPLE.replace("arm64-apple-darwin", "aarch64-unknown-linux-gnu")
    assert _outline(linux)[1]


def test_small_functions_stay_cross_module_inline_candidates():
    assert outline_slow_paths_text(SIMPLE) == (SIMPLE, False)


# The slow path rejoins kept code: the rejoined block is copied into the
# slow function, and the kept copy keeps only its kept predecessors.
REJOIN = HEADER + '''
@cache = global i64 -1, align 8
define i64 @compute(i64 %x) {
entry:
  %y = mul i64 %x, 3
  ret i64 %y
}
define i64 @probe(i64 %x) {
entry:
  %cached = load i64, ptr @cache, align 8
  %missing = icmp slt i64 %cached, 0
  br i1 %missing, label %fill, label %use
fill:
  %fresh = call i64 @compute(i64 %x)
  store i64 %fresh, ptr @cache, align 8
  br label %use
use:
  %value = phi i64 [ %cached, %entry ], [ %fresh, %fill ]
  %r = add i64 %value, %x
  ret i64 %r
}
'''


def test_a_rejoining_slow_path_copies_the_rest():
    text, changed = _outline(REJOIN)
    assert changed
    probe = _function(text, "probe")
    assert "fill:" not in probe
    assert re.search(r"%value = phi i64 \[ %cached, %entry \]\n", probe), probe
    slow = _function(text, "probe.slow.0")
    assert re.search(r"%value = phi i64 \[ %fresh, %fill \]\n", slow), slow


# Two cuts. The first found (s0, from p0) redirects p0; the second (s1,
# from q, later in block order) rejoins at p0, so its copy holds p0. That
# copy must keep p0's original branch to s0, not the first cut's stub.
TWO_CUTS = HEADER + '''
@log = global i64 0, align 8
define i64 @compute(i64 %x) {
entry:
  %y = mul i64 %x, 3
  ret i64 %y
}
define void @note(i64 %x) {
entry:
  store i64 %x, ptr @log, align 8
  ret void
}
define i64 @probe(i64 %x) {
entry:
  %even = and i64 %x, 1
  %odd = icmp ne i64 %even, 0
  br i1 %odd, label %q, label %p0
p0:
  %v = phi i64 [ %x, %entry ], [ %x, %q ], [ %fresh, %s1 ]
  %big = icmp sgt i64 %v, 100
  br i1 %big, label %s0, label %done
s0:
  call void @note(i64 %v)
  %l = load i64, ptr @log, align 8
  %r1 = add i64 %l, 1
  ret i64 %r1
done:
  %l2 = load i64, ptr @log, align 8
  %r = add i64 %v, %l2
  ret i64 %r
q:
  %small = icmp slt i64 %x, 40
  br i1 %small, label %s1, label %p0
s1:
  %fresh = call i64 @compute(i64 %x)
  br label %p0
}
'''


def test_a_rejoining_copy_keeps_the_original_branches_of_later_cuts():
    text, changed = _outline(TWO_CUTS)
    assert changed
    for name in ("probe.slow.0", "probe.slow.1"):
        body = _function(text, name)
        labels = set(re.findall(r"^([\w.$-]+):", body, re.M))
        targets = set(re.findall(r"label %([\w.$-]+)", body))
        assert targets <= labels, (name, body)
    emit_aarch64_darwin_asm(text, optimize=True)


def test_a_call_that_only_branches_to_a_return_becomes_a_tail_call():
    text = HEADER + '''
declare void @g(ptr)
declare void @h()
define void @probe(ptr %p) {
entry:
  %null = icmp eq ptr %p, null
  br i1 %null, label %done, label %call
call:
  call void @g(ptr %p)
  br label %done
done:
  ret void
}
'''
    # The call gets the return and becomes a tail call: nothing needs a
    # frame, nothing is outlined.
    outlined, changed = _outline(text)
    assert changed and "@probe.slow" not in outlined
    assert re.search(r"call void @g\(ptr %p\)\n  ret void\n", outlined), outlined
    assembly = emit_aarch64_darwin_asm(outlined, optimize=True)
    assert "x29" not in assembly.split("_probe:", 1)[1].split(".section", 1)[0]


def _main(calls: str) -> str:
    return '''
define i32 @main() {
entry:
  %cell = alloca i64, align 8
  store i64 0, ptr %cell, align 8
''' + calls + '''  %folded = trunc i64 %sum to i32
  %status = and i32 %folded, 127
  ret i32 %status
}
'''


PROGRAMS = {
    "simple": SIMPLE + _main('''  store i64 40, ptr %cell, align 8
  %a = call i64 @probe(ptr null, i64 3)
  %b = call i64 @probe(ptr %cell, i64 3)
  %sum = add i64 %a, %b
'''),
    "loop": LOOP + _main('''  store i64 5, ptr %cell, align 8
  %a = call i64 @probe(ptr null)
  %b = call i64 @probe(ptr %cell)
  %c = call i64 @probe(ptr %cell)
  %ab = add i64 %a, %b
  %sum = add i64 %ab, %c
'''),
    "phi": PHI + _main('''  %a = call i64 @probe(ptr null, i64 3)
  %b = call i64 @probe(ptr %cell, i64 3)
  %c = call i64 @probe(ptr %cell, i64 500)
  %d = call i64 @probe(ptr %cell, i64 0)
  %ab = add i64 %a, %b
  %abc = add i64 %ab, %c
  %sum = add i64 %abc, %d
'''),
    "two_cuts": TWO_CUTS + _main('''  %a = call i64 @probe(i64 35)
  %b = call i64 @probe(i64 5)
  %c = call i64 @probe(i64 6)
  %d = call i64 @probe(i64 41)
  %ab = add i64 %a, %b
  %abc = add i64 %ab, %c
  %sum = add i64 %abc, %d
'''),
    "rejoin": REJOIN + _main('''  %a = call i64 @probe(i64 4)
  %b = call i64 @probe(i64 5)
  %sum = add i64 %a, %b
'''),
}


def _run(text: str, tmp_path, name: str) -> int:
    output = tmp_path / name
    output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")], entry="_main"))
    output.chmod(0o755)
    return subprocess.run([str(output)], capture_output=True, timeout=10).returncode


@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_outlined_programs_compute_what_the_originals_compute(program, tmp_path):
    original = PROGRAMS[program]
    outlined, changed = _outline(original)
    assert changed
    expected = _run(original, tmp_path, program + "-original")
    assert _run(outlined, tmp_path, program + "-outlined") == expected
    assert expected != 0
