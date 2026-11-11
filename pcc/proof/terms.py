"""Core terms, values and case trees of the proof kernel.

Terms use de Bruijn indices.  Values use de Bruijn levels and closures.  A
case tree addresses the variables of its clause context by level; that context
only grows, so a level names the same variable in every subtree.
"""

from __future__ import annotations

from dataclasses import dataclass


# -- terms -------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Var:
    index: int


@dataclass(frozen=True, slots=True)
class Sort:
    level: int


@dataclass(frozen=True, slots=True)
class Const:
    """A global: a data type, a constructor or a defined function."""

    name: str


@dataclass(frozen=True, slots=True)
class App:
    fn: object
    arg: object


@dataclass(frozen=True, slots=True)
class Lam:
    name: str
    domain: object
    body: object


@dataclass(frozen=True, slots=True)
class Pi:
    name: str
    domain: object
    codomain: object


@dataclass(frozen=True, slots=True)
class Meta:
    """A metavariable.  Elaboration only; the kernel rejects it."""

    ident: int


def apps(head, args):
    term = head
    for arg in args:
        term = App(term, arg)
    return term


def unapply(term):
    """Split ``f a b c`` into ``(f, [a, b, c])``."""
    args = []
    while isinstance(term, App):
        args.append(term.arg)
        term = term.fn
    args.reverse()
    return term, args


# -- case trees --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Leaf:
    """A body, as a term in the leaf's clause context."""

    body: object


@dataclass(frozen=True, slots=True)
class Split:
    """Case analysis on the context variable at ``level``.

    Each branch appends the constructor's non-parameter arguments to the
    clause context.  Constructors whose indices cannot match have no branch.
    """

    level: int
    branches: tuple  # of (constructor name, subtree)

    def branch(self, constructor):
        for name, subtree in self.branches:
            if name == constructor:
                return subtree
        return None


@dataclass(frozen=True, slots=True)
class With:
    """Abstraction over an expression.

    Appends one variable ``w`` of the scrutinee's type to the clause context,
    then one copy of each context variable listed in ``rebinds``.  A rebind is
    ``(level, type)``: the copy's type is a term over ``w`` and the earlier
    copies, and must give back the original variable's type when ``w`` is the
    scrutinee and each copy is its original.  ``motive`` is the goal over
    ``w`` and the copies, with the same requirement.

    At run time ``w`` holds the scrutinee and each copy holds its original.
    """

    scrutinee: object
    motive: object
    body: object
    rebinds: tuple = ()  # of (level, type term)


# -- values ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Closure:
    env: tuple
    body: object


@dataclass(frozen=True, slots=True)
class VSort:
    level: int


@dataclass(frozen=True, slots=True)
class VPi:
    name: str
    domain: object
    closure: Closure


@dataclass(frozen=True, slots=True)
class VLam:
    name: str
    domain: object
    closure: Closure


@dataclass(frozen=True, slots=True)
class VData:
    """A data type applied to parameters and indices, possibly partially."""

    name: str
    args: tuple


@dataclass(frozen=True, slots=True)
class VCon:
    """A constructor applied to parameters and arguments, possibly partially."""

    name: str
    args: tuple


@dataclass(frozen=True, slots=True)
class HVar:
    level: int


@dataclass(frozen=True, slots=True)
class HMeta:
    ident: int


@dataclass(frozen=True, slots=True)
class HFun:
    name: str


@dataclass(frozen=True, slots=True)
class VNeutral:
    """A stuck computation: a head applied to a spine of arguments."""

    head: object
    spine: tuple


def var(level):
    return VNeutral(HVar(level), ())


def contains_meta(term):
    stack = [term]
    while stack:
        node = stack.pop()
        if isinstance(node, Meta):
            return True
        if isinstance(node, App):
            stack.append(node.fn)
            stack.append(node.arg)
        elif isinstance(node, Lam):
            stack.append(node.domain)
            stack.append(node.body)
        elif isinstance(node, Pi):
            stack.append(node.domain)
            stack.append(node.codomain)
    return False


def mentions_const(term, name):
    stack = [term]
    while stack:
        node = stack.pop()
        if isinstance(node, Const):
            if node.name == name:
                return True
        elif isinstance(node, App):
            stack.append(node.fn)
            stack.append(node.arg)
        elif isinstance(node, Lam):
            stack.append(node.domain)
            stack.append(node.body)
        elif isinstance(node, Pi):
            stack.append(node.domain)
            stack.append(node.codomain)
    return False


def free_levels(term, depth):
    """Levels below ``depth`` that a term at ``depth`` refers to."""
    found = set()
    stack = [(term, depth)]
    while stack:
        node, here = stack.pop()
        if isinstance(node, Var):
            level = here - 1 - node.index
            if level < depth:
                found.add(level)
        elif isinstance(node, App):
            stack.append((node.fn, here))
            stack.append((node.arg, here))
        elif isinstance(node, Lam):
            stack.append((node.domain, here))
            stack.append((node.body, here + 1))
        elif isinstance(node, Pi):
            stack.append((node.domain, here))
            stack.append((node.codomain, here + 1))
    return found


def tree_terms(tree):
    """Every term a case tree contains."""
    found = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, Leaf):
            found.append(node.body)
        elif isinstance(node, Split):
            for _, subtree in node.branches:
                stack.append(subtree)
        elif isinstance(node, With):
            found.append(node.scrutinee)
            found.append(node.motive)
            for _, type_term in node.rebinds:
                found.append(type_term)
            stack.append(node.body)
    return found
