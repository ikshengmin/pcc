"""GC4 copy keeps live weakref registration until forwarding commits.

Production public copy, payload planning/slots, forwarding installation,
pinning and weakref deallocation run over the existing finite GC3 provider.
Allocation, retained counts, reserved index capacity and the checked RC tail
are explicit doubles. This is not native execution, a concurrent race, a
stop-the-world admission proof or complete payload rollback coverage.
"""
from __future__ import annotations

import ast

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc_forwarding_install_pin_interleaving import _fixture
from tests.python.test_gc3_norc_source_retirement import PY


COPY = PY / "freestanding_gc_relocation_copy.py"
PAYLOAD = PY / "freestanding_gc_relocation_payload.py"


@pytest.mark.parametrize("position", ["head", "middle", "tail", "only"])
@pytest.mark.parametrize("outcome", ["pin", "index", "success"])
@pytest.mark.parametrize("stopped", [False, True])
def test_public_copy_weakref_registration_survives_install_rejection(
    position, outcome, stopped
):
    state, source, target, plan = _fixture(4, "preallocated")
    state.stopped = stopped
    ns = state.ns
    tag = ns["abi_constant"]("object.type.weakref")
    # The scalar fixture reserves larger address intervals than its objects.
    # Give both weakrefs their real 48-byte layout and exact known size.
    for obj in (source, target):
        state.raw[obj] = 48
        state.store(state.index[obj], 8, 48)
        state.store(obj, 8, tag)
        for offset in (16, 24, 32, 40):
            state.store(obj, offset, 0)
    referent, _, _ = state.value()
    callback, _, _ = state.value()
    state.store(source, 16, referent)
    state.store(source, 24, callback)
    prev = state.allocate(48) if position in ("middle", "tail") else 0
    nxt = state.allocate(48) if position in ("head", "middle") else 0
    state.store(source, 32, prev)
    state.store(source, 40, nxt)
    state.store("py_weakref_head", 0, prev or source)
    if prev:
        state.store(prev, 40, source)
    if nxt:
        state.store(nxt, 32, source)
    relocation = state.malloc(16)
    state.store(relocation, 0, source)
    state.store(relocation, 8, 0)
    state.store("pcc_gc_relocation_set_head", 0, relocation)
    allocations, drops, installs, commits, retained = [], [], [], [], []

    def links():
        return (state.load("py_weakref_head"), state.load(source, 32),
                state.load(source, 40), state.load(prev, 40) if prev else 0,
                state.load(nxt, 32) if nxt else 0)

    original = links()

    def alloc(size, actual_tag, flags):
        assert state.depth == 0 and (size, actual_tag) == (48, tag)
        allocations.append(target)
        state.store(target, 12, flags)
        return target

    def prepared_forwarding(from_obj, to_obj):
        # _fixture already ran the real prepare body for these exact objects.
        assert state.depth == 0 and (from_obj, to_obj) == (source, target)
        return plan

    def retain(record, obj):
        assert state.depth > 0
        if obj and not obj & 1:
            state.counted_incref(obj)
            retained.append(obj)
        return obj

    def free_object(obj):
        assert obj == target and state.depth == 0
        state.index.pop(obj)
        state.free(obj)

    def checked_drop(obj):
        assert state.depth == 0
        state.counted_decref(obj)
        drops.append(obj)
        if obj == target:
            assert state.load(obj, 0) == 0
            ns["py_dealloc_weakref"](obj)

    load_functions(COPY, {"pcc_gc_relocate_copy",
                          "pcc_gc_backend4_relocate_copy_preallocated_unlocked"}, ns)
    load_functions(PY / "py_weakref.py", {"py_dealloc_weakref"}, ns)
    install = ns["pcc_gc_install_forwarding_preallocated_unlocked"]
    commit = ns["pcc_gc_relocation_payload_commit_weakref"]
    index_commit = ns["pcc_gc_forwarding_plan_index_commit"]

    def install_after_payload(from_obj, to_obj, prepared):
        assert (from_obj, to_obj, prepared) == (source, target, plan)
        assert state.depth > 0 and links() == original
        assert state.load(target, 32) == state.load(target, 40) == 0
        assert state.load(target, 24) == callback and retained == [callback]
        if outcome == "pin":
            ns["pcc_gc_pin"](source)
        rc = install(from_obj, to_obj, prepared)
        installs.append(rc)
        return rc

    def commit_after_install(from_obj, to_obj, actual_tag):
        assert state.depth > 0 and installs == [0]
        assert state.load(state.forwardings[source], 8) == target
        commits.append((from_obj, to_obj, actual_tag))
        commit(from_obj, to_obj, actual_tag)

    def capacity_commit(*args):
        return -1 if outcome == "index" else index_commit(*args)

    ns.update(pcc_gc_alloc=alloc,
              pcc_gc_forwarding_install_plan_prepare=prepared_forwarding,
              pcc_gc_retain_plan_prepare_locked=retain,
              pcc_gc_backend4_remembered_owner_has_entries=lambda owner: 0,
              pcc_gc_install_forwarding_preallocated_unlocked=install_after_payload,
              pcc_gc_forwarding_plan_index_commit=capacity_commit,
              pcc_gc_relocation_payload_commit_weakref=commit_after_install,
              pcc_gc_backend4_zpage_finish_relocation_detach=lambda node: None,
              _py_decref_checked_tail=checked_drop,
              pcc_gc_free_object_memory=free_object)
    result = ns["pcc_gc_relocate_copy"](source, 48)
    assert allocations == [target]
    assert state.depth == 0 and state.stopped == stopped
    assert state.load(source, 16) == referent
    assert state.load(source, 24) == callback
    assert state.load(referent, 0) == 1
    assert plan in state.freed
    if outcome == "success":
        assert result == target and installs == [0]
        assert commits == [(source, target, tag)] and not drops
        assert state.load("py_weakref_head") == (prev or target)
        assert state.load(target, 32) == prev and state.load(target, 40) == nxt
        assert state.load(source, 32) == source and state.load(source, 40) == 0
        if prev:
            assert state.load(prev, 40) == target
        if nxt:
            assert state.load(nxt, 32) == target
        assert state.load(callback, 0) == 2 and relocation in state.freed
        assert target not in state.freed
    else:
        assert result == 0 and installs == [-2 if outcome == "pin" else -1]
        assert not commits and drops == [target, callback]
        assert links() == original and state.load(callback, 0) == 1
        assert target in state.freed and source not in state.freed
        assert source not in state.forwardings and target not in state.reverse
        assert state.load("pcc_gc_relocation_set_head") == relocation
        assert relocation not in state.freed


def _assert_weakref_commit_body_calls(commit):
    # Export decorators, defaults and annotations are definition metadata,
    # not operations performed by the commit body under the graph lock.
    body_calls = [node for statement in commit.body for node in ast.walk(statement)
                  if isinstance(node, ast.Call)]
    assert all(isinstance(node.func, ast.Name) for node in body_calls)
    calls = {node.func.id for node in body_calls}
    # Reading and comparing the two list heads (an open weak window's late
    # list, py_weakref.py) is allocation-free and cannot fail either.
    assert calls <= {"abi_constant", "load_ptr", "store_ptr", "ptr_is_null",
                     "ptr_eq", "global_load_ptr", "global_store_ptr", "null"}
    assert {"abi_constant", "load_ptr", "store_ptr", "ptr_is_null",
            "global_store_ptr", "null"} <= calls


@pytest.mark.parametrize("forbidden_call", [
    "malloc(48)", "callback()", "c_abi_export('commit')", "callbacks.run()",
])
def test_weakref_commit_body_audit_excludes_metadata_but_rejects_calls(forbidden_call):
    # Parse only: none of these metadata or body operations is executed.
    commit = ast.parse('''
@c_abi_export("commit")
def commit(value: parameter_metadata() = default_metadata()) -> return_metadata():
    if abi_constant("object.type.weakref"):
        prev = load_ptr(value, 32)
        if ptr_is_null(prev):
            global_store_ptr("py_weakref_head", value)
        store_ptr(value, 40, null())
''').body[0]
    _assert_weakref_commit_body_calls(commit)
    # Nested executable calls must still be audited, including a name that
    # is permitted only as metadata and calls through an attribute.
    commit.body.extend(ast.parse("if value:\n    " + forbidden_call).body)
    with pytest.raises(AssertionError):
        _assert_weakref_commit_body_calls(commit)


def test_weakref_commit_has_exact_abi_and_no_failing_or_callback_operations():
    from pcc.frontends.python.codegen.runtime_abi import (
        FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    )

    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_relocation_payload_commit_weakref"
    ] == (("c_ptr", "c_ptr", "c_int64"), "c_void")
    tree = ast.parse(PAYLOAD.read_text(encoding="utf-8"))
    commit = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "pcc_gc_relocation_payload_commit_weakref")
    _assert_weakref_commit_body_calls(commit)
    finish = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_relocate_copy_payload_finish")
    assert "py_weakref_head" not in ast.get_source_segment(PAYLOAD.read_text(), finish)


def test_legacy_payload_wrapper_keeps_successful_weakref_commit():
    events = []
    ns = {"i64": int, "null": lambda: 0, "ptr_is_null": lambda p: int(p == 0),
          "abi_constant": lambda name: 30,
          "_relocate_slot_pairs_prepare": lambda *args: 1,
          "pcc_gc_relocate_copy_payload_prepared_locked": lambda *args: 1,
          "pcc_gc_relocation_payload_commit_weakref":
              lambda *args: events.append(("commit", args)),
          "_relocate_slot_pairs_dispose": lambda ctx: events.append(("dispose", ctx))}
    load_functions(PAYLOAD, {"pcc_gc_relocate_copy_payload"}, ns)
    assert ns["pcc_gc_relocate_copy_payload"](10, 20, 30, 48) == 1
    assert events == [("commit", (10, 20, 30)), ("dispose", 1)]
    events.clear()
    ns["pcc_gc_relocate_copy_payload_prepared_locked"] = lambda *args: 0
    assert ns["pcc_gc_relocate_copy_payload"](10, 20, 30, 48) == 0
    assert events == [("dispose", 1)]
