"""Legacy builtin message ownership, not expanded Python keyword semantics.

Real emitted IR must parse and verify before either positive contracts or
single-mechanism negative controls run. CPython rejects the retained msg
extension. Native execution and the untested tuple/streaming consumer lanes
are not qualified here.
"""
from __future__ import annotations

import re

import pytest

from test_exception_constructor_owned_result import _body, _emit, _producer_contract
from test_exception_constructor_temporary_owner import (
    _published_producer,
    _temporary_owner_contract,
)
from test_exception_module_live_callee import (
    _calls,
    _must_precede,
    _position,
    _reaches,
    _roots,
    _successors,
)


def _message_contract(module, converted):
    roots = _roots(module)
    outer, output = _published_producer(roots, "py_exc_new", 2)
    utf8 = _calls(roots, "py_str_utf8")
    assert len(utf8) == 1
    utf8 = utf8[0]
    assert roots.args(outer[2])[1] == utf8[2].data[0], "constructor must consume this exact UTF8 pointer"
    loads = {ins.data[0]: ins.data[3] for _, _, ins in roots.rows if ins.kind == "load"}
    message = roots.slot(loads[roots.args(utf8[2])[0]])
    roots.require_owning(message, allow_lifo=True)
    inputs = {message}
    conversions = _calls(roots, "py_obj_str")
    assert len(conversions) == int(converted)
    if converted:
        conversion = conversions[0]
        obj = roots.slot(loads[roots.args(conversion[2])[0]])
        roots.require_owning(obj, allow_lifo=True)
        assert obj != message, "py_obj_str returns an independent NEW owner even on pointer alias"
        publish = conversion[0].instructions[conversion[1] + 1]
        assert publish.kind == "store" and publish.data[1] == conversion[2].data[0]
        assert roots.slot(publish.data[3]) == message
        inputs.add(obj)
    release_calls = (_calls(roots, "pcc_gc_foreign_lease_release")
                     + _calls(roots, "py_cleanup_one_lease_preserving_exception"))
    after_outer = (outer[0].name, outer[1] + 2)
    leases = [row for row in _calls(roots, "pcc_gc_foreign_lease_acquire")
              if roots.slot(roots.args(row[2])[0]) == message
              and any(roots.slot(roots.args(release[2])[0]) == message
                      and roots.args(release[2])[1] == row[2].data[0]
                      and _reaches(roots, after_outer, _position(release))
                      for release in release_calls)]
    assert len(leases) == 1
    lease, token = leases[0], leases[0][2].data[0]
    _must_precede(roots, _position(lease), _position(utf8))
    _must_precede(roots, _position(utf8), _position(outer))
    takes = [row for row in _calls(roots, "pcc_gc_take_pinned_slot")
             if roots.slot(roots.args(row[2])[0]) == output]
    assert len(takes) == 1
    normal_releases = [row for row in _calls(roots, "pcc_gc_foreign_lease_release")
                       if roots.slot(roots.args(row[2])[0]) == message
                       and roots.args(row[2])[1] == token
                       and _reaches(roots, (row[0].name, row[1] + 1), _position(takes[0]))]
    assert len(normal_releases) == 1
    tls = [row for row in _calls(roots, "py_err_occurred")
           if _reaches(roots, after_outer, _position(row))
           and _reaches(roots, (row[0].name, row[1] + 1), _position(normal_releases[0]))]
    assert len(tls) == 1
    _must_precede(roots, _position(tls[0]), _position(normal_releases[0]), start=after_outer)
    assert any(ins.kind == "load" and ins.data[0] == roots.args(utf8[2])[0]
               and _reaches(roots, (lease[0].name, lease[1] + 1), (block.name, index))
               for block, index, ins in roots.rows), "UTF8 projection must reload after lease acquisition"

    # One scalar lease token protects the interior pointer until constructor
    # consumption/publication. Follow normal and error paths from projection,
    # including UTF8/TLS failure before the constructor is reached.
    by_name = {block.name: block for block in roots.blocks}
    pending = [(utf8[0].name, utf8[1] + 1, False, frozenset(), False)]
    seen, transfers, failures = set(), 0, 0
    while pending:
        state = pending.pop()
        if state in seen:
            continue
        seen.add(state)
        name, offset, released, cleared, output_cleared = state
        cleared = set(cleared)
        block = by_name[name]
        transferred = False
        for index in range(offset, len(block.instructions)):
            ins = block.instructions[index]
            if (roots.called(ins, "pcc_gc_foreign_lease_release")
                    or roots.called(ins, "py_cleanup_one_lease_preserving_exception")):
                args = roots.args(ins)
                if roots.slot(args[0]) == message and args[1] == token:
                    assert not released, "UTF8 lease released twice"
                    assert not _reaches(roots, (name, index + 1), _position(outer)), (
                        "UTF8 lease released before constructor consumption"
                    )
                    released = True
            retired = []
            if roots.called(ins, "pcc_gc_store_root") and roots.args(ins)[1] == "null":
                retired.append(roots.slot(roots.args(ins)[0]))
            elif roots.called(ins, "py_cleanup_one_root_preserving_exception"):
                retired.append(roots.slot(roots.args(ins)[0]))
            elif roots.called(ins, "py_cleanup_two_roots_preserving_exception"):
                retired.extend(roots.slot(arg) for arg in roots.args(ins))
            for slot in inputs.intersection(retired):
                assert released and slot not in cleared, "message owner must retire once after the UTF8 lease"
                cleared.add(slot)
            if output in retired:
                assert not output_cleared
                output_cleared = True
            if (roots.called(ins, "pcc_gc_frame_leave") or roots.called(ins, "pcc_gc_frame_leave_lifo")):
                slot = roots.slot(roots.args(ins)[0])
                if slot in inputs:
                    assert released and slot in cleared, "message frame left before owner/lease cleanup"
            if roots.called(ins, "pcc_gc_take_pinned_slot") and roots.slot(roots.args(ins)[0]) == output:
                assert released and cleared == inputs and not output_cleared
                transfers += 1
                transferred = True
                break
        if transferred:
            continue
        successors = _successors(block)
        if successors:
            pending.extend((target, 0, released, frozenset(cleared), output_cleared) for target in successors)
        elif block.terminator.kind in ("ret", "ret_void"):
            assert released and cleared == inputs and output_cleared, "message error path must dispose all local owners"
            failures += 1
    assert transfers and failures
    _temporary_owner_contract(module, "py_exc_new", False, False)


@pytest.mark.parametrize("parameter,converted", (("value", True), ("value: str", False)))
def test_dynamic_message_keeps_its_utf8_owner_through_constructor(
        tmp_path, monkeypatch, parameter, converted):
    source = ("def consume(first, second=None):\n    return first\n"
              "def make_value(" + parameter + "):\n    return consume(ValueError(msg=value))\n")
    namespace = {}
    exec(source, namespace)
    with pytest.raises(TypeError):
        namespace["make_value"]("message")
    module, _ = _emit(tmp_path, monkeypatch, source)
    _message_contract(module, converted)


@pytest.mark.parametrize("mutation", ("publication", "tls-edge", "direct-argument", "premature-unpin", "duplicate-input-release"))
def test_direct_constructor_oracle_rejects_valid_ir_mutations(tmp_path, monkeypatch, mutation):
    from tests.owned_ir_validation import verify_ir_text

    source = ("def consume(first, second=None):\n    return first\n"
              "def make_value(value):\n    return consume(ValueError(value))\n")
    module, text = _emit(tmp_path, monkeypatch, source)
    _producer_contract(module, "py_exc_new_with_value", True)
    roots = _roots(module)
    outer, output = _published_producer(roots, "py_exc_new_with_value", 2)
    body = _body(text)
    if mutation == "publication":
        publish = outer[0].instructions[outer[1] + 1]
        before = "store ptr %" + outer[2].data[0] + ", ptr %" + publish.data[3]
        after = "store ptr null, ptr %" + publish.data[3]
        diagnostic = "constructor NEW result must be published"
    elif mutation == "direct-argument":
        call = _calls(roots, "user_exception_owner_consume")[0][2]
        before = "@user_exception_owner_consume(ptr %" + roots.args(call)[0] + ","
        after = "@user_exception_owner_consume(ptr null,"
        diagnostic = "original direct consumer must receive"
    elif mutation == "premature-unpin":
        consumer = _calls(roots, "user_exception_owner_consume")[0][2]
        value = roots.args(consumer)[0]
        lines = [line for line in body.splitlines() if "@user_exception_owner_consume(" in line]
        assert len(lines) == 1
        before = lines[0]
        after = "  call void (ptr) @pcc_gc_unpin(ptr %" + value + ")\n" + before
        diagnostic = "direct argument protection ended before consumer"
    elif mutation == "duplicate-input-release":
        consumer = _calls(roots, "user_exception_owner_consume")[0]
        value = roots.args(consumer[2])[0]
        releases = [row for row in _calls(roots, "pcc_gc_release_known")
                    if roots.args(row[2]) == [value]
                    and _reaches(roots, (consumer[0].name, consumer[1] + 1), _position(row))]
        assert len(releases) == 1
        label = "\n" + releases[0][0].name + ":\n"
        start = body.index(label)
        end = body.index("\n\n", start + len(label))
        before = body[start:end]
        call = "call void (ptr) @pcc_gc_release_known(ptr %" + value + ")"
        assert before.count(call) == 1
        after = before.replace(call, call + "\n  " + call, 1)
        diagnostic = "direct argument owner must be released exactly once after unpin"
    else:
        take = [row for row in _calls(roots, "pcc_gc_take_pinned_slot")
                if roots.slot(roots.args(row[2])[0]) == output][0]
        checks = [row for row in _calls(roots, "py_err_occurred")
                  if _reaches(roots, (outer[0].name, outer[1] + 2), _position(row))
                  and _reaches(roots, (row[0].name, row[1] + 1), _position(take))]
        assert len(checks) == 1
        branch = checks[0][0].terminator
        condition, error, normal = branch.data
        before = f"br i1 %{condition}, label %{error}, label %{normal}"
        after = f"br i1 %{condition}, label %{normal}, label %{error}"
        diagnostic = "TLS error cannot transfer a result"
    assert body.count(before) == 1
    damaged = text.replace(body, body.replace(before, after, 1), 1)
    # Parse and verify happen outside pytest.raises: syntax/SSA/CFG failures
    # cannot be counted as a semantic negative control.
    verified = verify_ir_text(damaged)
    with pytest.raises(AssertionError, match=re.escape(diagnostic)):
        _producer_contract(verified, "py_exc_new_with_value", True)
