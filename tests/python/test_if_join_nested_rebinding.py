"""A local widened at an ``if`` join is stored as an object in every nested arm.

``_typeconf_widen_if_join`` rebinds a local to one boxed slot when the two
arms of an ``if`` (or an arm and the fall-through path) leave it in different
storage. It used to retarget only the assignments at the top of each arm, so
an assignment nested one level deeper -- the usual ``elif`` -- kept its raw
``int`` slot. The read after the join then saw the stale object:

    spec, kind, ref, conv = part      # ref is a str
    if kind == "auto":
        ref = auto_index
    elif kind == "index":
        ref = int(ref)                # stored into a separate int slot
    ... ref < 0                       # read the str: TypeError natively

That shape is format_lowering._maybe_emit_literal_str_format, and the native
pcc1 failed Stage2 on it while compiling the C code generator.
Language reference 4.2.2 (resolution of names): one local, one binding.
The CPython comparison is the if_join_widens_nested_rebinding case in
test_python_cpython_alignment.py.
"""

from __future__ import annotations

import pytest

from pcc.frontends.python import py_ast as A
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


def _assignment_types(source: str, name: str) -> list[tuple[int, str]]:
    typed = infer_module(parse_and_lift(source, "<if-join>", "if_join"))
    found = []

    def walk(stmts):
        for stmt in stmts:
            if isinstance(stmt, A.Assign):
                for target in stmt.targets:
                    if getattr(target, "ident", None) == name:
                        found.append((stmt.span.line, type(target.ty).__name__))
            for field in ("body", "else_body", "finally_body"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, tuple):
                    walk(nested)
            for handler in getattr(stmt, "handlers", ()) or ():
                walk(handler.body)

    walk(typed.body[0].body)
    return found


@pytest.mark.parametrize(
    "source",
    [
        # elif: the nested arm rebinds the widened local
        "def f(t, k):\n    a, r = t\n    if k == 0:\n        r = 0\n"
        "    elif k == 1:\n        r = int(r)\n    return r\n",
        # deeper: a loop and a try inside the else arm
        "def f(t, k):\n    a, r = t\n    if k == 0:\n        r = 0\n"
        "    else:\n        for i in range(2):\n            try:\n"
        "                r = i\n            finally:\n                pass\n    return r\n",
        # with block inside the arm
        "def f(t, k, cm):\n    a, r = t\n    if k == 0:\n        r = 0\n"
        "    else:\n        with cm:\n            r = 1\n    return r\n",
    ],
    ids=["elif", "loop-try", "with"],
)
def test_every_store_to_a_widened_local_targets_the_object_slot(source):
    types = _assignment_types(source, "r")
    assert len(types) >= 2, types
    assert all(kind == "DynType" for _line, kind in types), types


def test_a_local_that_never_merges_keeps_its_native_slot():
    source = (
        "def f(k):\n    if k == 0:\n        r = 0\n    else:\n"
        "        if k == 1:\n            r = 1\n        else:\n            r = 2\n    return r\n"
    )
    types = _assignment_types(source, "r")
    assert types and all(kind == "IntType" for _line, kind in types), types


def test_nested_function_bodies_are_not_retargeted():
    source = (
        "def f(t, k):\n    a, r = t\n    if k == 0:\n        r = 0\n    else:\n"
        "        def g():\n            r = 5\n            return r\n        r = g()\n    return r\n"
    )
    typed = infer_module(parse_and_lift(source, "<if-join>", "if_join"))
    outer_if = typed.body[0].body[1]
    inner_def = next(s for s in outer_if.else_body if isinstance(s, A.FuncDef))
    inner_assign = inner_def.body[0]
    assert type(inner_assign.targets[0].ty).__name__ == "IntType"


def _read_types(source: str, name: str) -> list[str]:
    """Types of ``name`` where it is passed as a call argument, in source order."""
    typed = infer_module(parse_and_lift(source, "<if-join>", "if_join"))
    found = []

    def visit(stmts):
        for stmt in stmts:
            call = None
            if isinstance(stmt, A.Assign) and isinstance(stmt.value, A.Call):
                call = stmt.value
            elif isinstance(stmt, A.ExprStmt) and isinstance(stmt.expr, A.Call):
                call = stmt.expr
            if call is not None:
                for arg in call.args:
                    if getattr(arg, "ident", None) == name:
                        found.append(type(arg.ty).__name__)
            for field in ("body", "else_body"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, tuple):
                    visit(nested)

    visit(typed.body[0].body)
    return found


@pytest.mark.parametrize(
    "source",
    [
        # the else arm reads the value from before the if, not the then arm's
        "def f(t, k):\n    a, r = t\n    if k == 0:\n        r = 0\n"
        "    else:\n        print(r)\n    return r\n",
        # elif converts the pre-if value
        "def f(t, k):\n    a, r = t\n    if k == 0:\n        r = 0\n"
        "    elif k == 1:\n        r = int(r)\n    return r\n",
    ],
    ids=["else-read", "elif-convert"],
)
def test_the_else_arm_sees_the_bindings_from_before_the_if(source):
    assert _read_types(source, "r") == ["DynType"]


def test_bindings_after_the_if_are_the_union_of_both_arms():
    # A name the then arm introduces is still known after the if, and a name
    # both arms bind keeps the else arm's type where they agree.
    source = (
        "def f(k):\n    if k == 0:\n        x = 1\n        y = 2\n    else:\n"
        "        y = 3\n    print(x)\n    print(y)\n    return 0\n"
    )
    assert _read_types(source, "x") == ["IntType"]
    assert _read_types(source, "y") == ["IntType"]
