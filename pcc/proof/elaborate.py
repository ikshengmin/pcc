"""Elaboration: from the Python surface to core declarations.

The elaborator is not trusted.  It infers implicit arguments by unification,
compiles ``match`` statements to case trees, resolves operators to prelude
functions and generalizes goals over matched expressions.  Whatever it builds
is handed to the kernel, which checks it again with none of this machinery.

Agreement with Python's own reading of the same source is enforced here:

* a call to a declared function or constructor passes exactly its parameters;
* a function value is always unary: lambdas take one parameter, a call on a
  local function passes one argument, and only a one-parameter function or
  constructor can be passed as a value.  The logic curries, and with this
  discipline so does the program;
* ``n - k`` is accepted only where ``n`` is known to be at least ``k``, so that
  Python's subtraction and truncated subtraction agree;
* every ``match`` is exhaustive, so no path falls through to ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pcc.proof.kernel import CLASH, STUCK, Context, KernelError
from pcc.proof.machine import Machine, MachineError
from pcc.proof.pretty import show
from pcc.proof.surface import (
    PAs,
    PBool,
    PCon,
    PNum,
    PVar,
    PWild,
    SAlias,
    SBin,
    SBool,
    SCall,
    SCase,
    SCon,
    SConst,
    SData,
    SFun,
    SIf,
    SIfStmt,
    SImport,
    SIndex,
    SLambda,
    SMatch,
    SName,
    SNot,
    SNum,
    SReturn,
)
from pcc.proof.terms import (
    App,
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
    contains_meta,
    free_levels,
    var,
)

#: Largest literal written as a unary natural.
MAX_NUMERAL = 4096

#: Prelude functions that give operators their meaning.
OPERATOR_FUNCTIONS = {
    "add": "+",
    "mul": "*",
    "lt": "<",
    "le": "<=",
    "nat_eq": "==",
    "band": "and",
    "bor": "or",
    "bnot": "not",
}


class ElabError(Exception):
    def __init__(self, message, line=0):
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass(frozen=True)
class Global:
    """What a module-level name means to the elaborator."""

    kind: str  # data, constructor, function, alias, sort or special
    name: str = ""  # kernel name
    implicit: tuple = ()  # data: which telescope entries are implicit
    positions: tuple = ()  # constructor: kernel position of each type parameter
    leading: int = 0  # constructor and function: implicit kernel arguments
    explicit: int = 0  # explicit parameters or fields
    callable: bool = True  # function: declared with def, not as a constant
    level: int = 0  # sort
    special: str = ""


BUILTIN_SCOPE = {
    "Type": Global("sort", level=0),
    "Type1": Global("sort", level=1),
    "Nat": Global("data", "Nat"),
    "Bool": Global("data", "Bool"),
    "suc": Global("constructor", "suc", explicit=1),
    "Data": Global("special", special="Data"),
    "Pi": Global("special", special="Pi"),
    "Fn": Global("special", special="Fn"),
    "Implicit": Global("special", special="Implicit"),
    "impossible": Global("special", special="impossible"),
}


class MetaStore:
    def __init__(self):
        self.solutions = []
        self.scopes = []
        self.lines = []

    def fresh(self, scope_size, line):
        self.solutions.append(None)
        self.scopes.append(scope_size)
        self.lines.append(line)
        return len(self.solutions) - 1

    def solution(self, ident):
        return self.solutions[ident]


class Local:
    """A context together with the Python names that are in scope."""

    __slots__ = ("ctx", "scope")

    def __init__(self, ctx=None, scope=None):
        self.ctx = ctx if ctx is not None else Context()
        self.scope = scope if scope is not None else {}

    @property
    def env(self):
        return self.ctx.env()

    @property
    def depth(self):
        return self.ctx.size

    def push(self, name, type_value, visible=True):
        level = self.ctx.size
        scope = self.scope
        if visible:
            scope = dict(scope)
            scope[name] = (var(level), type_value)
        return Local(self.ctx.extend(name, type_value), scope)

    def with_ctx(self, ctx):
        return Local(ctx, self.scope)

    def bind(self, bindings):
        if not bindings:
            return self
        scope = dict(self.scope)
        for name, value in bindings.items():
            scope[name] = (value, None)
        return Local(self.ctx, scope)


@dataclass
class _Row:
    patterns: list
    body: tuple
    bindings: dict
    line: int


class Elaborator:
    def __init__(self, session, module, path):
        self.session = session
        self.kernel = session.kernel
        self.sig = session.kernel.sig
        self.module = module
        self.path = path
        self.scope = {}
        self.metas = MetaStore()
        self.pending = []
        self.checked = []

    # -- helpers -------------------------------------------------------------

    def fail(self, line, message):
        raise ElabError(message, line)

    def machine(self, local):
        return Machine(self.sig, local.ctx.defs, self.metas)

    def show_value(self, local, value):
        return show(self.machine(local).quote(value, local.depth), local.ctx.names)

    def const_type(self, name):
        return Machine(self.sig).evaluate(self.sig.type_of(name), ())

    def qualified(self, name):
        return f"{self.module}.{name}"

    def lookup(self, node):
        entry = self.scope.get(node.name)
        if entry is None:
            self.fail(node.line, f"{node.name} is not defined")
        return entry

    def reserve(self, name, line):
        if name in self.scope:
            self.fail(line, f"{name} is already defined in this module")

    def define(self, name, entry, line):
        self.reserve(name, line)
        self.scope[name] = entry

    # -- module --------------------------------------------------------------

    def run(self, module):
        declarations = module.declarations
        position = 0
        while position < len(declarations):
            declaration = declarations[position]
            position += 1
            if isinstance(declaration, SImport):
                self.import_names(declaration)
            elif isinstance(declaration, SData):
                constructors = []
                while (
                    position < len(declarations)
                    and isinstance(declarations[position], SCon)
                    and self._family_of(declarations[position]) == declaration.name
                ):
                    constructors.append(declarations[position])
                    position += 1
                self.guarded(declaration.line, self.data, declaration, constructors)
            elif isinstance(declaration, SCon):
                self.fail(
                    declaration.line,
                    f"constructor {declaration.name} must directly follow its family",
                )
            elif isinstance(declaration, SFun):
                self.guarded(declaration.line, self.function, declaration)
            elif isinstance(declaration, SConst):
                self.guarded(declaration.line, self.constant, declaration)
            elif isinstance(declaration, SAlias):
                self.guarded(declaration.line, self.alias, declaration)
            else:
                self.fail(getattr(declaration, "line", 0), "unknown declaration")

    def guarded(self, line, action, *args):
        try:
            action(*args)
        except ElabError as error:
            self.kernel.abort()
            if not error.line:
                error.line = line
            raise
        except (KernelError, MachineError) as error:
            self.kernel.abort()
            raise ElabError(str(error), line)
        except RecursionError:
            self.kernel.abort()
            raise ElabError("the checker ran out of stack on this declaration", line)

    @staticmethod
    def _family_of(constructor):
        base = constructor.base
        if isinstance(base, SIndex):
            base = base.base
        return base.name if isinstance(base, SName) else None

    def import_names(self, declaration):
        exported = self.session.load(declaration.module, self.path, declaration.line)
        for name, local_name in declaration.names:
            if name not in exported:
                self.fail(declaration.line, f"{declaration.module} does not define {name}")
            self.define(local_name, exported[name], declaration.line)

    # -- metavariables -------------------------------------------------------

    def begin_expression(self):
        self.metas = MetaStore()
        self.pending = []

    def fresh_meta(self, local, line):
        return self.metas.fresh(local.depth, line)

    def unify(self, machine, left, right, depth, solve=True):
        left = machine.force(left)
        right = machine.force(right)
        if left is right:
            return True
        if solve:
            left_meta = self._flexible(left)
            right_meta = self._flexible(right)
            if left_meta is not None and left_meta == right_meta:
                return True
            if left_meta is not None:
                return self._assign(machine, left_meta, right, depth)
            if right_meta is not None:
                return self._assign(machine, right_meta, left, depth)
        if isinstance(left, VLam) or isinstance(right, VLam):
            if not (machine._applicable(left) and machine._applicable(right)):
                return False
            fresh = var(depth)
            return self.unify(
                machine, machine.apply(left, fresh), machine.apply(right, fresh), depth + 1, solve
            )
        if isinstance(left, VSort):
            return isinstance(right, VSort) and left.level == right.level
        if isinstance(left, VPi):
            if not isinstance(right, VPi):
                return False
            if not self.unify(machine, left.domain, right.domain, depth, solve):
                return False
            fresh = var(depth)
            return self.unify(
                machine,
                machine.apply_closure(left.closure, fresh),
                machine.apply_closure(right.closure, fresh),
                depth + 1,
                solve,
            )
        if isinstance(left, VData):
            if not (isinstance(right, VData) and left.name == right.name):
                return False
            return self._unify_all(machine, left.args, right.args, depth, solve)
        if isinstance(left, VCon):
            if not (isinstance(right, VCon) and left.name == right.name):
                return False
            return self._unify_all(machine, left.args, right.args, depth, solve)
        if isinstance(left, VNeutral):
            if not (isinstance(right, VNeutral) and left.head == right.head):
                return False
            if isinstance(left.head, HVar):
                return self._unify_all(machine, left.spine, right.spine, depth, solve)
            # A stuck call may become equal later without its arguments being
            # equal, so comparing them must not commit to a solution.
            return self._unify_all(machine, left.spine, right.spine, depth, False)
        return False

    def _unify_all(self, machine, lefts, rights, depth, solve):
        if len(lefts) != len(rights):
            return False
        for index in range(len(lefts)):
            if not self.unify(machine, lefts[index], rights[index], depth, solve):
                return False
        return True

    @staticmethod
    def _flexible(value):
        if isinstance(value, VNeutral) and isinstance(value.head, HMeta) and not value.spine:
            return value.head.ident
        return None

    def _assign(self, machine, ident, value, depth):
        term = machine.quote(value, depth)
        stack = [term]
        while stack:
            node = stack.pop()
            if isinstance(node, Meta):
                if node.ident == ident:
                    return False
            elif isinstance(node, App):
                stack.append(node.fn)
                stack.append(node.arg)
            elif isinstance(node, (Lam, Pi)):
                stack.append(node.domain)
                stack.append(node.body if isinstance(node, Lam) else node.codomain)
        scope = self.metas.scopes[ident]
        for level in free_levels(term, depth):
            if level >= scope:
                return False
        self.metas.solutions[ident] = value
        return True

    def has_metas(self, machine, value, depth):
        return contains_meta(machine.quote(value, depth))

    def constrain(self, local, actual, expected, line, what="this expression"):
        machine = self.machine(local)
        if self.unify(machine, actual, expected, local.depth):
            return
        if not self._rigid_clash(machine, actual, expected) and (
            self.has_metas(machine, actual, local.depth)
            or self.has_metas(machine, expected, local.depth)
        ):
            self.pending.append((local, actual, expected, line, what))
            return
        self.mismatch(local, actual, expected, line, what)

    @staticmethod
    def _rigid_clash(machine, left, right):
        """Whether two types differ at their heads, whatever metas stand for."""
        left = machine.force(left)
        right = machine.force(right)
        rigid = (VData, VPi, VSort)
        if not (isinstance(left, rigid) and isinstance(right, rigid)):
            return False
        if type(left) is not type(right):
            return True
        if isinstance(left, VData):
            return left.name != right.name
        if isinstance(left, VSort):
            return left.level != right.level
        return False

    def mismatch(self, local, actual, expected, line, what):
        self.fail(
            line,
            f"{what} has type {self.show_value(local, actual)} "
            f"but {self.show_value(local, expected)} is required",
        )

    def finish_expression(self, line):
        progress = True
        while self.pending and progress:
            progress = False
            remaining = []
            for item in self.pending:
                local, actual, expected, item_line, what = item
                machine = self.machine(local)
                if self.unify(machine, actual, expected, local.depth):
                    progress = True
                elif not (
                    self.has_metas(machine, actual, local.depth)
                    or self.has_metas(machine, expected, local.depth)
                ):
                    self.mismatch(local, actual, expected, item_line, what)
                else:
                    remaining.append(item)
            self.pending = remaining
        if self.pending:
            local, actual, expected, item_line, what = self.pending[0]
            self.fail(
                item_line,
                f"cannot decide whether {self.show_value(local, actual)} is "
                f"{self.show_value(local, expected)}; an implicit argument is not determined",
            )

    def zonk(self, local, term, depth=None):
        if depth is None:
            depth = local.depth
        machine = self.machine(local)
        if isinstance(term, Meta):
            solution = self.metas.solution(term.ident)
            if solution is None:
                self.fail(
                    self.metas.lines[term.ident],
                    "cannot infer an implicit argument here; make it explicit",
                )
            result = machine.quote(solution, depth)
            if contains_meta(result):
                self.fail(
                    self.metas.lines[term.ident],
                    "cannot infer an implicit argument here; make it explicit",
                )
            return result
        if isinstance(term, App):
            return App(self.zonk(local, term.fn, depth), self.zonk(local, term.arg, depth))
        if isinstance(term, Lam):
            return Lam(
                term.name,
                self.zonk(local, term.domain, depth),
                self.zonk(local, term.body, depth + 1),
            )
        if isinstance(term, Pi):
            return Pi(
                term.name,
                self.zonk(local, term.domain, depth),
                self.zonk(local, term.codomain, depth + 1),
            )
        return term

    # -- whole expressions ---------------------------------------------------

    def leaf(self, local, expression, goal):
        """Elaborate ``expression`` against ``goal`` into a closed-up term."""
        self.begin_expression()
        term = self.check(local, expression, goal)
        self.finish_expression(expression.line)
        return self.zonk(local, term)

    def standalone(self, local, expression):
        """Elaborate ``expression`` and return its term and settled type."""
        self.begin_expression()
        term, type_value = self.infer(local, expression)
        self.finish_expression(expression.line)
        term = self.zonk(local, term)
        return term, self.settle(local, type_value, expression.line)

    def type_leaf(self, local, expression):
        """Elaborate a type; returns its term and universe level."""
        self.begin_expression()
        term, level = self.type_expression(local, expression)
        self.finish_expression(expression.line)
        return self.zonk(local, term), level

    def settle(self, local, value, line):
        """A value with every metavariable replaced by its solution."""
        term = self.machine(local).quote(value, local.depth)
        if contains_meta(term):
            self.fail(line, "cannot infer the type of this expression; annotate it")
        return self.kernel.machine(local.ctx).evaluate(term, local.env)

    # -- expressions ---------------------------------------------------------

    def infer(self, local, e):
        if isinstance(e, SName):
            return self.name(local, e)
        if isinstance(e, SNum):
            return self.numeral(e), VData("Nat", ())
        if isinstance(e, SBool):
            return Const("true" if e.value else "false"), VData("Bool", ())
        if isinstance(e, (SCall, SIndex)):
            return self.application(local, e, None)
        if isinstance(e, SBin):
            return self.binary(local, e)
        if isinstance(e, SNot):
            operand = self.check(local, e.operand, VData("Bool", ()))
            return App(Const(self.operator("not", e.line)), operand), VData("Bool", ())
        if isinstance(e, SIf):
            test = self.check(local, e.test, VData("Bool", ()))
            then, type_value = self.infer(local, e.then)
            orelse = self.check(local, e.orelse, type_value)
            carrier = self.machine(local).quote(type_value, local.depth)
            return apps(Const("ite"), [carrier, test, then, orelse]), type_value
        if isinstance(e, SLambda):
            self.fail(e.line, "the type of a lambda is not known here; pass it where a function is expected")
        self.fail(getattr(e, "line", 0), "this expression is outside the checked fragment")

    def check(self, local, e, expected):
        machine = self.machine(local)
        if isinstance(e, SLambda):
            if len(e.params) != 1:
                self.fail(e.line, "a checked lambda takes exactly one parameter; nest lambdas")
            function_type = machine.force(expected)
            if not isinstance(function_type, VPi):
                self.fail(
                    e.line,
                    f"a lambda is given where {self.show_value(local, expected)} is required",
                )
            level = local.depth
            inner = local.push(e.params[0], function_type.domain)
            body = self.check(
                inner, e.body, machine.apply_closure(function_type.closure, var(level))
            )
            return Lam(e.params[0], machine.quote(function_type.domain, level), body)
        if isinstance(e, SIf):
            test = self.check(local, e.test, VData("Bool", ()))
            then = self.check(local, e.then, expected)
            orelse = self.check(local, e.orelse, expected)
            carrier = machine.quote(expected, local.depth)
            return apps(Const("ite"), [carrier, test, then, orelse])
        if isinstance(e, (SCall, SIndex)):
            term, type_value = self.application(local, e, expected)
            self.constrain(local, type_value, expected, e.line)
            return term
        term, type_value = self.infer(local, e)
        self.constrain(local, type_value, expected, e.line)
        return term

    def type_expression(self, local, e):
        term, type_value = self.infer(local, e)
        sort = self.machine(local).force(type_value)
        if not isinstance(sort, VSort):
            self.fail(e.line, f"a type is required but this has type {self.show_value(local, sort)}")
        return term, sort.level

    def numeral(self, e):
        if e.value > MAX_NUMERAL:
            self.fail(e.line, f"numerals above {MAX_NUMERAL} are not supported yet")
        term = Const("zero")
        for _ in range(e.value):
            term = App(Const("suc"), term)
        return term

    def name(self, local, e):
        entry = local.scope.get(e.name)
        if entry is not None:
            value, type_value = entry
            if type_value is None:
                type_value = self.kernel.type_of_value(local.ctx, value)
            return self.machine(local).quote(value, local.depth), type_value
        entry = self.lookup(e)
        if entry.kind == "sort":
            return Sort(entry.level), VSort(entry.level + 1)
        if entry.kind in ("data", "alias"):
            if any(entry.implicit):
                self.fail(e.line, f"{e.name} has implicit parameters; apply it with [...]")
            return Const(entry.name), self.const_type(entry.name)
        if entry.kind == "constructor":
            if entry.explicit != 1:
                hint = f"write {e.name}()" if entry.explicit == 0 else "wrap it in a lambda"
                self.fail(e.line, f"constructor {e.name} is not a value by itself; {hint}")
            return self.apply_slots(
                local, Const(entry.name), self.const_type(entry.name),
                [("meta", None)] * entry.leading, None, e.line,
            )
        if entry.kind == "function":
            if entry.callable and entry.explicit != 1:
                hint = f"write {e.name}()" if entry.explicit == 0 else "wrap it in a lambda"
                self.fail(e.line, f"function {e.name} is not a value by itself; {hint}")
            return self.apply_slots(
                local, Const(entry.name), self.const_type(entry.name),
                [("meta", None)] * entry.leading, None, e.line,
            )
        self.fail(e.line, f"{e.name} cannot be used as a value")

    # -- application ---------------------------------------------------------

    def application(self, local, e, expected):
        if isinstance(e, SCall):
            head, args, style = e.fn, e.args, "call"
        else:
            head, args, style = e.base, e.args, "index"
        given = None
        if (
            style == "call"
            and isinstance(head, SIndex)
            and isinstance(head.base, SName)
            and head.base.name not in local.scope
        ):
            entry = self.scope.get(head.base.name)
            if entry is not None and entry.kind == "constructor":
                given = head.args
                head = head.base
        if not (isinstance(head, SName) and head.name not in local.scope):
            if style != "call":
                self.fail(e.line, "only a declared type or constructor takes [...]")
            if len(args) != 1:
                self.fail(e.line, "a function value is called with exactly one argument")
            term, type_value = self.infer(local, head)
            return self.apply_explicit(local, term, type_value, args[0], e.line)
        entry = self.lookup(head)
        name = head.name
        if entry.kind == "special":
            return self.special(local, entry, e, args, style)
        if entry.kind == "sort":
            self.fail(e.line, f"{name} takes no arguments")
        if entry.kind in ("data", "alias"):
            if style != "index":
                self.fail(e.line, f"{name} is a type; apply it as {name}[...]")
            flags = entry.implicit
            if len(args) != len(flags) - sum(1 for flag in flags if flag):
                self.fail(
                    e.line,
                    f"{name} takes {len(flags) - sum(1 for flag in flags if flag)} "
                    f"arguments but {len(args)} are given",
                )
            slots = []
            remaining = list(args)
            for flag in flags:
                slots.append(("meta", None) if flag else ("arg", remaining.pop(0)))
        elif entry.kind == "constructor":
            if style != "call":
                self.fail(e.line, f"{name}[...] must be called: {name}[...](...)")
            if len(args) != entry.explicit:
                self.fail(e.line, f"{name} takes {entry.explicit} arguments but {len(args)} are given")
            slots = [("meta", None)] * entry.leading
            if given is not None:
                if len(given) != len(entry.positions):
                    self.fail(e.line, f"{name} has {len(entry.positions)} type parameters")
                for index, argument in enumerate(given):
                    slots[entry.positions[index]] = ("arg", argument)
            slots = slots + [("arg", argument) for argument in args]
        elif entry.kind == "function":
            if style != "call":
                self.fail(e.line, f"{name} is a function; call it as {name}(...)")
            if not entry.callable:
                self.fail(e.line, f"{name} is a constant, not a function")
            if len(args) != entry.explicit:
                self.fail(e.line, f"{name} takes {entry.explicit} arguments but {len(args)} are given")
            slots = [("meta", None)] * entry.leading + [("arg", argument) for argument in args]
        else:
            self.fail(e.line, f"{name} cannot be applied")
        return self.apply_slots(
            local, Const(entry.name), self.const_type(entry.name), slots, expected, e.line
        )

    def apply_explicit(self, local, term, type_value, argument, line):
        machine = self.machine(local)
        function_type = machine.force(type_value)
        if not isinstance(function_type, VPi):
            self.fail(line, f"this is called but has type {self.show_value(local, function_type)}")
        argument_term = self.check(local, argument, function_type.domain)
        result = machine.apply_closure(
            function_type.closure, machine.evaluate(argument_term, local.env)
        )
        return App(term, argument_term), result

    def apply_slots(self, local, term, type_value, slots, expected, line):
        """Apply a global to implicit (``meta``) and explicit (``arg``) slots."""
        machine = self.machine(local)
        idents = []
        preview = type_value
        for _ in slots:
            preview = machine.force(preview)
            if not isinstance(preview, VPi):
                self.fail(line, "too many arguments")
            ident = self.fresh_meta(local, line)
            idents.append(ident)
            preview = machine.apply_closure(preview.closure, VNeutral(HMeta(ident), ()))
        if expected is not None and slots:
            # Read implicit arguments off the expected type before the explicit
            # arguments are elaborated.  A failure here is reported below, by
            # the caller, against the real result type.
            self.unify(machine, preview, expected, local.depth)
        for index, (kind, argument) in enumerate(slots):
            type_value = machine.force(type_value)
            ident = idents[index]
            if kind == "meta":
                argument_term = Meta(ident)
                argument_value = VNeutral(HMeta(ident), ())
            else:
                argument_term = self.check(local, argument, type_value.domain)
                argument_value = machine.evaluate(argument_term, local.env)
                if self.metas.solution(ident) is None:
                    self._assign(machine, ident, argument_value, local.depth)
            term = App(term, argument_term)
            type_value = machine.apply_closure(type_value.closure, argument_value)
        return term, type_value

    def special(self, local, entry, e, args, style):
        machine = self.machine(local)
        if entry.special == "Fn":
            if style != "index" or len(args) < 2:
                self.fail(e.line, "a function type is written Fn[A, B]")
            domains = []
            level = 0
            current = local
            for argument in args[:-1]:
                domain, domain_level = self.type_expression(current, argument)
                domains.append(domain)
                level = max(level, domain_level)
                current = current.push(
                    "_", self.machine(current).evaluate(domain, current.env), visible=False
                )
            result, result_level = self.type_expression(current, args[-1])
            for domain in reversed(domains):
                result = Pi("_", domain, result)
            return result, VSort(max(level, result_level))
        if entry.special == "Pi":
            if (
                style != "index"
                or len(args) != 2
                or not isinstance(args[1], SLambda)
                or len(args[1].params) != 1
            ):
                self.fail(e.line, "a dependent function type is written Pi[A, lambda x: B]")
            domain, domain_level = self.type_expression(local, args[0])
            parameter = args[1].params[0]
            inner = local.push(parameter, machine.evaluate(domain, local.env))
            codomain, codomain_level = self.type_expression(inner, args[1].body)
            return Pi(parameter, domain, codomain), VSort(max(domain_level, codomain_level))
        if entry.special == "impossible":
            self.fail(e.line, "impossible(...) is written as a whole statement: return impossible(x)")
        self.fail(e.line, f"{entry.special} cannot be used in an expression")

    # -- operators -----------------------------------------------------------

    def operator(self, symbol, line):
        name = self.session.operators.get(symbol)
        if name is None:
            self.fail(line, f"the operator '{symbol}' is not available before the prelude defines it")
        return name

    def binary(self, local, e):
        nat = VData("Nat", ())
        boolean = VData("Bool", ())
        op = e.op
        if op in ("+", "*"):
            left = self.check(local, e.left, nat)
            right = self.check(local, e.right, nat)
            return apps(Const(self.operator(op, e.line)), [left, right]), nat
        if op == "-":
            if not isinstance(e.right, SNum):
                self.fail(e.line, "only n - <literal> is checked; use monus(n, m) otherwise")
            machine = self.machine(local)
            left = self.check(local, e.left, nat)
            value = machine.evaluate(left, local.env)
            for _ in range(e.right.value):
                value = machine.force(value)
                if not (isinstance(value, VCon) and value.name == "suc" and len(value.args) == 1):
                    self.fail(
                        e.line,
                        f"n - {e.right.value} is checked only where n is known to be at least "
                        f"{e.right.value}; match on it first or use monus",
                    )
                value = value.args[0]
            return machine.quote(value, local.depth), nat
        if op in ("<", "<=", ">", ">="):
            left = self.check(local, e.left, nat)
            right = self.check(local, e.right, nat)
            if op in (">", ">="):
                left, right = right, left
            symbol = "<" if op in ("<", ">") else "<="
            return apps(Const(self.operator(symbol, e.line)), [left, right]), boolean
        if op in ("==", "!="):
            left = self.check(local, e.left, nat)
            right = self.check(local, e.right, nat)
            term = apps(Const(self.operator("==", e.line)), [left, right])
            if op == "!=":
                term = App(Const(self.operator("not", e.line)), term)
            return term, boolean
        if op in ("and", "or"):
            left = self.check(local, e.left, boolean)
            right = self.check(local, e.right, boolean)
            return apps(Const(self.operator(op, e.line)), [left, right]), boolean
        self.fail(e.line, f"operator {op} is outside the checked fragment")

    # -- statements and case trees -------------------------------------------

    def body(self, local, statements, goal, line):
        if not statements:
            self.fail(line, "this path does not return a value")
        first = statements[0]
        rest = statements[1:]
        if isinstance(first, SReturn):
            if rest:
                self.fail(rest[0].line, "this statement can never run")
            if self._is_impossible(local, first.value):
                return self.impossible(local, first.value)
            return Leaf(self.leaf(local, first.value, goal))
        if isinstance(first, SMatch):
            if rest:
                self.fail(rest[0].line, "every case must return; nothing may follow a match")
            return self.match(local, first, goal)
        if isinstance(first, SIfStmt):
            if first.orelse and rest:
                self.fail(rest[0].line, "this statement can never run")
            orelse = first.orelse if first.orelse else rest
            if not orelse:
                self.fail(first.line, "the false branch of this if does not return a value")
            as_match = SMatch(
                (first.test,),
                (
                    SCase((PBool(True, first.line),), first.then, first.line),
                    SCase((PBool(False, first.line),), orelse, first.line),
                ),
                first.line,
            )
            return self.match(local, as_match, goal)
        self.fail(getattr(first, "line", line), "this statement is outside the checked fragment")

    def _is_impossible(self, local, expression):
        if not (isinstance(expression, SCall) and isinstance(expression.fn, SName)):
            return False
        if expression.fn.name in local.scope:
            return False
        entry = self.scope.get(expression.fn.name)
        return entry is not None and entry.kind == "special" and entry.special == "impossible"

    def impossible(self, local, expression):
        line = expression.line
        if len(expression.args) != 1 or not isinstance(expression.args[0], SName):
            self.fail(line, "impossible(x) takes one variable")
        witness = expression.args[0]
        entry = local.scope.get(witness.name)
        if entry is None:
            self.fail(line, f"{witness.name} is not a local variable")
        level = self._context_variable(local, entry[0])
        if level is None:
            self.fail(line, f"{witness.name} is already determined; it cannot be impossible")
        possible = self._possible_constructors(local, level)
        if possible is None:
            self.fail(line, f"{witness.name} does not have a data type")
        if possible:
            self.fail(line, f"{witness.name} is not impossible: it can be {possible[0]}")
        return Split(level, ())

    def _context_variable(self, local, value):
        value = self.kernel.machine(local.ctx).force(value)
        if (
            isinstance(value, VNeutral)
            and isinstance(value.head, HVar)
            and not value.spine
            and value.head.level < local.depth
        ):
            return value.head.level
        return None

    def _possible_constructors(self, local, level):
        """Constructors variable ``level`` may be; ``None`` if it has no data type."""
        machine = self.kernel.machine(local.ctx)
        type_value = machine.force(local.ctx.types[level])
        if not isinstance(type_value, VData):
            return None
        data = self.sig.datas[type_value.name]
        if len(type_value.args) != data.arity:
            return None
        possible = []
        for cname in data.constructors:
            status, _, _ = self.kernel.split_branch(local.ctx, level, cname)
            if status != CLASH:
                possible.append(self._short(cname))
        return possible

    @staticmethod
    def _short(name):
        return name.rsplit(".", 1)[-1]

    def match(self, local, node, goal):
        columns = []
        wrappers = []
        for subject in node.subjects:
            value = None
            if isinstance(subject, SName) and subject.name in local.scope:
                candidate = local.scope[subject.name][0]
                forced = self.kernel.machine(local.ctx).force(candidate)
                if self._context_variable(local, forced) is not None or self._saturated(forced):
                    value = candidate
            if value is None:
                local, goal, columns, wrapper = self.generalize(local, subject, goal, columns)
                wrappers.append(wrapper)
                value = var(wrapper[3])
            columns.append(value)
        rows = [
            _Row([self.pattern(pattern) for pattern in case.patterns], case.body, {}, case.line)
            for case in node.cases
        ]
        tree = self.compile(local, columns, rows, goal, node.line)
        for term, motive, rebinds, _ in reversed(wrappers):
            tree = With(term, motive, tree, rebinds)
        return tree

    def generalize(self, local, subject, goal, columns):
        """Abstract the goal and the context over the expression ``subject``.

        A new variable stands for the expression.  Every undetermined context
        variable whose type mentions the expression, or mentions a variable
        already copied, gets a copy with the generalized type; names in scope
        then refer to the copies.
        """
        term, type_value = self.standalone(local, subject)
        machine = self.kernel.machine(local.ctx)
        target = machine.evaluate(term, local.env)
        with_level = local.depth
        inner = local.ctx.extend("_with", type_value)
        renaming = {}
        rebinds = []
        for level in range(local.depth):
            if self._context_variable(local, var(level)) is None:
                continue
            type_term, changed = self.abstract(
                local, local.ctx.types[level], target, with_level, renaming, inner.size
            )
            if not changed:
                continue
            rebinds.append((level, type_term))
            renaming[level] = inner.size
            copy_type = self.kernel.machine(inner).evaluate(type_term, inner.env())
            inner = inner.extend(local.ctx.names[level], copy_type)
        motive, _ = self.abstract(local, goal, target, with_level, renaming, inner.size)
        new_goal = self.kernel.machine(inner).evaluate(motive, inner.env())
        scope = {
            name: (self.kernel.rename(inner, value, renaming), None)
            for name, (value, _) in local.scope.items()
        }
        new_columns = [self.kernel.rename(inner, column, renaming) for column in columns]
        wrapper = (term, motive, tuple(rebinds), with_level)
        return Local(inner, scope), new_goal, new_columns, wrapper

    def _saturated(self, value):
        return isinstance(value, VCon) and len(value.args) == self.sig.constructors[value.name].arity

    def abstract(self, local, value, target, with_level, renaming, depth):
        """``value`` as a term at ``depth`` over the generalized context.

        Parts convertible to ``target`` become the variable at ``with_level``
        and each level in ``renaming`` becomes its copy.  Returns the term and
        whether anything was replaced.
        """
        machine = self.kernel.machine(local.ctx)
        changed = [False]

        def go(value, depth):
            value = machine.force(value)
            if machine.conv(value, target, depth):
                changed[0] = True
                return Var(depth - 1 - with_level)
            if isinstance(value, VNeutral):
                head = value.head
                if isinstance(head, HVar):
                    image = renaming.get(head.level)
                    if image is None:
                        image = head.level
                    else:
                        changed[0] = True
                    term = Var(depth - 1 - image)
                else:
                    term = Const(head.name)
                return apps(term, [go(arg, depth) for arg in value.spine])
            if isinstance(value, (VCon, VData)):
                return apps(Const(value.name), [go(arg, depth) for arg in value.args])
            if isinstance(value, VSort):
                return Sort(value.level)
            if isinstance(value, VPi):
                inner = machine.apply_closure(value.closure, var(depth))
                return Pi(value.name, go(value.domain, depth), go(inner, depth + 1))
            if isinstance(value, VLam):
                inner = machine.apply_closure(value.closure, var(depth))
                return Lam(value.name, go(value.domain, depth), go(inner, depth + 1))
            raise ElabError("cannot abstract over this goal")

        return go(value, depth), changed[0]

    def pattern(self, pattern):
        """Resolve a surface pattern to ``wild``, ``var``, ``con`` or ``as``."""
        if isinstance(pattern, PWild):
            return ("wild",)
        if isinstance(pattern, PVar):
            return ("var", pattern.name)
        if isinstance(pattern, PAs):
            return ("as", self.pattern(pattern.pattern), pattern.name)
        if isinstance(pattern, PBool):
            return ("con", "true" if pattern.value else "false", [], pattern.line)
        if isinstance(pattern, PNum):
            if pattern.value > 64:
                self.fail(pattern.line, "numeral patterns above 64 are not supported")
            resolved = ("con", "zero", [], pattern.line)
            for _ in range(pattern.value):
                resolved = ("con", "suc", [resolved], pattern.line)
            return resolved
        if isinstance(pattern, PCon):
            entry = self.scope.get(pattern.name)
            if entry is None:
                self.fail(pattern.line, f"{pattern.name} is not defined")
            if entry.kind != "constructor" or entry.name == "suc":
                hint = "match naturals with case 0 and case _" if entry.name == "suc" else ""
                self.fail(pattern.line, f"{pattern.name} is not a constructor class. {hint}".strip())
            if len(pattern.patterns) != entry.explicit:
                self.fail(
                    pattern.line,
                    f"{pattern.name} has {entry.explicit} fields but the pattern gives "
                    f"{len(pattern.patterns)}",
                )
            return ("con", entry.name, [self.pattern(sub) for sub in pattern.patterns], pattern.line)
        self.fail(getattr(pattern, "line", 0), "this pattern is outside the checked fragment")

    @staticmethod
    def _strip(pattern, column, bindings):
        """Peel ``as`` bindings off a pattern, recording them."""
        while pattern[0] == "as":
            bindings = dict(bindings)
            bindings[pattern[2]] = column
            pattern = pattern[1]
        return pattern, bindings

    def _refutable(self, pattern):
        while pattern[0] == "as":
            pattern = pattern[1]
        return pattern[0] == "con"

    def compile(self, local, columns, rows, goal, line):
        if not rows:
            return self.absurd(local, line, None)
        row = rows[0]
        pick = None
        for index, pattern in enumerate(row.patterns):
            if self._refutable(pattern):
                pick = index
                break
        if pick is None:
            bindings = dict(row.bindings)
            for index, pattern in enumerate(row.patterns):
                pattern, bindings = self._strip(pattern, columns[index], bindings)
                if pattern[0] == "var":
                    bindings[pattern[1]] = columns[index]
            return self.body(local.bind(bindings), row.body, goal, row.line)
        column = columns[pick]
        machine = self.kernel.machine(local.ctx)
        value = machine.force(column)
        if self._saturated(value):
            fields = self.session.fields[value.name]
            data = self.sig.constructors[value.name].data
            self._check_column(rows, pick, self.sig.datas[data], line)
            specialized = self._specialize(rows, pick, value.name, fields, column)
            if not specialized:
                return self.absurd(local, line, value.name)
            field_values = list(value.args[len(value.args) - fields :])
            return self.compile(
                local,
                columns[:pick] + field_values + columns[pick + 1 :],
                specialized,
                goal,
                line,
            )
        level = self._context_variable(local, value)
        if level is None:
            self.fail(line, f"cannot match on {self.show_value(local, value)}: it is not a variable")
        type_value = machine.force(local.ctx.types[level])
        if not isinstance(type_value, VData):
            self.fail(
                line,
                f"cannot match on {local.ctx.names[level]}: its type "
                f"{self.show_value(local, type_value)} is not an inductive family",
            )
        data = self.sig.datas[type_value.name]
        self._check_column(rows, pick, data, line)
        named = set()
        for candidate in rows:
            pattern, _ = self._strip(candidate.patterns[pick], column, candidate.bindings)
            if pattern[0] == "con":
                named.add(pattern[1])
        branches = []
        for cname in data.constructors:
            status, refined, new_levels = self.kernel.split_branch(local.ctx, level, cname)
            if status == CLASH:
                if cname in named:
                    self.fail(line, f"case {self._short(cname)} is impossible here; remove it")
                continue
            if status == STUCK:
                self.fail(
                    line,
                    f"cannot decide whether {local.ctx.names[level]} can be "
                    f"{self._short(cname)}: its indices do not unify by constructors alone",
                )
            fields = self.session.fields[cname]
            field_values = [var(new) for new in new_levels[len(new_levels) - fields :]]
            specialized = self._specialize(rows, pick, cname, fields, column)
            inner = local.with_ctx(refined)
            if specialized:
                subtree = self.compile(
                    inner,
                    columns[:pick] + field_values + columns[pick + 1 :],
                    specialized,
                    goal,
                    line,
                )
            else:
                subtree = self.absurd(inner, line, cname)
            branches.append((cname, subtree))
        return Split(level, tuple(branches))

    def _check_column(self, rows, pick, data, line):
        for row in rows:
            pattern = row.patterns[pick]
            while pattern[0] == "as":
                pattern = pattern[1]
            if pattern[0] == "con" and pattern[1] not in data.constructors:
                self.fail(
                    pattern[3] or line,
                    f"{self._short(pattern[1])} is not a constructor of {self._short(data.name)}",
                )

    def _specialize(self, rows, pick, cname, fields, column):
        result = []
        for row in rows:
            pattern, bindings = self._strip(row.patterns[pick], column, row.bindings)
            if pattern[0] == "con":
                if pattern[1] != cname:
                    continue
                subpatterns = list(pattern[2])
            else:
                if pattern[0] == "var":
                    bindings = dict(bindings)
                    bindings[pattern[1]] = column
                subpatterns = [("wild",)] * fields
            result.append(
                _Row(
                    row.patterns[:pick] + subpatterns + row.patterns[pick + 1 :],
                    row.body,
                    bindings,
                    row.line,
                )
            )
        return result

    def absurd(self, local, line, missing):
        """A missing case is fine when some variable's type has become empty."""
        for level in range(local.depth - 1, -1, -1):
            if level in local.ctx.defs:
                continue
            if self._context_variable(local, var(level)) is None:
                continue
            try:
                possible = self._possible_constructors(local, level)
            except KernelError:
                continue
            if possible is not None and not possible:
                return Split(level, ())
        if missing is None:
            self.fail(line, "this match does not cover every case")
        self.fail(line, f"this match does not cover the case {self._short(missing)}")

    # -- declarations --------------------------------------------------------

    def telescope(self, local, parameters):
        """Elaborate ``parameters`` in order; returns the extended local and binders."""
        binders = []
        for parameter in parameters:
            term, _ = self.type_leaf(local, parameter.type)
            value = self.kernel.machine(local.ctx).evaluate(term, local.env)
            local = local.push(parameter.name, value)
            binders.append((parameter.name, term))
        return local, binders

    @staticmethod
    def _pis(binders, result):
        for name, domain in reversed(binders):
            result = Pi(name, domain, result)
        return result

    def function(self, declaration):
        self.reserve(declaration.name, declaration.line)
        local, binders = self.telescope(Local(), declaration.tparams + declaration.params)
        result, _ = self.type_leaf(local, declaration.returns)
        name = self.qualified(declaration.name)
        self.kernel.begin_function(name, self._pis(binders, result), len(binders))
        entry = Global(
            "function",
            name,
            leading=len(declaration.tparams),
            explicit=len(declaration.params),
            callable=True,
        )
        self.define(declaration.name, entry, declaration.line)
        try:
            goal = self.kernel.machine(local.ctx).evaluate(result, local.env)
            tree = self.body(local, declaration.body, goal, declaration.line)
            self.kernel.finish_function(name, tree)
        except Exception:
            del self.scope[declaration.name]
            raise
        self.declared(declaration.name, entry)

    def constant(self, declaration):
        self.reserve(declaration.name, declaration.line)
        local = Local()
        type_term, _ = self.type_leaf(local, declaration.type)
        goal = self.kernel.machine(local.ctx).evaluate(type_term, ())
        body = self.leaf(local, declaration.value, goal)
        name = self.qualified(declaration.name)
        self.kernel.add_function(name, type_term, 0, Leaf(body))
        entry = Global("function", name, callable=False)
        self.define(declaration.name, entry, declaration.line)
        self.declared(declaration.name, entry)

    def alias(self, declaration):
        self.reserve(declaration.name, declaration.line)
        local, binders = self.telescope(Local(), declaration.tparams)
        self.begin_expression()
        body, level = self.type_expression(local, declaration.value)
        self.finish_expression(declaration.line)
        body = self.zonk(local, body)
        name = self.qualified(declaration.name)
        self.kernel.add_function(name, self._pis(binders, Sort(level)), len(binders), Leaf(body))
        entry = Global("alias", name, implicit=(False,) * len(binders))
        self.define(declaration.name, entry, declaration.line)
        self.declared(declaration.name, entry)

    def declared(self, name, entry):
        self.checked.append(name)
        if self.module == self.session.prelude_module and name in OPERATOR_FUNCTIONS:
            self.session.operators[OPERATOR_FUNCTIONS[name]] = entry.name

    def data(self, declaration, constructors):
        self.reserve(declaration.name, declaration.line)
        for constructor in constructors:
            self.reserve(constructor.name, constructor.line)
        family_local, family_binders = self.telescope(Local(), declaration.tparams)
        name = self.qualified(declaration.name)
        family_type = self._pis(family_binders, Sort(0))
        arity = len(family_binders)
        flags = tuple(parameter.implicit for parameter in declaration.tparams)
        provisional = Global("data", name, implicit=flags)
        self.kernel.open_data(name, family_type)
        self.define(declaration.name, provisional, declaration.line)
        built = []
        entries = []
        try:
            # The family is visible, without constructors, while their types
            # are elaborated; the real declaration replaces it below.
            drafts = [self._constructor_draft(name, arity, constructor) for constructor in constructors]
            nparams = self._parameter_count(name, arity, drafts)
            for constructor, draft in zip(constructors, drafts):
                ctype, positions = self._constructor_type(name, nparams, constructor, draft)
                cname = self.qualified(constructor.name)
                built.append((cname, ctype))
                entries.append(
                    (
                        constructor,
                        Global(
                            "constructor",
                            cname,
                            positions=positions,
                            leading=len(constructor.tparams),
                            explicit=len(constructor.fields),
                        ),
                    )
                )
        finally:
            del self.scope[declaration.name]
            self.kernel.abort()
        self.kernel.add_data(name, nparams, family_type, built)
        self.define(declaration.name, provisional, declaration.line)
        self.declared(declaration.name, provisional)
        for constructor, entry in entries:
            self.define(constructor.name, entry, constructor.line)
            self.session.fields[entry.name] = entry.explicit
            self.declared(constructor.name, entry)

    def _constructor_draft(self, family, arity, constructor):
        """Elaborate a constructor in the order its source gives."""
        try:
            local, implicit_binders = self.telescope(Local(), constructor.tparams)
            local, field_binders = self.telescope(local, constructor.fields)
            term, _ = self.standalone(local, constructor.base)
            result = self.kernel.machine(local.ctx).force(
                self.kernel.machine(local.ctx).evaluate(term, local.env)
            )
        except ElabError as error:
            if not error.line:
                error.line = constructor.line
            raise
        if not (isinstance(result, VData) and result.name == family and len(result.args) == arity):
            self.fail(constructor.line, f"{constructor.name} must derive from its family applied to all its parameters")
        return local, implicit_binders, field_binders, result.args

    def _parameter_count(self, family, arity, drafts):
        """How many leading family positions are uniform parameters.

        A position is a parameter when every constructor returns the family at
        a distinct type parameter of its own there, and every occurrence of
        the family in that constructor's argument types uses the same one.
        """
        count = arity
        for local, implicit_binders, field_binders, result_args in drafts:
            machine = self.kernel.machine(local.ctx)
            levels = []
            for position in range(arity):
                value = machine.force(result_args[position])
                if not (
                    isinstance(value, VNeutral)
                    and isinstance(value.head, HVar)
                    and not value.spine
                    and value.head.level < len(implicit_binders)
                    and value.head.level not in levels
                ):
                    break
                levels.append(value.head.level)
            count = min(count, len(levels))
            binders = implicit_binders + field_binders
            for depth, (_, binder_type) in enumerate(binders):
                normal = machine.quote(machine.evaluate(binder_type, local.env[:depth]), depth)
                for args, here in self._occurrences(normal, depth, family):
                    for position in range(count):
                        expected = Var(here - 1 - levels[position])
                        if position >= len(args) or args[position] != expected:
                            count = position
                            break
        return count

    @staticmethod
    def _occurrences(term, depth, family):
        """Applications of ``family`` inside ``term``, with their depths."""
        found = []
        stack = [(term, depth)]
        while stack:
            node, here = stack.pop()
            head = node
            args = []
            while isinstance(head, App):
                args.append(head.arg)
                head = head.fn
            args.reverse()
            if isinstance(head, Const) and head.name == family:
                found.append((args, here))
            elif isinstance(head, Lam):
                stack.append((head.domain, here))
                stack.append((head.body, here + 1))
            elif isinstance(head, Pi):
                stack.append((head.domain, here))
                stack.append((head.codomain, here + 1))
            for arg in args:
                stack.append((arg, here))
        return found

    def _constructor_type(self, family, nparams, constructor, draft):
        """Reorder a constructor's binders: parameters, other implicits, fields."""
        local, implicit_binders, field_binders, result_args = draft
        machine = self.kernel.machine(local.ctx)
        nimplicit = len(implicit_binders)
        parameter_levels = [machine.force(result_args[position]).head.level for position in range(nparams)]
        others = [level for level in range(nimplicit) if level not in parameter_levels]
        order = parameter_levels + others
        position_of = {level: position for position, level in enumerate(order)}
        total = nimplicit + len(field_binders)
        renaming = tuple(
            var(position_of[level]) if level < nimplicit else var(level) for level in range(total)
        )
        source_binders = implicit_binders + field_binders

        def rename(term, source_depth, target_depth):
            value = Machine(self.sig).evaluate(term, renaming[:source_depth])
            renamed = Machine(self.sig).quote(value, total)
            for level in free_levels(renamed, total):
                if level >= target_depth:
                    self.fail(
                        constructor.line,
                        f"{constructor.name}: a type parameter that is a family parameter "
                        f"depends on one that is not; reorder them",
                    )
            return Machine(self.sig).quote(value, target_depth)

        binders = []
        for position in range(total):
            source = order[position] if position < nimplicit else position
            binder_name, binder_type = source_binders[source]
            binders.append((binder_name, rename(binder_type, source, position)))
        arguments = []
        for position in range(len(result_args)):
            value = Machine(self.sig).evaluate(
                self.kernel.machine(local.ctx).quote(result_args[position], total), renaming
            )
            arguments.append(Machine(self.sig).quote(value, total))
        result = apps(Const(family), arguments)
        return self._pis(binders, result), tuple(position_of[level] for level in range(nimplicit))
