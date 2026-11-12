"""Synchronous assignment-bound exception callees use the live module namespace.

Language reference 4.2.2 (name resolution), 6.3.4 (calls), and 6.16
(evaluation order). These newly authored regressions emit real PCC IR and use
its owned parser/verifier, alongside CPython references of the same source.
They do not execute native output or qualify suspension/import/class lookup.
"""
from __future__ import annotations

import pytest

from test_exception_constructor_owned_result import _emit
from tests.python.root_slot_contract import RootSlotContract


class _DirectRoots(RootSlotContract):
    @staticmethod
    def called(ins, name):
        # A same-spelled indirect call cannot establish a runtime contract.
        return (ins.kind == "call" and not ins.data[3]
                and RootSlotContract.called(ins, name))


def _roots(module):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    function = next(fn for fn in module.functions
                    if fn.name == "user_exception_owner_make_value")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    return _DirectRoots(blocks, module.globals_)


def _calls(roots, name):
    return [(block, index, ins) for block, index, ins in roots.rows
            if roots.called(ins, name)]


def _successors(block):
    term = block.terminator
    if term.kind == "br":
        return (term.data[0],)
    if term.kind == "br_cond":
        return tuple(term.data[1:])
    if term.kind == "switch":
        return (term.data[2], *(target for _, target in term.data[3]))
    assert term.kind in ("ret", "ret_void", "unreachable"), term.kind
    return ()


def _position(row):
    return row[0].name, row[1]


def _reaches(roots, start, target, *, avoid=None):
    """Instruction-position reachability on this verified function's CFG."""
    blocks = {block.name: block for block in roots.blocks}
    pending, seen = [start], set()
    while pending:
        name, offset = pending.pop()
        if (name, offset) in seen:
            continue
        seen.add((name, offset))
        block = blocks[name]
        for index in range(offset, len(block.instructions)):
            point = (name, index)
            if point == avoid:
                break
            if point == target:
                return True
        else:
            pending.extend((name, 0) for name in _successors(block))
    return False


def _must_precede(roots, first, second, *, start=None):
    start = start or (roots.blocks[0].name, 0)
    assert _reaches(roots, start, second), "expected consumer is unreachable"
    assert not _reaches(roots, start, second, avoid=first), (
        "consumer is reachable before its required producer/check"
    )


def _live_callee_contract(module, tag):
    roots = _roots(module)
    lookups = _calls(roots, "py_module_attr_get")
    assert len(lookups) == 1, "callee must read the live module namespace once"
    lookup = lookups[0]
    block, index, invoke = lookup
    publish = block.instructions[index + 1]
    assert publish.kind == "store" and publish.data[1] == invoke.data[0], (
        "NEW module lookup result must be published immediately"
    )
    callee = roots.slot(publish.data[3])
    base, start, _count = roots.require_owning(callee, allow_lifo=True)
    entries = [row for row in roots.rows
               if (roots.called(row[2], "pcc_gc_frame_enter")
                   or roots.called(row[2], "pcc_gc_frame_enter_lifo"))
               and roots.slot(roots.args(row[2])[1]) == (base, start)]
    assert len(entries) == 1
    _must_precede(roots, _position(entries[0]), (block.name, index + 1))
    loads = {ins.data[0]: ins.data[3] for _, _, ins in roots.rows
             if ins.kind == "load"}
    missing = [(owner, position, ins) for owner, position, ins in roots.rows
               if ins.kind == "icmp" and ins.data[0] == "eq"
               and ins.data[4] == "null" and ins.data[3] in loads
               and roots.slot(loads[ins.data[3]]) == callee]
    assert len(missing) == 1, "fallback needs the exact published callee's NULL check"
    branch = missing[0][0].terminator
    assert branch.kind == "br_cond" and branch.data[0] == missing[0][2].data[1]
    absent, present = (branch.data[1], 0), (branch.data[2], 0)
    builtins = _calls(roots, "py_exc_builtin_class")
    caches = _calls(roots, "py_subs_exc_cache_slot")
    assert len(builtins) == len(caches) == 1
    assert roots.args(builtins[0][2]) == roots.args(caches[0][2]) == [str(tag)]
    builtin_point = _position(builtins[0])
    assert _reaches(roots, absent, builtin_point)
    assert not _reaches(roots, present, builtin_point), "present bindings must not select the builtin"
    tls_checks = [row for row in _calls(roots, "py_err_occurred")
                  if row[0].name == absent[0]]
    assert tls_checks, "a failed lookup must check TLS before builtin fallback"
    tls = tls_checks[0][2]
    _must_precede(roots, _position(tls_checks[0]), builtin_point, start=absent)
    tls_comparisons = [ins for ins in tls_checks[0][0].instructions
                       if ins.kind == "icmp" and ins.data[0] == "ne"
                       and ins.data[3:] == (tls.data[0], "0")]
    assert len(tls_comparisons) == 1, "lookup TLS result must control its error decision"
    tls_branch = tls_checks[0][0].terminator
    assert tls_branch.kind == "br_cond" and tls_branch.data[0] == tls_comparisons[0].data[1]
    tls_error, tls_clear = (tls_branch.data[1], 0), (tls_branch.data[2], 0)
    assert _reaches(roots, tls_clear, builtin_point)
    copies = [row for row in _calls(roots, "pcc_gc_root_copy_lease")
              if roots.slot(roots.args(row[2])[0]) == callee
              and roots.args(row[2])[1] == caches[0][2].data[0]]
    assert len(copies) == 1, "fallback must copy the canonical cache's physical owner"
    calls = _calls(roots, "py_obj_call_slots")
    assert len(calls) == 1
    assert roots.slot(roots.args(calls[0][2])[0]) == callee
    call_point = _position(calls[0])
    _must_precede(roots, (block.name, index + 1), call_point)
    for forbidden in (builtin_point, _position(copies[0]), call_point):
        assert not _reaches(roots, tls_error, forbidden), "lookup error must not fall through to fallback/call"
    # Scan only instructions on a path from publication to this invocation.
    # The missing branch's exact canonical-cache copy is the sole legal rewrite.
    after_publication = (block.name, index + 2)
    for owner, position, ins in roots.rows:
        point = (owner.name, position)
        if not (_reaches(roots, after_publication, point)
                and _reaches(roots, (owner.name, position + 1), call_point)):
            continue
        if ins.kind == "store":
            assert roots.slot(ins.data[3]) != callee, "captured callable overwritten before invocation"
        writers = ("pcc_gc_store_root", "pcc_gc_root_copy_lease",
                   "pcc_gc_root_copy_borrowed_lease", "pcc_gc_root_copy_local",
                   "py_cleanup_one_root_preserving_exception", "pcc_gc_frame_leave",
                   "pcc_gc_frame_leave_lifo")
        if any(roots.called(ins, name) for name in writers):
            if roots.slot(roots.args(ins)[0]) == callee:
                assert point == _position(copies[0]), "captured callable cleared/replaced/retired before invocation"
        if roots.called(ins, "py_cleanup_two_roots_preserving_exception"):
            assert all(roots.slot(arg) != callee for arg in roots.args(ins)), (
                "captured callable cleared by paired cleanup before invocation"
            )
    assert not _calls(roots, "py_exc_new") and not _calls(roots, "py_exc_new_with_value"), (
        "module binding must not unconditionally construct a builtin exception"
    )
    roots.assert_frame_exits(exact_abi=True)
    return roots, lookup, calls[0]


def _reference(source):
    namespace = {}
    exec(source, namespace)
    return namespace


@pytest.mark.parametrize("name,tag", [
    ("TypeError", 3), ("UnicodeDecodeError", 58), ("OSError", 14), ("ValueError", 2),
])
def test_assignment_bound_exception_family_calls_live_value(tmp_path, monkeypatch, name, tag):
    source = ("def custom(value):\n    return value\n" + name + " = custom\n"
              "def make_value():\n    return " + name + "('shadow')\n")
    assert _reference(source)["make_value"]() == "shadow"
    module, _ = _emit(tmp_path, monkeypatch, source)
    _live_callee_contract(module, tag)


@pytest.mark.parametrize("state", ["before-assignment", "deleted"])
def test_missing_module_binding_uses_builtin_fallback(tmp_path, monkeypatch, state):
    prefix = "def custom(value):\n    return value\n"
    function = "def make_value():\n    return ValueError('fallback')\n"
    if state == "before-assignment":
        source = prefix + function + "observed = make_value()\nValueError = custom\n"
    else:
        source = prefix + "ValueError = custom\ndel ValueError\n" + function + "observed = make_value()\n"
    error = _reference(source)["observed"]
    assert type(error) is ValueError and error.args == ("fallback",)
    module, _ = _emit(tmp_path, monkeypatch, source)
    _live_callee_contract(module, 2)


def test_present_noncallable_module_value_does_not_select_builtin(tmp_path, monkeypatch):
    source = "ValueError = None\ndef make_value():\n    return ValueError('bad')\n"
    with pytest.raises(TypeError):
        _reference(source)["make_value"]()
    module, _ = _emit(tmp_path, monkeypatch, source)
    _live_callee_contract(module, 2)


def test_distinct_local_shadow_does_not_read_module_binding(tmp_path, monkeypatch):
    source = ("ValueError = None\ndef make_value(ValueError):\n"
              "    return ValueError('local')\n")
    assert _reference(source)["make_value"](lambda value: value) == "local"
    module, _ = _emit(tmp_path, monkeypatch, source)
    roots = _roots(module)
    assert len(_calls(roots, "py_obj_call_slots")) == 1
    assert not _calls(roots, "py_module_attr_get")
    assert not _calls(roots, "py_exc_builtin_class")
    assert not _calls(roots, "py_exc_new")


def test_unbound_local_keeps_its_error_instead_of_builtin_fallback(tmp_path, monkeypatch):
    source = ("ValueError = None\ndef make_value(flag):\n"
              "    if flag:\n        ValueError = None\n"
              "    return ValueError('local')\n")
    with pytest.raises(UnboundLocalError):
        _reference(source)["make_value"](False)
    module, _ = _emit(tmp_path, monkeypatch, source)
    roots = _roots(module)
    assert not _calls(roots, "py_module_attr_get")
    assert not _calls(roots, "py_exc_builtin_class")
    assert _calls(roots, "py_err_occurred"), "local-bound failure must retain error handling"


def test_global_statement_observes_the_current_module_value(tmp_path, monkeypatch):
    source = ("def first(value):\n    return 'old'\n"
              "def second(value):\n    return value\nValueError = first\n"
              "def make_value():\n    global ValueError\n    return ValueError('new')\n"
              "ValueError = second\n")
    assert _reference(source)["make_value"]() == "new"
    module, _ = _emit(tmp_path, monkeypatch, source)
    _live_callee_contract(module, 2)


def test_synchronous_callee_is_captured_before_argument_rebinding(tmp_path, monkeypatch):
    source = ("def first(value):\n    return 'first:' + value\n"
              "def second(value):\n    return 'second:' + value\nValueError = first\n"
              "def mutate():\n    global ValueError\n    ValueError = second\n    return 'arg'\n"
              "def make_value():\n    return ValueError(mutate())\n")
    namespace = _reference(source)
    assert namespace["make_value"]() == "first:arg"
    assert namespace["make_value"]() == "second:arg"
    module, _ = _emit(tmp_path, monkeypatch, source)
    roots, lookup, call = _live_callee_contract(module, 2)
    mutations = _calls(roots, "user_exception_owner_mutate")
    assert len(mutations) == 1, "fixture must execute the actual rebinding argument call"
    _must_precede(roots, (lookup[0].name, lookup[1] + 1), _position(mutations[0]))
    _must_precede(roots, _position(mutations[0]), _position(call))


def test_bound_exception_name_uses_runtime_keyword_binding(tmp_path, monkeypatch):
    source = ("def custom(*, message):\n    return message\nValueError = custom\n"
              "def make_value():\n    return ValueError(message='keyword')\n")
    assert _reference(source)["make_value"]() == "keyword"
    module, _ = _emit(tmp_path, monkeypatch, source)
    roots, _, call = _live_callee_contract(module, 2)
    kwargs = roots.slot(roots.args(call[2])[2])
    roots.require_owning(kwargs, allow_lifo=True)
    assert _calls(roots, "py_dict_new"), "keyword binding needs an actual dictionary producer"


def _float_result_contract(module):
    roots, _, call = _live_callee_contract(module, 2)
    output = roots.slot(roots.args(call[2])[3])
    roots.require_owning(output, allow_lifo=True)
    unboxes = _calls(roots, "py_float_to_f64")
    assert len(unboxes) == 1
    unbox = unboxes[0]
    value = roots.args(unbox[2])[0]
    loads = {ins.data[0]: ins.data[3] for _, _, ins in roots.rows if ins.kind == "load"}
    assert value in loads and roots.slot(loads[value]) == output, (
        "float projection must load the exact slot-call result owner"
    )
    _must_precede(roots, _position(call), _position(unbox))
    leases = [row for row in _calls(roots, "pcc_gc_foreign_lease_acquire")
              if roots.slot(roots.args(row[2])[0]) == output]
    assert len(leases) == 1, "projection must acquire its exact result-owner lease"
    _must_precede(roots, _position(leases[0]), _position(unbox))
    token = leases[0][2].data[0]
    releases = (_calls(roots, "pcc_gc_foreign_lease_release")
                + _calls(roots, "py_cleanup_one_lease_preserving_exception"))
    for block, index, ins in releases:
        if roots.slot(roots.args(ins)[0]) == output and roots.args(ins)[1] == token:
            assert not _reaches(roots, (block.name, index + 1), _position(unbox)), (
                "projection address lease released before reading the float result"
            )
    for block, index, ins in roots.rows:
        clears = ((roots.called(ins, "pcc_gc_store_root") and roots.args(ins)[1] == "null")
                  or roots.called(ins, "py_cleanup_one_root_preserving_exception"))
        leaves = roots.called(ins, "pcc_gc_frame_leave_lifo")
        if (clears or leaves) and roots.slot(roots.args(ins)[0]) == output:
            assert not _reaches(roots, (block.name, index + 1), _position(unbox)), (
                "float result owner cleared or retired before projection"
            )
    blocks = {block.name: block for block in roots.blocks}
    pending = [(unbox[0].name, unbox[1] + 1, False, False, False)]
    visited, releases, lease_routes, returns = set(), set(), set(), 0
    while pending:
        name, offset, leased_released, cleared, retired = pending.pop()
        state = (name, offset, leased_released, cleared, retired)
        if state in visited:
            continue
        visited.add(state)
        block = blocks[name]
        for ins in block.instructions[offset:]:
            lease_release = roots.called(ins, "pcc_gc_foreign_lease_release")
            lease_cleanup = roots.called(ins, "py_cleanup_one_lease_preserving_exception")
            if ((lease_release or lease_cleanup)
                    and roots.slot(roots.args(ins)[0]) == output
                    and roots.args(ins)[1] == token):
                assert not leased_released, "result address lease released twice"
                leased_released = True
                lease_routes.add("error-release" if lease_cleanup else "normal-release")
            clear = roots.called(ins, "pcc_gc_store_root") and roots.args(ins)[1] == "null"
            cleanup = roots.called(ins, "py_cleanup_one_root_preserving_exception")
            if (clear or cleanup) and roots.slot(roots.args(ins)[0]) == output:
                assert leased_released, "result owner cleared before its exact address lease was released"
                assert not retired, "result cleanup must precede its frame leave"
                cleared = True
                releases.add("error-helper" if cleanup else "normal-clear")
            if (roots.called(ins, "pcc_gc_frame_leave_lifo")
                    and roots.slot(roots.args(ins)[0]) == output):
                assert leased_released and cleared, "result frame retired before lease and owner cleanup"
                retired = True
        if block.terminator.kind in ("ret", "ret_void"):
            assert leased_released and cleared and retired, "projection return leaks its lease/owner/frame"
            returns += 1
        pending.extend((target, 0, leased_released, cleared, retired) for target in _successors(block))
    assert lease_routes == {"normal-release", "error-release"}, "both projection paths must release the exact lease token"
    assert returns and releases == {"normal-clear", "error-helper"}, (
        "fixture must exercise both ordinary release and error-edge root cleanup"
    )
    return roots


def test_float_result_stays_owned_through_projection_and_error_cleanup(tmp_path, monkeypatch):
    source = ("def produce(value: float) -> float:\n    return value\nValueError = produce\n"
              "def make_value(value: float) -> float:\n    return ValueError(value)\n")
    assert _reference(source)["make_value"](1.25) == 1.25
    module, _ = _emit(tmp_path, monkeypatch, source)
    function = next(fn for fn in module.functions
                    if fn.name == "user_exception_owner_make_value")
    assert function.ret_type.describe() == "double", "fixture must use the typed float return ABI"
    _float_result_contract(module)

    # Execute the existing runtime-body model, not a handwritten replacement
    # for py_cleanup_one_root_preserving_exception. This is host model evidence.
    from test_owned_cleanup_helpers import CleanupModel

    model = CleanupModel(pending=True, moving=True)
    output = model.root("float-result")

    def finalizer():
        assert model.pending == 0 and model.memory[output] == 0
        model.set_exception("callback-error")
        model.collect()

    model.callbacks[output] = finalizer
    assert model.cleanup("root", output) is None
    model.assert_balanced(pending=True)
    assert model.memory[output] == 0
    assert model.retired["float-result"] == 1 and model.references["float-result"] == 0
    assert model.references["callback-error"] == 0 and model.retired["callback-error"] == 1
    assert not model.releases and model.moves > 0
