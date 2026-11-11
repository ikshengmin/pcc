"""The checked prelude.

Every definition below is checked by ``pcc.proof`` like any user module, and
it is also plain Python: importing this module and calling these functions
runs them.

The functions ``add``, ``mul``, ``lt``, ``le``, ``nat_eq``, ``band``, ``bor``
and ``bnot`` give the operators ``+ * < <= == and or not`` their meaning in the
logic.  A checked program that writes ``n + m`` is read as ``add(n, m)`` and
executed by Python's own ``+``; on natural numbers the two agree.
"""

from __future__ import annotations

from dataclasses import dataclass

from pcc.proof.runtime import Bool, Data, Fn, Implicit, Nat, Type, impossible, suc

__all__ = [
    "And",
    "Both",
    "Cons",
    "Dec",
    "Empty",
    "Eq",
    "Exists",
    "Inl",
    "Inr",
    "Just",
    "List",
    "Maybe",
    "Nil",
    "No",
    "Not",
    "Nothing",
    "Or",
    "Pair",
    "Refl",
    "Tt",
    "Unit",
    "Yes",
    "absurd",
    "add",
    "band",
    "bnot",
    "bor",
    "cong",
    "le",
    "lt",
    "monus",
    "mul",
    "nat_eq",
    "subst",
    "sym",
    "trans",
]


# -- natural numbers ---------------------------------------------------------

def add(n: Nat, m: Nat) -> Nat:
    """Addition, by recursion on the second argument: ``n + 1`` is ``suc(n)``."""
    match m:
        case 0:
            return n
        case _:
            return suc(add(n, m - 1))


def mul(n: Nat, m: Nat) -> Nat:
    match m:
        case 0:
            return 0
        case _:
            return add(mul(n, m - 1), n)


def monus(n: Nat, m: Nat) -> Nat:
    """Truncated subtraction: ``monus(n, m)`` is ``0`` when ``m`` exceeds ``n``."""
    match m:
        case 0:
            return n
        case _:
            match n:
                case 0:
                    return 0
                case _:
                    return monus(n - 1, m - 1)


def lt(n: Nat, m: Nat) -> Bool:
    match m:
        case 0:
            return False
        case _:
            match n:
                case 0:
                    return True
                case _:
                    return lt(n - 1, m - 1)


def le(n: Nat, m: Nat) -> Bool:
    match n:
        case 0:
            return True
        case _:
            match m:
                case 0:
                    return False
                case _:
                    return le(n - 1, m - 1)


def nat_eq(n: Nat, m: Nat) -> Bool:
    match n:
        case 0:
            match m:
                case 0:
                    return True
                case _:
                    return False
        case _:
            match m:
                case 0:
                    return False
                case _:
                    return nat_eq(n - 1, m - 1)


# -- booleans ----------------------------------------------------------------

def bnot(b: Bool) -> Bool:
    match b:
        case True:
            return False
        case False:
            return True


def band(a: Bool, b: Bool) -> Bool:
    match a:
        case True:
            return b
        case False:
            return False


def bor(a: Bool, b: Bool) -> Bool:
    match a:
        case True:
            return True
        case False:
            return b


# -- propositions ------------------------------------------------------------

class Empty(Data):
    """The proposition with no proof."""


class Unit(Data):
    """The proposition with exactly one proof."""


@dataclass(frozen=True)
class Tt(Unit):
    pass


type Not[P: Type] = Fn[P, Empty]


def absurd[A: Type](e: Empty) -> A:
    return impossible(e)


class Eq[A: Implicit[Type], x: A, y: A](Data):
    """Propositional equality."""


@dataclass(frozen=True)
class Refl[A: Type, x: A](Eq[x, x]):
    pass


def sym[A: Type, x: A, y: A](e: Eq[x, y]) -> Eq[y, x]:
    match e:
        case Refl():
            return Refl()


def trans[A: Type, x: A, y: A, z: A](p: Eq[x, y], q: Eq[y, z]) -> Eq[x, z]:
    match p:
        case Refl():
            return q


def cong[A: Type, B: Type, x: A, y: A](f: Fn[A, B], e: Eq[x, y]) -> Eq[f(x), f(y)]:
    match e:
        case Refl():
            return Refl()


def subst[A: Type, x: A, y: A](P: Fn[A, Type], e: Eq[x, y], px: P(x)) -> P(y):
    match e:
        case Refl():
            return px


class And[A: Type, B: Type](Data):
    """Conjunction, and the non-dependent pair."""


@dataclass(frozen=True)
class Both[A: Type, B: Type](And[A, B]):
    left: A
    right: B


class Or[A: Type, B: Type](Data):
    """Disjunction, and the tagged union."""


@dataclass(frozen=True)
class Inl[A: Type, B: Type](Or[A, B]):
    left: A


@dataclass(frozen=True)
class Inr[A: Type, B: Type](Or[A, B]):
    right: B


class Exists[A: Type, B: Fn[A, Type]](Data):
    """The dependent pair: a witness and a proof about it."""


@dataclass(frozen=True)
class Pair[A: Type, B: Fn[A, Type]](Exists[A, B]):
    witness: A
    proof: B(witness)


class Dec[P: Type](Data):
    """A decision: a proof of ``P`` or a refutation of it."""


@dataclass(frozen=True)
class Yes[P: Type](Dec[P]):
    proof: P


@dataclass(frozen=True)
class No[P: Type](Dec[P]):
    refutation: Not[P]


# -- data --------------------------------------------------------------------

class Maybe[A: Type](Data):
    pass


@dataclass(frozen=True)
class Nothing[A: Type](Maybe[A]):
    pass


@dataclass(frozen=True)
class Just[A: Type](Maybe[A]):
    value: A


class List[A: Type](Data):
    pass


@dataclass(frozen=True)
class Nil[A: Type](List[A]):
    pass


@dataclass(frozen=True)
class Cons[A: Type](List[A]):
    head: A
    tail: List[A]
