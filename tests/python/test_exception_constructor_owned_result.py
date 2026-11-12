"""Actual-IR checks for the existing builtin exception producer's handoff.

Constructor dispatch, including its existing keyword semantics, is unchanged.
These tests do not qualify the separate runtime exception-class-call path.
"""
from __future__ import annotations

import re

import pytest


def _emit(tmp_path, monkeypatch, source):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    path = tmp_path / "exception_owner.py"
    path.write_text(source)
    output = path.with_suffix(".ll")
    compile_python(str(path), str(output), emit_llvm_only=True, python_library=True,
                   ir_scaffold_mode="on", backend="self",
                   target_triple="x86_64-unknown-linux-gnu")
    text = output.read_text()
    return verify_ir_text(text), text


def _body(text):
    match = re.search(r"(?ms)^define[^\n]*@user_exception_owner_make_value\([^\n]*\).*?^}", text)
    assert match is not None
    return match.group(0)


def _producer_contract(module, symbol, has_argument):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    function = next(fn for fn in module.functions if fn.name == "user_exception_owner_make_value")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    # Import at use time: both newer modules reuse this file's _emit.
    from test_exception_module_live_callee import (
        _DirectRoots,
        _must_precede,
        _position,
        _reaches,
        _successors,
    )

    addresses = _DirectRoots(blocks, module.globals_)
    direct = [ins for _, _, ins in addresses.rows
              if addresses.called(ins, "user_exception_owner_consume")]
    if direct:
        # The exact positional ABI transfers through a pinned output take,
        # rather than retaining this object in a dynamic call's list/tuple.
        # The shared real-IR contract binds the take SSA to consume's first
        # argument and checks publication, TLS, leases and all frame exits.
        from test_exception_constructor_temporary_owner import _temporary_owner_contract

        nested = has_argument and any(
            addresses.called(ins, "py_exc_new") and addresses.args(ins)[0] == "3"
            for _, _, ins in addresses.rows
        )
        _temporary_owner_contract(module, symbol, has_argument, nested)
        consumer = next(row for row in addresses.rows
                        if addresses.called(row[2], "user_exception_owner_consume"))
        argument = addresses.args(consumer[2])[0]
        pins = [row for row in addresses.rows
                if addresses.called(row[2], "pcc_gc_pin")
                and addresses.args(row[2]) == [argument]]
        assert len(pins) == 1, "direct argument needs its exact pre-call pin"
        _must_precede(addresses, _position(pins[0]), _position(consumer))
        for row in addresses.rows:
            ins = row[2]
            if (addresses.called(ins, "pcc_gc_unpin")
                    or addresses.called(ins, "pcc_gc_release")
                    or addresses.called(ins, "pcc_gc_release_known")):
                if addresses.args(ins) == [argument]:
                    assert not (_reaches(addresses, (pins[0][0].name, pins[0][1] + 1), _position(row))
                                and _reaches(addresses, (row[0].name, row[1] + 1), _position(consumer))), (
                        "direct argument protection ended before consumer"
                    )
        comparisons = [ins for ins in consumer[0].instructions
                       if ins.kind == "icmp" and ins.data[0] == "eq"
                       and ins.data[3:] == (consumer[2].data[0], "null")]
        assert len(comparisons) == 1, "direct result must select its actual NULL error edge"
        branch = consumer[0].terminator
        assert branch.kind == "br_cond" and branch.data[0] == comparisons[0].data[1]
        assert branch.data[1] != branch.data[2]
        by_name = {block.name: block for block in blocks}
        pending = [(consumer[0].name, consumer[1] + 1, 0, 0, None)]
        seen, exits = set(), set()
        while pending:
            state = pending.pop()
            if state in seen:
                continue
            seen.add(state)
            name, offset, unpinned, released, lane = state
            block = by_name[name]
            for ins in block.instructions[offset:]:
                if addresses.called(ins, "pcc_gc_unpin") and addresses.args(ins) == [argument]:
                    assert unpinned == released == 0, "direct argument unpinned twice or after release"
                    unpinned = 1
                if (addresses.called(ins, "pcc_gc_release") or addresses.called(ins, "pcc_gc_release_known")):
                    if addresses.args(ins) == [argument]:
                        assert unpinned == 1 and released == 0, "direct argument owner must be released exactly once after unpin"
                        released = 1
            successors = _successors(block)
            if successors:
                for target in successors:
                    next_lane = lane
                    if name == consumer[0].name:
                        next_lane = "error" if target == branch.data[1] else "normal"
                    pending.append((target, 0, unpinned, released, next_lane))
            elif block.terminator.kind in ("ret", "ret_void"):
                assert unpinned == released == 1, "direct argument owner must be released exactly once on every exit"
                exits.add(lane)
        assert exits == {"normal", "error"}, "direct consumer needs checked normal/error exits"
        # This proves disposal of the transferred input owner. It does not
        # prove the separate raw returned-result window across that disposal.
        return
    rows = addresses.rows
    slot, called, args = addresses.slot, addresses.called, addresses.args
    invocations = [(block, index, ins) for block, index, ins in rows
                   if called(ins, symbol) and args(ins)[0] == "2"]
    assert len(invocations) == 1
    block, index, invoke = invocations[0]
    published = block.instructions[index + 1]
    assert published.kind == "store" and published.data[1] == invoke.data[0], (
        "exception producer must publish before any TLS, lease or cleanup call"
    )
    output = slot(published.data[3])
    addresses.require_owning(output, allow_lifo=True)
    loads = {ins.data[0]: ins.data[3] for _, _, ins in rows if ins.kind == "load"}
    argument = slot(loads[args(invoke)[1]]) if has_argument else None
    if argument is not None:
        assert argument != output
        addresses.require_owning(argument, allow_lifo=True)
    by_name = {candidate.name: candidate for candidate in blocks}
    current, offset = block, index + 2
    path, seen, consumed = [], set(), False
    while current.name not in seen:
        seen.add(current.name)
        for ins in current.instructions[offset:]:
            path.append(ins)
            if called(ins, "pcc_gc_store_root") and args(ins)[1] == "null":
                assert slot(args(ins)[0]) != output, "exception result cleared before consumer"
            if called(ins, "pcc_gc_frame_leave_lifo"):
                assert slot(args(ins)[0]) != output, "exception result retired before consumer"
            if called(ins, "py_list_append"):
                consumed = any(value in loads and slot(loads[value]) == output for value in args(ins))
                if consumed:
                    break
        if consumed:
            break
        term = current.terminator
        if term.kind == "br":
            current = by_name[term.data[0]]
        elif term.kind == "br_cond":
            current = by_name[term.data[2]]
        else:
            break
        offset = 0
    assert consumed, "exact exception output root must reach its retaining call-argument consumer"
    assert any(called(ins, "py_err_occurred") for ins in path)
    if argument is not None:
        releases = [ins for ins in path if called(ins, "pcc_gc_foreign_lease_release")
                    and slot(args(ins)[0]) == argument]
        assert releases
        token = args(releases[0])[1]
        assert any(called(ins, "pcc_gc_foreign_lease_acquire") and ins.data[0] == token
                   and slot(args(ins)[0]) == argument for _, _, ins in rows)
        clears = [ins for ins in path if called(ins, "pcc_gc_store_root")
                  and args(ins)[1] == "null" and slot(args(ins)[0]) == argument]
        assert len(clears) == 1
        assert path.index(releases[0]) < path.index(clears[0])
    assert any(called(ins, "py_tls_exc_swap_slot") for _, _, ins in rows)
    addresses.assert_frame_exits()


@pytest.mark.parametrize("expression,symbol,has_argument", (
    ("ValueError()", "py_exc_new", False),
    ("ValueError('sentinel')", "py_exc_new", False),
    ("ValueError(value)", "py_exc_new_with_value", True),
    ("ValueError(TypeError('inner'))", "py_exc_new_with_value", True),
))
def test_exception_constructor_actual_producer_slots(tmp_path, monkeypatch, expression, symbol, has_argument):
    source = ("def consume(first, second=None):\n    return first\n"
              "def make_value(value):\n    return consume(" + expression + ")\n")
    module, _ = _emit(tmp_path, monkeypatch, source)
    _producer_contract(module, symbol, has_argument)


@pytest.mark.parametrize("source", (
    "def make_value(ValueError):\n    return ValueError('shadow')\n",
    "def custom(value):\n    return value\nValueError = custom\n"
    "def make_value():\n    global ValueError\n    return ValueError('shadow')\n",
))
def test_exception_constructor_shadow_routes_stay_unchanged(tmp_path, monkeypatch, source):
    _, text = _emit(tmp_path, monkeypatch, source)
    body = _body(text)
    assert "@py_obj_call_slots(" in body
    assert not re.search(r"@py_exc_new(?:_with_value)?\(i64 2,", body)


def test_exception_constructor_message_keyword_keeps_existing_route(tmp_path, monkeypatch):
    # This is a routing/ownership regression, not a claim that the historical
    # message-keyword extension implements CPython keyword validation.
    source = ("def consume(first, second=None):\n    return first\n"
              "def make_value():\n    return consume(ValueError(msg='sentinel'))\n")
    module, _ = _emit(tmp_path, monkeypatch, source)
    _producer_contract(module, "py_exc_new", False)


def test_exception_constructor_keeps_raise_cause_route(tmp_path, monkeypatch):
    _, text = _emit(tmp_path, monkeypatch,
                    "def make_value(original):\n    error = ValueError('replacement')\n    raise error from original\n")
    body = _body(text)
    assert "@py_exc_set_cause(" in body
    assert "@py_raise(" in body or "@py_raise_owned(" in body
    assert re.search(r"@py_exc_new\(i64 2,", body)


def test_exception_constructor_cpython_reference():
    value = []
    error = ValueError(value)
    assert error.args == (value,) and error.args[0] is value
    nested = ValueError(TypeError("inner"))
    assert isinstance(nested.args[0], TypeError)
    original = RuntimeError("original")
    try:
        raise error from original
    except ValueError as caught:
        assert caught is error and caught.__cause__ is original
