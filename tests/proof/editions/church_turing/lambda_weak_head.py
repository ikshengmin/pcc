"""Weak head reduction.

Python edition of part of the Agda module ``LambdaWeakHead``: the weak head
step relation, its determinism, its inclusion in beta reduction, the weak head
normal forms of variables and abstractions, and explicit evaluation bounds.
A bound is data: no excluded middle or termination oracle is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from church_turing.lambda_de_bruijn import App, Lam, Term, Var, subst1
from church_turing.lambda_untyped import App1, Beta, BetaRefl, BetaStep, Step, Steps
from pcc.proof.prelude import And, Both, Empty, Eq, Exists, Inl, Inr, Not, Or, Pair, Refl
from pcc.proof.runtime import Data, Nat, Pi, Type, impossible, suc


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


# -- an explicit bound on weak head evaluation --------------------------------

def WHNIn(n: Nat, m: Term) -> Type:
    """``m`` reaches a weak head normal form in at most ``n`` weak head steps."""
    match n:
        case 0:
            return IsWhnf(m)
        case _:
            return Or[IsWhnf(m), Exists[Term, lambda k: And[WhStep[m, k], WHNIn(n - 1, k)]]]


def HasWhnfEval(m: Term) -> Type:
    return Exists[Nat, lambda n: WHNIn(n, m)]


def whn_in_from_whnf(n: Nat, m: Term, nf: IsWhnf(m)) -> WHNIn(n, m):
    match n:
        case 0:
            return nf
        case _:
            return Inl(nf)


def whn_in_weaken(n: Nat, m: Term, h: WHNIn(n, m)) -> WHNIn(suc(n), m):
    """A bound can always be raised by one."""
    match n:
        case 0:
            return Inl(h)
        case _:
            match h:
                case Inl(nf):
                    return Inl(nf)
                case Inr(Pair(k, Both(step, rest))):
                    return Inr(Pair(k, Both(step, whn_in_weaken(n - 1, k, rest))))


def has_whnf_eval_from_whnf(m: Term, nf: IsWhnf(m)) -> HasWhnfEval(m):
    return Pair(0, nf)


def has_whnf_eval_prepend[m: Term, k: Term](step: WhStep[m, k], e: HasWhnfEval(k)) -> HasWhnfEval(m):
    """One weak head step before an evaluation is an evaluation."""
    match e:
        case Pair(n, rest):
            return Pair(suc(n), Inr(Pair(k, Both(step, rest))))


def whnf_reduct_from_bound(n: Nat, m: Term, h: WHNIn(n, m)) -> Exists[
    Term, lambda j: And[Steps[m, j], IsWhnf(j)]
]:
    """A bounded evaluation reaches a weak head normal form by beta steps."""
    match n:
        case 0:
            return Pair(m, Both(BetaRefl(), h))
        case _:
            match h:
                case Inl(nf):
                    return Pair(m, Both(BetaRefl(), nf))
                case Inr(Pair(k, Both(step, rest))):
                    match whnf_reduct_from_bound(n - 1, k, rest):
                        case Pair(j, Both(steps, nf)):
                            return Pair(j, Both(BetaStep(wh_to_beta(step), steps), nf))
