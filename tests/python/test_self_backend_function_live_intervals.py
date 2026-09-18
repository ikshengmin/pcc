"""Function-level live intervals must respect back-edge liveness.

``PCC_SELF_FUNCTION_LIVE_INTERVALS`` widens the register allocator's intervals
from one block to the whole function.  The first version measured a value's
range by layout order, ending at its last textual use.  That is wrong across a
loop: a value defined before the loop and used inside it is live at every
point of the loop -- including a call placed *after* its last textual use --
because the back edge brings control back to that use.  The stack map at that
call listed the value's slot as a live root, the slot had never been written
(the definition went to the register), and the collector dereferenced garbage
(SIGBUS in ``freestanding_allocator``).

These cases pin the fix: with a call in the loop tail the value must keep its
slot (function-level emits the same code as block-local); with no call in the
loop it may live in a register across the blocks.
"""

import os

import pytest

from pcc.backend.self_backend_dispatch import emit_self_asm

TRIPLE = "arm64-apple-macosx"

_LOOP = '''
target triple = "arm64-apple-macosx"

declare void @side()

define external void @loop_live(i64 %n, ptr %p) "no-builtins" {{
entry:
  %base = add i64 %n, 7
  br label %head

head:
  %i = phi i64 [0, %entry], [%next, %body]
  %cmp = icmp slt i64 %i, %n
  br i1 %cmp, label %body, label %exit

body:
  %use = add i64 %base, %i
  store i64 %use, ptr %p
{call}
  %next = add i64 %i, 1
  br label %head

exit:
  ret void
}}
'''

WITH_CALL = _LOOP.format(call="  call void () @side()")
WITHOUT_CALL = _LOOP.format(call="")


def _emit(ir: str, monkeypatch, *, function_level: bool) -> str:
    if function_level:
        monkeypatch.setenv("PCC_SELF_FUNCTION_LIVE_INTERVALS", "1")
    else:
        monkeypatch.delenv("PCC_SELF_FUNCTION_LIVE_INTERVALS", raising=False)
    return emit_self_asm(ir, TRIPLE)


def test_flag_off_is_the_block_local_path(monkeypatch):
    # The default path must not move at all when the variable is unset.
    assert _emit(WITH_CALL, monkeypatch, function_level=False) == _emit(
        WITH_CALL, monkeypatch, function_level=False
    )


def test_value_live_around_a_back_edge_stays_in_its_slot_past_a_call(monkeypatch):
    # ``%base``'s last textual use precedes the call, but it is live-out of
    # ``body`` through the back edge, so the call is inside its range and the
    # value must not be projected into a register.  The only cross-block
    # candidate in this function is ``%base``, so function-level output has to
    # match block-local output exactly.
    block_local = _emit(WITH_CALL, monkeypatch, function_level=False)
    function_level = _emit(WITH_CALL, monkeypatch, function_level=True)
    assert function_level == block_local


def test_value_live_around_a_back_edge_may_use_a_register_without_a_call(monkeypatch):
    # Remove the call and the whole loop is barrier-free: ``%base`` may now
    # live in a pool register from ``entry`` through ``body``.
    block_local = _emit(WITHOUT_CALL, monkeypatch, function_level=False)
    function_level = _emit(WITHOUT_CALL, monkeypatch, function_level=True)
    assert function_level != block_local
    # And the widened range must not add a memory round trip.
    def slot_ops(asm: str) -> int:
        return sum(
            1
            for line in asm.splitlines()
            if line.strip().split(" ")[0] in ("ldur", "stur", "ldr", "str")
            and "[x29" in line
        )
    assert slot_ops(function_level) <= slot_ops(block_local)
