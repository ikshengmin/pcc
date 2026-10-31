"""Typed Go scalar semantics, independent of IR construction.

Go int is 64-bit on this frontend's admitted targets, not a Python integer.
Untyped constants are evaluated exactly before representability checks. Heap,
concurrency and recoverable-panic operations are explicit capability gaps.
"""

from dataclasses import dataclass
import re
import unicodedata

from pcc.frontends.go.syntax import ArraySyntax, Expr, GoDiagnostic
from pcc.frontends.go.types import (
    ArrayType, TYPE_NAMES, TYPES, integer_bits, integer_bounds, integer_signed, is_integer,
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
    package_variable: bool = False
    initializer: object = None
    abi_symbol: str = ""


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
        for declaration in self.program.constants + self.program.variables:
            if declaration.exprs and len(declaration.names) != len(declaration.exprs):
                raise GoDiagnostic(declaration.token, "constant declaration count mismatch")
            for index, token in enumerate(declaration.names):
                expr = declaration.exprs[index] if declaration.exprs else None
                self.check_name(token)
                if token.text == "_":
                    raise GoDiagnostic(token, "blank package declarations are not supported")
                if token.text == "init":
                    raise GoDiagnostic(token, "package name init is reserved for init function declarations")
                if token.text in self.globals or token.text in self.functions:
                    raise GoDiagnostic(token, "duplicate package declaration " + token.text)
                symbol = Symbol(token, declaration.type, declaration=expr, resolved=False,
                                package_variable=declaration.kind == "var",
                                abi_symbol=function_symbol(token.text, self.package_path))
                self.globals[token.text] = symbol
                declaration.symbols.append(symbol)
        # The package block spans every source file. An import's file-block
        # name may not collide with any package declaration, including one in
        # a sibling file (Go specification: Declarations and scope).
        package_names = set(self.functions) | set(self.globals)
        for file_imports in self.imports.values():
            for alias, binding in file_imports.items():
                if alias in package_names:
                    raise GoDiagnostic(binding.token, "import name conflicts with package declaration: " + alias)
        for symbol in self.globals.values():
            if not symbol.package_variable:
                self.resolve_constant(symbol)
        for function in self.program.functions:
            function.params = [(token, self.resolve_type(dtype)) for token, dtype in function.params]
            function.result = self.resolve_type(function.result)
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

    def resolve_type(self, dtype):
        if not isinstance(dtype, ArraySyntax):
            return dtype
        if isinstance(dtype.element, ArraySyntax):
            raise GoDiagnostic(dtype.token, "nested arrays are not implemented")
        if dtype.length is None:
            raise GoDiagnostic(dtype.token, "array length inference requires a composite literal")
        count = self.array_index_constant(dtype.length, "array length")
        if count > 4096:
            raise GoDiagnostic(dtype.token, "array exceeds implementation limit (4096 elements)")
        return ArrayType(count, dtype.element)

    def array_index_constant(self, expr, label):
        self.expression(expr)
        if not is_integer(expr.type) or expr.constant is None:
            raise GoDiagnostic(expr.token, label + " must be an integer constant")
        if not 0 <= expr.constant <= (1 << 63) - 1:
            raise GoDiagnostic(expr.token, label + " must be nonnegative and representable as int")
        return expr.constant

    def resolve_constant(self, symbol):
        if symbol.resolved:
            return
        if symbol.resolving:
            raise GoDiagnostic(symbol.token, "package initialization cycle" if symbol.package_variable else "constant initialization cycle")
        symbol.resolving = True
        symbol.type = self.resolve_type(symbol.type)
        expr = symbol.declaration
        if expr is not None:
            self.expression(expr)
            dtype = symbol.type or (default_type(expr.type) if symbol.package_variable else expr.type)
            coerce(expr, dtype)
            symbol.type = dtype
            if symbol.package_variable:
                if expr.constant is not None:
                    symbol.initializer = expr.constant
                elif expr.kind == "array" and all(item.constant is not None for item in expr.args):
                    values = [0] * expr.type.count
                    for index, item in zip(expr.indices, expr.args):
                        values[index] = item.constant
                    symbol.initializer = tuple(values)
                else:
                    raise GoDiagnostic(symbol.token, "nonconstant package initializer requires shared-runtime initialization support")
            else:
                if expr.constant is None:
                    raise GoDiagnostic(symbol.token, "const initializer is not a constant")
                symbol.constant = expr.constant
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
        if kind == "=":
            if len(statement.targets) != len(statement.exprs):
                raise GoDiagnostic(statement.token, "assignment count mismatch")
            for target in statement.targets:
                self.assignment_target(target, read=False)
            for target, expr in zip(statement.targets, statement.exprs):
                self.expression(expr)
                if expr.type == "void":
                    raise GoDiagnostic(expr.token, "void function call used as a value")
                coerce(expr, default_type(expr.type) if target.value == "_" else target.type)
            return
        if kind in ("var", "const", ":="):
            statement.type = self.resolve_type(statement.type)
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
                if kind == ":=" and token.text in self.scopes[-1]:
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
            left = statement.targets[0]
            self.assignment_target(left)
            if left.value == "_":
                raise GoDiagnostic(left.token, "cannot use blank identifier as a value")
            right = statement.exprs[0] if statement.exprs else Expr("integer", statement.token, "1")
            operation = kind[0] if kind in ("++", "--") else kind[:-1]
            expr = Expr("binary", statement.token, operation, [left, right])
            self.expression(expr)
            coerce(expr, left.type)
            statement.exprs = [expr]
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

    def assignment_target(self, expr, *, read=True):
        if expr.kind == "name" and expr.value == "_":
            return
        was_used = self.lookup(expr.token).used if expr.kind == "name" else None
        self.expression(expr)
        if expr.kind == "name" and not read:
            expr.symbol.used = was_used
        if not expr.addressable:
            raise GoDiagnostic(expr.token, "cannot assign to constant or non-addressable value")

    def index_upper_bound(self, expr):
        """Conservative, type-based proof; never assumes a runtime array index."""
        if expr.constant is not None:
            return expr.constant if expr.constant >= 0 else None
        if expr.kind == "binary" and expr.value == "&":
            masks = [arg.constant for arg in expr.args if arg.constant is not None and arg.constant >= 0]
            if masks:
                return min(masks)
        if not integer_signed(expr.type):
            maximum = (1 << integer_bits(expr.type)) - 1
            if expr.kind == "binary" and expr.value == ">>" and expr.args[1].constant is not None:
                maximum >>= min(expr.args[1].constant, integer_bits(expr.type))
            return maximum
        return None

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
        if kind == "array":
            indices, next_index, used = [], 0, set()
            for key, item in zip(expr.keys, expr.args):
                index = next_index if key is None else self.array_index_constant(key, "array literal index")
                if index in used:
                    raise GoDiagnostic(item.token, "duplicate array literal index")
                used.add(index)
                indices.append(index)
                next_index = index + 1
            syntax = expr.value
            if syntax.length is None:
                if isinstance(syntax.element, ArraySyntax):
                    raise GoDiagnostic(syntax.token, "nested arrays are not implemented")
                count = max(indices, default=-1) + 1
                if count > 4096:
                    raise GoDiagnostic(syntax.token, "array exceeds implementation limit (4096 elements)")
                expr.type = ArrayType(count, syntax.element)
            else:
                expr.type = self.resolve_type(syntax)
            expr.indices = indices
            for index, item in zip(indices, expr.args):
                if index >= expr.type.count:
                    raise GoDiagnostic(item.token, "array literal index out of bounds")
                self.expression(item)
                coerce(item, expr.type.element)
            if self.function is not None:
                self.function.temporaries.append(expr)
            return
        if kind == "index":
            base, index = expr.args
            self.expression(base)
            self.expression(index)
            if not isinstance(base.type, ArrayType):
                raise GoDiagnostic(expr.token, "indexing requires an array (slices/maps/strings are not implemented)")
            if not is_integer(index.type):
                raise GoDiagnostic(index.token, "array index must be an integer")
            if index.constant is not None:
                value = self.array_index_constant(index, "array index")
                if value >= base.type.count:
                    raise GoDiagnostic(index.token, "array index out of bounds")
            else:
                upper = self.index_upper_bound(index)
                if upper is None or upper >= base.type.count:
                    raise GoDiagnostic(index.token, "unproven array bounds require shared-runtime bounds-panic support")
            coerce(index, default_type(index.type))
            expr.type, expr.addressable = base.type.element, base.addressable
            if not base.addressable and self.function is not None:
                self.function.temporaries.append(base)
            return
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
                expr.addressable = symbol.constant is None
            return
        if kind == "selector":
            imported = self.imported(expr.args[0].value, expr.token)
            symbol = imported.globals.get(expr.value)
            if symbol is None:
                raise GoDiagnostic(expr.token, "undefined imported constant or variable " + expr.value)
            expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
            expr.addressable = symbol.constant is None
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
                    raise GoDiagnostic(expr.token, f"invalid conversion from {arg.type} to {name}")
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
            if left.type in ("untyped int", "untyped bool") and right.type not in ("untyped int", "untyped bool"):
                coerce(left, right.type)
            elif right.type in ("untyped int", "untyped bool") and left.type not in ("untyped int", "untyped bool"):
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
