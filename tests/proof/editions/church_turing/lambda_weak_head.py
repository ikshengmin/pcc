"""Weak head reduction.

Python edition of the first part of the Agda module ``LambdaWeakHead``: the
weak head step relation, its determinism, its inclusion in beta reduction, and
the weak head normal forms of variables and abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass

from church_turing.lambda_de_bruijn import App, Lam, Term, Var, subst1
from church_turing.lambda_untyped import App1, Beta, Step
from pcc.proof.prelude import Empty, Eq, Not, Refl
from pcc.proof.runtime import Data, Nat, Pi, Type, impossible


class WhStep[t: Term, u: Term](Data):
    """A weak head step contracts only the function spine of an application."""


@dataclass(frozen=True)
class WhBeta[m: Term, n: Term](WhStep[App(Lam(m), n), subst1(m, n)]):
    pass


@dataclass(frozen=True)
class WhApp[m: Term, mp: Term, n: Term](WhStep[App(m, n), App(mp, n)]):
    step: WhStep[m, mp]


def IsWhnf(m: Term) -> Type:
    """No weak head step leaves ``m``."""
    return Pi[Term, lambda n: Not[WhStep[m, n]]]


def wh_to_beta[m: Term, n: Term](r: WhStep[m, n]) -> Step[m, n]:
    """Every weak head step is a beta step."""
    match r:
        case WhBeta():
            return Beta()
        case WhApp(s):
            return App1(wh_to_beta(s))


def cong_app_function[a: Term, b: Term, c: Term](e: Eq[a, b]) -> Eq[App(a, c), App(b, c)]:
    match e:
        case Refl():
            return Refl()


def wh_deterministic[m: Term, n1: Term, n2: Term](
    r1: WhStep[m, n1], r2: WhStep[m, n2]
) -> Eq[n1, n2]:
    """At most one weak head step leaves a term."""
    match r1, r2:
        case (WhBeta(), WhBeta()):
            return Refl()
        case (WhApp(s1), WhApp(s2)):
            return cong_app_function(wh_deterministic(s1, s2))


def no_step_from_variable(x: Nat, n: Term, r: WhStep[Var(x), n]) -> Empty:
    return impossible(r)


def no_step_from_abstraction(b: Term, n: Term, r: WhStep[Lam(b), n]) -> Empty:
    return impossible(r)


def whnf_var(x: Nat) -> IsWhnf(Var(x)):
    return lambda n: lambda r: no_step_from_variable(x, n, r)


def whnf_lam(b: Term) -> IsWhnf(Lam(b)):
    return lambda n: lambda r: no_step_from_abstraction(b, n, r)


def whnf_app_function(m: Term, n: Term, nf: IsWhnf(App(m, n))) -> IsWhnf(m):
    """If an application is in weak head normal form, so is its function."""
    return lambda mp: lambda r: nf(App(mp, n))(WhApp(r))
