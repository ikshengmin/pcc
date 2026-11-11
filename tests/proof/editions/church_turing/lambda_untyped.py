"""Beta reduction of untyped lambda terms.

Python edition of the Agda module ``LambdaUntyped``: one-step reduction, its
reflexive transitive closure, normal forms, and the congruence rules of the
closure.  A value of ``Steps[t, u]`` is a reduction sequence from ``t`` to
``u``; the lemmas below are programs that build such sequences.
"""

from __future__ import annotations

from dataclasses import dataclass

from church_turing.lambda_de_bruijn import App, Lam, Term, subst1
from pcc.proof.prelude import And, Exists, Not
from pcc.proof.runtime import Data, Fn, Pi, Type


class Step[t: Term, u: Term](Data):
    """``t`` reduces to ``u`` by contracting one redex."""


@dataclass(frozen=True)
class Beta[t: Term, u: Term](Step[App(Lam(t), u), subst1(t, u)]):
    pass


@dataclass(frozen=True)
class App1[t1: Term, t1p: Term, t2: Term](Step[App(t1, t2), App(t1p, t2)]):
    step: Step[t1, t1p]


@dataclass(frozen=True)
class App2[t1: Term, t2: Term, t2p: Term](Step[App(t1, t2), App(t1, t2p)]):
    step: Step[t2, t2p]


@dataclass(frozen=True)
class Under[t: Term, tp: Term](Step[Lam(t), Lam(tp)]):
    step: Step[t, tp]


class Steps[t: Term, u: Term](Data):
    """``t`` reduces to ``u`` in zero or more steps."""


@dataclass(frozen=True)
class BetaRefl[t: Term](Steps[t, t]):
    pass


@dataclass(frozen=True)
class BetaStep[t1: Term, t2: Term, t3: Term](Steps[t1, t3]):
    head: Step[t1, t2]
    tail: Steps[t2, t3]


def Normal(t: Term) -> Type:
    """No step leaves ``t``."""
    return Not[Exists[Term, lambda tp: Step[t, tp]]]


def trans_steps[t1: Term, t2: Term, t3: Term](
    r1: Steps[t1, t2], r2: Steps[t2, t3]
) -> Steps[t1, t3]:
    match r1:
        case BetaRefl():
            return r2
        case BetaStep(s, r):
            return BetaStep(s, trans_steps(r, r2))


def cong_app_l[t1: Term, t1p: Term, u: Term](r: Steps[t1, t1p]) -> Steps[App(t1, u), App(t1p, u)]:
    match r:
        case BetaRefl():
            return BetaRefl()
        case BetaStep(s, rest):
            return BetaStep(App1(s), cong_app_l(rest))


def cong_app_r[t: Term, u: Term, up: Term](r: Steps[u, up]) -> Steps[App(t, u), App(t, up)]:
    match r:
        case BetaRefl():
            return BetaRefl()
        case BetaStep(s, rest):
            return BetaStep(App2(s), cong_app_r(rest))


def cong_lam[t: Term, tp: Term](r: Steps[t, tp]) -> Steps[Lam(t), Lam(tp)]:
    match r:
        case BetaRefl():
            return BetaRefl()
        case BetaStep(s, rest):
            return BetaStep(Under(s), cong_lam(rest))


type Confluence = Pi[
    Term,
    lambda L: Pi[
        Term,
        lambda M1: Pi[
            Term,
            lambda M2: Fn[
                Steps[L, M1],
                Steps[L, M2],
                Exists[Term, lambda N: And[Steps[M1, N], Steps[M2, N]]],
            ],
        ],
    ],
]
