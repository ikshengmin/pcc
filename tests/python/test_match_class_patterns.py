"""Class patterns in ``match`` follow PEP 634 (Python reference 8.6.4.9).

``case C(p0, p1, k=q)`` checks ``isinstance(subject, C)``, then matches each
positional sub-pattern against ``getattr(subject, C.__match_args__[i])`` and
each keyword sub-pattern against ``getattr(subject, k)``; a missing attribute
is a failed match.  The eleven built-in types that match themselves (``int``,
``str``, ``tuple`` and so on) take a single positional sub-pattern that is
matched against the subject itself, as do their subclasses that do not define
``__match_args__``.  ``@dataclass`` defines ``__match_args__`` as its field
names.

Every program here is compiled by pcc, run natively, and compared with the
output of the same source under CPython.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _cpython(tmp_path, source):
    path = tmp_path / "reference.py"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=60.0
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _native(tmp_path, source):
    from pcc.frontends.python.pipeline import compile_python

    path = tmp_path / "case.py"
    executable = tmp_path / "case.out"
    path.write_text(source, encoding="utf-8")
    compile_python(str(path), str(executable), ir_scaffold_mode="on")
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30.0)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _agree(tmp_path, source, expected):
    source = textwrap.dedent(source).lstrip()
    reference = _cpython(tmp_path, source)
    assert reference.splitlines() == expected, "the expectation must be CPython's"
    assert _native(tmp_path, source).splitlines() == expected


DATACLASSES = """
from dataclasses import dataclass


class Term:
    pass


@dataclass(frozen=True)
class Var(Term):
    index: int


@dataclass(frozen=True)
class Lam(Term):
    body: Term


@dataclass(frozen=True)
class App(Term):
    fn: Term
    arg: Term
"""


def test_positional_subpatterns_read_dataclass_fields(tmp_path):
    _agree(
        tmp_path,
        DATACLASSES
        + """

def lift(c, t):
    match t:
        case Var(i):
            return Var(i) if i < c else Var(i + 1)
        case Lam(b):
            return Lam(lift(c + 1, b))
        case App(f, a):
            return App(lift(c, f), lift(c, a))


def describe(t):
    match t:
        case App(Lam(body), arg):
            return "redex " + repr(body) + " " + repr(arg)
        case App(f, a):
            return "application"
        case Var(i):
            return "variable " + str(i)
        case _:
            return "other"


def main():
    print(lift(0, App(Var(0), Lam(Var(1)))))
    print(describe(App(Lam(Var(0)), Var(3))))
    print(describe(App(Var(1), Var(2))))
    print(describe(Var(5)))
    print(describe(Lam(Var(0))))
    print(App.__match_args__)
    print(Var.__match_args__)


if __name__ == "__main__":
    main()
""",
        [
            "App(fn=Var(index=1), arg=Lam(body=Var(index=2)))",
            "redex Var(index=0) Var(index=3)",
            "application",
            "variable 5",
            "other",
            "('fn', 'arg')",
            "('index',)",
        ],
    )


def test_keyword_subpatterns_and_missing_attributes(tmp_path):
    _agree(
        tmp_path,
        """
from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int


def where(p):
    match p:
        case Point(x=0, y=0):
            return "origin"
        case Point(x=0, y=height):
            return "on the y axis at " + str(height)
        case Point(z=_):
            return "impossible: Point has no z"
        case Point(width, y=1):
            return "at height one, x = " + str(width)
        case Point():
            return "elsewhere"


def main():
    print(where(Point(0, 0)))
    print(where(Point(0, 4)))
    print(where(Point(7, 1)))
    print(where(Point(7, 2)))


if __name__ == "__main__":
    main()
""",
        [
            "origin",
            "on the y axis at 4",
            "at height one, x = 7",
            "elsewhere",
        ],
    )


def test_explicit_match_args_on_an_ordinary_class(tmp_path):
    _agree(
        tmp_path,
        """
class Interval:
    __match_args__ = ("low", "high")

    def __init__(self, low, high):
        self.low = low
        self.high = high


def width(value):
    match value:
        case Interval(low, high):
            return high - low
        case _:
            return -1


def main():
    print(width(Interval(3, 10)))
    print(width(42))


if __name__ == "__main__":
    main()
""",
        ["7", "-1"],
    )


def test_builtin_types_match_themselves(tmp_path):
    _agree(
        tmp_path,
        """
def classify(value):
    match value:
        case bool(flag):
            return "bool " + str(flag)
        case int(number):
            return "int " + str(number + 1)
        case str(text):
            return "str " + text.upper()
        case tuple((int(first), int(second))):
            return "pair " + str(first * 10 + second)
        case list(items):
            return "list of " + str(len(items))
        case _:
            return "other"


def main():
    print(classify(True))
    print(classify(4))
    print(classify("abc"))
    print(classify((2, 7)))
    print(classify([1, 2, 3]))
    print(classify(None))


if __name__ == "__main__":
    main()
""",
        ["bool True", "int 5", "str ABC", "pair 27", "list of 3", "other"],
    )


@pytest.mark.parametrize("frozen", [False, True])
def test_dataclass_match_args_keep_field_order_with_inheritance(tmp_path, frozen):
    decorator = "@dataclass(frozen=True)" if frozen else "@dataclass"
    _agree(
        tmp_path,
        f"""
from dataclasses import dataclass


{decorator}
class Base:
    first: int


{decorator}
class Child(Base):
    second: int


def main():
    print(Base.__match_args__)
    print(Child.__match_args__)
    match Child(1, 2):
        case Child(a, b):
            print(a, b)


if __name__ == "__main__":
    main()
""",
        ["('first',)", "('first', 'second')", "1 2"],
    )


def test_a_user_match_args_is_not_replaced(tmp_path):
    _agree(
        tmp_path,
        """
from dataclasses import dataclass


@dataclass
class Pair:
    __match_args__ = ("right",)
    left: int
    right: int


def main():
    print(Pair.__match_args__)
    match Pair(1, 2):
        case Pair(value):
            print(value)


if __name__ == "__main__":
    main()
""",
        ["('right',)", "2"],
    )
