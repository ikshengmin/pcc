"""Evaluation, forcing, readback and conversion.

A :class:`Machine` interprets values relative to three things:

``signature``  global data types, constructors and functions
``defs``       context variables refined by pattern matching (level -> value)
``metas``      metavariable solutions, during elaboration only

Values are never updated in place.  A neutral value whose head later becomes
defined is resolved on demand by :meth:`Machine.force`, so the same value can
be read under different refinements in different branches of a case tree.
"""

from __future__ import annotations

from pcc.proof.terms import (
    App,
    Closure,
    Const,
    HFun,
    HMeta,
    HVar,
    Lam,
    Leaf,
    Meta,
    Pi,
    Sort,
    Split,
    VCon,
    VData,
    VLam,
    VNeutral,
    VPi,
    VSort,
    Var,
    With,
    apps,
    var,
)


class MachineError(Exception):
    """The machine was asked to do something no well-typed term needs."""


class Machine:
    __slots__ = ("sig", "defs", "metas")

    def __init__(self, signature, defs=None, metas=None):
        self.sig = signature
        self.defs = defs if defs is not None else {}
        self.metas = metas

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, term, env):
        if isinstance(term, Var):
            return env[len(env) - 1 - term.index]
        if isinstance(term, App):
            return self.apply(self.evaluate(term.fn, env), self.evaluate(term.arg, env))
        if isinstance(term, Const):
            return self.constant(term.name)
        if isinstance(term, Lam):
            return VLam(term.name, self.evaluate(term.domain, env), Closure(env, term.body))
        if isinstance(term, Pi):
            return VPi(term.name, self.evaluate(term.domain, env), Closure(env, term.codomain))
        if isinstance(term, Sort):
            return VSort(term.level)
        if isinstance(term, Meta):
            return VNeutral(HMeta(term.ident), ())
        raise MachineError(f"cannot evaluate {term!r}")

    def constant(self, name):
        kind = self.sig.kind(name)
        if kind == "data":
            return VData(name, ())
        if kind == "constructor":
            return VCon(name, ())
        if kind == "function":
            value = VNeutral(HFun(name), ())
            if self.sig.functions[name].arity == 0:
                return self.force(value)
            return value
        raise MachineError(f"unknown constant {name!r}")

    def apply_closure(self, closure, value):
        return self.evaluate(closure.body, closure.env + (value,))

    def apply(self, fn, arg):
        if isinstance(fn, VLam):
            return self.apply_closure(fn.closure, arg)
        if isinstance(fn, VNeutral):
            value = VNeutral(fn.head, fn.spine + (arg,))
            if isinstance(fn.head, HFun):
                return self.force(value)
            return value
        if isinstance(fn, VData):
            if len(fn.args) >= self.sig.datas[fn.name].arity:
                raise MachineError(f"data type {fn.name} applied to too many arguments")
            return VData(fn.name, fn.args + (arg,))
        if isinstance(fn, VCon):
            if len(fn.args) >= self.sig.constructors[fn.name].arity:
                raise MachineError(f"constructor {fn.name} applied to too many arguments")
            return VCon(fn.name, fn.args + (arg,))
        raise MachineError("application of a value that is not a function")

    def apply_spine(self, value, spine):
        for arg in spine:
            value = self.apply(value, arg)
        return value

    # -- forcing -------------------------------------------------------------

    def force(self, value):
        """Resolve the head of a value as far as current knowledge allows."""
        while isinstance(value, VNeutral):
            head = value.head
            if isinstance(head, HVar):
                definition = self.defs.get(head.level)
                if definition is None:
                    return value
                value = self.apply_spine(definition, value.spine)
            elif isinstance(head, HMeta):
                solution = None if self.metas is None else self.metas.solution(head.ident)
                if solution is None:
                    return value
                value = self.apply_spine(solution, value.spine)
            else:
                unfolded = self.unfold(head.name, value.spine)
                if unfolded is None:
                    return value
                value = unfolded
        return value

    def unfold(self, name, spine):
        """Run a function's case tree.  ``None`` when it is stuck."""
        info = self.sig.functions[name]
        tree = info.tree
        if tree is None or len(spine) < info.arity:
            return None
        env = spine[: info.arity]
        while True:
            if isinstance(tree, Leaf):
                result = self.evaluate(tree.body, env)
                return self.apply_spine(result, spine[info.arity :])
            if isinstance(tree, Split):
                scrutinee = self.force(env[tree.level])
                if not isinstance(scrutinee, VCon):
                    return None
                constructor = self.sig.constructors[scrutinee.name]
                if len(scrutinee.args) != constructor.arity:
                    return None
                subtree = tree.branch(scrutinee.name)
                if subtree is None:
                    return None
                env = env + scrutinee.args[constructor.nparams :]
                tree = subtree
            elif isinstance(tree, With):
                copies = tuple(env[level] for level, _ in tree.rebinds)
                env = env + (self.evaluate(tree.scrutinee, env),) + copies
                tree = tree.body
            else:
                raise MachineError(f"malformed case tree in {name}")

    # -- readback ------------------------------------------------------------

    def quote(self, value, depth):
        """The normal form of a value, as a term at ``depth``."""
        value = self.force(value)
        if isinstance(value, VNeutral):
            head = value.head
            if isinstance(head, HVar):
                term = Var(depth - 1 - head.level)
            elif isinstance(head, HMeta):
                term = Meta(head.ident)
            else:
                term = Const(head.name)
            return apps(term, [self.quote(arg, depth) for arg in value.spine])
        if isinstance(value, VCon):
            return apps(Const(value.name), [self.quote(arg, depth) for arg in value.args])
        if isinstance(value, VData):
            return apps(Const(value.name), [self.quote(arg, depth) for arg in value.args])
        if isinstance(value, VSort):
            return Sort(value.level)
        if isinstance(value, VPi):
            body = self.apply_closure(value.closure, var(depth))
            return Pi(value.name, self.quote(value.domain, depth), self.quote(body, depth + 1))
        if isinstance(value, VLam):
            body = self.apply_closure(value.closure, var(depth))
            return Lam(value.name, self.quote(value.domain, depth), self.quote(body, depth + 1))
        raise MachineError(f"cannot read back {value!r}")

    def quote_renamed(self, value, depth, renaming):
        """Like :meth:`quote`, sending each level in ``renaming`` to its image."""
        value = self.force(value)
        if isinstance(value, VNeutral):
            head = value.head
            if isinstance(head, HVar):
                term = Var(depth - 1 - renaming.get(head.level, head.level))
            elif isinstance(head, HMeta):
                term = Meta(head.ident)
            else:
                term = Const(head.name)
            return apps(term, [self.quote_renamed(arg, depth, renaming) for arg in value.spine])
        if isinstance(value, (VCon, VData)):
            return apps(
                Const(value.name),
                [self.quote_renamed(arg, depth, renaming) for arg in value.args],
            )
        if isinstance(value, VSort):
            return Sort(value.level)
        if isinstance(value, VPi):
            body = self.apply_closure(value.closure, var(depth))
            return Pi(
                value.name,
                self.quote_renamed(value.domain, depth, renaming),
                self.quote_renamed(body, depth + 1, renaming),
            )
        if isinstance(value, VLam):
            body = self.apply_closure(value.closure, var(depth))
            return Lam(
                value.name,
                self.quote_renamed(value.domain, depth, renaming),
                self.quote_renamed(body, depth + 1, renaming),
            )
        raise MachineError(f"cannot read back {value!r}")

    # -- conversion ----------------------------------------------------------

    def conv(self, left, right, depth):
        """Definitional equality of two values of the same type."""
        if left is right:
            return True
        left = self.force(left)
        right = self.force(right)
        if isinstance(left, VLam) or isinstance(right, VLam):
            if not (self._applicable(left) and self._applicable(right)):
                return False
            fresh = var(depth)
            return self.conv(self.apply(left, fresh), self.apply(right, fresh), depth + 1)
        if isinstance(left, VSort):
            return isinstance(right, VSort) and left.level == right.level
        if isinstance(left, VPi):
            if not isinstance(right, VPi):
                return False
            if not self.conv(left.domain, right.domain, depth):
                return False
            fresh = var(depth)
            return self.conv(
                self.apply_closure(left.closure, fresh),
                self.apply_closure(right.closure, fresh),
                depth + 1,
            )
        if isinstance(left, VData):
            return (
                isinstance(right, VData)
                and left.name == right.name
                and self._conv_all(left.args, right.args, depth)
            )
        if isinstance(left, VCon):
            return (
                isinstance(right, VCon)
                and left.name == right.name
                and self._conv_all(left.args, right.args, depth)
            )
        if isinstance(left, VNeutral):
            return (
                isinstance(right, VNeutral)
                and left.head == right.head
                and self._conv_all(left.spine, right.spine, depth)
            )
        return False

    def _conv_all(self, lefts, rights, depth):
        if len(lefts) != len(rights):
            return False
        for index in range(len(lefts)):
            if not self.conv(lefts[index], rights[index], depth):
                return False
        return True

    def _applicable(self, value):
        """Whether applying ``value`` to one more argument is meaningful."""
        if isinstance(value, (VLam, VNeutral)):
            return True
        if isinstance(value, VData):
            return len(value.args) < self.sig.datas[value.name].arity
        if isinstance(value, VCon):
            return len(value.args) < self.sig.constructors[value.name].arity
        return False
