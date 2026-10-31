"""Typed Go scalar semantics, independent of IR construction.

Go int is 64-bit on this frontend's admitted targets, not a Python integer.
Untyped constants are evaluated exactly before representability checks. Heap,
concurrency and recoverable-panic operations are explicit capability gaps.
"""

from dataclasses import dataclass
import re
import unicodedata

from pcc.frontends.go.syntax import ArraySyntax, Expr, GoDiagnostic, StructSyntax, TypeName
from pcc.frontends.go.records import DefinedType, Field, StructType, underlying, is_named, same_underlying
from pcc.frontends.go.types import (
    ArrayType, Results, TYPE_NAMES, TYPES, result_types, integer_bits, integer_bounds, integer_signed, is_integer, is_string,
)


PREDECLARED = frozenset(
    "true false nil iota append cap clear close complex copy "
    "delete imag len make max min new panic print println real recover error any comparable float32 float64 complex64 complex128".split()
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
    iota: int | None = None


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
    return underlying(dtype) in ("bool", "untyped bool")


def default_type(dtype):
    return {"untyped int": "int", "untyped bool": "bool", "untyped string": "string"}.get(dtype, dtype)


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
            (expr.type == "untyped bool" and is_boolean(dtype)) or
            (expr.type == "untyped string" and is_string(dtype))):
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
    if same_underlying(expr.type, dtype) and not (is_named(expr.type) and is_named(dtype)):
        expr.type = dtype
        return
    raise GoDiagnostic(expr.token, f"cannot use {expr.type} as {dtype}")


class Analyzer:
    def __init__(self, program, *, imports=None, require_main=True, package_path=None):
        self.program = program
        self.imports = imports or {}
        self.require_main = require_main
        self.package_path = package_path
        self.functions = {}
        self.types = {}
        self.globals = {}
        self.scopes = []
        self.function = None
        self.loop_depth = 0
        self.iota = None
        self.type_depth = 0

    def analyze(self):
        from pcc.frontends.go.lowering import function_symbol

        for declaration in self.program.types:
            self.check_name(declaration.token)
            name = declaration.token.text
            if name in ("_", "init"):
                raise GoDiagnostic(declaration.token, "blank or init package type declarations are not implemented")
            if name in self.types:
                raise GoDiagnostic(declaration.token, "duplicate package declaration " + name)
            self.types[name] = declaration
        for function in self.program.functions:
            self.check_name(function.token)
            function.abi_symbol = function_symbol(function.token.text, self.package_path)
            name = function.token.text
            if name == "_":
                raise GoDiagnostic(function.token, "blank function declarations are not supported")
            if name in self.functions or name in self.types:
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
                if token.text == "_" and declaration.kind != "const":
                    raise GoDiagnostic(token, "blank package variables are not supported")
                if token.text == "init":
                    raise GoDiagnostic(token, "package name init is reserved for init function declarations")
                if token.text in self.globals or token.text in self.functions or token.text in self.types:
                    raise GoDiagnostic(token, "duplicate package declaration " + token.text)
                symbol = Symbol(token, declaration.type, declaration=expr, resolved=False,
                                package_variable=declaration.kind == "var",
                                abi_symbol=function_symbol(token.text, self.package_path), iota=declaration.iota)
                if token.text != "_":
                    self.globals[token.text] = symbol
                declaration.symbols.append(symbol)
        # The package block spans every source file. An import's file-block
        # name may not collide with any package declaration, including one in
        # a sibling file (Go specification: Declarations and scope).
        package_names = set(self.functions) | set(self.globals) | set(self.types)
        for file_imports in self.imports.values():
            for alias, binding in file_imports.items():
                if alias in package_names:
                    raise GoDiagnostic(binding.token, "import name conflicts with package declaration: " + alias)
        for declaration in self.program.types:
            self.resolve_type_declaration(declaration)
        for declaration in self.program.constants:
            for symbol in declaration.symbols:
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

    def resolve_type_declaration(self, declaration):
        if declaration.resolved is not None:
            return declaration.resolved
        if declaration.resolving:
            raise GoDiagnostic(declaration.token, "invalid recursive type or alias cycle")
        if self.type_depth >= 32:
            raise GoDiagnostic(declaration.token, "type declaration expansion exceeds implementation limit (32 levels)")
        declaration.resolving = True
        self.type_depth += 1
        try:
            dtype = self.resolve_type(declaration.type)
            declaration.resolved = (dtype if declaration.alias else
                                    DefinedType(declaration.token.text, self.package_path or "", underlying(dtype)))
        finally:
            declaration.resolving = False
            self.type_depth -= 1
        return declaration.resolved

    def resolve_type(self, dtype):
        if isinstance(dtype, Results):
            return Results(tuple(self.resolve_type(item) for item in dtype.elements))
        if isinstance(dtype, TypeName):
            if dtype.qualifier is not None:
                owner = self.imported(dtype.qualifier, dtype.token)
            else:
                if any(dtype.token.text in scope for scope in self.scopes):
                    raise GoDiagnostic(dtype.token, "identifier is not a type: " + dtype.token.text)
                owner = self
            declaration = owner.types.get(dtype.token.text)
            if declaration is None:
                raise GoDiagnostic(dtype.token, "undefined type " + dtype.token.text)
            return owner.resolve_type_declaration(declaration)
        if isinstance(dtype, StructSyntax):
            if len(dtype.fields) > 256:
                raise GoDiagnostic(dtype.token, "struct exceeds implementation limit (256 fields)")
            fields, names = [], set()
            for token, field_type in dtype.fields:
                if token.text == "_":
                    raise GoDiagnostic(token, "blank struct fields are not implemented")
                if token.text in names:
                    raise GoDiagnostic(token, "duplicate struct field " + token.text)
                names.add(token.text)
                package = "" if unicodedata.category(token.text[0]) == "Lu" else (self.package_path or "<main>")
                fields.append(Field(token.text, self.resolve_type(field_type), package))
            result = StructType(tuple(fields))
            pending, count = [result], 0
            while pending:
                item = underlying(pending.pop())
                count += item.count if isinstance(item, ArrayType) else 1
                if count > 4096:
                    raise GoDiagnostic(dtype.token, "record layout exceeds implementation limit (4096 scalar slots)")
                if isinstance(item, StructType):
                    pending.extend(field.type for field in item.fields)
            return result
        if not isinstance(dtype, ArraySyntax):
            return dtype
        element = self.resolve_type(dtype.element)
        if isinstance(underlying(element), ArrayType) or is_string(element):
            raise GoDiagnostic(dtype.token, "nested arrays and string-element arrays are not implemented")
        if not is_integer(element) and not is_boolean(element):
            raise GoDiagnostic(dtype.token, "arrays of aggregate elements are not implemented")
        if dtype.length is None:
            raise GoDiagnostic(dtype.token, "array length inference requires a composite literal")
        count = self.array_index_constant(dtype.length, "array length")
        if count > 4096:
            raise GoDiagnostic(dtype.token, "array exceeds implementation limit (4096 elements)")
        return ArrayType(count, element)

    def static_initializer(self, expr):
        if expr.constant is not None:
            return expr.constant
        if expr.kind in ("array", "struct"):
            dtype = underlying(expr.type)
            count = dtype.count if isinstance(dtype, ArrayType) else len(dtype.fields)
            values = [None] * count
            for index, item in zip(expr.indices, expr.args):
                values[index] = self.static_initializer(item)
            return tuple(values)
        if expr.kind == "call" and expr.symbol is None and same_underlying(expr.type, expr.args[0].type):
            return self.static_initializer(expr.args[0])
        raise GoDiagnostic(expr.token, "nonconstant package initializer requires shared-runtime initialization support")

    def array_index_constant(self, expr, label):
        self.expression(expr)
        if not is_integer(expr.type) or expr.constant is None:
            raise GoDiagnostic(expr.token, label + " must be an integer constant")
        if not 0 <= expr.constant <= (1 << 63) - 1:
            raise GoDiagnostic(expr.token, label + " must be nonnegative and representable as int")
        return expr.constant

    def resolve_constant(self, symbol):
        previous = self.iota
        self.iota = symbol.iota
        try:
            self._resolve_constant(symbol)
        finally:
            self.iota = previous

    def _resolve_constant(self, symbol):
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
                symbol.initializer = self.static_initializer(expr)
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
        for token, dtype in zip(function.result_names, result_types(function.result)):
            if token is None:
                continue
            self.check_name(token)
            if token.text != "_" and token.text in self.scopes[-1]:
                raise GoDiagnostic(token, "duplicate parameter or result " + token.text)
            symbol = Symbol(token, dtype, parameter=True)
            if token.text != "_":
                self.scopes[-1][token.text] = symbol
            function.result_symbols.append(symbol)
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
        if expr.type == "untyped bool":
            coerce(expr, "bool")

    def values(self, expressions):
        for expr in expressions:
            self.expression(expr)
        if len(expressions) == 1 and isinstance(expressions[0].type, Results):
            call = expressions[0]
            if call.kind != "call":
                raise GoDiagnostic(call.token, "multiple values require a function call")
            return [Expr("result", call.token, index, [call], type=dtype)
                    for index, dtype in enumerate(call.type.elements)]
        for expr in expressions:
            if isinstance(expr.type, Results):
                raise GoDiagnostic(expr.token, "multiple-value call in single-value context")
        return expressions

    def statement(self, statement):
        kind = statement.kind
        if kind == "=":
            for target in statement.targets:
                self.assignment_target(target, read=False)
            values = self.values(statement.exprs)
            if len(statement.targets) != len(values):
                raise GoDiagnostic(statement.token, "assignment count mismatch")
            for target, expr in zip(statement.targets, values):
                if expr.type == "void":
                    raise GoDiagnostic(expr.token, "void function call used as a value")
                coerce(expr, default_type(expr.type) if target.value == "_" else target.type)
            return
        if kind in ("var", "const", ":="):
            previous = self.iota
            self.iota = statement.iota if kind == "const" else None
            try:
                statement.type = self.resolve_type(statement.type)
                values = self.values(statement.exprs)
            finally:
                self.iota = previous
            if values and len(statement.names) != len(values):
                raise GoDiagnostic(statement.token, "assignment count mismatch")
            for expr in values:
                if expr.type == "void":
                    raise GoDiagnostic(expr.token, "void function call used as a value")
            if kind in ("var", "const", ":="):
                names = [token.text for token in statement.names if token.text != "_"]
                if len(names) != len(set(names)):
                    raise GoDiagnostic(statement.token, "duplicate declaration name")
            new_count = 0
            for index, token in enumerate(statement.names):
                expr = values[index] if values else None
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
            expected = result_types(self.function.result)
            if not statement.exprs and expected:
                if len(self.function.result_symbols) != len(expected):
                    raise GoDiagnostic(statement.token, "return requires explicit result values")
                for symbol in self.function.result_symbols:
                    if symbol.token.text != "_" and self.lookup(symbol.token) is not symbol:
                        raise GoDiagnostic(statement.token, "named result is shadowed at bare return: " + symbol.token.text)
            else:
                values = self.values(statement.exprs)
                if len(values) != len(expected):
                    message = "void function cannot return a value" if not expected else "return requires " + str(len(expected)) + " result value(s)"
                    raise GoDiagnostic(statement.token, message)
                for value, dtype in zip(values, expected):
                    coerce(value, dtype)
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
        if kind in ("array", "composite"):
            syntax = expr.value
            if isinstance(syntax, ArraySyntax) and syntax.length is None:
                element = self.resolve_type(syntax.element)
                if not is_integer(element) and not is_boolean(element):
                    raise GoDiagnostic(syntax.token, "nested arrays and non-scalar array elements are not implemented")
                dtype = None
            else:
                expr.type = self.resolve_type(syntax)
                dtype = underlying(expr.type)
            if isinstance(dtype, StructType):
                expr.kind = "struct"
                keyed = any(key is not None for key in expr.keys)
                if keyed and any(key is None for key in expr.keys):
                    raise GoDiagnostic(expr.token, "cannot mix keyed and unkeyed struct elements")
                if not keyed and expr.args and len(expr.args) != len(dtype.fields):
                    raise GoDiagnostic(expr.token, "unkeyed struct literal requires one value for every field")
                indices, used = [], set()
                names = [field.name for field in dtype.fields]
                for position, (key, item) in enumerate(zip(expr.keys, expr.args)):
                    if key is not None:
                        if key.kind != "name" or key.parenthesized or key.value not in names:
                            raise GoDiagnostic(key.token, "unknown or invalid struct literal field")
                        index = names.index(key.value)
                    else:
                        index = position
                    if index in used:
                        raise GoDiagnostic(item.token, "duplicate struct literal field")
                    used.add(index)
                    field = dtype.fields[index]
                    self.field_access(field, item.token)
                    self.expression(item)
                    coerce(item, field.type)
                    indices.append(index)
                expr.indices = indices
            else:
                if dtype is not None and not isinstance(dtype, ArrayType):
                    raise GoDiagnostic(expr.token, "composite literal requires an array or struct type")
                expr.kind = "array"
                indices, next_index, used = [], 0, set()
                for key, item in zip(expr.keys, expr.args):
                    index = next_index if key is None else self.array_index_constant(key, "array literal index")
                    if index in used:
                        raise GoDiagnostic(item.token, "duplicate array literal index")
                    used.add(index)
                    indices.append(index)
                    next_index = index + 1
                if dtype is None:
                    count = max(indices, default=-1) + 1
                    if count > 4096:
                        raise GoDiagnostic(syntax.token, "array exceeds implementation limit (4096 elements)")
                    expr.type = dtype = ArrayType(count, element)
                expr.indices = indices
                for index, item in zip(indices, expr.args):
                    if index >= dtype.count:
                        raise GoDiagnostic(item.token, "array literal index out of bounds")
                    self.expression(item)
                    coerce(item, dtype.element)
            if self.function is not None:
                self.function.temporaries.append(expr)
            return
        if kind == "index":
            base, index = expr.args
            self.expression(base)
            self.expression(index)
            if is_string(base.type):
                if not is_integer(index.type):
                    raise GoDiagnostic(index.token, "string index must be an integer")
                if index.constant is not None and index.constant < 0:
                    raise GoDiagnostic(index.token, "string index must be nonnegative")
                if base.constant is None:
                    raise GoDiagnostic(index.token, "unproven string bounds require shared-runtime bounds-panic support")
                if index.constant is not None:
                    value = self.array_index_constant(index, "string index")
                    if value >= len(base.constant):
                        raise GoDiagnostic(index.token, "string index out of bounds")
                else:
                    upper = self.index_upper_bound(index)
                    if upper is None or upper >= len(base.constant):
                        raise GoDiagnostic(index.token, "unproven string bounds require shared-runtime bounds-panic support")
                coerce(index, default_type(index.type))
                # A string index is a byte value, but is not a Go constant
                # expression even when both operands happen to be constant.
                expr.type = "uint8"
                return
            array_type = underlying(base.type)
            if not isinstance(array_type, ArrayType):
                raise GoDiagnostic(expr.token, "indexing requires an array or string (slices/maps are not implemented)")
            if not is_integer(index.type):
                raise GoDiagnostic(index.token, "array index must be an integer")
            if index.constant is not None:
                value = self.array_index_constant(index, "array index")
                if value >= array_type.count:
                    raise GoDiagnostic(index.token, "array index out of bounds")
            else:
                upper = self.index_upper_bound(index)
                if upper is None or upper >= array_type.count:
                    raise GoDiagnostic(index.token, "unproven array bounds require shared-runtime bounds-panic support")
            coerce(index, default_type(index.type))
            expr.type, expr.addressable = array_type.element, base.addressable
            if not base.addressable and self.function is not None:
                self.function.temporaries.append(base)
            return
        if kind == "string":
            from pcc.frontends.go.strings import literal_bytes
            expr.type, expr.constant = "untyped string", literal_bytes(expr.token)
            return
        if kind == "integer":
            expr.type, expr.constant = "untyped int", integer_literal(expr)
            return
        if kind == "name":
            if expr.value == "iota":
                if self.iota is None:
                    raise GoDiagnostic(expr.token, "iota is only defined within a constant declaration")
                expr.type, expr.constant = "untyped int", self.iota
            elif expr.value in ("true", "false"):
                expr.type, expr.constant = "untyped bool", expr.value == "true"
            else:
                symbol = self.lookup(expr.token)
                symbol.used = True
                expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
                expr.addressable = symbol.constant is None
            return
        if kind == "selector":
            base = expr.args[0]
            bindings = self.imports.get(expr.token.filename, {})
            local = base.kind == "name" and any(base.value in scope for scope in self.scopes)
            if base.kind == "name" and base.value in bindings and not local:
                if base.parenthesized:
                    raise GoDiagnostic(base.token, "selector requires an unparenthesized imported package name")
                imported = self.imported(base.value, expr.token)
                symbol = imported.globals.get(expr.value)
                if symbol is None:
                    raise GoDiagnostic(expr.token, "undefined imported constant or variable " + expr.value)
                imported.resolve_constant(symbol)
                expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
                expr.addressable = symbol.constant is None
                return
            if (base.kind == "name" and not local and base.value not in self.globals
                    and base.value not in self.types):
                raise GoDiagnostic(base.token, "undefined imported package " + base.value)
            self.expression(base)
            dtype = underlying(base.type)
            if not isinstance(dtype, StructType):
                raise GoDiagnostic(expr.token, "field selector requires a struct value")
            names = [field.name for field in dtype.fields]
            if expr.value not in names:
                raise GoDiagnostic(expr.token, "unknown struct field " + expr.value)
            index = names.index(expr.value)
            field = dtype.fields[index]
            self.field_access(field, expr.token)
            expr.kind, expr.indices = "field", [index]
            expr.type, expr.addressable = field.type, base.addressable
            return
        if kind == "call":
            name = expr.value
            if expr.qualifier is not None:
                owner = self.imported(expr.qualifier, expr.token)
                if name in owner.types:
                    self.conversion(expr, owner.resolve_type_declaration(owner.types[name]))
                    return
                function = owner.functions.get(name)
                if function is None:
                    raise GoDiagnostic(expr.token, "undefined imported function " + name)
                self.call(expr, function)
                return
            if name == "len":
                if len(expr.args) != 1:
                    raise GoDiagnostic(expr.token, "len requires one argument")
                arg = expr.args[0]
                self.expression(arg)
                if not is_string(arg.type):
                    raise GoDiagnostic(expr.token, "len currently supports strings; array/slice/map length is not implemented")
                expr.kind = "string_len"
                expr.type = "int"  # len is typed int even when its value is constant.
                expr.constant = len(arg.constant) if arg.constant is not None else None
                return
            # Values in a lexical scope shadow package type/function names.
            if any(name in scope for scope in self.scopes) or name in self.globals:
                raise GoDiagnostic(expr.token, "cannot call non-function " + name)
            if name in TYPE_NAMES or name in self.types:
                target = TYPE_NAMES[name] if name in TYPE_NAMES else self.resolve_type_declaration(self.types[name])
                self.conversion(expr, target)
                return
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

    def field_access(self, field, token):
        if field.package and field.package != (self.package_path or "<main>"):
            raise GoDiagnostic(token, "cannot access unexported struct field " + field.name)

    def conversion(self, expr, target):
        if len(expr.args) != 1:
            raise GoDiagnostic(expr.token, "conversion requires one argument")
        arg = expr.args[0]
        self.expression(arg)
        if is_string(target) and is_integer(arg.type):
            if arg.constant is None:
                raise GoDiagnostic(expr.token, "integer-to-string conversion requires shared-runtime allocation support")
            codepoint = arg.constant
            if codepoint < 0 or codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
                codepoint = 0xfffd
            expr.type, expr.constant = target, chr(codepoint).encode("utf-8")
            return
        if not (same_underlying(target, arg.type) or
                is_integer(target) and is_integer(arg.type) or
                is_boolean(target) and is_boolean(arg.type) or
                is_string(target) and is_string(arg.type)):
            raise GoDiagnostic(expr.token, f"invalid conversion from {arg.type} to {target}")
        if arg.constant is None and arg.type == "untyped int":
            coerce(arg, target)
        expr.type, expr.constant = target, arg.constant
        check_representable(expr)

    def call(self, expr, function):
        values = self.values(expr.args)
        if len(values) != len(function.params):
            raise GoDiagnostic(expr.token, "argument count mismatch for " + expr.value)
        for arg, (_, dtype) in zip(values, function.params):
            coerce(arg, dtype)
        expr.type, expr.symbol = function.result, function

    def binary(self, expr):
        left, right = expr.args
        op = expr.value
        if is_string(left.type) or is_string(right.type):
            if not (is_string(left.type) and is_string(right.type)):
                raise GoDiagnostic(expr.token, "mismatched string operand types")
            if left.type != right.type:
                if left.type == "untyped string":
                    coerce(left, right.type)
                elif right.type == "untyped string":
                    coerce(right, left.type)
                else:
                    raise GoDiagnostic(expr.token, "mismatched string operand types")
            if op not in ("+", "==", "!=", "<", "<=", ">", ">="):
                raise GoDiagnostic(expr.token, "unsupported string operator " + op)
            if left.constant is None or right.constant is None:
                if op == "+":
                    raise GoDiagnostic(expr.token, "dynamic string concatenation requires shared-runtime allocation support")
                expr.kind, expr.type = "string_compare", "untyped bool"
                return
            a, b = left.constant, right.constant
            if op == "+":
                from pcc.frontends.go.strings import MAX_STRING_BYTES
                if len(a) + len(b) > MAX_STRING_BYTES:
                    raise GoDiagnostic(expr.token, "string exceeds implementation limit (1 MiB)")
                expr.type, expr.constant = left.type, a + b
            else:
                expr.type = "untyped bool"
                expr.constant = {"==": a == b, "!=": a != b, "<": a < b,
                                 "<=": a <= b, ">": a > b, ">=": a >= b}[op]
            return
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
