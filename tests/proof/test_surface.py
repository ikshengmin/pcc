"""The surface reader accepts a fragment of Python and rejects the rest."""

from __future__ import annotations

import textwrap

import pytest

from pcc.proof.surface import (
    PAs,
    PBool,
    PCon,
    PNum,
    PVar,
    PWild,
    SAlias,
    SBin,
    SCall,
    SCon,
    SConst,
    SData,
    SFun,
    SIf,
    SIfStmt,
    SImport,
    SIndex,
    SLambda,
    SMatch,
    SName,
    SNot,
    SNum,
    SReturn,
    SurfaceError,
    read_module,
)


def read(source):
    return read_module(textwrap.dedent(source), "sample", "sample.py")


def reject(source, message):
    with pytest.raises(SurfaceError, match=message):
        read(source)


def test_reads_a_family_and_its_constructors():
    module = read(
        '''
        """A module docstring is not a declaration."""
        from dataclasses import dataclass
        from pcc.proof.runtime import Data, Nat

        class Term(Data):
            """Untyped terms."""

        @dataclass(frozen=True)
        class Var(Term):
            index: Nat

        @dataclass(frozen=True)
        class App(Term):
            fn: Term
            arg: Term

        class Step[t: Term, u: Term](Data): pass

        @dataclass(frozen=True)
        class AppL[t1: Term, t1p: Term, t2: Term](Step[App(t1, t2), App(t1p, t2)]):
            step: Step[t1, t1p]
        '''
    )
    kinds = [type(declaration) for declaration in module.declarations]
    assert kinds == [SImport, SData, SCon, SCon, SData, SCon]
    imported = module.declarations[0]
    assert imported.module == "pcc.proof.runtime"
    assert imported.names == (("Data", "Data"), ("Nat", "Nat"))
    app = module.declarations[3]
    assert [field.name for field in app.fields] == ["fn", "arg"]
    step = module.declarations[4]
    assert [(p.name, p.implicit) for p in step.tparams] == [("t", False), ("u", False)]
    app_left = module.declarations[5]
    assert isinstance(app_left.base, SIndex)
    assert isinstance(app_left.base.args[0], SCall)
    assert [p.name for p in app_left.tparams] == ["t1", "t1p", "t2"]


def test_reads_implicit_family_parameters():
    module = read(
        """
        class Eq[A: Implicit[Type], x: A, y: A](Data): pass
        """
    )
    family = module.declarations[0]
    assert [(p.name, p.implicit) for p in family.tparams] == [
        ("A", True),
        ("x", False),
        ("y", False),
    ]


def test_reads_function_bodies():
    module = read(
        """
        def lift(c: Nat, t: Term) -> Term:
            '''Shift free variables.'''
            match t:
                case Var(i):
                    return Var(i) if i < c else Var(i + 1)
                case Lam(b):
                    return Lam(lift(c + 1, b))
                case App(f, a) as whole:
                    return App(lift(c, f), lift(c, a))

        def pick(n: Nat, m: Nat, b: Bool) -> Nat:
            match n, m:
                case (0, _):
                    return m
                case (_, 0):
                    return n
                case _:
                    if not b and n == m:
                        return 0
                    return n * m - 1
        """
    )
    lift, pick = module.declarations
    assert isinstance(lift, SFun) and [p.name for p in lift.params] == ["c", "t"]
    match = lift.body[0]
    assert isinstance(match, SMatch) and len(match.subjects) == 1
    first = match.cases[0]
    assert first.patterns == (PCon("Var", (PVar("i", 5),), 5),)
    assert isinstance(first.body[0], SReturn) and isinstance(first.body[0].value, SIf)
    assert isinstance(match.cases[2].patterns[0], PAs)
    multi = pick.body[0]
    assert len(multi.subjects) == 2
    assert multi.cases[0].patterns == (PNum(0, 14), PWild(14))
    assert multi.cases[2].patterns == (PWild(18), PWild(18))
    conditional = multi.cases[2].body[0]
    assert isinstance(conditional, SIfStmt) and conditional.orelse == ()
    assert isinstance(conditional.test, SBin) and conditional.test.op == "and"
    assert isinstance(conditional.test.left, SNot)
    final = multi.cases[2].body[1].value
    assert isinstance(final, SBin) and final.op == "-"


def test_reads_constants_aliases_lambdas_and_booleans():
    module = read(
        """
        type Rename = Fn[Nat, Nat]
        type Not[P: Type] = Fn[P, Empty]
        IDENTITY: Term = Lam(Var(0))

        def twice(f: Rename, b: Bool) -> Rename:
            match b:
                case True:
                    return lambda n: f(f(n))
                case False:
                    return f
        """
    )
    rename, negation, identity, twice = module.declarations
    assert isinstance(rename, SAlias) and rename.tparams == ()
    assert isinstance(negation, SAlias) and negation.tparams[0].name == "P"
    assert isinstance(identity, SConst) and isinstance(identity.type, SName)
    cases = twice.body[0].cases
    assert cases[0].patterns == (PBool(True, 8),)
    assert isinstance(cases[0].body[0].value, SLambda)
    assert cases[0].body[0].value.params == ("n",)


def test_main_guard_and_dunder_all_are_not_checked():
    module = read(
        """
        __all__ = ["ZERO"]
        ZERO: Nat = 0

        if __name__ == "__main__":
            import sys
            print(ZERO, file=sys.stderr)
        """
    )
    assert [type(declaration) for declaration in module.declarations] == [SConst]
    assert module.declarations[0].value == SNum(0, 3)


def test_reports_the_source_position():
    with pytest.raises(SurfaceError) as raised:
        read(
            """
            ZERO: Nat = 0

            def f(n: Nat) -> Nat:
                while n:
                    pass
            """
        )
    assert raised.value.path == "sample.py"
    assert raised.value.line == 5
    assert "While is outside the checked fragment" in str(raised.value)


@pytest.mark.parametrize(
    "source, message",
    [
        ("import os\n", "Import is outside the checked fragment"),
        ("from .sibling import x\n", "relative imports"),
        ("from m import *\n", "'\\*' is not checked"),
        ("x = 1\n", "needs its type"),
        ("x: Nat\n", "NAME: Type = value"),
        ("def f(n) -> Nat:\n    return n\n", "parameter n needs a type"),
        ("def f(n: Nat):\n    return n\n", "needs a result type"),
        ("def f(n: Nat = 0) -> Nat:\n    return n\n", "plain positional parameters"),
        ("def f(*ns: Nat) -> Nat:\n    return 0\n", "plain positional parameters"),
        ("@cache\ndef f(n: Nat) -> Nat:\n    return n\n", "decorators"),
        ("def f[T](n: Nat) -> Nat:\n    return n\n", "needs a type: \\[x: T\\]"),
        ("def f(n: Nat) -> Nat:\n    return\n", "return needs a value"),
        ("def f(n: Nat) -> Nat:\n    n = 1\n    return n\n", "Assign is outside"),
        ("def f(n: Nat) -> Nat:\n    return g(n=n)\n", "keyword arguments"),
        ("def f(n: Nat) -> Nat:\n    return g(*n)\n", "starred arguments"),
        ("def f(n: Nat) -> Nat:\n    return -1\n", "UnaryOp is outside"),
        ("def f(n: Nat) -> Nat:\n    return n / 2\n", "operator Div"),
        ("def f(n: Nat) -> Bool:\n    return 0 < n < 2\n", "chained comparisons"),
        ("def f(n: Nat) -> Nat:\n    return 'text'\n", "literal 'text'"),
        ("def f(n: Nat) -> Nat:\n    return n.real\n", "Attribute is outside"),
        (
            "def f(n: Nat) -> Nat:\n    match n:\n        case 0 if n:\n            return n\n",
            "case guards",
        ),
        (
            "def f(n: Nat) -> Nat:\n    match n:\n        case 0 | 1:\n            return n\n",
            "MatchOr patterns",
        ),
        (
            "def f(t: T) -> Nat:\n    match t:\n        case C(x=y):\n            return y\n",
            "keyword patterns",
        ),
        (
            "def f(t: T) -> Nat:\n    match t:\n        case None:\n            return 0\n",
            "None is outside",
        ),
        (
            "def f(a: Nat, b: Nat) -> Nat:\n    match a, b:\n        case (0,):\n            return 0\n",
            "needs 2 patterns",
        ),
        ("class C(Data):\n    x: Nat\n", "an inductive family has no body"),
        ("@dataclass\nclass C(Data): pass\n", "takes no decorator"),
        ("class C(D): pass\n", "@dataclass\\(frozen=True\\)"),
        ("@dataclass\nclass C(D): pass\n", "@dataclass\\(frozen=True\\)"),
        ("@dataclass(frozen=True)\nclass C(D, E): pass\n", "exactly one base"),
        ("@dataclass(frozen=True)\nclass C(D):\n    x: Nat = 0\n", "lists fields as name: Type"),
        (
            "@dataclass(frozen=True)\nclass C(D):\n    def m(self): pass\n",
            "lists fields as name: Type",
        ),
        ("@dataclass(frozen=True)\nclass C[x: Implicit[Nat]](D): pass\n", "of a family only"),
        ("def f(:\n", "not valid Python"),
    ],
)
def test_rejects_syntax_outside_the_fragment(source, message):
    reject(source, message)
