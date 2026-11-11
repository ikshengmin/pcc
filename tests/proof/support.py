"""Helpers for checking Python sources in tests."""

from __future__ import annotations

import textwrap

import pytest

from pcc.proof.kernel import Context
from pcc.proof.session import CheckError, Session

PRELUDE = "pcc.proof.prelude"

HEADER = """\
from dataclasses import dataclass
from pcc.proof.runtime import Bool, Data, Fn, Implicit, Nat, Pi, Type, Type1, impossible, suc
from pcc.proof.prelude import (
    And, Both, Cons, Dec, Empty, Eq, Exists, Inl, Inr, Just, List, Maybe, Nil, No, Not,
    Nothing, Or, Pair, Refl, Tt, Unit, Yes, absurd, add, cong, monus, mul, sym, trans,
)
"""

HEADER_LINES = HEADER.count("\n")


def write_module(tmp_path, source, name="sample", header=True):
    text = textwrap.dedent(source).lstrip("\n")
    if header:
        text = HEADER + text
    path = tmp_path / f"{name}.py"
    path.write_text(text, encoding="utf-8")
    return path


def check(tmp_path, source, name="sample", header=True):
    """Check ``source`` as module ``name``; returns the session."""
    path = write_module(tmp_path, source, name, header)
    session = Session(roots=[str(tmp_path)])
    session.check_file(str(path))
    return session


def rejects(tmp_path, source, message, name="sample", header=True):
    """Assert that ``source`` is rejected with ``message``; returns the error."""
    path = write_module(tmp_path, source, name, header)
    session = Session(roots=[str(tmp_path)])
    with pytest.raises(CheckError, match=message) as raised:
        session.check_file(str(path))
    assert session.kernel.idle
    return raised.value


def normal_form(session, module, name):
    """The normal form of the closed constant ``module.name``, rendered."""
    from pcc.proof.pretty import show
    from pcc.proof.terms import Const

    machine = session.kernel.machine(Context())
    value = machine.evaluate(Const(f"{module}.{name}"), ())
    return show(machine.quote(value, 0))


def erased_normal_form(session, module, name):
    """The normal form of ``module.name`` as the running program sees it.

    Implicit constructor arguments have no run-time counterpart, so they are
    dropped.  The result is rendered like :func:`render_runtime_value`.
    """
    from pcc.proof.terms import App, Const, unapply

    machine = session.kernel.machine(Context())
    term = machine.quote(machine.evaluate(Const(f"{module}.{name}"), ()), 0)

    def numeral(node):
        count = 0
        while True:
            if isinstance(node, Const) and node.name == "zero":
                return count
            if isinstance(node, App) and isinstance(node.fn, Const) and node.fn.name == "suc":
                count += 1
                node = node.arg
                continue
            return None

    def render(node):
        number = numeral(node)
        if number is not None:
            return str(number)
        head, args = unapply(node)
        if not isinstance(head, Const):
            raise AssertionError(f"{module}.{name} is not a closed constructor form")
        if head.name in ("true", "false"):
            return head.name
        fields = session.fields[head.name]
        shown = [render(arg) for arg in args[len(args) - fields :]] if fields else []
        return head.name.rsplit(".", 1)[-1] + "(" + ", ".join(shown) + ")"

    return render(term)


def render_runtime_value(value):
    """A Python value of the checked fragment, rendered for comparison."""
    import dataclasses

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if dataclasses.is_dataclass(value):
        shown = [render_runtime_value(getattr(value, f.name)) for f in dataclasses.fields(value)]
        return type(value).__name__ + "(" + ", ".join(shown) + ")"
    raise AssertionError(f"{value!r} is not a value of the checked fragment")
