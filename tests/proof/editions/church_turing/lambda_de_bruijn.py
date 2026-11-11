"""Lambda terms with de Bruijn indices.

Python edition of the Agda module ``LambdaDeBruijn``.  The definitions compute
when the module runs and are the objects the proofs in this package are about.
"""

from __future__ import annotations

from dataclasses import dataclass

from pcc.proof.prelude import Eq, Refl, monus
from pcc.proof.runtime import Data, Fn, Nat, suc


class Term(Data):
    """An untyped lambda term; variables are de Bruijn indices."""


@dataclass(frozen=True)
class Var(Term):
    index: Nat


@dataclass(frozen=True)
class Lam(Term):
    body: Term


@dataclass(frozen=True)
class App(Term):
    fn: Term
    arg: Term


# -- index-based substitution ------------------------------------------------

def lift(c: Nat, t: Term) -> Term:
    """Shift by one every variable at or above the cutoff ``c``."""
    match t:
        case Var(i):
            return Var(i) if i < c else Var(i + 1)
        case Lam(b):
            return Lam(lift(c + 1, b))
        case App(f, a):
            return App(lift(c, f), lift(c, a))


def subst(n: Term, j: Nat, t: Term) -> Term:
    """Replace variable ``j`` in ``t`` by ``n`` and close the gap."""
    match t:
        case Var(i):
            if i == j:
                return n
            if i < j:
                return Var(i)
            return Var(monus(i, 1))
        case Lam(b):
            return Lam(subst(lift(0, n), j + 1, b))
        case App(f, a):
            return App(subst(n, j, f), subst(n, j, a))


# -- renaming and simultaneous substitution ----------------------------------

type Rename = Fn[Nat, Nat]

type Subst = Fn[Nat, Term]


def ext(rho: Rename, x: Nat) -> Nat:
    """Extend a renaming under a binder."""
    match x:
        case 0:
            return 0
        case _:
            return suc(rho(x - 1))


def rename(rho: Rename, t: Term) -> Term:
    match t:
        case Var(x):
            return Var(rho(x))
        case Lam(b):
            return Lam(rename(lambda x: ext(rho, x), b))
        case App(f, a):
            return App(rename(rho, f), rename(rho, a))


def exts(sigma: Subst, x: Nat) -> Term:
    """Extend a substitution under a binder."""
    match x:
        case 0:
            return Var(0)
        case _:
            return rename(suc, sigma(x - 1))


def subst_all(sigma: Subst, t: Term) -> Term:
    """Apply a simultaneous substitution."""
    match t:
        case Var(x):
            return sigma(x)
        case Lam(b):
            return Lam(subst_all(lambda x: exts(sigma, x), b))
        case App(f, a):
            return App(subst_all(sigma, f), subst_all(sigma, a))


def ids(x: Nat) -> Term:
    """The identity substitution."""
    return Var(x)


def subst_zero(m: Term, x: Nat) -> Term:
    """Send variable zero to ``m`` and shift the others down."""
    match x:
        case 0:
            return m
        case _:
            return Var(x - 1)


def subst1(n: Term, m: Term) -> Term:
    """``n`` with variable zero replaced by ``m``."""
    return subst_all(lambda x: subst_zero(m, x), n)


# -- constructors are injective ----------------------------------------------

def var_inj[i: Nat, j: Nat](e: Eq[Var(i), Var(j)]) -> Eq[i, j]:
    match e:
        case Refl():
            return Refl()


def lam_inj[t1: Term, t2: Term](e: Eq[Lam(t1), Lam(t2)]) -> Eq[t1, t2]:
    match e:
        case Refl():
            return Refl()


def app_inj1[t1: Term, t2: Term, u1: Term, u2: Term](
    e: Eq[App(t1, u1), App(t2, u2)]
) -> Eq[t1, t2]:
    match e:
        case Refl():
            return Refl()


def app_inj2[t1: Term, t2: Term, u1: Term, u2: Term](
    e: Eq[App(t1, u1), App(t2, u2)]
) -> Eq[u1, u2]:
    match e:
        case Refl():
            return Refl()
