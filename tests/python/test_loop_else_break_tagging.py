"""``while``/``for`` with ``else``: which ``break`` statements skip the clause.

Language reference 8.2 (``while``) and 8.3 (``for``): a ``break`` executed in
the loop body terminates the loop without executing the ``else`` clause.
7.9 (``break``): it terminates the nearest enclosing loop.

Both loop lowerings implement the clause as a flag that the body sets before
it breaks. ``_tag_loop_breaks`` finds the breaks that leave the loop; these
tests pin which ones it finds. Execution against CPython is covered by the
``loop_else_break_*`` cases in ``test_python_cpython_alignment.py``.
"""

from __future__ import annotations

import textwrap

from pcc.frontends.python.codegen import control_flow_lowering, for_loop_lowering
from pcc.frontends.python.codegen.control_flow_lowering import _tag_loop_breaks
from pcc.frontends.python.py_ast import Break, For, If, Try, While, With
from pcc.frontends.python.py_lift import parse_and_lift


def _lift_function_body(source: str):
    module = parse_and_lift(textwrap.dedent(source).lstrip(), "<loop-else>", "loop_else")
    return module.body[0].body


def _mark():
    return _lift_function_body("def marker():\n    pass\n")[0]


def _breaks(stmts, mark, in_nested_loop_body: bool = False):
    """``(owner, tagged)`` for every ``break`` in source order."""
    found = []
    previous = None
    for stmt in stmts:
        if isinstance(stmt, Break):
            owner = "nested" if in_nested_loop_body else "own"
            found.append((owner, previous is mark))
        elif isinstance(stmt, If):
            found += _breaks(stmt.body, mark, in_nested_loop_body)
            found += _breaks(stmt.else_body, mark, in_nested_loop_body)
        elif isinstance(stmt, Try):
            found += _breaks(stmt.body, mark, in_nested_loop_body)
            for handler in stmt.handlers:
                found += _breaks(handler.body, mark, in_nested_loop_body)
            found += _breaks(stmt.else_body, mark, in_nested_loop_body)
            found += _breaks(stmt.finally_body, mark, in_nested_loop_body)
        elif isinstance(stmt, With):
            found += _breaks(stmt.body, mark, in_nested_loop_body)
        elif isinstance(stmt, (For, While)):
            found += _breaks(stmt.body, mark, True)
            found += _breaks(stmt.else_body, mark, in_nested_loop_body)
        previous = stmt
    return found


LOOP_BODY = """
def scan(xs, cm):
    for x in xs:
        if x:
            break
        elif x is None:
            break
        with cm:
            break
        with cm as a, cm as b:
            try:
                break
            except ValueError:
                break
            else:
                break
            finally:
                pass
        for y in xs:
            break
        else:
            break
        while x:
            with cm:
                break
        else:
            with cm:
                break
    else:
        pass
"""


def test_every_break_that_leaves_the_loop_is_tagged_and_no_other():
    loop = _lift_function_body(LOOP_BODY)[0]
    assert isinstance(loop, For) and loop.else_body
    mark = _mark()

    before = _breaks(loop.body, mark)
    assert [owner for owner, _tagged in before] == [
        "own", "own",              # if / elif
        "own",                     # with
        "own", "own", "own",       # try body, handler, else inside a with
        "nested", "own",           # for body, for else
        "nested", "own",           # while body, while else
    ]
    assert not any(tagged for _owner, tagged in before)

    tagged = _tag_loop_breaks(loop.body, mark)
    after = _breaks(tagged, mark)
    assert [owner for owner, _tagged in after] == [owner for owner, _tagged in before]
    assert all(is_tagged == (owner == "own") for owner, is_tagged in after), after


def test_while_body_is_walked_the_same_way():
    loop = _lift_function_body(
        """
        def scan(n, cm):
            while n:
                with cm:
                    for item in n:
                        break
                    else:
                        break
            else:
                pass
        """
    )[0]
    assert isinstance(loop, While) and loop.else_body
    mark = _mark()
    assert _breaks(_tag_loop_breaks(loop.body, mark), mark) == [("nested", False), ("own", True)]


def test_statements_without_an_owned_break_keep_their_identity():
    loop = _lift_function_body(
        """
        def scan(xs):
            for x in xs:
                y = x
                for z in xs:
                    break
                while y:
                    break
            else:
                pass
        """
    )[0]
    mark = _mark()
    tagged = _tag_loop_breaks(loop.body, mark)
    assert len(tagged) == len(loop.body)
    assert all(new is old for new, old in zip(tagged, loop.body))


def test_for_and_while_lowerings_share_the_one_walk():
    assert for_loop_lowering._tag_loop_breaks is control_flow_lowering._tag_loop_breaks
    for mixin in (
        control_flow_lowering.ControlFlowLoweringMixin._emit_while,
        for_loop_lowering.ForLoopLoweringMixin._emit_for,
    ):
        assert "tag_breaks" not in mixin.__code__.co_varnames
        assert "tag_breaks" not in mixin.__code__.co_cellvars
