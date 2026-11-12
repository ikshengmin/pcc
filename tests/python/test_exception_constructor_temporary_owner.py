"""NEW regressions for no-sink builtin exception producer ownership.

Python reference 6.3.4/6.16: arguments retain identity and evaluation order.
Real PCC emission and owned parsing/verification are required. These checks
cover the constructor's temporary owners, not native GC execution or the
separate direct user's return-value cleanup contract. The original producer
fixture and its slot-call-only oracle remain unchanged. The CFG walk starts
at producer publication; pre-producer argument/lease failure cleanup needs
separate validation and is not claimed by these four tests.
"""
from __future__ import annotations

import pytest

from test_exception_constructor_owned_result import _emit
from test_exception_module_live_callee import (
    _calls,
    _must_precede,
    _position,
    _roots,
    _reaches,
    _successors,
)


def _published_producer(roots, symbol, tag):
    calls = [row for row in _calls(roots, symbol)
             if roots.args(row[2])[0] == str(tag)]
    assert len(calls) == 1
    block, index, invoke = calls[0]
    publication = block.instructions[index + 1]
    assert publication.kind == "store" and publication.data[1] == invoke.data[0], (
        "constructor NEW result must be published before another call"
    )
    slot = roots.slot(publication.data[3])
    base, offset, _ = roots.require_owning(slot, allow_lifo=True)
    entries = [row for row in roots.rows
               if (roots.called(row[2], "pcc_gc_frame_enter")
                   or roots.called(row[2], "pcc_gc_frame_enter_lifo"))
               and roots.slot(roots.args(row[2])[1]) == (base, offset)]
    assert len(entries) == 1
    _must_precede(roots, _position(entries[0]), (block.name, index + 1))
    return calls[0], slot


def _temporary_owner_contract(module, symbol, has_argument, nested):
    roots = _roots(module)
    (block, index, invoke), output = _published_producer(roots, symbol, 2)
    argument, token = None, None
    if has_argument:
        loads = {ins.data[0]: ins.data[3] for _, _, ins in roots.rows if ins.kind == "load"}
        argument = roots.slot(loads[roots.args(invoke)[1]])
        assert argument != output
        roots.require_owning(argument, allow_lifo=True)
        releases = (_calls(roots, "pcc_gc_foreign_lease_release")
                    + _calls(roots, "py_cleanup_one_lease_preserving_exception"))
        # Publication can acquire an earlier lease on this same physical slot.
        # The operand lease is the one still releasable after the outer call.
        leases = [row for row in _calls(roots, "pcc_gc_foreign_lease_acquire")
                  if roots.slot(roots.args(row[2])[0]) == argument
                  and any(roots.slot(roots.args(release[2])[0]) == argument
                          and roots.args(release[2])[1] == row[2].data[0]
                          and _reaches(roots, (block.name, index + 2), _position(release))
                          for release in releases)]
        assert len(leases) == 1
        token = leases[0][2].data[0]
        _must_precede(roots, _position(leases[0]), (block.name, index))
        if nested:
            inner, inner_output = _published_producer(roots, "py_exc_new", 3)
            assert inner_output == argument, "nested NEW must transfer directly into the argument owner"
            _must_precede(roots, (inner[0].name, inner[1] + 1), _position(leases[0]))

    takes = [row for row in _calls(roots, "pcc_gc_take_pinned_slot")
             if roots.slot(roots.args(row[2])[0]) == output]
    assert len(takes) == 1
    take_point = _position(takes[0])
    after_publication = (block.name, index + 2)
    tls = [row for row in _calls(roots, "py_err_occurred")
           if _reaches(roots, after_publication, _position(row))
           and _reaches(roots, (row[0].name, row[1] + 1), take_point)]
    assert len(tls) == 1, "constructor TLS must be checked before output transfer"
    check = tls[0]
    comparisons = [ins for ins in check[0].instructions
                   if ins.kind == "icmp" and ins.data[0] == "ne"
                   and ins.data[3:] == (check[2].data[0], "0")]
    assert len(comparisons) == 1
    branch = check[0].terminator
    assert branch.kind == "br_cond" and branch.data[0] == comparisons[0].data[1]
    assert not _reaches(roots, (branch.data[1], 0), take_point), "TLS error cannot transfer a result"
    assert _reaches(roots, (branch.data[2], 0), take_point), "TLS-clear edge must reach the transfer"
    _must_precede(roots, _position(check), take_point, start=after_publication)
    consumers = _calls(roots, "user_exception_owner_consume")
    assert len(consumers) == 1
    assert roots.args(consumers[0][2])[0] == takes[0][2].data[0], (
        "the original direct consumer must receive this output's transferred owner"
    )
    _must_precede(roots, take_point, _position(consumers[0]))

    # Track only the exact constructor output and its one argument from the
    # real producer onward. Every post-call error edge must dispose them;
    # the successful edge must dispose the argument before transferring output.
    by_name = {candidate.name: candidate for candidate in roots.blocks}
    pending = [(block.name, index + 2, not has_argument, not has_argument, False)]
    seen, transfers, error_returns = set(), 0, 0
    while pending:
        state = pending.pop()
        if state in seen:
            continue
        seen.add(state)
        name, offset, lease_released, argument_cleared, output_cleared = state
        current = by_name[name]
        transferred = False
        for ins in current.instructions[offset:]:
            if has_argument and (roots.called(ins, "pcc_gc_foreign_lease_release")
                    or roots.called(ins, "py_cleanup_one_lease_preserving_exception")):
                args = roots.args(ins)
                if roots.slot(args[0]) == argument:
                    assert args[1] == token, "must release the acquired argument lease token"
                    assert not lease_released, "argument lease released twice"
                    lease_released = True
            cleared = []
            if roots.called(ins, "pcc_gc_store_root") and roots.args(ins)[1] == "null":
                cleared.append(roots.slot(roots.args(ins)[0]))
            elif roots.called(ins, "py_cleanup_one_root_preserving_exception"):
                cleared.append(roots.slot(roots.args(ins)[0]))
            elif roots.called(ins, "py_cleanup_two_roots_preserving_exception"):
                cleared.extend(roots.slot(arg) for arg in roots.args(ins))
            if argument is not None and argument in cleared:
                assert lease_released and not argument_cleared, "argument owner must retire once, after its lease"
                argument_cleared = True
            if has_argument and (roots.called(ins, "pcc_gc_frame_leave")
                    or roots.called(ins, "pcc_gc_frame_leave_lifo")):
                if roots.slot(roots.args(ins)[0]) == argument:
                    assert lease_released and argument_cleared, "argument frame retired before lease/owner cleanup"
            if output in cleared:
                assert not output_cleared, "output owner disposed twice"
                output_cleared = True
            if roots.called(ins, "pcc_gc_take_pinned_slot") and roots.slot(roots.args(ins)[0]) == output:
                assert lease_released and argument_cleared and not output_cleared, (
                    "constructor output transfer must follow argument retirement without losing its owner"
                )
                transfers += 1
                transferred = True
                break
        if transferred:
            continue
        successors = _successors(current)
        if successors:
            pending.extend((target, 0, lease_released, argument_cleared, output_cleared)
                           for target in successors)
        elif current.terminator.kind in ("ret", "ret_void"):
            assert lease_released and argument_cleared and output_cleared, (
                "constructor error exit must release both temporary owners and the argument lease"
            )
            error_returns += 1
    assert transfers and error_returns, "check both successful transfer and real error cleanup"
    roots.assert_frame_exits(exact_abi=True)


@pytest.mark.parametrize("expression,symbol,has_argument,nested", (
    ("ValueError()", "py_exc_new", False, False),
    ("ValueError('sentinel')", "py_exc_new", False, False),
    ("ValueError(value)", "py_exc_new_with_value", True, False),
    ("ValueError(TypeError('inner'))", "py_exc_new_with_value", True, True),
))
def test_direct_exception_constructor_retires_temporary_owners(
        tmp_path, monkeypatch, expression, symbol, has_argument, nested):
    source = ("def consume(first, second=None):\n    return first\n"
              "def make_value(value):\n    return consume(" + expression + ")\n")
    namespace = {}
    exec(source, namespace)
    value = []
    result = namespace["make_value"](value)
    assert type(result) is ValueError
    if nested:
        assert type(result.args[0]) is TypeError and result.args[0].args == ("inner",)
    elif has_argument:
        assert result.args == (value,) and result.args[0] is value
    else:
        assert result.args == (() if expression == "ValueError()" else ("sentinel",))
    module, _ = _emit(tmp_path, monkeypatch, source)
    _temporary_owner_contract(module, symbol, has_argument, nested)
