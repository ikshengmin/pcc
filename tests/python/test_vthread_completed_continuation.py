"""Completion drops captures only after the owning resume and lease end."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"


class Cell:
    def __init__(self, **unused):
        self.fields = {}
        self.leases = 0
        self.alive = True


def execute_step(mode, kind=1, later_completion=False):
    thread, continuation, none = Cell(), Cell(), Cell()
    thread.fields.update({16: continuation, 32: 2, 40: 2})
    continuation.fields[8] = kind
    state = {"running": False, "failure": None, "detached": False}
    globals_ = {"py_None": none, "pcc_current_virtual_thread_py": None}

    def acquire(cell):
        cell.fields[0].leases += 1
        return 1

    def release(cell, token):
        assert token == 1
        cell.fields[0].leases -= 1

    def store(owner, slot, value):
        obj, offset = slot
        if obj is thread and offset == 16 and value is None:
            assert not state["running"]
            assert continuation.leases == 0
            assert thread.leases == 1
            state["detached"] = True
            continuation.alive = False
        obj.fields[offset] = value

    def complete(owner, result):
        owner.fields[32] = 4
        owner.fields[40] = 0
        return 0

    def resume(owner, context):
        state["running"] = True
        assert context.leases == 1 and context.alive
        if mode == "return":
            complete(owner, none)
        elif mode == "fail":
            state["failure"] = Cell()
        elif mode == "park":
            owner.fields[32] = 3
        # The callback still borrows its continuation after publishing DONE.
        assert context.alive and owner.fields[16] is context
        state["running"] = False
        return 0

    def finish(owner):
        if later_completion:
            # A later carrier can finish after the old carrier relinquishes
            # its run claim. That does not authorize the old step to detach.
            complete(owner, none)
        return 0

    namespace = {
        "py_virtual_thread_poll_ready": lambda: thread,
        "null": lambda: None,
        "ptr_is_null": lambda value: value is None,
        "is_tagged_int": lambda value: False,
        "stack_alloc": lambda size: Cell(),
        "ptr_add": lambda obj, offset: (obj, offset),
        "load_ptr": lambda cell, offset: cell.fields.get(offset),
        "load_i32": lambda cell, offset: cell.fields.get(offset, 0),
        "store_ptr": lambda cell, offset, value: cell.fields.__setitem__(offset, value),
        "pcc_gc_load_ptr": lambda owner, slot: slot[0].fields.get(slot[1]),
        "pcc_gc_store_ptr": store,
        "pcc_gc_foreign_lease_acquire": acquire,
        "pcc_gc_foreign_lease_release": release,
        "atomic_load_i64": lambda obj, offset, order: obj.fields.get(offset, 0),
        "py_continuation_resume_pc": lambda context: 1,
        "py_continuation_resume_abi": lambda context: 1,
        "call_i64_ptr2": lambda pc, owner, context: resume(owner, context),
        "_resume_generator_owned": resume,
        "py_incref": lambda obj: None,
        "py_decref": lambda obj: None,
        "py_virtual_thread_complete": complete,
        "py_virtual_thread_fail": complete,
        "global_load_ptr": lambda name: globals_[name],
        "global_store_ptr": lambda name, value: globals_.__setitem__(name, value),
        "py_handled_context_swap": lambda value: None,
        "py_current_exception": lambda: state["failure"],
        "py_clear_exception": lambda: state.__setitem__("failure", None),
        "_step_finished": finish,
        "PY_TYPE_CONTINUATION": 1,
        "PY_TYPE_GEN": 2,
    }
    names = {"_run_ready_step", "_run_ready_thread"}
    nodes = [node for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    exec(compile(ast.Module(nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    assert namespace["_run_ready_step"]() == 1
    assert thread.leases == continuation.leases == 0
    return thread, continuation, state


@pytest.mark.parametrize("kind", [1, 2])
@pytest.mark.parametrize("mode", ["return", "fail", "implicit-return"])
def test_completed_step_releases_captures_after_resume_and_lease(mode, kind):
    thread, continuation, state = execute_step(mode, kind)
    assert state["detached"] and not continuation.alive
    assert thread.fields[16] is None


@pytest.mark.parametrize("later_completion", [False, True])
def test_parked_step_does_not_retire_another_carriers_continuation(later_completion):
    thread, continuation, state = execute_step("park", later_completion=later_completion)
    assert not state["detached"] and continuation.alive
    assert thread.fields[16] is continuation
