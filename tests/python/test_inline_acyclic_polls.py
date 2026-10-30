"""Inlining keeps one stop poll per acyclic path and every poll on a cycle.

A callee's entry poll comes along when it is inlined. Where the caller's own
entry already polled and the inlined block is on no cycle, only bounded work
separates the two, so that copy is dropped; a copy inside a loop stays, and
nothing changes in a function whose entry does not poll. The transformed
module is emitted and linked by the owned toolchain and executed.
"""
from __future__ import annotations

import platform
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.ir.optimization.inline import inline_module

POLL = """  %{p}.flag = load atomic i32, ptr @pcc_thread_stop_requested acquire, align 4
  %{p}.need = icmp ne i32 %{p}.flag, 0
  br i1 %{p}.need, label %{p}.slow, label %{p}.cont
{p}.slow:
  call void () @pcc_thread_safepoint()
  br label %{p}.cont
{p}.cont:
"""

MODULE = """target triple = "arm64-apple-darwin"
@pcc_thread_stop_requested = global i32 0, align 4
@polls = global i64 0, align 8

define void @pcc_thread_safepoint() noinline {
entry:
  %old = load i64, ptr @polls, align 8
  %new = add i64 %old, 1
  store i64 %new, ptr @polls, align 8
  ret void
}

define i64 @helper(i64 %x) {
entry:
""" + POLL.format(p="h") + """  %y = add i64 %x, 1
  ret i64 %y
}

define i64 @caller(i64 %a) noinline {
entry:
""" + POLL.format(p="c") + """  %r = call i64 @helper(i64 %a)
  br label %loop
loop:
  %i = phi i64 [ 0, %c.cont ], [ %next, %loop.tail ]
  %s = call i64 @helper(i64 %i)
  br label %loop.tail
loop.tail:
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, 10
  br i1 %done, label %exit, label %loop
exit:
  %sum = add i64 %r, %s
  ret i64 %sum
}

define i64 @no_entry_poll(i64 %a) noinline {
entry:
  %r = call i64 @helper(i64 %a)
  ret i64 %r
}

define i32 @main() {
entry:
  store atomic i32 1, ptr @pcc_thread_stop_requested release, align 4
  %v = call i64 @caller(i64 5)
  %w = call i64 @no_entry_poll(i64 7)
  %polls = load i64, ptr @polls, align 8
  %ok1 = icmp eq i64 %v, 16
  %ok2 = icmp eq i64 %w, 8
  %ok3 = icmp eq i64 %polls, 12
  %ok12 = and i1 %ok1, %ok2
  %ok = and i1 %ok12, %ok3
  br i1 %ok, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 9
}
"""


def _body(text: str, name: str) -> str:
    return text.split("@" + name + "(", 1)[1].split("\n}\n", 1)[0]


def test_inlined_acyclic_poll_is_dropped_and_loop_poll_kept():
    text, changed = inline_module(MODULE, include_definitions=True)
    assert changed
    caller = _body(text, "caller")
    assert caller.count("@pcc_thread_stop_requested acquire") == 2, caller
    assert "%c.flag = load atomic" in caller
    assert "@helper(" not in caller
    # The one inlined poll left is inside the loop.
    loop_part = caller.split("\nloop:", 1)[1].split("\nexit:", 1)[0]
    assert loop_part.count("@pcc_thread_stop_requested acquire") == 1, caller
    # A function whose entry does not poll keeps the inlined poll.
    assert _body(text, "no_entry_poll").count("@pcc_thread_stop_requested acquire") == 1


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
def test_transformed_module_runs(tmp_path):
    text, _changed = inline_module(MODULE, include_definitions=True)
    executable = tmp_path / "polls"
    executable.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    # caller: entry poll + ten loop polls; no_entry_poll: its inlined poll.
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
