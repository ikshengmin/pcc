"""Typed Go scalar semantics, independent of IR construction.

Go int is 64-bit on this frontend's admitted targets, not a Python integer.
Untyped constants are evaluated exactly before representability checks. Heap,
concurrency and recoverable-panic operations are explicit capability gaps.
"""

from dataclasses import dataclass
import re
import unicodedata

from pcc.frontends.go.syntax import Expr, GoDiagnostic
from pcc.frontends.go.types import (
    TYPE_NAMES, TYPES, integer_bits, integer_bounds, integer_signed, is_integer,
)


PREDECLARED = frozenset(
    "true false nil iota append cap clear close complex copy "
    "delete imag len make max min new panic print println real recover".split()
) | frozenset(TYPE_NAMES)
MAX_SHIFT_COUNT = (1 << 64) - 1
MAX_CONSTANT_BITS = 4096


@dataclass(eq=False)
class Symbol:
    token: object
    type: str | None
    constant: object = None
    declaration: object = None
    resolving: bool = False
    resolved: bool = True
    used: bool = False
    parameter: bool = False


def integer_literal(expr):
    text = str(expr.value)
    patterns = (
        (r"0[xX]_?[0-9a-fA-F](?:_?[0-9a-fA-F])*", 16),
        (r"0[bB]_?[01](?:_?[01])*", 2),
        (r"0[oO]_?[0-7](?:_?[0-7])*", 8),
        (r"0(?:_?[0-7])+", 8),
        (r"(?:0|[1-9](?:_?[0-9])*)", 10),
    )
    for pattern, base in patterns:
        if re.fullmatch(pattern, text):
            digits = text.replace("_", "")
            if base in (2, 16) or (base == 8 and digits[:2].lower() == "0o"):
                digits = digits[2:]
            value = 0
            for char in digits:
                value = value * base + int(char, base)
                if value.bit_length() > MAX_CONSTANT_BITS:
                    raise GoDiagnostic(expr.token, "integer constant exceeds implementation precision limit (4096 bits)")
            return value
    raise GoDiagnostic(expr.token, "invalid or unsupported integer literal " + repr(text))


def is_boolean(dtype):
    return dtype in ("bool", "untyped bool")


def default_type(dtype):
    return {"untyped int": "int", "untyped bool": "bool"}.get(dtype, dtype)


def check_representable(expr):
    if expr.constant is None:
        return
    if is_integer(expr.type):
        if abs(expr.constant).bit_length() > MAX_CONSTANT_BITS:
            raise GoDiagnostic(expr.token, "constant exceeds implementation precision limit (4096 bits)")
        if expr.type != "untyped int":
            minimum, maximum = integer_bounds(expr.type)
            if not minimum <= expr.constant <= maximum:
                raise GoDiagnostic(expr.token, f"constant overflows {expr.type}")


def coerce(expr, dtype):
    if expr.type == dtype:
        check_representable(expr)
        return
    if ((expr.type == "untyped int" and is_integer(dtype)) or
            (expr.type == "untyped bool" and is_boolean(dtype))):
        expr.type = dtype
        # Nonconstant shifts with an untyped left operand acquire their type
        # from the surrounding assignment/conversion/expression, before shift.
        if expr.constant is None and is_integer(dtype) and expr.kind in ("unary", "binary"):
            arguments = expr.args[:1] if expr.value in ("<<", ">>") else expr.args
            for argument in arguments:
                if argument.type == "untyped int":
                    coerce(argument, dtype)
        check_representable(expr)
        return
    raise GoDiagnostic(expr.token, f"cannot use {expr.type} as {dtype}")


class Analyzer:
    def __init__(self, program, *, imports=None, require_main=True, package_path=None):
        self.program = program
        self.imports = imports or {}
        self.require_main = require_main
        self.package_path = package_path
        self.functions = {}
        self.globals = {}
        self.scopes = []
        self.function = None
        self.loop_depth = 0

    def analyze(self):
        from pcc.frontends.go.lowering import function_symbol

        for function in self.program.functions:
            self.check_name(function.token)
            function.abi_symbol = function_symbol(function.token.text, self.package_path)
            name = function.token.text
            if name == "_":
                raise GoDiagnostic(function.token, "blank function declarations are not supported")
            if name in self.functions:
                raise GoDiagnostic(function.token, "duplicate package declaration " + name)
            if name == "init":
                raise GoDiagnostic(function.token, "init functions require shared-runtime initialization support")
            self.functions[name] = function
        for declaration in self.program.constants:
            if len(declaration.names) != len(declaration.exprs):
                raise GoDiagnostic(declaration.token, "constant declaration count mismatch")
            for token, expr in zip(declaration.names, declaration.exprs):
                self.check_name(token)
                if token.text == "_":
                    raise GoDiagnostic(token, "blank package constants are not supported")
                if token.text == "init":
                    raise GoDiagnostic(token, "package name init is reserved for init function declarations")
                if token.text in self.globals or token.text in self.functions:
                    raise GoDiagnostic(token, "duplicate package declaration " + token.text)
                self.globals[token.text] = Symbol(token, declaration.type, declaration=expr, resolved=False)
        # The package block spans every source file. An import's file-block
        # name may not collide with any package declaration, including one in
        # a sibling file (Go specification: Declarations and scope).
        package_names = set(self.functions) | set(self.globals)
        for file_imports in self.imports.values():
            for alias, binding in file_imports.items():
                if alias in package_names:
                    raise GoDiagnostic(binding.token, "import name conflicts with package declaration: " + alias)
        for symbol in self.globals.values():
            self.resolve_constant(symbol)
        main = self.functions.get("main")
        if self.require_main:
            if main is None:
                raise GoDiagnostic(self.program.token, "package main requires func main()")
            if main.params or main.result != "void":
                raise GoDiagnostic(main.token, "main must have no parameters and no result")
        for function in self.program.functions:
            self.analyze_function(function)
        for file_imports in self.imports.values():
            for binding in file_imports.values():
                if not binding.used:
                    raise GoDiagnostic(binding.token, "imported and not used: " + binding.alias)
        return self.program

    def imported(self, qualifier, token):
        if any(qualifier in scope for scope in self.scopes):
            raise GoDiagnostic(token, "package selector is shadowed by local variable " + qualifier)
        binding = self.imports.get(token.filename, {}).get(qualifier)
        if binding is None:
            raise GoDiagnostic(token, "undefined imported package " + qualifier)
        if not token.text or unicodedata.category(token.text[0]) != "Lu":
            raise GoDiagnostic(token, "cannot access unexported package member " + token.text)
        binding.used = True
        return binding.analyzer

    def check_name(self, token):
        if token.text in PREDECLARED:
            raise GoDiagnostic(token, "shadowing predeclared names is not implemented: " + token.text)

    def resolve_constant(self, symbol):
        if symbol.resolved:
            return
        if symbol.resolving:
            raise GoDiagnostic(symbol.token, "constant initialization cycle")
        symbol.resolving = True
        self.expression(symbol.declaration)
        if symbol.declaration.constant is None:
            raise GoDiagnostic(symbol.token, "const initializer is not a constant")
        if symbol.type is not None:
            coerce(symbol.declaration, symbol.type)
        symbol.type = symbol.declaration.type
        symbol.constant = symbol.declaration.constant
        symbol.resolved = True
        symbol.resolving = False

    def lookup(self, token):
        for scope in reversed(self.scopes):
            if token.text in scope:
                return scope[token.text]
        if token.text in self.globals:
            result = self.globals[token.text]
            self.resolve_constant(result)
            return result
        raise GoDiagnostic(token, "undefined name " + token.text)

    def analyze_function(self, function):
        self.function = function
        self.scopes = [{}]
        self.loop_depth = 0
        for token, dtype in function.params:
            self.check_name(token)
            symbol = Symbol(token, dtype, parameter=True)
            if token.text != "_":
                if token.text in self.scopes[-1]:
                    raise GoDiagnostic(token, "duplicate parameter " + token.text)
                self.scopes[-1][token.text] = symbol
            function.parameters.append(symbol)
            function.locals.append(symbol)
        self.statements(function.body, new_scope=False)
        if function.result != "void" and not self.terminates(function.body):
            raise GoDiagnostic(function.token, "missing return at end of function")
        for symbol in function.locals:
            if not symbol.used and not symbol.parameter and symbol.token.text != "_":
                raise GoDiagnostic(symbol.token, "declared and not used: " + symbol.token.text)

    def statements(self, statements, new_scope=True):
        if new_scope:
            self.scopes.append({})
        for statement in statements:
            self.statement(statement)
        if new_scope:
            self.scopes.pop()

    def declare(self, token, dtype, constant=None):
        self.check_name(token)
        if token.text != "_" and token.text in self.scopes[-1]:
            raise GoDiagnostic(token, "redeclaration in this block: " + token.text)
        symbol = Symbol(token, dtype, constant=constant)
        if token.text != "_":
            self.scopes[-1][token.text] = symbol
        if constant is None:
            self.function.locals.append(symbol)
        return symbol

    def condition(self, expr):
        self.expression(expr)
        if not is_boolean(expr.type):
            raise GoDiagnostic(expr.token, "condition must be bool (Go has no integer truth conversion)")
        coerce(expr, "bool")

    def statement(self, statement):
        kind = statement.kind
        if kind in ("var", "const", ":=", "="):
            if statement.exprs and len(statement.names) != len(statement.exprs):
                raise GoDiagnostic(statement.token, "assignment count mismatch")
            for expr in statement.exprs:
                self.expression(expr)
                if expr.type == "void":
                    raise GoDiagnostic(expr.token, "void function call used as a value")
            if kind in ("var", "const", ":="):
                names = [token.text for token in statement.names if token.text != "_"]
                if len(names) != len(set(names)):
                    raise GoDiagnostic(statement.token, "duplicate declaration name")
            new_count = 0
            for index, token in enumerate(statement.names):
                expr = statement.exprs[index] if statement.exprs else None
                if token.text == "_" and kind == "=":
                    symbol = None
                    if expr is not None:
                        coerce(expr, default_type(expr.type))
                elif kind == "=" or (kind == ":=" and token.text in self.scopes[-1]):
                    symbol = self.lookup(token)
                    if symbol.constant is not None:
                        raise GoDiagnostic(token, "cannot assign to constant " + token.text)
                    if expr is not None:
                        coerce(expr, symbol.type)
                else:
                    dtype = statement.type or (expr.type if kind == "const" else default_type(expr.type)) if expr else statement.type
                    if expr is not None:
                        coerce(expr, dtype)
                    constant = None
                    if kind == "const":
                        if expr.constant is None:
                            raise GoDiagnostic(token, "const initializer is not a constant")
                        constant = expr.constant
                    symbol = self.declare(token, dtype, constant)
                    if token.text != "_":
                        new_count += 1
                statement.symbols.append(symbol)
            if kind == ":=" and not new_count:
                raise GoDiagnostic(statement.token, "short declaration requires at least one new non-blank variable")
            return
        if kind in ("++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "&^=", "<<=", ">>="):
            if len(statement.names) != 1 or (statement.exprs and len(statement.exprs) != 1):
                raise GoDiagnostic(statement.token, "compound assignment requires one variable and value")
            token = statement.names[0]
            symbol = self.lookup(token)
            if symbol.constant is not None:
                raise GoDiagnostic(token, "cannot assign to constant " + token.text)
            left = Expr("name", token, token.text)
            right = statement.exprs[0] if statement.exprs else Expr("integer", statement.token, "1")
            operation = kind[0] if kind in ("++", "--") else kind[:-1]
            expr = Expr("binary", statement.token, operation, [left, right])
            self.expression(expr)
            coerce(expr, symbol.type)
            statement.exprs, statement.symbols = [expr], [symbol]
            return
        if kind == "expression":
            expr = statement.exprs[0]
            self.expression(expr)
            if expr.kind != "call" or expr.symbol is None:
                raise GoDiagnostic(expr.token, "only function calls may be expression statements")
            return
        if kind == "return":
            if self.function.result == "void":
                if statement.exprs:
                    raise GoDiagnostic(statement.token, "void function cannot return a value")
            else:
                if len(statement.exprs) != 1:
                    raise GoDiagnostic(statement.token, "return requires one result")
                self.expression(statement.exprs[0])
                coerce(statement.exprs[0], self.function.result)
            statement.terminates = True
            return
        if kind in ("break", "continue"):
            if not self.loop_depth:
                raise GoDiagnostic(statement.token, kind + " outside loop")
            return
        if kind == "block":
            self.statements(statement.body)
            statement.terminates = self.terminates(statement.body)
            return
        if kind in ("if", "for"):
            self.scopes.append({})
            if statement.init is not None:
                self.statement(statement.init)
            if statement.exprs:
                self.condition(statement.exprs[0])
            if kind == "for":
                self.loop_depth += 1
            self.statements(statement.body)
            if statement.post is not None:
                self.statement(statement.post)
            if kind == "for":
                self.loop_depth -= 1
                statement.terminates = not statement.exprs and not self.has_break(statement.body)
            elif statement.otherwise is not None:
                self.statements(statement.otherwise)
                statement.terminates = self.terminates(statement.body) and self.terminates(statement.otherwise)
            self.scopes.pop()
            return
        raise GoDiagnostic(statement.token, "unsupported statement " + kind)

    def has_break(self, statements):
        for statement in statements:
            if statement.kind == "break":
                return True
            if statement.kind != "for" and (
                self.has_break(statement.body) or self.has_break(statement.otherwise or [])
            ):
                return True
        return False

    def terminates(self, statements):
        return bool(statements and statements[-1].terminates)

    def expression(self, expr):
        if expr.type is not None:
            return
        kind = expr.kind
        if kind == "integer":
            expr.type, expr.constant = "untyped int", integer_literal(expr)
            return
        if kind == "name":
            if expr.value in ("true", "false"):
                expr.type, expr.constant = "untyped bool", expr.value == "true"
            else:
                symbol = self.lookup(expr.token)
                symbol.used = True
                expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
            return
        if kind == "selector":
            imported = self.imported(expr.args[0].value, expr.token)
            symbol = imported.globals.get(expr.value)
            if symbol is None:
                raise GoDiagnostic(expr.token, "undefined imported constant " + expr.value)
            expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
            return
        if kind == "call":
            name = expr.value
            if expr.qualifier is not None:
                function = self.imported(expr.qualifier, expr.token).functions.get(name)
                if function is None:
                    raise GoDiagnostic(expr.token, "undefined imported function " + name)
                self.call(expr, function)
                return
            if name in TYPE_NAMES:
                name = TYPE_NAMES[name]
                if len(expr.args) != 1:
                    raise GoDiagnostic(expr.token, "conversion requires one argument")
                arg = expr.args[0]
                self.expression(arg)
                if not ((is_integer(name) and is_integer(arg.type)) or
                        (is_boolean(name) and is_boolean(arg.type))):
                    raise GoDiagnostic(expr.token, "invalid conversion from " + arg.type + " to " + name)
                if arg.constant is None and arg.type == "untyped int":
                    coerce(arg, name)
                expr.type, expr.constant = name, arg.constant
                check_representable(expr)
                return
            # Lexical variables/constants shadow package functions. They are not
            # callable: first-class function values are not in this slice.
            if any(name in scope for scope in self.scopes) or name in self.globals:
                raise GoDiagnostic(expr.token, "cannot call non-function " + name)
            function = self.functions.get(name)
            if function is None:
                label = "unsupported builtin " if name in PREDECLARED else "undefined function "
                raise GoDiagnostic(expr.token, label + name)
            self.call(expr, function)
            return
        for arg in expr.args:
            self.expression(arg)
        if kind == "unary":
            arg = expr.args[0]
            expr.type = arg.type
            if expr.value == "!":
                if not is_boolean(arg.type):
                    raise GoDiagnostic(expr.token, "logical not requires bool")
            elif not is_integer(arg.type):
                raise GoDiagnostic(expr.token, "integer unary operation requires integer")
            if arg.constant is not None:
                value = arg.constant
                mask = -1 if not is_integer(arg.type) or integer_signed(arg.type) else (1 << integer_bits(arg.type)) - 1
                expr.constant = {"+": lambda: value, "-": lambda: -value,
                                 "^": lambda: value ^ mask, "!": lambda: not value}[expr.value]()
            check_representable(expr)
            return
        if kind == "binary":
            self.binary(expr)
            return
        raise GoDiagnostic(expr.token, "unsupported expression " + kind)

    def call(self, expr, function):
        if len(expr.args) != len(function.params):
            raise GoDiagnostic(expr.token, "argument count mismatch for " + expr.value)
        for arg, (_, dtype) in zip(expr.args, function.params):
            self.expression(arg)
            coerce(arg, dtype)
        expr.type, expr.symbol = function.result, function

    def binary(self, expr):
        left, right = expr.args
        op = expr.value
        if op in ("<<", ">>"):
            if not is_integer(left.type) or not is_integer(right.type):
                raise GoDiagnostic(expr.token, "shifts require integer operands")
            if right.constant is None:
                if integer_signed(right.type):
                    raise GoDiagnostic(expr.token, "dynamic signed shift counts require shared-runtime negative-count panic support")
            elif right.constant < 0:
                raise GoDiagnostic(expr.token, "negative shift count")
            elif right.constant > MAX_SHIFT_COUNT:
                raise GoDiagnostic(expr.token, "shift count overflows uint64")
            expr.type = left.type
        else:
            if left.type.startswith("untyped ") and not right.type.startswith("untyped "):
                coerce(left, right.type)
            elif right.type.startswith("untyped ") and not left.type.startswith("untyped "):
                coerce(right, left.type)
            if left.type != right.type:
                raise GoDiagnostic(expr.token, f"mismatched operand types {left.type} and {right.type}")
            expr.type = left.type
            if op in ("==", "!=", "<", "<=", ">", ">=") and (left.constant is None or right.constant is None):
                if left.type == "untyped int":
                    coerce(left, "int")
                    coerce(right, "int")
            if op in ("&&", "||"):
                if not is_boolean(left.type):
                    raise GoDiagnostic(expr.token, "logical operators require bool")
            elif op in ("==", "!="):
                if not is_integer(left.type) and not is_boolean(left.type):
                    raise GoDiagnostic(expr.token, "unsupported comparison operands")
                expr.type = "untyped bool"
            elif op in ("<", "<=", ">", ">="):
                if not is_integer(left.type):
                    raise GoDiagnostic(expr.token, "ordered comparison requires integer operands")
                expr.type = "untyped bool"
            elif not is_integer(left.type):
                raise GoDiagnostic(expr.token, "arithmetic and bitwise operators require integers")
        if op in ("/", "%"):
            if right.constant == 0:
                raise GoDiagnostic(expr.token, "division by zero")
            if right.constant is None:
                raise GoDiagnostic(expr.token, "dynamic division requires shared-runtime division-by-zero panic support")
        if left.constant is not None and right.constant is not None:
            a, b = left.constant, right.constant
            if op == "<<" and b > MAX_CONSTANT_BITS:
                if a != 0:
                    raise GoDiagnostic(expr.token, "constant exceeds implementation precision limit (4096 bits)")
                expr.constant = 0
            elif op in ("/", "%"):
                quotient = abs(a) // abs(b)
                if (a < 0) != (b < 0):
                    quotient = -quotient
                expr.constant = quotient if op == "/" else a - quotient * b
            else:
                operations = {
                    "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                    "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
                    "&^": lambda: a & ~b, "<<": lambda: a << b, ">>": lambda: a >> b,
                    "==": lambda: a == b, "!=": lambda: a != b, "<": lambda: a < b,
                    "<=": lambda: a <= b, ">": lambda: a > b, ">=": lambda: a >= b,
                    "&&": lambda: a and b, "||": lambda: a or b,
                }
                expr.constant = operations[op]()
            check_representable(expr)
