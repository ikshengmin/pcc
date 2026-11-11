"""The trusted kernel.

The kernel accepts three kinds of declaration and nothing else:

* an inductive family with its constructors,
* a function given by a case tree,
* (through the function form with arity zero) a constant.

There is no way to add an axiom.  Every declaration is checked from scratch:

* terms are typed by plain inference; conversion is the machine's;
* a data type must be strictly positive, live in ``Sort 0`` and take only
  small constructor arguments and indices;
* a case tree must split only on undetermined variables of data type, give
  exactly the branches whose indices can match (coverage), and end in leaves
  of the goal type;
* recursive calls must decrease in a lexicographic order of arguments.

Pattern matching refines context variables by first-order unification of
indices.  A solved variable keeps its place in the context and gains a
definition; :class:`Machine` reads values modulo those definitions.  The rules
used are deletion (so K holds), solution, injectivity, conflict and cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from pcc.proof.machine import Machine
from pcc.proof.pretty import show
from pcc.proof.terms import (
    App,
    Closure,
    Const,
    HVar,
    Lam,
    Leaf,
    Pi,
    Sort,
    Split,
    VCon,
    VData,
    VNeutral,
    VPi,
    VSort,
    Var,
    With,
    contains_meta,
    free_levels,
    mentions_const,
    tree_terms,
    unapply,
    var,
)


OK = "ok"
CLASH = "clash"
STUCK = "stuck"


class KernelError(Exception):
    """A declaration was rejected."""


# -- signature ---------------------------------------------------------------

@dataclass
class DataInfo:
    name: str
    type: object
    nparams: int
    nindices: int
    constructors: tuple = ()

    @property
    def arity(self):
        return self.nparams + self.nindices


@dataclass
class ConInfo:
    name: str
    data: str
    type: object
    nparams: int
    nargs: int

    @property
    def arity(self):
        return self.nparams + self.nargs


@dataclass
class FunInfo:
    name: str
    type: object
    arity: int
    tree: object  # None while the function is being checked


class Signature:
    def __init__(self):
        self.datas = {}
        self.constructors = {}
        self.functions = {}

    def kind(self, name):
        if name in self.datas:
            return "data"
        if name in self.constructors:
            return "constructor"
        if name in self.functions:
            return "function"
        return None

    def type_of(self, name):
        if name in self.datas:
            return self.datas[name].type
        if name in self.constructors:
            return self.constructors[name].type
        if name in self.functions:
            return self.functions[name].type
        raise KernelError(f"unknown constant {name}")


# -- contexts ----------------------------------------------------------------

class Context:
    """Typed variables by level, with the definitions matching has given them."""

    __slots__ = ("names", "types", "defs", "_env")

    def __init__(self, names=(), types=(), defs=None):
        self.names = names
        self.types = types
        self.defs = defs if defs is not None else {}
        self._env = None

    @property
    def size(self):
        return len(self.types)

    def env(self):
        if self._env is None:
            self._env = tuple(var(level) for level in range(len(self.types)))
        return self._env

    def extend(self, name, type_value):
        return Context(self.names + (name,), self.types + (type_value,), self.defs)

    def define(self, level, value):
        defs = dict(self.defs)
        defs[level] = value
        return Context(self.names, self.types, defs)


# -- the kernel --------------------------------------------------------------

class Kernel:
    def __init__(self):
        self.sig = Signature()

    def machine(self, ctx):
        return Machine(self.sig, ctx.defs)

    def show_value(self, ctx, value):
        return show(self.machine(ctx).quote(value, ctx.size), ctx.names)

    # -- typing --------------------------------------------------------------

    def infer(self, ctx, term):
        machine = self.machine(ctx)
        if isinstance(term, Var):
            if not 0 <= term.index < ctx.size:
                raise KernelError("variable out of scope")
            return ctx.types[ctx.size - 1 - term.index]
        if isinstance(term, Sort):
            if term.level < 0:
                raise KernelError("negative universe level")
            return VSort(term.level + 1)
        if isinstance(term, Const):
            return machine.evaluate(self.sig.type_of(term.name), ())
        if isinstance(term, App):
            fn_type = machine.force(self.infer(ctx, term.fn))
            if not isinstance(fn_type, VPi):
                raise KernelError(
                    f"{show(term.fn, ctx.names)} is applied but has type "
                    f"{self.show_value(ctx, fn_type)}"
                )
            self.check(ctx, term.arg, fn_type.domain)
            return machine.apply_closure(fn_type.closure, machine.evaluate(term.arg, ctx.env()))
        if isinstance(term, Lam):
            self.sort_of(ctx, term.domain)
            domain = machine.evaluate(term.domain, ctx.env())
            inner = ctx.extend(term.name, domain)
            body_type = self.infer(inner, term.body)
            codomain = self.machine(inner).quote(body_type, inner.size)
            return VPi(term.name, domain, Closure(ctx.env(), codomain))
        if isinstance(term, Pi):
            domain_level = self.sort_of(ctx, term.domain)
            domain = machine.evaluate(term.domain, ctx.env())
            codomain_level = self.sort_of(ctx.extend(term.name, domain), term.codomain)
            return VSort(max(domain_level, codomain_level))
        raise KernelError(f"not a core term: {term!r}")

    def check(self, ctx, term, expected):
        actual = self.infer(ctx, term)
        if not self.machine(ctx).conv(actual, expected, ctx.size):
            raise KernelError(
                f"{show(term, ctx.names)} has type {self.show_value(ctx, actual)} "
                f"but {self.show_value(ctx, expected)} is required"
            )

    def sort_of(self, ctx, term):
        """The universe level of a type."""
        sort = self.machine(ctx).force(self.infer(ctx, term))
        if not isinstance(sort, VSort):
            raise KernelError(f"{show(term, ctx.names)} is not a type")
        return sort.level

    def sort_of_value(self, ctx, value):
        return self.sort_of(ctx, self.machine(ctx).quote(value, ctx.size))

    def type_of_value(self, ctx, value):
        """The type of a value known to be well typed in ``ctx``."""
        return self.infer(ctx, self.machine(ctx).quote(value, ctx.size))

    # -- declarations: common ------------------------------------------------

    def _fresh_name(self, name):
        if self.sig.kind(name) is not None:
            raise KernelError(f"{name} is already declared")

    def _closed_type(self, name, term):
        if contains_meta(term):
            raise KernelError(f"{name}: unsolved metavariable in a declaration")
        if free_levels(term, 0) or self._escapes(term, 0):
            raise KernelError(f"{name}: declaration is not closed")
        self.sort_of(Context(), term)

    @staticmethod
    def _escapes(term, depth):
        """Whether ``term`` at ``depth`` has an index pointing outside it."""
        stack = [(term, depth)]
        while stack:
            node, here = stack.pop()
            if isinstance(node, Var):
                if node.index < 0 or node.index >= here:
                    return True
            elif isinstance(node, App):
                stack.append((node.fn, here))
                stack.append((node.arg, here))
            elif isinstance(node, Lam):
                stack.append((node.domain, here))
                stack.append((node.body, here + 1))
            elif isinstance(node, Pi):
                stack.append((node.domain, here))
                stack.append((node.codomain, here + 1))
        return False

    # -- declarations: data --------------------------------------------------

    def add_data(self, name, nparams, type_term, constructors):
        """Declare an inductive family.

        ``type_term`` is ``Pi params. Pi indices. Sort 0``.  Each constructor
        is ``(name, type)`` with type ``Pi params. Pi args. D params indices``.
        """
        self._fresh_name(name)
        self._closed_type(name, type_term)
        machine = self.machine(Context())
        ctx = Context()
        telescope = machine.force(machine.evaluate(type_term, ()))
        count = 0
        while isinstance(telescope, VPi):
            if count >= nparams and self.sort_of_value(ctx, telescope.domain) != 0:
                raise KernelError(f"{name}: index {telescope.name} has a large type")
            ctx = ctx.extend(telescope.name, telescope.domain)
            telescope = machine.force(machine.apply_closure(telescope.closure, var(count)))
            count += 1
        if not (isinstance(telescope, VSort) and telescope.level == 0):
            raise KernelError(f"{name}: a data type must end in Type")
        if count < nparams or nparams < 0:
            raise KernelError(f"{name}: fewer binders than declared parameters")
        seen = set()
        for cname, _ in constructors:
            if cname in seen or cname == name:
                raise KernelError(f"{name}: duplicate constructor {cname}")
            self._fresh_name(cname)
            seen.add(cname)
        info = DataInfo(name, type_term, nparams, count - nparams)
        self.sig.datas[name] = info
        added = []
        try:
            for cname, ctype in constructors:
                con = self._check_constructor(info, cname, ctype)
                self.sig.constructors[cname] = con
                added.append(cname)
        except Exception:
            for cname in added:
                del self.sig.constructors[cname]
            del self.sig.datas[name]
            raise
        info.constructors = tuple(cname for cname, _ in constructors)

    def _check_constructor(self, data, cname, ctype):
        self._closed_type(cname, ctype)
        machine = self.machine(Context())
        ctx = Context()
        mine = machine.force(machine.evaluate(ctype, ()))
        theirs = machine.force(machine.evaluate(data.type, ()))
        for position in range(data.nparams):
            if not isinstance(mine, VPi):
                raise KernelError(f"{cname}: missing data parameter {position}")
            if not machine.conv(mine.domain, theirs.domain, ctx.size):
                raise KernelError(f"{cname}: parameter {position} differs from the data type's")
            ctx = ctx.extend(mine.name, mine.domain)
            mine = machine.force(machine.apply_closure(mine.closure, var(position)))
            theirs = machine.force(machine.apply_closure(theirs.closure, var(position)))
        nargs = 0
        while isinstance(mine, VPi):
            if self.sort_of_value(ctx, mine.domain) != 0:
                raise KernelError(f"{cname}: argument {mine.name} has a large type")
            self._check_positive(ctx, data, cname, mine.domain)
            ctx = ctx.extend(mine.name, mine.domain)
            mine = machine.force(machine.apply_closure(mine.closure, var(ctx.size - 1)))
            nargs += 1
        if not (
            isinstance(mine, VData)
            and mine.name == data.name
            and len(mine.args) == data.arity
        ):
            raise KernelError(f"{cname}: a constructor must build a value of {data.name}")
        for position in range(data.nparams):
            if not machine.conv(mine.args[position], var(position), ctx.size):
                raise KernelError(f"{cname}: result does not use the data parameters")
        for index in mine.args[data.nparams :]:
            if mentions_const(machine.quote(index, ctx.size), data.name):
                raise KernelError(f"{cname}: {data.name} occurs in its own index")
        return ConInfo(cname, data.name, ctype, data.nparams, nargs)

    def _check_positive(self, ctx, data, cname, argument_type):
        """Strict positivity of ``data`` in one constructor argument type."""
        machine = self.machine(ctx)
        current = machine.force(argument_type)
        while isinstance(current, VPi):
            if mentions_const(machine.quote(current.domain, ctx.size), data.name):
                raise KernelError(f"{cname}: {data.name} occurs negatively")
            ctx = ctx.extend(current.name, current.domain)
            current = machine.force(machine.apply_closure(current.closure, var(ctx.size - 1)))
        if isinstance(current, VData) and current.name == data.name:
            if len(current.args) != data.arity:
                raise KernelError(f"{cname}: {data.name} is not fully applied")
            for position in range(data.nparams):
                if not machine.conv(current.args[position], var(position), ctx.size):
                    raise KernelError(f"{cname}: recursive occurrence changes a parameter")
            for index in current.args[data.nparams :]:
                if mentions_const(machine.quote(index, ctx.size), data.name):
                    raise KernelError(f"{cname}: {data.name} occurs in its own index")
        elif mentions_const(machine.quote(current, ctx.size), data.name):
            raise KernelError(f"{cname}: {data.name} does not occur strictly positively")

    # -- declarations: functions ---------------------------------------------

    def add_function(self, name, type_term, arity, tree):
        """Declare a function by a case tree over its first ``arity`` arguments."""
        self._fresh_name(name)
        self._closed_type(name, type_term)
        for term in tree_terms(tree):
            if contains_meta(term):
                raise KernelError(f"{name}: unsolved metavariable in the body")
        info = FunInfo(name, type_term, arity, None)
        self.sig.functions[name] = info
        try:
            machine = self.machine(Context())
            ctx = Context()
            goal = machine.force(machine.evaluate(type_term, ()))
            for _ in range(arity):
                if not isinstance(goal, VPi):
                    raise KernelError(f"{name}: more arguments than its type has")
                ctx = ctx.extend(goal.name, goal.domain)
                goal = machine.force(machine.apply_closure(goal.closure, var(ctx.size - 1)))
            sites = []
            self._check_tree(name, ctx, tree, goal, sites)
            self._check_termination(name, arity, sites)
        except Exception:
            del self.sig.functions[name]
            raise
        info.tree = tree

    def _check_tree(self, name, ctx, tree, goal, sites):
        if isinstance(tree, Leaf):
            if self._escapes(tree.body, ctx.size):
                raise KernelError(f"{name}: body refers outside its context")
            actual = self.infer(ctx, tree.body)
            if not self.machine(ctx).conv(actual, goal, ctx.size):
                raise KernelError(
                    f"{name}: {show(tree.body, ctx.names)} has type "
                    f"{self.show_value(ctx, actual)} but {self.show_value(ctx, goal)} "
                    f"is required"
                )
            sites.append((ctx, tree.body))
            return
        if isinstance(tree, Split):
            if not 0 <= tree.level < ctx.size:
                raise KernelError(f"{name}: split on an unknown variable")
            scrutinee_type = self.machine(ctx).force(ctx.types[tree.level])
            if not isinstance(scrutinee_type, VData):
                raise KernelError(
                    f"{name}: cannot split on {ctx.names[tree.level]} of type "
                    f"{self.show_value(ctx, scrutinee_type)}"
                )
            data = self.sig.datas[scrutinee_type.name]
            given = [cname for cname, _ in tree.branches]
            if len(set(given)) != len(given):
                raise KernelError(f"{name}: duplicate branch")
            for cname in given:
                if cname not in data.constructors:
                    raise KernelError(f"{name}: {cname} is not a constructor of {data.name}")
            for cname in data.constructors:
                status, refined, _ = self.split_branch(ctx, tree.level, cname)
                subtree = tree.branch(cname)
                if status == STUCK:
                    raise KernelError(
                        f"{name}: cannot decide whether {ctx.names[tree.level]} "
                        f"can be {cname}"
                    )
                if status == CLASH:
                    if subtree is not None:
                        raise KernelError(f"{name}: case {cname} is impossible here")
                    continue
                if subtree is None:
                    raise KernelError(
                        f"{name}: missing case {cname} for {ctx.names[tree.level]}"
                    )
                self._check_tree(name, refined, subtree, goal, sites)
            return
        if isinstance(tree, With):
            if self._escapes(tree.scrutinee, ctx.size):
                raise KernelError(f"{name}: scrutinee refers outside its context")
            scrutinee_type = self.infer(ctx, tree.scrutinee)
            sites.append((ctx, tree.scrutinee))
            inner = ctx.extend("_with", scrutinee_type)
            if self._escapes(tree.motive, inner.size):
                raise KernelError(f"{name}: motive refers outside its context")
            self.sort_of(inner, tree.motive)
            sites.append((inner, tree.motive))
            machine = self.machine(ctx)
            scrutinee = machine.evaluate(tree.scrutinee, ctx.env())
            instance = machine.evaluate(tree.motive, ctx.env() + (scrutinee,))
            if not machine.conv(instance, goal, ctx.size):
                raise KernelError(
                    f"{name}: abstracted goal {self.show_value(ctx, instance)} "
                    f"is not the goal {self.show_value(ctx, goal)}"
                )
            new_goal = self.machine(inner).evaluate(tree.motive, inner.env())
            self._check_tree(name, inner, tree.body, new_goal, sites)
            return
        raise KernelError(f"{name}: malformed case tree")

    # -- splitting and index unification -------------------------------------

    def split_branch(self, ctx, level, cname):
        """Refine ``ctx`` for the case that variable ``level`` is a ``cname``.

        Appends the constructor's arguments as new variables and unifies its
        result indices with the indices of the variable's type.  Returns
        ``(status, refined context, new levels)``; the refined context is
        meaningful only when the status is ``OK``.
        """
        machine = self.machine(ctx)
        scrutinee = machine.force(var(level))
        if not (
            isinstance(scrutinee, VNeutral)
            and isinstance(scrutinee.head, HVar)
            and scrutinee.head.level == level
            and not scrutinee.spine
        ):
            raise KernelError(f"{ctx.names[level]} is already determined; cannot split on it")
        scrutinee_type = machine.force(ctx.types[level])
        if not isinstance(scrutinee_type, VData):
            raise KernelError(f"{ctx.names[level]} does not have a data type")
        data = self.sig.datas[scrutinee_type.name]
        if len(scrutinee_type.args) != data.arity:
            raise KernelError(f"{data.name} is not fully applied")
        con = self.sig.constructors.get(cname)
        if con is None or con.data != data.name:
            raise KernelError(f"{cname} is not a constructor of {data.name}")
        params = scrutinee_type.args[: data.nparams]
        indices = scrutinee_type.args[data.nparams :]
        telescope = machine.evaluate(con.type, ())
        for param in params:
            telescope = machine.apply_closure(machine.force(telescope).closure, param)
        current = ctx
        new_levels = []
        for _ in range(con.nargs):
            telescope = machine.force(telescope)
            level_here = current.size
            current = current.extend(telescope.name, telescope.domain)
            new_levels.append(level_here)
            telescope = machine.apply_closure(telescope.closure, var(level_here))
        result = machine.force(telescope)
        status, current = self._unify_all(current, result.args[data.nparams :], indices)
        if status == OK:
            built = VCon(cname, params + tuple(var(new) for new in new_levels))
            current = current.define(level, built)
        return status, current, new_levels

    def _unify_all(self, ctx, lefts, rights):
        for position in range(len(lefts)):
            status, ctx = self.unify(ctx, lefts[position], rights[position])
            if status == CLASH:
                return CLASH, ctx
            if status == STUCK:
                machine = self.machine(ctx)
                for later in range(position + 1, len(lefts)):
                    if self._conflict(machine, lefts[later], rights[later]):
                        return CLASH, ctx
                return STUCK, ctx
        return OK, ctx

    def unify(self, ctx, left, right):
        """First-order unification of two values of one type."""
        machine = self.machine(ctx)
        left = machine.force(left)
        right = machine.force(right)
        if machine.conv(left, right, ctx.size):
            return OK, ctx
        level = self._flexible(ctx, left)
        if level is not None:
            return self._solve(ctx, level, right)
        level = self._flexible(ctx, right)
        if level is not None:
            return self._solve(ctx, level, left)
        if self._saturated(left) and self._saturated(right):
            if left.name != right.name:
                return CLASH, ctx
            return self._unify_all(ctx, left.args, right.args)
        return STUCK, ctx

    def _saturated(self, value):
        return isinstance(value, VCon) and len(value.args) == self.sig.constructors[value.name].arity

    def _conflict(self, machine, left, right):
        """Whether two values certainly differ by a constructor mismatch."""
        left = machine.force(left)
        right = machine.force(right)
        if not (self._saturated(left) and self._saturated(right)):
            return False
        if left.name != right.name:
            return True
        nparams = self.sig.constructors[left.name].nparams
        for position in range(nparams, len(left.args)):
            if self._conflict(machine, left.args[position], right.args[position]):
                return True
        return False

    @staticmethod
    def _flexible(ctx, value):
        if (
            isinstance(value, VNeutral)
            and isinstance(value.head, HVar)
            and not value.spine
            and value.head.level < ctx.size
            and value.head.level not in ctx.defs
        ):
            return value.head.level
        return None

    def _solve(self, ctx, level, value):
        occurrence = self._occurs(ctx, level, value, True)
        if occurrence == "rigid":
            return CLASH, ctx
        if occurrence == "flexible":
            return STUCK, ctx
        machine = self.machine(ctx)
        if not machine.conv(self.type_of_value(ctx, value), ctx.types[level], ctx.size):
            raise KernelError(f"ill-typed solution for {ctx.names[level]}")
        return OK, ctx.define(level, value)

    def _occurs(self, ctx, level, value, rigid):
        """How ``level`` occurs in ``value``: ``no``, ``rigid`` or ``flexible``.

        An occurrence is rigid when only constructor applications sit above
        it; an equation ``x = c (... x ...)`` then has no solution.
        """
        machine = self.machine(ctx)
        value = machine.force(value)
        if (
            isinstance(value, VNeutral)
            and isinstance(value.head, HVar)
            and value.head.level == level
            and not value.spine
        ):
            return "rigid" if rigid else "flexible"
        if self._saturated(value):
            nparams = self.sig.constructors[value.name].nparams
            found = "no"
            for position in range(len(value.args)):
                inner = self._occurs(ctx, level, value.args[position], rigid and position >= nparams)
                if inner == "flexible":
                    return "flexible"
                if inner == "rigid":
                    found = "rigid"
            return found
        if level in free_levels(machine.quote(value, ctx.size), ctx.size):
            return "flexible"
        return "no"

    # -- termination ---------------------------------------------------------

    def _check_termination(self, name, arity, sites):
        calls = []
        for ctx, term in sites:
            self._collect_calls(name, arity, ctx, term, ctx.env(), ctx.size, calls)
        if not calls:
            return
        if arity == 0:
            raise KernelError(f"{name}: a constant cannot refer to itself")
        relations = [self._call_relation(arity, call) for call in calls]
        remaining = list(range(len(relations)))
        unused = set(range(arity))
        while remaining:
            chosen = None
            for position in sorted(unused):
                column = [relations[index][position] for index in remaining]
                if "?" not in column and "<" in column:
                    chosen = position
                    break
            if chosen is None:
                raise KernelError(
                    f"{name}: recursion is not structurally decreasing "
                    f"in any lexicographic order of its arguments"
                )
            unused.discard(chosen)
            remaining = [index for index in remaining if relations[index][chosen] != "<"]

    def _collect_calls(self, name, arity, ctx, term, env, depth, calls):
        head, args = unapply(term)
        if isinstance(head, Const) and head.name == name:
            if len(args) < arity:
                raise KernelError(
                    f"{name}: must be applied to all {arity} arguments in its own definition"
                )
            machine = self.machine(ctx)
            values = tuple(machine.evaluate(arg, env) for arg in args[:arity])
            calls.append((ctx, depth, values))
        elif isinstance(head, Lam):
            self._collect_calls(name, arity, ctx, head.domain, env, depth, calls)
            self._collect_calls(name, arity, ctx, head.body, env + (var(depth),), depth + 1, calls)
        elif isinstance(head, Pi):
            self._collect_calls(name, arity, ctx, head.domain, env, depth, calls)
            self._collect_calls(
                name, arity, ctx, head.codomain, env + (var(depth),), depth + 1, calls
            )
        for arg in args:
            self._collect_calls(name, arity, ctx, arg, env, depth, calls)

    def _call_relation(self, arity, call):
        ctx, depth, values = call
        machine = self.machine(ctx)
        relation = []
        for position in range(arity):
            parameter = var(position)
            if machine.conv(values[position], parameter, depth):
                relation.append("=")
            elif self._smaller(machine, values[position], parameter, depth):
                relation.append("<")
            else:
                relation.append("?")
        return relation

    def _smaller(self, machine, candidate, pattern, depth):
        """Whether ``candidate`` is a strict structural part of ``pattern``."""
        pattern = machine.force(pattern)
        if not self._saturated(pattern):
            return False
        nparams = self.sig.constructors[pattern.name].nparams
        for position in range(nparams, len(pattern.args)):
            part = pattern.args[position]
            if machine.conv(candidate, part, depth):
                return True
            if self._applied(machine, candidate, part):
                return True
            if self._smaller(machine, candidate, part, depth):
                return True
        return False

    @staticmethod
    def _applied(machine, candidate, part):
        """Whether ``candidate`` applies the function-valued variable ``part``."""
        part = machine.force(part)
        candidate = machine.force(candidate)
        return (
            isinstance(part, VNeutral)
            and isinstance(part.head, HVar)
            and not part.spine
            and isinstance(candidate, VNeutral)
            and candidate.head == part.head
            and len(candidate.spine) > 0
        )
