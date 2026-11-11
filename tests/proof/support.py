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
