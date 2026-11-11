"""Size measures of lambda terms.

Python edition of the Agda module ``LambdaSize``: the two size measures of the
Lean source, their unfolding equations, and the exact size of Church numerals.
Addition here recurses on its second argument, so several equations that need
rewriting in Agda hold by computation.
"""

from __future__ import annotations

from church_turing.lambda_de_bruijn import App, Lam, Rename, Term, Var, ext, rename
from pcc.proof.prelude import Eq, Refl, cong, trans
from pcc.proof.runtime import Bool, Nat, suc


def size(t: Term) -> Nat:
    """Syntax nodes, where the index ``i`` counts as ``i + 1``."""
    match t:
        case Var(i):
            return suc(i)
        case App(f, a):
            return suc(size(f) + size(a))
        case Lam(b):
            return suc(size(b))


def nodes(t: Term) -> Nat:
    """Syntax nodes, every variable counting as one."""
    match t:
        case Var(_):
            return 1
        case App(f, a):
            return suc(nodes(f) + nodes(a))
        case Lam(b):
            return suc(nodes(b))


def size_var(i: Nat) -> Eq[size(Var(i)), i + 1]:
    return Refl()


def size_app(m: Term, n: Term) -> Eq[size(App(m, n)), size(m) + size(n) + 1]:
    return Refl()


def size_lam(m: Term) -> Eq[size(Lam(m)), size(m) + 1]:
    return Refl()


def size_positive(m: Term) -> Eq[0 < size(m), True]:
    match m:
        case Var(_):
            return Refl()
        case App(_, _):
            return Refl()
        case Lam(_):
            return Refl()


def nodes_positive(m: Term) -> Eq[0 < nodes(m), True]:
    match m:
        case Var(_):
            return Refl()
        case App(_, _):
            return Refl()
        case Lam(_):
            return Refl()


def cong_add[a: Nat, b: Nat, c: Nat, d: Nat](left: Eq[a, b], right: Eq[c, d]) -> Eq[a + c, b + d]:
    match left, right:
        case (Refl(), Refl()):
            return Refl()


def nodes_rename(rho: Rename, m: Term) -> Eq[nodes(rename(rho, m)), nodes(m)]:
    """Renaming never changes the number of nodes."""
    match m:
        case Var(_):
            return Refl()
        case App(f, a):
            return cong(suc, cong_add(nodes_rename(rho, f), nodes_rename(rho, a)))
        case Lam(b):
            return cong(suc, nodes_rename(lambda x: ext(rho, x), b))


# -- Church numerals ---------------------------------------------------------

def church_body(n: Nat) -> Term:
    """``f (f (... x))`` with ``n`` applications, ``f`` and ``x`` free."""
    match n:
        case 0:
            return Var(0)
        case _:
            return App(Var(1), church_body(n - 1))


def church(n: Nat) -> Term:
    """The Church numeral ``lambda f. lambda x. f (f ... x)``."""
    return Lam(Lam(church_body(n)))


def two_plus(n: Nat) -> Eq[2 + n, suc(suc(n))]:
    match n:
        case 0:
            return Refl()
        case _:
            return cong(suc, two_plus(n - 1))


def size_church_body(n: Nat) -> Eq[size(church_body(n)), 3 * n + 1]:
    match n:
        case 0:
            return Refl()
        case _:
            return trans(
                cong(lambda s: suc(2 + s), size_church_body(n - 1)),
                cong(lambda k: suc(suc(k)), two_plus(3 * (n - 1))),
            )


def size_church(n: Nat) -> Eq[size(church(n)), 3 * n + 3]:
    """A Church numeral for ``n`` has size ``3 n + 3``."""
    return cong(lambda k: suc(suc(k)), size_church_body(n))
