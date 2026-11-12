"""A local a loop rebinds with another storage type is one object slot.

Language reference 8.2 (``while``) and 8.3 (``for``): the body runs again from
the top after it completes or reaches ``continue``, the loop may run zero
times, and ``break`` leaves it from the middle. Reference 4.2.2: one name in a
function is one binding. So the type a loop body reads at its top, and the
type after the loop, are the join of the type before the loop with the types
at the end of the body and at every ``break``/``continue``.

Inference used to infer the body once with the pre-loop bindings. A local
bound to ``int`` before the loop and rebound to ``str`` in it stayed ``int`` at
the top of the next iteration:

    ref = 0
    while i < 2:
        out.append(int(ref) * 10)   # int(ref) folded to ref
        ref = "5"

and the native program multiplied the string through the int path and ended
with ``Unhandled non-exception object (null)``. The CPython comparison is the
``loop_backedge_widens_rebinding`` case in test_python_cpython_alignment.py.
"""

from __future__ import annotations

import pytest

from pcc.frontends.python import py_ast as A
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


def _function(source: str, index: int = 0):
    typed = infer_module(parse_and_lift(source, "<loop-join>", "loop_join"))
    return [s for s in typed.body if isinstance(s, A.FuncDef)][index]


def _walk(stmts, visit):
    for stmt in stmts:
        visit(stmt)
        for field in ("body", "else_body", "finally_body"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, tuple) and not isinstance(stmt, (A.FuncDef, A.ClassDef)):
                _walk(nested, visit)
        for handler in getattr(stmt, "handlers", ()) or ():
            _walk(handler.body, visit)


def _call_arg_types(fn, name: str, callee: str) -> list[str]:
    """Types of ``name`` passed directly to ``callee(...)``, in source order."""
    found = []

    def visit_expr(expr):
        if isinstance(expr, A.Call):
            if getattr(expr.func, "ident", None) == callee:
                for arg in expr.args:
                    if getattr(arg, "ident", None) == name:
                        found.append(type(arg.ty).__name__)
            for arg in expr.args:
                visit_expr(arg)
            visit_expr(expr.func)
        elif isinstance(expr, (A.BinOp, A.Compare)):
            visit_expr(expr.lhs)
            visit_expr(expr.rhs)
        elif isinstance(expr, A.Attr):
            visit_expr(expr.obj)

    def visit(stmt):
        if isinstance(stmt, A.Assign):
            visit_expr(stmt.value)
        elif isinstance(stmt, A.ExprStmt):
            visit_expr(stmt.expr)
        elif isinstance(stmt, A.Return) and stmt.value is not None:
            visit_expr(stmt.value)
        elif isinstance(stmt, (A.While, A.If)):
            visit_expr(stmt.cond)

    _walk(fn.body, visit)
    return found


def _assignment_types(fn, name: str) -> list[str]:
    found = []

    def visit(stmt):
        if isinstance(stmt, A.Assign):
            for target in stmt.targets:
                if getattr(target, "ident", None) == name:
                    found.append(type(target.ty).__name__)

    _walk(fn.body, visit)
    return found


WHILE_BACKEDGE = (
    "def g(out):\n"
    "    ref = 0\n"
    "    i = 0\n"
    "    while i < 2:\n"
    "        out.append(int(ref) * 10)\n"
    "        ref = '5'\n"
    "        i += 1\n"
    "    return out\n"
)

FOR_BACKEDGE = (
    "def f(items):\n"
    "    ref = 0\n"
    "    for s in items:\n"
    "        print(ref)\n"
    "        ref = s\n"
    "    return ref\n"
)


@pytest.mark.parametrize(
    "source,callee",
    [(WHILE_BACKEDGE, "int"), (FOR_BACKEDGE, "print")],
    ids=["while", "for"],
)
def test_the_top_of_the_body_reads_the_type_the_back_edge_brings(source, callee):
    fn = _function(source)
    assert _call_arg_types(fn, "ref", callee) == ["DynType"]
    # Every store of the widened local goes to the one object slot.
    assert set(_assignment_types(fn, "ref")) <= {"DynType", "IntType"}
    body_stores = [kind for kind in _assignment_types(fn, "ref")[1:]]
    assert body_stores and all(kind == "DynType" for kind in body_stores)


def test_a_loop_that_runs_zero_times_leaves_the_pre_loop_value():
    fn = _function(
        "def f(items: list[str]):\n"
        "    x = 0\n"
        "    for s in items:\n"
        "        x = s\n"
        "    print(x)\n"
        "    return 0\n"
    )
    assert _call_arg_types(fn, "x", "print") == ["DynType"]


def test_break_carries_the_type_bound_before_it():
    fn = _function(
        "def f(c, d):\n"
        "    x = 0\n"
        "    while c:\n"
        "        x = 's'\n"
        "        if d:\n"
        "            break\n"
        "        x = 1\n"
        "    print(x)\n"
        "    return 0\n"
    )
    assert _call_arg_types(fn, "x", "print") == ["DynType"]
    assert _assignment_types(fn, "x")[1:] == ["DynType", "DynType"]


def test_continue_carries_the_type_bound_before_it_to_the_top():
    fn = _function(
        "def f(c, d):\n"
        "    x = 0\n"
        "    while c:\n"
        "        print(x)\n"
        "        x = 's'\n"
        "        if d:\n"
        "            continue\n"
        "        x = 1\n"
        "    return 0\n"
    )
    assert _call_arg_types(fn, "x", "print") == ["DynType"]


def test_a_loop_whose_bindings_agree_keeps_native_types():
    fn = _function(
        "def f(n):\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        total = total + i\n"
        "        print(total)\n"
        "        i = i + 1\n"
        "    return total\n"
    )
    assert _call_arg_types(fn, "total", "print") == ["IntType"]
    assert set(_assignment_types(fn, "total")) == {"IntType"}
    assert set(_assignment_types(fn, "i")) == {"IntType"}


def test_a_local_first_bound_in_the_body_keeps_its_straight_line_types():
    # Each iteration binds ``a`` before reading it, so the back edge never
    # reaches a read; nothing is widened.
    fn = _function(
        "def f(items):\n"
        "    for s in items:\n"
        "        a = 1\n"
        "        print(a)\n"
        "        a = s\n"
        "        print(a)\n"
        "    return 0\n"
    )
    reads = _call_arg_types(fn, "a", "print")
    assert len(reads) == 2 and reads[0] == "IntType", reads
    assert _assignment_types(fn, "a")[0] == "IntType"


def test_an_inner_break_is_not_an_exit_of_the_outer_loop():
    # The inner loop's break leaves ``x`` a str only inside the outer body;
    # ``x = 1`` then rebinds it before the outer back edge, so the outer loop
    # top keeps ``int``.
    fn = _function(
        "def f(a, b):\n"
        "    x = 0\n"
        "    while a:\n"
        "        print(x)\n"
        "        while b:\n"
        "            x = 's'\n"
        "            break\n"
        "        x = 1\n"
        "    return 0\n"
    )
    assert _call_arg_types(fn, "x", "print") == ["IntType"]


def test_nested_function_bodies_are_not_retargeted():
    fn = _function(
        "def f(items: list[str]):\n"
        "    r = 0\n"
        "    for s in items:\n"
        "        def g():\n"
        "            r = 5\n"
        "            return r\n"
        "        r = s\n"
        "    return r\n"
    )
    loop = next(s for s in fn.body if isinstance(s, A.For))
    inner_def = next(s for s in loop.body if isinstance(s, A.FuncDef))
    assert type(inner_def.body[0].targets[0].ty).__name__ == "IntType"
    assert type(loop.body[1].targets[0].ty).__name__ == "DynType"


def test_the_while_condition_reads_the_joined_type():
    fn = _function(
        "def f(n):\n"
        "    x = 0\n"
        "    while check(x):\n"
        "        x = 'done'\n"
        "    return 0\n"
    )
    assert _call_arg_types(fn, "x", "check") == ["DynType"]
