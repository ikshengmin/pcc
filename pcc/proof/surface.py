"""Reading a Python module as a checked development.

This is the only module that touches Python syntax.  It accepts a fragment of
ordinary Python and turns it into a small tree for the elaborator.  Anything
outside the fragment is rejected here, with a source position, instead of
being given a meaning it does not have in Python.

The fragment:

* ``from module import names``
* ``class D[...](Data)`` declares an inductive family; the frozen dataclasses
  that follow it and derive from ``D[...]`` are its constructors
* ``def f[implicit...](explicit...) -> T`` whose body is built from
  ``return``, ``match`` and ``if``
* ``NAME: T = value`` for a constant and ``type N[...] = T`` for a type alias
* ``if __name__ == "__main__":`` blocks, which are executed but not checked
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


class SurfaceError(Exception):
    def __init__(self, message, path="<source>", line=0):
        super().__init__(f"{path}:{line}: {message}")
        self.message = message
        self.path = path
        self.line = line


# -- expressions -------------------------------------------------------------

@dataclass(frozen=True)
class SName:
    name: str
    line: int = 0


@dataclass(frozen=True)
class SNum:
    value: int
    line: int = 0


@dataclass(frozen=True)
class SBool:
    value: bool
    line: int = 0


@dataclass(frozen=True)
class SCall:
    fn: object
    args: tuple
    line: int = 0


@dataclass(frozen=True)
class SIndex:
    base: object
    args: tuple
    line: int = 0


@dataclass(frozen=True)
class SLambda:
    params: tuple
    body: object
    line: int = 0


@dataclass(frozen=True)
class SIf:
    test: object
    then: object
    orelse: object
    line: int = 0


@dataclass(frozen=True)
class SBin:
    op: str
    left: object
    right: object
    line: int = 0


@dataclass(frozen=True)
class SNot:
    operand: object
    line: int = 0


# -- patterns ----------------------------------------------------------------

@dataclass(frozen=True)
class PWild:
    line: int = 0


@dataclass(frozen=True)
class PVar:
    name: str
    line: int = 0


@dataclass(frozen=True)
class PCon:
    name: str
    patterns: tuple
    line: int = 0


@dataclass(frozen=True)
class PNum:
    value: int
    line: int = 0


@dataclass(frozen=True)
class PBool:
    value: bool
    line: int = 0


@dataclass(frozen=True)
class PAs:
    pattern: object
    name: str
    line: int = 0


# -- statements --------------------------------------------------------------

@dataclass(frozen=True)
class SReturn:
    value: object
    line: int = 0


@dataclass(frozen=True)
class SCase:
    patterns: tuple
    body: tuple
    line: int = 0


@dataclass(frozen=True)
class SMatch:
    subjects: tuple
    cases: tuple
    line: int = 0


@dataclass(frozen=True)
class SIfStmt:
    test: object
    then: tuple
    orelse: tuple
    line: int = 0


# -- declarations ------------------------------------------------------------

@dataclass(frozen=True)
class SParam:
    name: str
    type: object
    implicit: bool = False
    line: int = 0


@dataclass(frozen=True)
class SImport:
    module: str
    names: tuple  # of (name, local name)
    line: int = 0


@dataclass(frozen=True)
class SData:
    name: str
    tparams: tuple
    line: int = 0


@dataclass(frozen=True)
class SCon:
    name: str
    tparams: tuple
    base: object
    fields: tuple
    line: int = 0


@dataclass(frozen=True)
class SFun:
    name: str
    tparams: tuple
    params: tuple
    returns: object
    body: tuple
    line: int = 0


@dataclass(frozen=True)
class SConst:
    name: str
    type: object
    value: object
    line: int = 0


@dataclass(frozen=True)
class SAlias:
    name: str
    tparams: tuple
    value: object
    line: int = 0


@dataclass
class SModule:
    name: str
    path: str
    declarations: list = field(default_factory=list)


#: Imports that only matter when the module runs.
RUNTIME_ONLY_MODULES = ("__future__", "dataclasses", "typing")

_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
_COMPARE = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}


def read_module(source, name, path="<source>"):
    """Parse ``source`` and return its :class:`SModule`."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        raise SurfaceError(f"not valid Python: {error.msg}", path, error.lineno or 0)
    return _Reader(name, path).module(tree)


class _Reader:
    def __init__(self, name, path):
        self.name = name
        self.path = path

    def fail(self, node, message):
        raise SurfaceError(message, self.path, getattr(node, "lineno", 0))

    # -- module --------------------------------------------------------------

    def module(self, tree):
        result = SModule(self.name, self.path)
        for statement in tree.body:
            declaration = self.declaration(statement)
            if declaration is not None:
                result.declarations.append(declaration)
        return result

    def declaration(self, node):
        if self._is_docstring(node):
            return None
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                self.fail(node, "relative imports are outside the checked fragment")
            if node.module in RUNTIME_ONLY_MODULES:
                return None
            names = []
            for alias in node.names:
                if alias.name == "*":
                    self.fail(node, "import the names you use; '*' is not checked")
                names.append((alias.name, alias.asname or alias.name))
            return SImport(node.module, tuple(names), node.lineno)
        if isinstance(node, ast.ClassDef):
            return self.class_definition(node)
        if isinstance(node, ast.FunctionDef):
            return self.function(node)
        if isinstance(node, ast.AnnAssign):
            if not (isinstance(node.target, ast.Name) and node.simple and node.value is not None):
                self.fail(node, "a constant is written NAME: Type = value")
            return SConst(
                node.target.id,
                self.expression(node.annotation),
                self.expression(node.value),
                node.lineno,
            )
        if isinstance(node, ast.TypeAlias):
            return SAlias(
                node.name.id,
                self.type_parameters(node.type_params, False),
                self.expression(node.value),
                node.lineno,
            )
        if isinstance(node, ast.If) and self._is_main_guard(node.test):
            return None
        if isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"
            ):
                return None
            self.fail(node, "a constant needs its type: NAME: Type = value")
        self.fail(node, f"{type(node).__name__} is outside the checked fragment")

    @staticmethod
    def _is_docstring(node):
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

    @staticmethod
    def _is_main_guard(test):
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        )

    # -- classes -------------------------------------------------------------

    def class_definition(self, node):
        if node.keywords:
            self.fail(node, "class keywords are outside the checked fragment")
        if len(node.bases) != 1:
            self.fail(node, "a checked class has exactly one base")
        base = node.bases[0]
        if isinstance(base, ast.Name) and base.id == "Data":
            if node.decorator_list:
                self.fail(node, "an inductive family takes no decorator")
            self._only_documentation(node, "an inductive family has no body")
            return SData(node.name, self.type_parameters(node.type_params, True), node.lineno)
        if not self._is_frozen_dataclass(node):
            self.fail(node, "a constructor must be declared @dataclass(frozen=True)")
        fields = []
        for statement in node.body:
            if self._is_docstring(statement) or isinstance(statement, ast.Pass):
                continue
            if not (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.simple
                and statement.value is None
            ):
                self.fail(statement, "a constructor body lists fields as name: Type")
            fields.append(
                SParam(
                    statement.target.id,
                    self.expression(statement.annotation),
                    False,
                    statement.lineno,
                )
            )
        return SCon(
            node.name,
            self.type_parameters(node.type_params, False),
            self.expression(base),
            tuple(fields),
            node.lineno,
        )

    def _only_documentation(self, node, message):
        for statement in node.body:
            if not (self._is_docstring(statement) or isinstance(statement, ast.Pass)):
                self.fail(statement, message)

    @staticmethod
    def _is_frozen_dataclass(node):
        if len(node.decorator_list) != 1:
            return False
        decorator = node.decorator_list[0]
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
            and not decorator.args
            and len(decorator.keywords) == 1
        ):
            return False
        keyword = decorator.keywords[0]
        return (
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
        )

    def type_parameters(self, parameters, allow_implicit):
        result = []
        for parameter in parameters:
            if not isinstance(parameter, ast.TypeVar):
                self.fail(parameter, "only plain type parameters are checked")
            if parameter.bound is None:
                self.fail(parameter, f"type parameter {parameter.name} needs a type: [x: T]")
            if getattr(parameter, "default_value", None) is not None:
                self.fail(parameter, "type parameter defaults are outside the checked fragment")
            bound = parameter.bound
            implicit = False
            if (
                isinstance(bound, ast.Subscript)
                and isinstance(bound.value, ast.Name)
                and bound.value.id == "Implicit"
            ):
                if not allow_implicit:
                    self.fail(parameter, "Implicit[...] marks parameters of a family only")
                implicit = True
                bound = bound.slice
            result.append(
                SParam(parameter.name, self.expression(bound), implicit, parameter.lineno)
            )
        return tuple(result)

    # -- functions -----------------------------------------------------------

    def function(self, node):
        if node.decorator_list:
            self.fail(node, "decorators are outside the checked fragment")
        arguments = node.args
        if (
            arguments.posonlyargs
            or arguments.kwonlyargs
            or arguments.vararg
            or arguments.kwarg
            or arguments.defaults
            or arguments.kw_defaults
        ):
            self.fail(node, "only plain positional parameters are checked")
        params = []
        for argument in arguments.args:
            if argument.annotation is None:
                self.fail(argument, f"parameter {argument.arg} needs a type")
            params.append(
                SParam(argument.arg, self.expression(argument.annotation), False, argument.lineno)
            )
        if node.returns is None:
            self.fail(node, f"{node.name} needs a result type")
        return SFun(
            node.name,
            self.type_parameters(node.type_params, False),
            tuple(params),
            self.expression(node.returns),
            self.block(node.body),
            node.lineno,
        )

    def block(self, statements):
        result = []
        for statement in statements:
            if self._is_docstring(statement) or isinstance(statement, ast.Pass):
                continue
            result.append(self.statement(statement))
        return tuple(result)

    def statement(self, node):
        if isinstance(node, ast.Return):
            if node.value is None:
                self.fail(node, "return needs a value")
            return SReturn(self.expression(node.value), node.lineno)
        if isinstance(node, ast.Match):
            if isinstance(node.subject, ast.Tuple):
                subjects = tuple(self.expression(item) for item in node.subject.elts)
            else:
                subjects = (self.expression(node.subject),)
            cases = tuple(self.case(case, len(subjects)) for case in node.cases)
            return SMatch(subjects, cases, node.lineno)
        if isinstance(node, ast.If):
            return SIfStmt(
                self.expression(node.test),
                self.block(node.body),
                self.block(node.orelse),
                node.lineno,
            )
        self.fail(node, f"{type(node).__name__} is outside the checked fragment")

    def case(self, node, width):
        if node.guard is not None:
            self.fail(node.pattern, "case guards are outside the checked fragment")
        pattern = node.pattern
        line = pattern.lineno
        if width == 1:
            patterns = (self.pattern(pattern),)
        elif isinstance(pattern, ast.MatchSequence):
            if len(pattern.patterns) != width:
                self.fail(pattern, f"this case needs {width} patterns")
            patterns = tuple(self.pattern(item) for item in pattern.patterns)
        elif isinstance(pattern, ast.MatchAs) and pattern.pattern is None and pattern.name is None:
            patterns = tuple(PWild(line) for _ in range(width))
        else:
            self.fail(pattern, f"this case needs a tuple of {width} patterns")
        return SCase(patterns, self.block(node.body), line)

    def pattern(self, node):
        line = node.lineno
        if isinstance(node, ast.MatchAs):
            if node.pattern is None:
                return PWild(line) if node.name is None else PVar(node.name, line)
            return PAs(self.pattern(node.pattern), node.name, line)
        if isinstance(node, ast.MatchClass):
            if node.kwd_attrs:
                self.fail(node, "keyword patterns are outside the checked fragment")
            if not isinstance(node.cls, ast.Name):
                self.fail(node, "a constructor pattern names its class directly")
            return PCon(node.cls.id, tuple(self.pattern(item) for item in node.patterns), line)
        if isinstance(node, ast.MatchValue):
            value = node.value
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)
                and value.value >= 0
            ):
                return PNum(value.value, line)
            self.fail(node, "only natural number literals are checked as value patterns")
        if isinstance(node, ast.MatchSingleton):
            if node.value is True or node.value is False:
                return PBool(node.value, line)
            self.fail(node, "None is outside the checked fragment")
        self.fail(node, f"{type(node).__name__} patterns are outside the checked fragment")

    # -- expressions ---------------------------------------------------------

    def expression(self, node):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            return SName(node.id, line)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                return SBool(value, line)
            if isinstance(value, int) and value >= 0:
                return SNum(value, line)
            self.fail(node, f"the literal {value!r} is outside the checked fragment")
        if isinstance(node, ast.Call):
            if node.keywords:
                self.fail(node, "keyword arguments are outside the checked fragment")
            for argument in node.args:
                if isinstance(argument, ast.Starred):
                    self.fail(node, "starred arguments are outside the checked fragment")
            return SCall(
                self.expression(node.func),
                tuple(self.expression(argument) for argument in node.args),
                line,
            )
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Tuple):
                args = tuple(self.expression(item) for item in node.slice.elts)
            else:
                args = (self.expression(node.slice),)
            return SIndex(self.expression(node.value), args, line)
        if isinstance(node, ast.Lambda):
            arguments = node.args
            if (
                arguments.posonlyargs
                or arguments.kwonlyargs
                or arguments.vararg
                or arguments.kwarg
                or arguments.defaults
                or arguments.kw_defaults
            ):
                self.fail(node, "only plain lambda parameters are checked")
            return SLambda(
                tuple(argument.arg for argument in arguments.args),
                self.expression(node.body),
                line,
            )
        if isinstance(node, ast.IfExp):
            return SIf(
                self.expression(node.test),
                self.expression(node.body),
                self.expression(node.orelse),
                line,
            )
        if isinstance(node, ast.BinOp):
            operator = _BINARY.get(type(node.op))
            if operator is None:
                self.fail(node, f"operator {type(node.op).__name__} is outside the checked fragment")
            return SBin(operator, self.expression(node.left), self.expression(node.right), line)
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                self.fail(node, "chained comparisons are outside the checked fragment")
            operator = _COMPARE.get(type(node.ops[0]))
            if operator is None:
                self.fail(node, f"comparison {type(node.ops[0]).__name__} is outside the checked fragment")
            return SBin(
                operator,
                self.expression(node.left),
                self.expression(node.comparators[0]),
                line,
            )
        if isinstance(node, ast.BoolOp):
            operator = "and" if isinstance(node.op, ast.And) else "or"
            result = self.expression(node.values[0])
            for value in node.values[1:]:
                result = SBin(operator, result, self.expression(value), line)
            return result
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return SNot(self.expression(node.operand), line)
        self.fail(node, f"{type(node).__name__} is outside the checked fragment")
