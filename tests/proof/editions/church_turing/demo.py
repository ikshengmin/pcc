"""Closed examples: the definitions compute, and the proofs are values.

Every constant below has a value in the logic (its normal form in the kernel)
and a value in Python (what the module holds after import).  The test suite
compares the two.  Run the module to print them.
"""

from __future__ import annotations

from church_turing.lambda_de_bruijn import (
    App,
    Lam,
    Term,
    Var,
    lift,
    rename,
    subst,
    subst1,
)
from church_turing.lambda_untyped import (
    App1,
    Beta,
    BetaRefl,
    BetaStep,
    Step,
    Steps,
    cong_app_l,
    trans_steps,
)
from pcc.proof.prelude import Eq, Refl
from pcc.proof.runtime import Nat, suc

IDENTITY: Term = Lam(Var(0))
K: Term = Lam(Lam(Var(1)))
OMEGA_HALF: Term = Lam(App(Var(0), Var(0)))


def church_body(n: Nat) -> Term:
    match n:
        case 0:
            return Var(0)
        case _:
            return App(Var(1), church_body(n - 1))


def church(n: Nat) -> Term:
    """The Church numeral: ``lambda f. lambda x. f (f ... x)``."""
    return Lam(Lam(church_body(n)))


def size(t: Term) -> Nat:
    match t:
        case Var(_):
            return 1
        case Lam(b):
            return suc(size(b))
        case App(f, a):
            return suc(size(f) + size(a))


LIFTED: Term = lift(0, App(Var(0), Lam(Var(1))))
SUBSTITUTED: Term = subst(IDENTITY, 0, App(Var(0), Var(1)))
RENAMED: Term = rename(suc, Lam(App(Var(0), Var(1))))
CONTRACTED: Term = subst1(App(Var(0), Var(1)), IDENTITY)
THREE: Term = church(3)
SIZE_OF_THREE: Nat = size(church(3))

# Proofs are programs: these are reduction sequences, as data.
ONE_STEP: Step[App(IDENTITY, Var(7)), Var(7)] = Beta()
IN_CONTEXT: Step[App(App(IDENTITY, K), Var(2)), App(K, Var(2))] = App1(Beta())
TWO_STEPS: Steps[App(App(K, Var(5)), Var(9)), Var(5)] = BetaStep(
    App1(Beta()), BetaStep(Beta(), BetaRefl())
)
LIFTED_CONTEXT: Steps[App(App(IDENTITY, K), Var(2)), App(K, Var(2))] = cong_app_l(
    BetaStep(Beta(), BetaRefl())
)
JOINED: Steps[App(App(IDENTITY, K), Var(2)), App(K, Var(2))] = trans_steps(
    LIFTED_CONTEXT, BetaRefl()
)


def three_has_size_nine() -> Eq[size(church(3)), 9]:
    return Refl()


if __name__ == "__main__":
    for name in (
        "LIFTED",
        "SUBSTITUTED",
        "RENAMED",
        "CONTRACTED",
        "THREE",
        "SIZE_OF_THREE",
        "TWO_STEPS",
        "JOINED",
    ):
        print(name, "=", globals()[name])
