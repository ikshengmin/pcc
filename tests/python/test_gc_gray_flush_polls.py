"""GC1/2 tail-publication requests use the existing safe poll boundaries.

Host IR contracts exercise both production emitters and the exact inliner
recognizer. They do not execute a collector or qualify native self-hosting.
See docs/plans/p0-threaded-concurrent-gc-runtime.md for poll ownership.
"""
from __future__ import annotations

import re

import pytest

from pcc.frontends.python.codegen.core_helpers import CoreHelperMixin
from pcc.frontends.python.codegen.user_function_lowering import (
    _emit_thread_safepoint_poll_llvm,
)
from pcc.ir.compat import ir
from pcc.ir.optimization.inline import _drop_acyclic_inlined_polls, inline_module
from tests.python.test_inline_acyclic_polls import MODULE, POLL, _body


STOP = "pcc_thread_stop_requested"
FLUSH = "pcc_gc_gray_worklist_flush_requested"
PLAIN = "pcc_thread_safepoint"
GATE = "pcc_thread_safepoint_gate"


class _PollHost(CoreHelperMixin):
    def __init__(self, *, library=False, runtime_port=False, enabled=True,
                 function_name="poll_owner"):
        self.module = ir.Module(name="gray_poll_contract")
        self.current_function = ir.Function(
            self.module, ir.FunctionType(ir.VoidType(), []), function_name,
        )
        self.builder = ir.IRBuilder(self.current_function.append_basic_block("entry"))
        self.runtime = {
            name: ir.Function(self.module, ir.FunctionType(ir.VoidType(), []), name)
            for name in (PLAIN, GATE)
        }
        self._python_library = library
        self._runtime_port_module = runtime_port
        self._thread_safepoints_enabled = enabled
        self.serial = 0

    def _fresh(self, name):
        self.serial += 1
        return name + "." + str(self.serial)


def _assert_combined_poll(text, callee):
    for name in (STOP, FLUSH):
        assert "@" + name + " = external global i32" in text
    loads = []
    for name in (STOP, FLUSH):
        match = re.search(r"%([\w.$-]+) = load atomic i32, ptr @" + name
                          + r" acquire, align 4", text)
        assert match, text
        loads.append(match[1])
    pending = re.search(r"%([\w.$-]+) = or i32 %" + re.escape(loads[0])
                        + r", %" + re.escape(loads[1]), text)
    assert pending, text
    need = re.search(r"%([\w.$-]+) = icmp ne i32 %" + re.escape(pending[1])
                     + r", 0", text)
    assert need, text
    assert "br i1 %" + need[1] + ", label %" in text
    assert re.search(r"call void (?:\(\) )?@" + callee + r"\(\)", text)
    assert "@pcc_gc_gray_worklist_flush(" not in text
    assert not re.search(r"store[^\n]*@" + FLUSH, text)


@pytest.mark.parametrize("library,runtime_port,callee", [
    (False, False, GATE), (True, False, PLAIN), (False, True, PLAIN),
])
def test_core_poll_combines_both_requests_without_changing_gate_owner(
    library, runtime_port, callee,
):
    host = _PollHost(library=library, runtime_port=runtime_port)
    host._emit_thread_safepoint()
    host.builder.ret_void()
    _assert_combined_poll(str(host.module), callee)


def test_low_ir_poll_combines_both_requests_through_plain_safepoint():
    host = _PollHost()
    _emit_thread_safepoint_poll_llvm(
        host.builder, host.module, host.current_function, host.runtime,
    )
    host.builder.ret_void()
    _assert_combined_poll(str(host.module), PLAIN)


@pytest.mark.parametrize("condition", ["disabled", "leaf", "terminated"])
def test_core_poll_retains_existing_suppression_conditions(condition):
    host = _PollHost(enabled=condition != "disabled",
                     function_name="py_tuple_len" if condition == "leaf" else "poll_owner")
    if condition == "terminated":
        host.builder.ret_void()
    host._emit_thread_safepoint()
    if condition != "terminated":
        host.builder.ret_void()
    text = str(host.module)
    assert "@" + STOP not in text
    assert "@" + FLUSH not in text
    assert "call " not in text


def test_low_ir_poll_does_not_emit_after_terminator():
    host = _PollHost()
    host.builder.ret_void()
    _emit_thread_safepoint_poll_llvm(
        host.builder, host.module, host.current_function, host.runtime,
    )
    assert "@" + FLUSH not in str(host.module)
    assert "call " not in str(host.module)


def test_low_ir_poll_does_not_append_branch_after_terminated_slow_path(monkeypatch):
    host = _PollHost()
    original_call = host.builder.call

    def terminating_call(callee, args):
        result = original_call(callee, args)
        host.builder.unreachable()
        return result

    monkeypatch.setattr(host.builder, "call", terminating_call)
    _emit_thread_safepoint_poll_llvm(
        host.builder, host.module, host.current_function, host.runtime,
    )
    host.builder.ret_void()
    slow = next(block for block in host.current_function.blocks
                if block.name == "low.safepoint.slow")
    instructions = [str(instruction).strip() for instruction in slow.instructions]
    assert len(instructions) == 2
    assert "@pcc_thread_safepoint()" in instructions[0]
    assert instructions[1] == "unreachable"


DUAL_POLL = POLL.replace(
    "  %{p}.need = icmp ne i32 %{p}.flag, 0",
    "  %{p}.flush = load atomic i32, ptr @pcc_gc_gray_worklist_flush_requested acquire, align 4\n"
    "  %{p}.pending = or i32 %{p}.flag, %{p}.flush\n"
    "  %{p}.need = icmp ne i32 %{p}.pending, 0",
)
DUAL_MODULE = MODULE.replace(
    "@polls = global", "@pcc_gc_gray_worklist_flush_requested = global i32 0, align 4\n@polls = global",
).replace(POLL.format(p="h"), DUAL_POLL.format(p="h")).replace(
    POLL.format(p="c"), DUAL_POLL.format(p="c"),
)


def test_dual_request_inlining_keeps_loop_and_uncovered_entry_polls():
    text, changed = inline_module(DUAL_MODULE, include_definitions=True)
    assert changed
    caller = _body(text, "caller")
    assert "@helper(" not in caller
    for flag in (STOP, FLUSH):
        assert caller.count("@" + flag + " acquire") == 2, caller
        loop = caller.split("\nloop:", 1)[1].split("\nexit:", 1)[0]
        assert loop.count("@" + flag + " acquire") == 1
        assert _body(text, "no_entry_poll").count("@" + flag + " acquire") == 1


def test_legacy_caller_cannot_cover_an_inlined_publication_poll():
    module = DUAL_MODULE.replace(DUAL_POLL.format(p="c"), POLL.format(p="c"))
    text, _changed = inline_module(module, include_definitions=True)
    caller = _body(text, "caller")
    assert caller.count("@" + STOP + " acquire") == 3
    assert caller.count("@" + FLUSH + " acquire") == 2


def test_dual_request_caller_can_cover_legacy_stop_only_callee():
    module = DUAL_MODULE.replace(DUAL_POLL.format(p="h"), POLL.format(p="h"))
    text, _changed = inline_module(module, include_definitions=True)
    caller = _body(text, "caller")
    assert caller.count("@" + STOP + " acquire") == 2
    assert caller.count("@" + FLUSH + " acquire") == 1


def _coalescing_fixture(inner_poll):
    return ("@" + STOP + " = external global i32\n@" + FLUSH
            + " = external global i32\ndeclare void @pcc_thread_safepoint()\n"
            "define i32 @coverage() {\nentry:\n"
            + DUAL_POLL.format(p="entry")
            + "  br label %inl.cfg.1.entry\ninl.cfg.1.entry:\n"
            + inner_poll + "  ret i32 0\n}\n")


@pytest.mark.parametrize("before,after", [
    ("flush_requested acquire", "flush_requested monotonic"),
    (" = or i32 ", " = and i32 "),
    ("icmp ne i32 %inner.pending", "icmp ne i32 %inner.flag"),
    ("%inner.flag, %inner.flush", "%inner.flag, %entry.flush"),
    ("%inner.pending, 0", "%inner.pending, 1"),
    ("inner.cont:\n", "inner.cont:\n  %extra = add i32 %inner.flush, 1\n"),
    ("inner.cont:\n", "inner.cont:\n  %extra = add i32 %inner.pending, 1\n"),
    ("inner.cont:\n", "inner.cont:\n  %extra = add i32 %inner.flag, 1\n"),
])
def test_coalescing_rejects_inexact_or_shared_dual_poll_values(before, after):
    inner = DUAL_POLL.format(p="inner").replace(before, after)
    module = _coalescing_fixture(inner)
    assert _drop_acyclic_inlined_polls(module) == module


def test_coalescing_removes_all_five_dual_poll_instructions():
    module = _coalescing_fixture(DUAL_POLL.format(p="inner"))
    text = _drop_acyclic_inlined_polls(module)
    assert text != module
    assert "%inner.flag" not in text
    assert "%inner.flush" not in text
    assert "%inner.pending" not in text
    assert "%inner.need" not in text
    assert "inner.slow:" not in text
    assert text.count("@" + FLUSH + " acquire") == 1


@pytest.mark.parametrize("handler", [
    "  br label %entry.cont\n",
    "  call void () @not_a_safepoint()\n  br label %entry.cont\n",
    "  call void () @pcc_thread_safepoint()\n  ret i32 1\n",
])
def test_entry_flag_shape_without_valid_safepoint_cannot_cover_inner_poll(handler):
    module = _coalescing_fixture(DUAL_POLL.format(p="inner"))
    module = module.replace("declare void @pcc_thread_safepoint()\n",
                            "declare void @pcc_thread_safepoint()\n"
                            "declare void @not_a_safepoint()\n")
    module = module.replace(
        "entry.slow:\n  call void () @pcc_thread_safepoint()\n  br label %entry.cont\n",
        "entry.slow:\n" + handler,
    )
    assert _drop_acyclic_inlined_polls(module) == module
