"""Typed Go scalar semantics, independent of IR construction.

Go int is 64-bit on this frontend's admitted targets, not a Python integer.
Untyped constants are evaluated exactly before representability checks. Heap,
concurrency and recoverable-panic operations are explicit capability gaps.
"""

from dataclasses import dataclass
import re
import unicodedata

from pcc.frontends.go.diagnostics import UnsupportedGo
from pcc.frontends.go.branches import Branches, unlabeled
from pcc.frontends.go.interface_methods import (
    basic_interface, interface_type, method_set, require_implements,
)
from pcc.frontends.go.interface_values import (
    boxed_interface_plan, interface_storage_plan, nil_interface_plan, transferred_interface_plan,
)
from pcc.frontends.go.generics import GenericSession, TypeParameter, comparable, contains_parameter
from pcc.frontends.go.syntax import (
    ArraySyntax, Expr, GoDiagnostic, InterfaceMethodSyntax, InterfaceSyntax,
    PointerSyntax, SliceSyntax, Stmt, StructSyntax, TypeName,
)
from pcc.frontends.go.records import (
    DefinedType, Field, InterfaceMethod, MethodSignature, StructType,
    underlying, is_named, same_underlying,
)
from pcc.frontends.go.types import (
    ArrayType, PointerType, Results, SliceType, TYPE_NAMES, TYPES,
    result_types, integer_bits, integer_bounds, integer_signed, is_integer, is_string,
    is_error_interface, is_untyped_integer, same_underlying_ignoring_tags,
)


PREDECLARED_FUNCTIONS = frozenset(
    "append cap clear close complex copy delete imag len make max min new "
    "panic print println real recover".split()
)
PREDECLARED = PREDECLARED_FUNCTIONS | frozenset(
    "true false nil iota error any comparable float32 float64 complex64 complex128".split()
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
    depth: int = 0
    iteration_binding: bool = False
    # The owned runtime object a //go:linkname directive bound, if any.
    linkname: object = None


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
                    raise UnsupportedGo(expr.token, 'go.unsupported.limit-constant-precision', "integer constant exceeds implementation precision limit (4096 bits)")
            return value
    raise GoDiagnostic(expr.token, "invalid or unsupported integer literal " + repr(text))


def is_boolean(dtype):
    return underlying(dtype) in ("bool", "untyped bool")


def checked_array_type(token, count, element):
    """Abstract elements are opaque; every concrete instance rechecks layout.

    Value-record elements reuse the existing aggregate ABI. Their entire
    storage closure is checked even for an empty array or an unused result;
    an array cannot hide a pointer/interface/slice-bearing record.
    """
    if not isinstance(element, TypeParameter):
        if isinstance(underlying(element), PointerType):
            raise UnsupportedGo(token, 'go.unsupported.pointer-storage',
                                "pointer-bearing arrays require aggregate lifetime/storage analysis")
        if isinstance(underlying(element), ArrayType) or is_string(element):
            raise UnsupportedGo(token, 'go.unsupported.array-element-types',
                                "nested arrays and string-element arrays are not implemented")
        if (not isinstance(underlying(element), StructType) and
                not is_integer(element) and not is_boolean(element)):
            raise UnsupportedGo(token, 'go.unsupported.array-element-types',
                                "arrays require scalar or admitted value-record elements")
    if count > 4096:
        raise UnsupportedGo(token, 'go.unsupported.limit-array-elements',
                            "array exceeds implementation limit (4096 elements)")
    result = ArrayType(count, element)
    require_bounded_value_layout(result, token)
    if isinstance(underlying(element), StructType):
        require_value_type(element, token)
    return result


def require_bounded_value_layout(dtype, token):
    """Bound expanded values and retained element types without expanding them.

    Keep the existing 4096-slot record budget (including record nodes), and
    multiply it through arrays. Counting only the outer array length would
    otherwise admit arbitrarily large arrays of records containing arrays.
    Empty arrays still retain their element type in semantic checks and IR, so
    charge it once even when they have no runtime storage. Memoize by identity
    rather than structural hashing, which would itself expand a shared DAG.
    Storage eligibility is checked separately, including zero-sized elements.
    """
    costs = {}

    def slots(value):
        value = underlying(value)
        key = id(value)
        if key in costs:
            return costs[key]
        if isinstance(value, ArrayType):
            count = max(1, value.count) * slots(value.element)
        elif isinstance(value, StructType):
            count = 1
            for field in value.fields:
                count += slots(field.type)
                if count > 4096:
                    break
        else:
            count = 1
        if count > 4096:
            raise UnsupportedGo(token, 'go.unsupported.limit-record-layout',
                                "record layout exceeds implementation limit (4096 scalar slots)")
        costs[key] = count
        return count

    slots(dtype)


def default_type(dtype):
    return {"untyped int": "int", "untyped rune": "int32",
            "untyped bool": "bool", "untyped string": "string"}.get(dtype, dtype)


def has_evaluated_call(expr):
    """Typed constant builtins/conversions do not count as runtime calls.

    Receive expressions are not admitted by this frontend. Do not skip calls
    hidden in a method receiver, array initializer, conversion or index.
    """
    if expr.constant is not None:
        return False
    if expr.kind == "call" and expr.symbol is not None:
        return True
    if expr.kind == "minmax":
        # A nonconstant builtin invocation counts for the array len/cap
        # unevaluated-operand rule, even when its inputs are only variables.
        return True
    return (any(has_evaluated_call(arg) for arg in expr.args) or
            (expr.receiver is not None and has_evaluated_call(expr.receiver)))


def check_representable(expr):
    if expr.constant is None:
        return
    if is_integer(expr.type):
        if abs(expr.constant).bit_length() > MAX_CONSTANT_BITS:
            raise UnsupportedGo(expr.token, 'go.unsupported.limit-constant-precision', "constant exceeds implementation precision limit (4096 bits)")
        if not is_untyped_integer(expr.type):
            minimum, maximum = integer_bounds(expr.type)
            if not minimum <= expr.constant <= maximum:
                raise GoDiagnostic(expr.token, f"constant overflows {expr.type}")


def coerce(expr, dtype):
    if interface_type(dtype) is not None:
        # Only the existing exact Error() string domain has nil-only transport.
        # An equivalent basic declaration does not admit any concrete payload.
        if is_error_interface(dtype) and (expr.type == "untyped nil" or is_error_interface(expr.type)):
            expr.type = dtype
            return
        if expr.type == "untyped nil":
            plan = nil_interface_plan(dtype)
            message = "general interface values require an owned runtime storage contract"
        elif interface_type(expr.type) is not None:
            plan = transferred_interface_plan(expr.type, dtype, expr.token)
            message = "interface conversion requires preserved dynamic identity and runtime storage support"
        else:
            if expr.type == "void":
                raise GoDiagnostic(expr.token, "void function call used as a value")
            if contains_parameter(expr.type):
                require_implements(expr.type, dtype, expr.token)
                raise UnsupportedGo(expr.token, 'go.unsupported.generic-boundaries',
                                    "type-parameter boxing requires abstract dynamic-value checking")
            source_type = default_type(expr.type)
            if source_type != expr.type:
                coerce(expr, source_type)
            plan = boxed_interface_plan(expr.type, dtype, expr.token)
            label = "error" if is_error_interface(dtype) else "interface"
            message = "concrete " + label + " values require interface boxing and runtime identity support"
        failure = UnsupportedGo(expr.token, 'go.unsupported.interface-types', message)
        failure.managed_plan = plan
        raise failure
    if expr.type == "untyped nil":
        if not isinstance(underlying(dtype), PointerType):
            raise GoDiagnostic(expr.token, "cannot use untyped nil as " + str(dtype))
        expr.type = dtype
        return
    if expr.type == dtype:
        check_representable(expr)
        return
    if ((is_untyped_integer(expr.type) and is_integer(dtype)) or
            (expr.type == "untyped bool" and is_boolean(dtype)) or
            (expr.type == "untyped string" and is_string(dtype))):
        expr.type = dtype
        # Nonconstant shifts with an untyped left operand acquire their type
        # from the surrounding assignment/conversion/expression, before shift.
        if expr.constant is None and is_integer(dtype) and expr.kind in ("unary", "binary"):
            arguments = expr.args[:1] if expr.value in ("<<", ">>") else expr.args
            for argument in arguments:
                if is_untyped_integer(argument.type):
                    coerce(argument, dtype)
        check_representable(expr)
        return
    if same_underlying(expr.type, dtype) and not (is_named(expr.type) and is_named(dtype)):
        expr.type = dtype
        return
    raise GoDiagnostic(expr.token, f"cannot use {expr.type} as {dtype}")


def require_value_type(dtype, token, *, declaration=False):
    """Separate complete semantic metadata from currently admitted storage.

    Declaration-only basic interfaces have no allocation or IR representation.
    Signature-only shapes must pass the same recursive storage checks before
    entering an emitted signature, variable or temporary, including empty arrays.
    """
    if isinstance(dtype, Results):
        for item in dtype.elements:
            require_value_type(item, token)
        return
    if interface_type(dtype) is not None:
        if not declaration and not is_error_interface(dtype):
            failure = UnsupportedGo(token, 'go.unsupported.interface-types',
                                    "general interface values require an owned runtime storage contract")
            failure.managed_plan = interface_storage_plan(dtype)
            raise failure
        return
    base = underlying(dtype)
    if isinstance(base, SliceType):
        raise UnsupportedGo(token, 'go.unsupported.slice-storage',
                            "signature slice identity does not supply slice storage")
    if isinstance(base, ArrayType):
        checked_array_type(token, base.count, base.element)
    elif isinstance(base, PointerType):
        if interface_type(base.element) is not None:
            raise UnsupportedGo(token, 'go.unsupported.interface-types',
                                "pointers to interface storage require a runtime storage contract")
        if isinstance(underlying(base.element), PointerType):
            raise UnsupportedGo(token, 'go.unsupported.pointer-storage',
                                "pointer-bearing pointees require a recursive lifetime/storage contract")
        require_value_type(base.element, token)
    elif isinstance(base, StructType):
        # Bound shared record/empty-array type graphs before recursively
        # revisiting their storage closures or constructing expanded IR types.
        require_bounded_value_layout(base, token)
        for field in base.fields:
            if isinstance(underlying(field.type), PointerType):
                raise UnsupportedGo(token, 'go.unsupported.pointer-storage',
                                    "pointer-bearing records require aggregate lifetime/storage analysis")
            if interface_type(field.type) is not None:
                raise UnsupportedGo(token, 'go.unsupported.interface-types',
                                    "interface-bearing records require a runtime storage contract")
            require_value_type(field.type, token)


def validate_value_types(program):
    """Last semantic boundary over emitted carriers, excluding type declarations."""
    seen = set()

    def expression(expr):
        if expr is None or id(expr) in seen:
            return
        seen.add(id(expr))
        # A type operand denotes metadata, not an evaluated value.
        if expr.kind != "type":
            require_value_type(expr.type, expr.token)
        for child in expr.args:
            expression(child)
        expression(expr.receiver)
        expression(expr.callee)

    def statement(stmt):
        for symbol in stmt.symbols:
            require_value_type(symbol.type, symbol.token)
        for expr in stmt.exprs + stmt.targets:
            expression(expr)
        for child in stmt.body + (stmt.otherwise or []) + stmt.cases:
            statement(child)
        if stmt.init is not None:
            statement(stmt.init)
        if stmt.post is not None:
            statement(stmt.post)

    for function in program.functions:
        for _, dtype in function.params:
            require_value_type(dtype, function.token)
        require_value_type(function.result, function.token)
        for symbol in function.locals + function.result_symbols:
            require_value_type(symbol.type, symbol.token)
        for expr in function.temporaries:
            expression(expr)
        for stmt in function.body:
            statement(stmt)
    for declaration in program.variables:
        statement(declaration)


def validate_containment_cycles(declarations):
    """Aliases may anchor signature names, never infinitely expand a value.

    Pointers and interface method signatures break memory containment. Interface
    embeddings have their own completion walk in resolve_interface; method
    signatures must not be confused with those embedding edges.
    """
    complete, active = set(), set()

    def visit(dtype, token):
        if not isinstance(dtype, (DefinedType, ArrayType, StructType)):
            return
        identity = id(dtype)
        if identity in active:
            raise GoDiagnostic(token, "invalid recursive type or alias cycle")
        if identity in complete:
            return
        active.add(identity)
        if isinstance(dtype, DefinedType):
            if dtype.underlying is None:
                raise AssertionError("uncompleted Go declaration reached containment checking")
            visit(dtype.underlying, token)
        elif isinstance(dtype, ArrayType):
            visit(dtype.element, token)
        else:
            for field in dtype.fields:
                visit(field.type, token)
        active.remove(identity)
        complete.add(identity)

    for declaration in declarations:
        visit(declaration.resolved, declaration.token)


class Analyzer:
    def __init__(self, program, *, imports=None, require_main=True, package_path=None, generics=None):
        self.program = program
        self.imports = imports or {}
        self.dot_imports = {}
        self.require_main = require_main
        self.package_path = package_path
        self.generics = generics if generics is not None else GenericSession()
        self.type_bindings = {}
        self.abstract_function = False
        self.functions = {}
        self.types = {}
        self.globals = {}
        self.scopes = []
        self.function = None
        self.loop_depth = 0
        self.break_depth = 0
        self.allowed_fallthrough = None
        self.iota = None
        self.type_depth = 0
        self.pointer_type_depth = 0
        self.interface_signature_depth = 0
        self.type_stack = []
        self.iteration_binding = False

    def analyze(self):
        from pcc.frontends.go.lowering import function_symbol

        for declaration in self.program.types:
            self.check_name(declaration.token)
            name = declaration.token.text
            if name == "init":
                raise GoDiagnostic(declaration.token, "init is reserved for package initialization function declarations")
            if name != "_" and name in self.types:
                raise GoDiagnostic(declaration.token, "duplicate package declaration " + name)
            # A blank type declares no source binding, but its RHS must still
            # pass the same resolution, cycle and capability checks below.
            # Distinct definition objects retain nominal identity; aliases
            # instead keep their canonical target without reserving "_".
            if name != "_":
                self.types[name] = declaration
            if not declaration.alias:
                declaration.identity = DefinedType(name, self.package_path or "")
        for index, function in enumerate(self.program.functions):
            if function.receiver is not None:
                continue
            self.check_name(function.token)
            function.abi_symbol = function_symbol(function.token.text, self.package_path)
            name = function.token.text
            if name == "_":
                # Blank functions have no source binding, but their signatures
                # and bodies still pass every ordinary check below. Give each
                # declaration a distinct owner for generics, pointer flow and
                # IR; the suffix cannot collide with a hex-encoded Go name.
                function.abi_symbol += "_blank_" + str(index)
                continue
            if name in self.functions or name in self.types:
                raise GoDiagnostic(function.token, "duplicate package declaration " + name)
            if name == "init":
                raise UnsupportedGo(function.token, 'go.unsupported.package-initialization', "init functions require shared-runtime initialization support")
            self.functions[name] = function
        for declaration in self.program.constants + self.program.variables:
            if declaration.exprs and len(declaration.names) != len(declaration.exprs):
                raise GoDiagnostic(declaration.token, "constant declaration count mismatch")
            for index, token in enumerate(declaration.names):
                expr = declaration.exprs[index] if declaration.exprs else None
                self.check_name(token)
                if token.text == "_" and declaration.kind != "const":
                    raise UnsupportedGo(token, 'go.unsupported.blank-package-variables', "blank package variables are not supported")
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
        self.prepare_dot_imports(package_names)
        for declaration in self.program.types:
            self.resolve_type_declaration(declaration)
        validate_containment_cycles(self.program.types)
        for declaration in self.program.types:
            require_value_type(declaration.resolved, declaration.token, declaration=True)
        self.bind_linknames()
        for declaration in self.program.constants:
            for symbol in declaration.symbols:
                self.resolve_constant(symbol)
        for function in self.program.functions:
            self.type_bindings = self.generics.prepare(self, function)
            function.params = [(token, self.resolve_type(dtype)) for token, dtype in function.params]
            function.result = self.resolve_type(function.result)
            if any(isinstance(underlying(dtype), PointerType)
                   for dtype in result_types(function.result)):
                raise UnsupportedGo(function.token, 'go.unsupported.pointer-signatures',
                                    "pointer results require an escaping lifetime contract")
            if function.receiver is not None:
                token, syntax = function.receiver
                if isinstance(syntax, PointerSyntax):
                    # Receiver base validity precedes the general nested-pointer
                    # storage frontier: **T and *P (P a defined pointer) are not
                    # legal receiver types, rather than unsupported Go methods.
                    base = self.resolve_type(syntax.element)
                    if isinstance(underlying(base), PointerType):
                        raise GoDiagnostic(token, "method receiver base cannot be a defined pointer type")
                    receiver_type = PointerType(base)
                else:
                    receiver_type = self.resolve_type(syntax)
                dtype = receiver_type.element if isinstance(receiver_type, PointerType) else receiver_type
                if isinstance(underlying(dtype), PointerType):
                    raise GoDiagnostic(token, "method receiver base cannot be a defined pointer type")
                if not isinstance(dtype, DefinedType) or dtype.package != (self.package_path or ""):
                    raise GoDiagnostic(token, "method receiver base must be a defined type in the same package")
                name = function.token.text
                if name == "_":
                    raise UnsupportedGo(function.token, 'go.unsupported.blank-method-declarations', "blank method declarations are not implemented")
                if name in dtype.methods:
                    raise GoDiagnostic(function.token, "duplicate method " + name)
                base = underlying(dtype)
                if interface_type(dtype) is not None:
                    raise GoDiagnostic(token, "method receiver base cannot be an interface type")
                if isinstance(base, StructType) and any(field.name == name for field in base.fields):
                    raise GoDiagnostic(function.token, "method name conflicts with struct field " + name)
                dtype.methods[name] = function
                function.receiver = (token, receiver_type)
                function.params.insert(0, (token, receiver_type))
                function.abi_symbol = function_symbol(dtype.name + "." + name, self.package_path)
            for _, dtype in function.params:
                require_value_type(dtype, function.token)
            require_value_type(function.result, function.token)
        self.type_bindings = {}
        for symbol in self.globals.values():
            self.resolve_constant(symbol)
        main = self.functions.get("main")
        if self.require_main:
            if main is None:
                raise GoDiagnostic(self.program.token, "package main requires func main()")
            if main.params or main.result != "void" or main.type_parameters:
                raise GoDiagnostic(main.token, "main must have no parameters and no result")
        for function in self.program.functions:
            self.analyze_function(function)
        for file_imports in self.imports.values():
            for binding in file_imports.values():
                if not binding.used:
                    raise GoDiagnostic(binding.token, "imported and not used: " + binding.alias)
        return self.program

    def imported(self, qualifier, token):
        if any(qualifier in scope for scope in self.scopes) or qualifier in self.type_bindings:
            raise GoDiagnostic(token, "package selector is shadowed by local variable " + qualifier)
        binding = self.imports.get(token.filename, {}).get(qualifier)
        if binding is None:
            raise GoDiagnostic(token, "undefined imported package " + qualifier)
        if not token.text or unicodedata.category(token.text[0]) != "Lu":
            raise GoDiagnostic(token, "cannot access unexported package member " + token.text)
        binding.used = True
        return binding.analyzer

    def prepare_dot_imports(self, package_names):
        """Build each file block without copying declarations into the package.

        Entries point to the existing dependency owner. Nominal types, shared
        globals and generic function instances therefore keep their identity.
        """
        for filename, imports in self.imports.items():
            names = {}
            for binding in imports.values():
                if binding.alias != ".":
                    continue
                owner = binding.analyzer
                exported = set(owner.types) | set(owner.functions) | set(owner.globals)
                for name in sorted(exported):
                    if not name or unicodedata.category(name[0]) != "Lu":
                        continue
                    if name in package_names:
                        raise GoDiagnostic(binding.token, "dot-imported name conflicts with package declaration: " + name)
                    if name in imports:
                        raise GoDiagnostic(binding.token, "dot-imported name conflicts with import name: " + name)
                    if name in names:
                        raise GoDiagnostic(binding.token, "duplicate dot-imported name " + name)
                    names[name] = binding
            self.dot_imports[filename] = names

    def dot_imported(self, token, *, use=True):
        binding = self.dot_imports.get(token.filename, {}).get(token.text)
        if binding is None:
            return None
        if use:
            binding.used = True
        return binding.analyzer

    def check_name(self, token, *, local_value=False):
        # A lexical value can hide a builtin function without changing type or
        # predeclared-constant lookup. Package/type-parameter bindings retain
        # their separate, explicit shadowing boundary.
        if token.text in PREDECLARED and not (local_value and token.text in PREDECLARED_FUNCTIONS):
            raise UnsupportedGo(token, "go.unsupported.predeclared-shadowing", "shadowing predeclared names is not implemented in this binding: " + token.text)

    def complete_named_identity(self, dtype):
        if isinstance(dtype, DefinedType) and dtype.underlying is None:
            declaration = self.types.get(dtype.name)
            if declaration is None or declaration.identity is not dtype:
                raise AssertionError("incomplete foreign Go declaration identity")
            self.resolve_type_declaration(declaration)
        return dtype

    def resolve_type_declaration(self, declaration, *, signature=False):
        # A named reference is a finite signature anchor, not proof that its
        # underlying type or embedded method set has completed successfully.
        if signature and not declaration.alias:
            return declaration.identity
        if declaration.resolved is not None:
            return (declaration.resolved if signature else
                    self.complete_named_identity(declaration.resolved))
        if declaration.resolving:
            start = next(index for index, item in enumerate(self.type_stack) if item is declaration)
            if all(item.alias for item in self.type_stack[start:]):
                raise GoDiagnostic(declaration.token, "invalid recursive type or alias cycle")
            if self.interface_signature_depth > declaration.signature_depth:
                # Named signature references return their canonical identity
                # above. Re-entering completion here instead expands an
                # anonymous interface's method set through itself, including
                # aliases and pointer/slice/array wrappers. The Go compiler
                # rejects this type cycle (issue 56103); it is not a missing
                # runtime representation or deferred valid method set.
                raise GoDiagnostic(declaration.token,
                                   "invalid recursive type: anonymous interface refers to itself")
            if self.pointer_type_depth > declaration.pointer_depth:
                raise UnsupportedGo(declaration.token, 'go.unsupported.pointer-storage',
                                    "recursive pointer types require recursive lifetime/storage analysis")
            raise GoDiagnostic(declaration.token, "invalid recursive type or alias cycle")
        if self.type_depth >= 32:
            raise UnsupportedGo(declaration.token, 'go.unsupported.limit-type-expansion', "type declaration expansion exceeds implementation limit (32 levels)")
        declaration.resolving = True
        declaration.pointer_depth = self.pointer_type_depth
        declaration.signature_depth = self.interface_signature_depth
        self.type_depth += 1
        self.type_stack.append(declaration)
        try:
            # Aliases preserve a canonical target even through a named
            # signature back-reference. Their value layouts are checked only
            # after all package declarations have completed.
            dtype = self.resolve_type(declaration.type, signature=declaration.alias)
            if declaration.alias:
                declaration.resolved = dtype
            else:
                self.complete_named_identity(dtype)
                declaration.identity.underlying = underlying(dtype)
                declaration.resolved = declaration.identity
        finally:
            declaration.resolving = False
            self.type_stack.pop()
            self.type_depth -= 1
        return (declaration.resolved if signature else
                self.complete_named_identity(declaration.resolved))

    def resolve_interface(self, syntax):
        explicit, embedded = [], []
        for element in syntax.elements:
            if isinstance(element, InterfaceMethodSyntax):
                self.interface_signature_depth += 1
                try:
                    parameters = tuple(self.resolve_type(item, signature=True) for item in element.parameters)
                    result = self.resolve_type(element.result, signature=True)
                finally:
                    self.interface_signature_depth -= 1
                if any(contains_parameter(item) for item in parameters + (result,)):
                    raise UnsupportedGo(element.token, 'go.unsupported.generic-boundaries',
                                        "dependent interface signatures require generic interface checking")
                explicit.append(InterfaceMethod(element.token.text, self.package_path or "<main>",
                                                MethodSignature(parameters, result, element.variadic)))
                continue
            resolved, general = [], len(element.terms) != 1
            for approximate, item in element.terms:
                if isinstance(item, TypeName) and item.qualifier is None and item.token.text == "comparable":
                    general = True
                    continue
                dtype = self.resolve_type(item, signature=True)
                self.complete_named_identity(dtype)
                if contains_parameter(dtype):
                    raise GoDiagnostic(element.token, "interface cannot embed a type parameter")
                general = general or approximate or interface_type(dtype) is None
                resolved.append(dtype)
            if general:
                raise UnsupportedGo(element.token, 'go.unsupported.generic-boundaries',
                                    "general interface type sets require constraint resolution")
            embedded.extend(resolved)
        return basic_interface(explicit, embedded, syntax.token)

    def resolve_type(self, dtype, *, signature=False):
        if isinstance(dtype, InterfaceSyntax):
            return self.resolve_interface(dtype)
        if isinstance(dtype, SliceSyntax):
            if not signature:
                raise UnsupportedGo(dtype.token, 'go.unsupported.slice-storage',
                                    "slice types require shared-runtime backing storage support")
            return SliceType(self.resolve_type(dtype.element, signature=True))
        if isinstance(dtype, PointerSyntax):
            # Distinguish valid pointer recursion from invalid recursive value
            # layouts without constructing recursive/untraced storage.
            self.pointer_type_depth += 1
            try:
                element = self.resolve_type(dtype.element, signature=signature)
            finally:
                self.pointer_type_depth -= 1
            if signature:
                return PointerType(element)
            if contains_parameter(element):
                raise UnsupportedGo(dtype.token, 'go.unsupported.generic-boundaries',
                                    'dependent pointer layouts require abstract lifetime checking')
            if interface_type(element) is not None:
                raise UnsupportedGo(dtype.token, 'go.unsupported.interface-types',
                                    "pointers to interface storage require a runtime storage contract")
            if isinstance(underlying(element), PointerType):
                raise UnsupportedGo(dtype.token, 'go.unsupported.pointer-storage',
                                    "pointer-bearing pointees require a recursive lifetime/storage contract")
            return PointerType(element)
        if isinstance(dtype, Results):
            return Results(tuple(self.resolve_type(item, signature=signature) for item in dtype.elements))
        if isinstance(dtype, TypeName):
            if dtype.qualifier is not None:
                owner = self.imported(dtype.qualifier, dtype.token)
            else:
                if any(dtype.token.text in scope for scope in self.scopes):
                    raise GoDiagnostic(dtype.token, "identifier is not a type: " + dtype.token.text)
                if dtype.token.text in self.type_bindings:
                    return self.type_bindings[dtype.token.text]
                owner = self.dot_imported(dtype.token) or self
            declaration = owner.types.get(dtype.token.text)
            if declaration is None:
                raise GoDiagnostic(dtype.token, "undefined type " + dtype.token.text)
            return owner.resolve_type_declaration(declaration, signature=signature)
        if isinstance(dtype, StructSyntax):
            from pcc.frontends.go.strings import literal_bytes

            if len(dtype.fields) > 256:
                raise UnsupportedGo(dtype.token, 'go.unsupported.limit-struct-fields', "struct exceeds implementation limit (256 fields)")
            fields, names = [], set()
            for index, (token, field_type) in enumerate(dtype.fields):
                if token.text == "_":
                    raise UnsupportedGo(token, 'go.unsupported.blank-struct-fields', "blank struct fields are not implemented")
                if token.text in names:
                    raise GoDiagnostic(token, "duplicate struct field " + token.text)
                names.add(token.text)
                package = "" if unicodedata.category(token.text[0]) == "Lu" else (self.package_path or "<main>")
                field_type = self.resolve_type(field_type, signature=signature)
                if contains_parameter(field_type):
                    raise UnsupportedGo(token, 'go.unsupported.generic-boundaries',
                                        'dependent record layouts require generic data checking')
                if not signature and isinstance(underlying(field_type), PointerType):
                    raise UnsupportedGo(token, 'go.unsupported.pointer-storage',
                                        "pointer-bearing records require aggregate lifetime/storage analysis")
                if not signature and interface_type(field_type) is not None:
                    raise UnsupportedGo(token, 'go.unsupported.interface-types',
                                        "interface-bearing records require a runtime storage contract")
                tag = dtype.tags[index] if dtype.tags else None
                fields.append(Field(token.text, field_type, package,
                                    tag=literal_bytes(tag) if tag else b""))
            result = StructType(tuple(fields))
            require_bounded_value_layout(result, dtype.token)
            return result
        if not isinstance(dtype, ArraySyntax):
            return dtype
        element = self.resolve_type(dtype.element, signature=signature)
        if not signature:
            checked_array_type(dtype.token, 0, element)
        if dtype.length is None:
            raise GoDiagnostic(dtype.token, "array length inference requires a composite literal")
        count = self.array_index_constant(dtype.length, "array length")
        if signature:
            if count > 4096:
                raise UnsupportedGo(dtype.token, 'go.unsupported.limit-array-elements',
                                    "array exceeds implementation limit (4096 elements)")
            return ArrayType(count, element)
        return checked_array_type(dtype.token, count, element)

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
        if expr.kind == "call" and expr.symbol is None and same_underlying_ignoring_tags(expr.type, expr.args[0].type):
            return self.static_initializer(expr.args[0])
        raise UnsupportedGo(expr.token, 'go.unsupported.package-initialization', "nonconstant package initializer requires shared-runtime initialization support")

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

    def bind_linknames(self):
        """cmd/compile //go:linkname: the local name is a declared package
        function or variable; only owned runtime objects can be bound."""
        from pcc.frontends.go.runtime_provider import runtime_object
        for directive in self.program.linknames:
            symbol = self.globals.get(directive.local)
            variable = symbol is not None and symbol.package_variable
            if not variable and directive.local not in self.functions:
                raise GoDiagnostic(directive.token, "//go:linkname must refer to declared function or variable")
            provided = runtime_object(directive.target)
            if not variable or provided is None:
                raise UnsupportedGo(directive.token, 'go.unsupported.linkname-directives',
                                    "linkname directives require owned object-symbol binding and runtime ownership")
            if symbol.declaration is not None:
                # The target already owns its definition and initializer.
                raise GoDiagnostic(symbol.token, "duplicated definition of symbol " + provided.symbol)
            if not is_error_interface(self.resolve_type(symbol.type)):
                raise UnsupportedGo(directive.token, 'go.unsupported.linkname-directives',
                                    "linkname binding requires the runtime object's declared type")
            symbol.linkname = provided

    def _resolve_constant(self, symbol):
        if symbol.resolved:
            return
        if symbol.resolving:
            raise GoDiagnostic(symbol.token, "package initialization cycle" if symbol.package_variable else "constant initialization cycle")
        symbol.resolving = True
        symbol.type = self.resolve_type(symbol.type)
        if not symbol.package_variable and interface_type(symbol.type) is not None:
            raise GoDiagnostic(symbol.token, "interface-typed initializer is not a constant")
        if symbol.package_variable and symbol.linkname is not None:
            # cmd/compile //go:linkname: the runtime owns this variable's
            # storage and initializer, so the package declares a reference and
            # never manufactures an ordinary nil global. Each value use stays an
            # explicit gap until the owned runtime provides the error object.
            symbol.resolved = True
            symbol.resolving = False
            return
        if symbol.package_variable and interface_type(symbol.type) is not None:
            raise UnsupportedGo(symbol.token, 'go.unsupported.interface-types',
                                "package interface storage requires runtime initialization and symbol ownership")
        if symbol.package_variable and isinstance(underlying(symbol.type), PointerType):
            raise UnsupportedGo(symbol.token, 'go.unsupported.pointer-storage',
                                "package pointer storage requires an escape and initialization contract")
        expr = symbol.declaration
        if expr is not None:
            self.expression(expr)
            dtype = symbol.type or (default_type(expr.type) if symbol.package_variable else expr.type)
            coerce(expr, dtype)
            symbol.type = dtype
            if symbol.package_variable:
                if interface_type(dtype) is not None:
                    raise UnsupportedGo(symbol.token, 'go.unsupported.interface-types',
                                        "package interface storage requires runtime initialization and symbol ownership")
                if isinstance(underlying(dtype), PointerType):
                    raise UnsupportedGo(symbol.token, 'go.unsupported.pointer-storage',
                                        "package pointer storage requires an escape and initialization contract")
                symbol.initializer = self.static_initializer(expr)
            else:
                if expr.constant is None:
                    raise GoDiagnostic(symbol.token, "const initializer is not a constant")
                symbol.constant = expr.constant
        symbol.resolved = True
        symbol.resolving = False

    @staticmethod
    def require_runtime_owned_value(symbol, token):
        if getattr(symbol, 'linkname', None) is not None:
            raise UnsupportedGo(token, 'go.unsupported.interface-types',
                                "runtime-owned " + symbol.linkname.symbol
                                + " values require the owned runtime error object and interface values")

    def lookup(self, token):
        for scope in reversed(self.scopes):
            if token.text in scope:
                return scope[token.text]
        owner = self.dot_imported(token) or self
        if token.text in owner.globals:
            result = owner.globals[token.text]
            owner.resolve_constant(result)
            return result
        raise GoDiagnostic(token, "undefined name " + token.text)

    def analyze_function(self, function):
        self.function = function
        self.type_bindings = function.type_bindings
        self.abstract_function = bool(function.type_parameters)
        self.scopes = [{}]
        self.loop_depth = 0
        self.break_depth = 0
        self.allowed_fallthrough = None
        self.branches = Branches(function)
        require_value_type(function.result, function.token)
        for token, dtype in function.params:
            require_value_type(dtype, token)
            self.check_name(token, local_value=True)
            if token.text in self.type_bindings:
                raise GoDiagnostic(token, "parameter redeclares type parameter " + token.text)
            symbol = Symbol(token, dtype, parameter=True, depth=1)
            if token.text != "_":
                if token.text in self.scopes[-1]:
                    raise GoDiagnostic(token, "duplicate parameter " + token.text)
                self.scopes[-1][token.text] = symbol
            function.parameters.append(symbol)
            function.locals.append(symbol)
        for token, dtype in zip(function.result_names, result_types(function.result)):
            if token is None:
                continue
            self.check_name(token, local_value=True)
            if token.text in self.type_bindings:
                raise GoDiagnostic(token, "result redeclares type parameter " + token.text)
            if token.text != "_" and token.text in self.scopes[-1]:
                raise GoDiagnostic(token, "duplicate parameter or result " + token.text)
            symbol = Symbol(token, dtype, parameter=True, depth=1)
            if token.text != "_":
                self.scopes[-1][token.text] = symbol
            function.result_symbols.append(symbol)
            function.locals.append(symbol)
        self.statements(function.body, new_scope=False)
        self.branches.validate()
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
        require_value_type(dtype, token)
        self.check_name(token, local_value=True)
        if len(self.scopes) == 1 and token.text in self.type_bindings:
            raise GoDiagnostic(token, "redeclaration in this block: " + token.text)
        if token.text != "_" and token.text in self.scopes[-1]:
            raise GoDiagnostic(token, "redeclaration in this block: " + token.text)
        symbol = Symbol(token, dtype, constant=constant, depth=len(self.scopes),
                        iteration_binding=self.iteration_binding or self.branches.reentered(token))
        if token.text != "_":
            self.scopes[-1][token.text] = symbol
        if constant is None:
            self.function.locals.append(symbol)
        return symbol

    def pointer_store(self, expr, depth, token):
        """Admitted aliases cannot leave the pointee's lexical region.

        A pointer variable's own region is a conservative bound on everything
        it can hold, independent of branch/loop order. Never infer noalias.
        """
        # Nil is a storable pointer value, not an indirection or a panic.
        # Runtime nullness is checked independently by the typed CFG validator.
        if expr is None or expr.kind == "nil":
            return
        if expr.pointer_depth > depth:
            raise UnsupportedGo(token, 'go.unsupported.pointer-escape',
                                "pointer may outlive its lexical storage region")

    def dereference(self, expr):
        dtype = underlying(expr.type)
        if not isinstance(dtype, PointerType):
            raise GoDiagnostic(expr.token, "indirection requires a pointer")
        # Typing never claims a pointer is nonnull. The separate use-site CFG
        # validator checks only evaluated indirections before any IR is built.
        return Expr("dereference", expr.token, args=[expr], type=dtype.element,
                    addressable=True, storage_depth=expr.pointer_depth)

    def address_of(self, arg, token):
        """Use the same region/identity rules for explicit and receiver addresses."""
        if contains_parameter(arg.type):
            raise UnsupportedGo(token, 'go.unsupported.generic-boundaries',
                                'addresses of type-parameter values require abstract lifetime checking')
        if arg.kind in ("array", "struct"):
            raise UnsupportedGo(token, 'go.unsupported.pointer-composite-address',
                                "addressed composite literals require distinct allocation/lifetime analysis")
        if not arg.addressable:
            raise GoDiagnostic(token, "address operator requires an addressable operand")
        if isinstance(underlying(arg.type), PointerType):
            raise UnsupportedGo(token, 'go.unsupported.pointer-storage',
                                "pointer-bearing pointees require a recursive lifetime/storage contract")
        if interface_type(arg.type) is not None:
            raise UnsupportedGo(token, 'go.unsupported.interface-types',
                                "addresses of interface values require a runtime storage contract")
        if arg.iteration_binding:
            raise UnsupportedGo(token, 'go.unsupported.pointer-iteration-identity',
                                "addressed repeated bindings require distinct storage identity")
        return Expr("address", token, args=[arg], type=PointerType(arg.type),
                    pointer_depth=arg.storage_depth, pointer_nonnull=True)

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
        if kind == "empty":
            return
        if kind in ("label", "goto"):
            self.branches.capture(statement, self.scopes)
            if kind == "label":
                self.statements(statement.body, new_scope=False)
                statement.terminates = self.terminates(statement.body)
            else:
                statement.terminates = True
            return
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
                if target.value != "_" and isinstance(underlying(target.type), PointerType):
                    self.pointer_store(expr, target.storage_depth, target.token)
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
                    if kind == "const" and interface_type(dtype) is not None:
                        raise GoDiagnostic(token, "interface-typed initializer is not a constant")
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
                if isinstance(underlying(symbol.type), PointerType) and token.text != "_":
                    self.pointer_store(expr, symbol.depth, token)
                statement.symbols.append(symbol)
            if kind == ":=" and not new_count:
                raise GoDiagnostic(statement.token, "short declaration requires at least one new non-blank variable")
            if kind == ":=" or (kind == "var" and statement.names):
                self.branches.declaration(statement, self.scopes[-1])
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
            if expr.kind == "builtin_io":
                statement.terminates = expr.value == "panic"
            elif expr.kind != "call" or expr.symbol is None:
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
            if not (self.break_depth if kind == "break" else self.loop_depth):
                raise GoDiagnostic(statement.token, kind + " outside loop")
            return
        if kind == "fallthrough":
            if statement is not self.allowed_fallthrough:
                raise GoDiagnostic(statement.token, "fallthrough must be the final non-empty statement of a non-final switch clause")
            statement.terminates = True
            return
        if kind == "switch":
            self.scopes.append({})
            if statement.init is not None:
                self.statement(statement.init)
            selector = statement.exprs[0] if statement.exprs else Expr("name", statement.token, "true")
            self.expression(selector)
            coerce(selector, default_type(selector.type))
            if interface_type(selector.type) is not None:
                raise UnsupportedGo(selector.token, 'go.unsupported.interface-types',
                                    "interface switches require dynamic-value comparison support")
            # The equality checker owns assignability, pointer identity and
            # aggregate value comparison. Lowering captures the selector once.
            if not (is_integer(selector.type) or is_boolean(selector.type) or is_string(selector.type)
                    or isinstance(underlying(selector.type), (PointerType, ArrayType, StructType))
                    or isinstance(selector.type, TypeParameter) and comparable(selector.type)):
                raise GoDiagnostic(selector.token, "switch requires a comparable type")
            if contains_parameter(selector.type) and not comparable(selector.type):
                raise GoDiagnostic(selector.token, "switch requires a comparable type")
            statement.exprs = [selector]
            defaults, constants = 0, set()
            self.break_depth += 1
            for index, clause in enumerate(statement.cases):
                defaults += clause.kind == "default"
                if defaults > 1:
                    raise GoDiagnostic(clause.token, "multiple default clauses in switch")
                comparisons = []
                for expr in clause.exprs:
                    self.expression(expr)
                    if expr.type in ("untyped int", "untyped rune", "untyped bool", "untyped string"):
                        coerce(expr, selector.type)
                    comparison = Expr("binary", expr.token, "==", [selector, expr])
                    self.expression(comparison)
                    if expr.constant is not None:
                        key = (expr.type, expr.constant)
                        if key in constants:
                            raise GoDiagnostic(expr.token, "duplicate constant switch case")
                        constants.add(key)
                    comparisons.append(comparison)
                clause.exprs = comparisons
                previous = self.allowed_fallthrough
                last = unlabeled(clause.body[-1]) if clause.body else None
                self.allowed_fallthrough = (last if last is not None and index + 1 < len(statement.cases)
                                            and last.kind == "fallthrough" else None)
                self.statements(clause.body)
                self.allowed_fallthrough = previous
            self.break_depth -= 1
            statement.terminates = (bool(defaults) and not self.branches.breaks(statement) and
                                    all(self.terminates(clause.body) for clause in statement.cases))
            self.scopes.pop()
            return
        if kind == "block":
            self.statements(statement.body)
            statement.terminates = self.terminates(statement.body)
            return
        if kind == "range":
            ranged = statement.exprs[0]
            # Resolve the range expression before declaring the iteration
            # variables: `for a := range a` reads the enclosing a.
            self.expression(ranged)
            dtype = underlying(ranged.type)
            if is_integer(ranged.type):
                if len(statement.targets) > 1:
                    raise GoDiagnostic(statement.token, "integer range permits at most one iteration variable")
                # An untyped bound is assigned to the preexisting iteration
                # target's type, not defaulted to int before that assignment.
                # Resolve target types here without evaluating their operands;
                # ordinary assignment lowering evaluates them each iteration.
                iteration_type = default_type(ranged.type)
                if is_untyped_integer(ranged.type) and statement.targets and not statement.range_declaration:
                    target = statement.targets[0]
                    self.assignment_target(target, read=False)
                    if not (target.kind == "name" and target.value == "_"):
                        if not is_integer(target.type):
                            raise GoDiagnostic(target.token, "integer range requires an integer iteration variable")
                        iteration_type = target.type
                coerce(ranged, iteration_type)
                statement.evaluate_range = ranged.constant is None
                value_types = (iteration_type,)
            elif (isinstance(dtype, ArrayType) or
                  isinstance(dtype, PointerType) and isinstance(underlying(dtype.element), ArrayType)):
                array_type = underlying(dtype.element) if isinstance(dtype, PointerType) else dtype
                if len(statement.targets) > 2:
                    raise GoDiagnostic(statement.token, "array range permits at most two iteration variables")
                uses_element = len(statement.targets) == 2 and not (
                    statement.targets[-1].kind == "name" and statement.targets[-1].value == "_")
                # len(array-or-array-pointer) is constant only in the absence
                # of evaluated calls. A blank second variable does not read an
                # element; never turn a real call into an unevaluated operand.
                statement.evaluate_range = uses_element or has_evaluated_call(ranged)
                if uses_element:
                    # The temporary holds an array snapshot OR the captured
                    # pointer, never a snapshot of the pointed-to array.
                    self.function.temporaries.append(ranged)
                value_types = ("int", array_type.element)
            elif is_string(ranged.type):
                if len(statement.targets) > 2:
                    raise GoDiagnostic(statement.token, "string range permits at most two iteration variables")
                # Capture the immutable byte descriptor once. Decoding needs
                # neither allocation nor the arbitrary-index panic contract.
                statement.evaluate_range = True
                value_types = ("int", "int32")
            else:
                raise GoDiagnostic(ranged.token, "range currently requires a fixed array or integer, a string, or a pointer to a fixed array; other iterator/runtime contracts are not implemented")
            statement.range_index = Expr("range_index", statement.token, type=value_types[0])
            self.function.temporaries.append(statement.range_index)
            values = [Expr("range_value", statement.token, type=typ) for typ in value_types][:len(statement.targets)]
            self.scopes.append({})
            if statement.targets:
                statement.init = Stmt(":=" if statement.range_declaration else "=", statement.token,
                                      exprs=values, names=statement.names, targets=statement.targets)
                self.iteration_binding = True
                self.statement(statement.init)
                self.iteration_binding = False
            self.loop_depth += 1
            self.break_depth += 1
            self.statements(statement.body)
            self.break_depth -= 1
            self.loop_depth -= 1
            self.scopes.pop()
            # Even a known nonempty range is not a terminating statement
            # under Go's syntactic missing-return rule.
            return
        if kind in ("if", "for"):
            self.scopes.append({})
            if statement.init is not None:
                self.iteration_binding = kind == "for"
                self.statement(statement.init)
                self.iteration_binding = False
            if statement.exprs:
                self.condition(statement.exprs[0])
            if kind == "for":
                self.loop_depth += 1
                self.break_depth += 1
            self.statements(statement.body)
            if statement.post is not None:
                self.statement(statement.post)
            if kind == "for":
                self.loop_depth -= 1
                self.break_depth -= 1
                statement.terminates = not statement.exprs and not self.branches.breaks(statement)
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

    def terminates(self, statements):
        return bool(statements and statements[-1].terminates)

    def expression(self, expr):
        if expr.type is not None:
            return
        kind = expr.kind
        if kind in ("index", "type_apply"):
            base = expr.args[0] if kind == "index" else expr.callee
            function = self.direct_function(base)
            if function is not None and function.type_parameters:
                raise UnsupportedGo(expr.token, 'go.unsupported.generic-boundaries',
                                    'instantiated function values require function-value support')
            if kind == "type_apply":
                raise GoDiagnostic(expr.token, "multiple indices require a generic function instantiation")
        if kind in ("array", "composite"):
            syntax = expr.value
            if isinstance(syntax, ArraySyntax) and syntax.length is None:
                element = self.resolve_type(syntax.element)
                if (not isinstance(element, TypeParameter) and not is_integer(element)
                        and not is_boolean(element) and not isinstance(underlying(element), StructType)):
                    raise UnsupportedGo(syntax.token, 'go.unsupported.array-element-types',
                                        "nested arrays and non-scalar array elements are not implemented")
                checked_array_type(syntax.token, 0, element)
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
                    expr.type = dtype = checked_array_type(syntax.token, count, element)
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
                    raise UnsupportedGo(index.token, 'go.unsupported.string-bounds-panic', "unproven string bounds require shared-runtime bounds-panic support")
                if index.constant is not None:
                    value = self.array_index_constant(index, "string index")
                    if value >= len(base.constant):
                        raise GoDiagnostic(index.token, "string index out of bounds")
                else:
                    upper = self.index_upper_bound(index)
                    if upper is None or upper >= len(base.constant):
                        raise UnsupportedGo(index.token, 'go.unsupported.string-bounds-panic', "unproven string bounds require shared-runtime bounds-panic support")
                coerce(index, default_type(index.type))
                # A string index is a byte value, but is not a Go constant
                # expression even when both operands happen to be constant.
                expr.type = "uint8"
                return
            array_type = underlying(base.type)
            if isinstance(array_type, PointerType) and isinstance(underlying(array_type.element), ArrayType):
                base = self.dereference(base)
                expr.args[0] = base
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
                    raise UnsupportedGo(index.token, 'go.unsupported.array-bounds-panic', "unproven array bounds require shared-runtime bounds-panic support")
            coerce(index, default_type(index.type))
            expr.type, expr.addressable = array_type.element, base.addressable
            expr.storage_depth, expr.iteration_binding = base.storage_depth, base.iteration_binding
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
        if kind == "rune":
            from pcc.frontends.go.runes import literal_value
            expr.type, expr.constant = "untyped rune", literal_value(expr.token)
            return
        if kind == "name":
            if expr.value in self.type_bindings and not any(expr.value in scope for scope in self.scopes):
                raise GoDiagnostic(expr.token, "type parameter is not an expression: " + expr.value)
            function = self.direct_function(expr)
            if function is not None and function.type_parameters:
                raise UnsupportedGo(expr.token, 'go.unsupported.generic-boundaries',
                                    'generic function values require direct instantiation calls')
            if expr.value == "iota":
                if self.iota is None:
                    raise GoDiagnostic(expr.token, "iota is only defined within a constant declaration")
                expr.type, expr.constant = "untyped int", self.iota
            elif expr.value == "nil":
                expr.kind, expr.type = "nil", "untyped nil"
            elif expr.value in ("true", "false"):
                expr.type, expr.constant = "untyped bool", expr.value == "true"
            else:
                symbol = self.lookup(expr.token)
                symbol.used = True
                self.require_runtime_owned_value(symbol, expr.token)
                expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
                expr.addressable = symbol.constant is None
                expr.storage_depth, expr.iteration_binding = symbol.depth, symbol.iteration_binding
                if isinstance(underlying(symbol.type), PointerType):
                    expr.pointer_depth = symbol.depth
            return
        if kind == "selector":
            base = expr.args[0]
            bindings = self.imports.get(expr.token.filename, {})
            local = base.kind == "name" and (any(base.value in scope for scope in self.scopes) or base.value in self.type_bindings)
            if base.kind == "name" and base.value in bindings and not local:
                if base.parenthesized:
                    raise GoDiagnostic(base.token, "selector requires an unparenthesized imported package name")
                imported = self.imported(base.value, expr.token)
                if expr.value in imported.functions and imported.functions[expr.value].type_parameters:
                    raise UnsupportedGo(expr.token, 'go.unsupported.generic-boundaries',
                                        'generic function values require direct instantiation calls')
                symbol = imported.globals.get(expr.value)
                if symbol is None:
                    raise GoDiagnostic(expr.token, "undefined imported constant or variable " + expr.value)
                imported.resolve_constant(symbol)
                self.require_runtime_owned_value(symbol, expr.token)
                expr.type, expr.constant, expr.symbol = symbol.type, symbol.constant, symbol
                expr.addressable = symbol.constant is None
                return
            if (base.kind == "name" and not local and base.value not in self.globals
                    and base.value not in self.types and base.value not in TYPE_NAMES
                    and self.dot_imported(base.token, use=False) is None):
                raise GoDiagnostic(base.token, "undefined imported package " + base.value)
            type_operand = self.type_operand(base)
            if interface_type(type_operand) is not None:
                if method_set(type_operand).lookup(expr.value, self.package_path or "<main>") is not None:
                    raise UnsupportedGo(expr.token, 'go.unsupported.interface-types',
                                        "interface method expressions require dynamic dispatch support")
                raise GoDiagnostic(expr.token, "no method " + expr.value + " on " + str(type_operand))
            if (isinstance(type_operand, PointerType) and isinstance(type_operand.element, DefinedType)
                    and expr.value in type_operand.element.methods):
                raise UnsupportedGo(expr.token, 'go.unsupported.method-values',
                                    "stored pointer method expressions require function-value support")
            if isinstance(type_operand, DefinedType) and expr.value in type_operand.methods:
                if isinstance(type_operand.methods[expr.value].receiver[1], PointerType):
                    raise GoDiagnostic(expr.token, "invalid method expression: pointer receiver requires *" + str(type_operand))
                raise GoDiagnostic(expr.token, "first-class method expressions require function-value support")
            self.expression(base)
            if interface_type(base.type) is not None:
                if method_set(base.type).lookup(expr.value, self.package_path or "<main>") is not None:
                    raise UnsupportedGo(expr.token, 'go.unsupported.interface-types',
                                        "interface method values require dynamic dispatch support")
                raise GoDiagnostic(expr.token, "no method " + expr.value + " on " + str(base.type))
            method_type = base.type.element if isinstance(base.type, PointerType) else base.type
            if isinstance(method_type, DefinedType) and expr.value in method_type.methods:
                method = method_type.methods[expr.value]
                if (isinstance(method.receiver[1], PointerType)
                        and not isinstance(base.type, PointerType) and not base.addressable):
                    raise GoDiagnostic(expr.token, "cannot call pointer method on a non-addressable value")
                raise UnsupportedGo(expr.token, 'go.unsupported.method-values', "stored method values require shared closure and bound-receiver support")
            dtype = underlying(base.type)
            if isinstance(dtype, PointerType) and isinstance(underlying(dtype.element), StructType):
                base = self.dereference(base)
                expr.args[0] = base
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
            expr.storage_depth, expr.iteration_binding = base.storage_depth, base.iteration_binding
            return
        if kind == "call":
            name = expr.value
            callee = expr.callee
            if callee is not None and callee.kind in ("index", "type_apply"):
                base = callee.args[0] if callee.kind == "index" else callee.callee
                explicit = callee.args[1:] if callee.kind == "index" else callee.args
                function = self.direct_function(base)
                if function is None:
                    raise GoDiagnostic(expr.token, "type application requires a direct generic function")
                expr.value = function.token.text
                selected = self.generics.select(self, function, expr.args, explicit)
                self.call(expr, selected)
                return
            if callee is not None and callee.kind in ("unary", "type"):
                dtype = self.type_operand(callee)
                if dtype is None:
                    raise GoDiagnostic(callee.token, "call requires a function or type")
                self.conversion(expr, dtype)
                return
            if callee is not None and callee.kind == "selector":
                base = callee.args[0]
                binding = (self.imports.get(expr.token.filename, {}).get(base.value)
                           if base.kind == "name" else None)
                shadowed = base.kind == "name" and (any(base.value in scope for scope in self.scopes) or base.value in self.type_bindings)
                if binding is not None and not shadowed:
                    if base.parenthesized:
                        raise GoDiagnostic(base.token, "selector requires an unparenthesized imported package name")
                    owner = self.imported(base.value, expr.token)
                    if name in owner.types:
                        self.conversion(expr, owner.resolve_type_declaration(owner.types[name]))
                        return
                    function = owner.functions.get(name)
                    if function is None:
                        raise GoDiagnostic(expr.token, "undefined imported function " + name)
                    self.call(expr, function)
                    return
                dtype = self.type_operand(base)
                method_expression = dtype is not None
                if not method_expression:
                    self.expression(base)
                    dtype = base.type
                if interface_type(dtype) is not None:
                    if method_set(dtype).lookup(name, self.package_path or "<main>") is not None:
                        raise UnsupportedGo(expr.token, 'go.unsupported.interface-types',
                                            "interface calls require dynamic dispatch and nil-panic support")
                    raise GoDiagnostic(expr.token, "no method " + name + " on " + str(dtype))
                pointer_receiver_type = dtype if method_expression and isinstance(dtype, PointerType) else None
                if isinstance(dtype, PointerType):
                    dtype = dtype.element
                method = dtype.methods.get(name) if isinstance(dtype, DefinedType) else None
                if method is None:
                    if binding is not None and shadowed:
                        raise GoDiagnostic(expr.token, "package selector is shadowed by local variable " + base.value)
                    raise GoDiagnostic(expr.token, "no method " + name + " on " + str(dtype))
                if unicodedata.category(name[0]) != "Lu" and dtype.package != (self.package_path or ""):
                    raise GoDiagnostic(expr.token, "cannot access unexported method " + name)
                pointer_method = isinstance(method.receiver[1], PointerType)
                if method_expression:
                    if pointer_method and pointer_receiver_type is None:
                        raise GoDiagnostic(expr.token, "invalid method expression: pointer receiver requires *" + str(dtype))
                elif pointer_method:
                    if not isinstance(base.type, PointerType):
                        if not base.addressable:
                            raise GoDiagnostic(expr.token, "cannot call pointer method on a non-addressable value")
                        base = self.address_of(base, expr.token)
                elif isinstance(base.type, PointerType):
                    base = self.dereference(base)
                self.call(expr, method, receiver=None if method_expression else base,
                          pointer_receiver_type=pointer_receiver_type if not pointer_method else None)
                return
            # Values in a lexical scope shadow builtin and package type/function names.
            owner = (self.dot_imported(expr.token) if name not in self.type_bindings
                     and not any(name in scope for scope in self.scopes) else None) or self
            if any(name in scope for scope in self.scopes) or name in owner.globals and name not in self.type_bindings:
                raise GoDiagnostic(expr.token, "cannot call non-function " + name)
            if name in ("min", "max"):
                self.minmax(expr)
                return
            if name in ("len", "cap"):
                if len(expr.args) != 1:
                    raise GoDiagnostic(expr.token, name + " requires one argument")
                arg = expr.args[0]
                self.expression(arg)
                dtype = underlying(arg.type)
                if isinstance(dtype, PointerType):
                    dtype = underlying(dtype.element)
                expr.type = "int"  # Both builtins are typed int, including constants.
                if isinstance(dtype, ArrayType):
                    expr.kind = "array_len"
                    expr.constant = None if has_evaluated_call(arg) else dtype.count
                elif name == "len" and is_string(arg.type):
                    expr.kind = "string_len"
                    expr.constant = len(arg.constant) if arg.constant is not None else None
                else:
                    raise GoDiagnostic(expr.token, name + " requires an admitted array" + (" or string" if name == "len" else ""))
                return
            if name in ("print", "println", "panic"):
                values = self.values(expr.args)
                if name == "panic" and len(values) != 1:
                    raise GoDiagnostic(expr.token, "panic requires exactly one argument")
                for argument in values:
                    if argument.type == "void":
                        raise GoDiagnostic(argument.token, "void function call used as a value")
                    if argument.type == "untyped nil" and name != "panic":
                        raise GoDiagnostic(argument.token, "cannot print untyped nil")
                    if not (is_integer(argument.type) or is_boolean(argument.type) or is_string(argument.type)):
                        raise UnsupportedGo(argument.token, 'go.unsupported.builtin-io',
                                            "non-scalar or nil builtin payload requires shared interface/runtime support")
                    if name == "panic" and isinstance(argument.type, DefinedType):
                        for method_name in ("Error", "String"):
                            method = argument.type.methods.get(method_name)
                            if (method is not None and len(method.params) == 1 and
                                    method.result == "string" and
                                    not isinstance(method.receiver[1], PointerType)):
                                raise UnsupportedGo(argument.token, 'go.unsupported.builtin-io',
                                                    "panic formatting methods require shared interface/runtime support")
                    coerce(argument, default_type(argument.type))
                expr.kind, expr.type = "builtin_io", "void"
                return
            if name in TYPE_NAMES or name in owner.types or name in self.type_bindings:
                target = (self.type_bindings[name] if name in self.type_bindings else
                          TYPE_NAMES[name] if name in TYPE_NAMES else owner.resolve_type_declaration(owner.types[name]))
                self.conversion(expr, target)
                return
            function = owner.functions.get(name)
            if function is None:
                label = "unsupported builtin " if name in PREDECLARED else "undefined function "
                raise GoDiagnostic(expr.token, label + name)
            self.call(expr, function)
            return
        for arg in expr.args:
            self.expression(arg)
        if kind == "unary":
            arg = expr.args[0]
            if expr.value == "&":
                checked = self.address_of(arg, expr.token)
                expr.kind, expr.type = checked.kind, checked.type
                expr.pointer_depth, expr.pointer_nonnull = checked.pointer_depth, checked.pointer_nonnull
                return
            if expr.value == "*":
                checked = self.dereference(arg)
                expr.kind, expr.type = checked.kind, checked.type
                expr.addressable, expr.storage_depth = True, checked.storage_depth
                return
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

    def type_operand(self, expr):
        if expr.kind == "type":
            return self.resolve_type(expr.value)
        if expr.kind == "unary" and expr.value == "*":
            element = self.type_operand(expr.args[0])
            if element is None:
                return None
            if contains_parameter(element):
                raise UnsupportedGo(expr.token, 'go.unsupported.generic-boundaries',
                                    'dependent pointer layouts require abstract lifetime checking')
            if interface_type(element) is not None:
                raise UnsupportedGo(expr.token, 'go.unsupported.interface-types',
                                    "pointers to interface storage require a runtime storage contract")
            if isinstance(underlying(element), PointerType):
                raise UnsupportedGo(expr.token, 'go.unsupported.pointer-storage',
                                    "pointer-bearing pointees require a recursive lifetime/storage contract")
            return PointerType(element)
        if expr.kind == "name":
            if any(expr.value in scope for scope in self.scopes):
                return None
            if expr.value in self.type_bindings:
                return self.type_bindings[expr.value]
            owner = self.dot_imported(expr.token) or self
            if expr.value in owner.globals:
                return None
            if expr.value in owner.types:
                return owner.resolve_type_declaration(owner.types[expr.value])
            return TYPE_NAMES.get(expr.value)
        if expr.kind == "selector":
            base = expr.args[0]
            if (base.kind == "name" and base.value in self.imports.get(expr.token.filename, {})
                    and not any(base.value in scope for scope in self.scopes)):
                if base.parenthesized:
                    raise GoDiagnostic(base.token, "selector requires an unparenthesized imported package name")
                owner = self.imported(base.value, expr.token)
                declaration = owner.types.get(expr.value)
                return owner.resolve_type_declaration(declaration) if declaration else None
        return None

    def field_access(self, field, token):
        if field.package and field.package != (self.package_path or "<main>"):
            raise GoDiagnostic(token, "cannot access unexported struct field " + field.name)

    def minmax(self, expr):
        """Ordered scalar builtins, with one common source type for all inputs.

        Do not fold pairwise before checking every operand: a later typed
        argument must still constrain an earlier, non-selected constant.
        A sole multi-result call is expanded once by the ordinary value owner.
        """
        arguments = self.values(expr.args)
        if not arguments:
            raise GoDiagnostic(expr.token, expr.value + " requires at least one argument")
        for argument in arguments:
            if not (is_integer(argument.type) or is_string(argument.type)):
                raise GoDiagnostic(argument.token, expr.value + " requires ordered integer or string arguments")
        first = arguments[0]
        numeric = is_integer(first.type)
        common = first.type
        untyped = ("untyped int", "untyped rune") if numeric else ("untyped string",)
        for argument in arguments[1:]:
            dtype = argument.type
            if is_integer(dtype) != numeric:
                raise GoDiagnostic(argument.token, "mismatched " + expr.value + " argument types")
            if common == dtype:
                continue
            if common in untyped:
                common = ("untyped rune" if numeric and dtype in untyped
                          and "untyped rune" in (common, dtype) else dtype)
            elif dtype not in untyped:
                raise GoDiagnostic(argument.token, "mismatched " + expr.value + " argument types")
        constant = all(argument.constant is not None for argument in arguments)
        # Nonconstant min/max values are typed here, not by an enclosing
        # assignment. This includes min(1 << s), the issue61486 original.
        if not constant:
            common = default_type(common)
        for argument in arguments:
            coerce(argument, common)
        expr.kind, expr.type = "minmax", common
        if constant:
            operation = min if expr.value == "min" else max
            expr.constant = operation(argument.constant for argument in arguments)
            check_representable(expr)
        elif is_string(common) and self.function is not None:
            # Selection reuses immutable descriptors. No concatenation,
            # allocation, aggregate select instruction or runtime helper.
            self.function.temporaries.append(expr)

    def conversion(self, expr, target):
        if len(expr.args) != 1:
            raise GoDiagnostic(expr.token, "conversion requires one argument")
        arg = expr.args[0]
        self.expression(arg)
        if interface_type(target) is not None:
            coerce(arg, target)
            expr.type = target
            return
        if isinstance(underlying(target), PointerType):
            if arg.type == "untyped nil":
                coerce(arg, target)
            elif not same_underlying(target, arg.type):
                source = underlying(arg.type)
                if (same_underlying_ignoring_tags(target, arg.type) or
                        isinstance(source, PointerType) and not is_named(target) and not is_named(arg.type)
                        and same_underlying_ignoring_tags(source.element, underlying(target).element)):
                    raise UnsupportedGo(expr.token, 'go.unsupported.pointer-conversions',
                                        "pointer conversions between distinct base types are not implemented")
                raise GoDiagnostic(expr.token, f"invalid conversion from {arg.type} to {target}")
            expr.type = target
            expr.pointer_depth, expr.pointer_nonnull = arg.pointer_depth, arg.pointer_nonnull
            return
        if is_string(target) and is_integer(arg.type):
            if arg.constant is None:
                raise UnsupportedGo(expr.token, 'go.unsupported.dynamic-string-conversion', "integer-to-string conversion requires shared-runtime allocation support")
            codepoint = arg.constant
            if codepoint < 0 or codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
                codepoint = 0xfffd
            expr.type, expr.constant = target, chr(codepoint).encode("utf-8")
            return
        if not (same_underlying_ignoring_tags(target, arg.type) or
                is_integer(target) and is_integer(arg.type) or
                is_boolean(target) and is_boolean(arg.type) or
                is_string(target) and is_string(arg.type)):
            raise GoDiagnostic(expr.token, f"invalid conversion from {arg.type} to {target}")
        if arg.constant is None and is_untyped_integer(arg.type):
            coerce(arg, target)
        expr.type, expr.constant = target, arg.constant
        check_representable(expr)

    def direct_function(self, expression):
        if expression.kind == "name":
            if (any(expression.value in scope for scope in self.scopes) or
                    expression.value in self.type_bindings or expression.value in self.globals):
                return None
            owner = self.dot_imported(expression.token) or self
            return owner.functions.get(expression.value)
        if expression.kind == "selector" and expression.args[0].kind == "name":
            base = expression.args[0]
            if base.value in self.imports.get(expression.token.filename, {}) and not any(base.value in scope for scope in self.scopes):
                if base.parenthesized:
                    raise GoDiagnostic(base.token, "selector requires an unparenthesized imported package name")
                return self.imported(base.value, expression.token).functions.get(expression.value)
        return None

    def call(self, expr, function, *, receiver=None, pointer_receiver_type=None):
        if function.type_parameters:
            function = self.generics.select(self, function, expr.args)
        values = self.values(expr.args)
        parameters = function.params[1:] if receiver is not None else function.params
        if pointer_receiver_type is not None:
            # The derived (*T).M function has a *T source receiver, even
            # though the implementation uses the already-owned value ABI.
            parameters = [(parameters[0][0], pointer_receiver_type), *parameters[1:]]
        if len(values) != len(parameters):
            raise GoDiagnostic(expr.token, "argument count mismatch for " + expr.value)
        if receiver is not None:
            coerce(receiver, function.params[0][1])
        for arg, (_, dtype) in zip(values, parameters):
            coerce(arg, dtype)
        if pointer_receiver_type is not None:
            # Keep the source pointer argument unchanged. The CFG validator
            # proves its nullness at the delayed wrapper load after arguments.
            self.dereference(values[0])
        expr.type, expr.symbol, expr.receiver = function.result, function, receiver
        expr.indirect_receiver = pointer_receiver_type is not None

    def binary(self, expr):
        left, right = expr.args
        op = expr.value
        if is_error_interface(left.type) or is_error_interface(right.type):
            if op not in ("==", "!="):
                raise GoDiagnostic(expr.token, "interfaces permit only equality and inequality operators")
            target = left.type if is_error_interface(left.type) else right.type
            coerce(left, target)
            coerce(right, target)
            # The admitted domain has no dynamic value: concrete boxing is
            # rejected by coerce. Evaluate both operands, never fold a call.
            expr.kind, expr.type = "error_nil_compare", "untyped bool"
            return
        if isinstance(left.type, TypeParameter) or isinstance(right.type, TypeParameter):
            if left.type != right.type:
                raise GoDiagnostic(expr.token, "mismatched type-parameter operands")
            if op not in ("==", "!=") or not comparable(left.type):
                raise GoDiagnostic(expr.token, "operation is not permitted by the type parameter constraint")
            expr.kind, expr.type = "generic_compare", "untyped bool"
            return
        if (isinstance(underlying(left.type), (ArrayType, StructType)) or
                isinstance(underlying(right.type), (ArrayType, StructType))):
            if op not in ("==", "!="):
                raise GoDiagnostic(expr.token, "aggregates permit only equality and inequality operators")
            if left.type != right.type:
                if same_underlying(left.type, right.type) and not (is_named(left.type) and is_named(right.type)):
                    coerce(right, left.type)
                else:
                    raise GoDiagnostic(expr.token, "mismatched aggregate operand types")
            # First diagnose incompatible operand types (including nil), not
            # a generic constraint on an ordinary concrete array comparison.
            if not comparable(left.type) or not comparable(right.type):
                raise GoDiagnostic(expr.token, "operation is not permitted by the type parameter constraint")
            # Abstract array elements must be comparable independently of
            # concrete calls. Concrete storage still uses admitted leaves;
            # pointer/interface/slice aggregates fail during type resolution.
            if self.function is not None:
                self.function.temporaries.extend((left, right))
            expr.kind, expr.type = "aggregate_compare", "untyped bool"
            return
        if isinstance(underlying(left.type), PointerType) or isinstance(underlying(right.type), PointerType):
            if op not in ("==", "!="):
                raise GoDiagnostic(expr.token, "pointers permit only equality and inequality operators")
            if left.type == "untyped nil":
                coerce(left, right.type)
            elif right.type == "untyped nil":
                coerce(right, left.type)
            elif left.type != right.type:
                if same_underlying(left.type, right.type) and not (is_named(left.type) and is_named(right.type)):
                    coerce(right, left.type)
                else:
                    raise GoDiagnostic(expr.token, "mismatched pointer operand types")
            expr.type = "untyped bool"
            return
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
                    raise UnsupportedGo(expr.token, 'go.unsupported.dynamic-string-concatenation', "dynamic string concatenation requires shared-runtime allocation support")
                expr.kind, expr.type = "string_compare", "untyped bool"
                return
            a, b = left.constant, right.constant
            if op == "+":
                from pcc.frontends.go.strings import MAX_STRING_BYTES
                if len(a) + len(b) > MAX_STRING_BYTES:
                    raise UnsupportedGo(expr.token, 'go.unsupported.limit-string-bytes', "string exceeds implementation limit (1 MiB)")
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
                    raise UnsupportedGo(expr.token, 'go.unsupported.negative-shift-panic', "dynamic signed shift counts require shared-runtime negative-count panic support")
            elif right.constant < 0:
                raise GoDiagnostic(expr.token, "negative shift count")
            elif right.constant > MAX_SHIFT_COUNT:
                raise GoDiagnostic(expr.token, "shift count overflows uint64")
            expr.type = left.type
        else:
            untyped = ("untyped int", "untyped rune", "untyped bool")
            if left.type in untyped and right.type not in untyped:
                coerce(left, right.type)
            elif right.type in untyped and left.type not in untyped:
                coerce(right, left.type)
            elif is_untyped_integer(left.type) and is_untyped_integer(right.type):
                # Rune is the later constant kind. Shifts deliberately use
                # only their left operand's kind and do not take this path.
                common = "untyped rune" if "untyped rune" in (left.type, right.type) else "untyped int"
                coerce(left, common)
                coerce(right, common)
            if left.type != right.type:
                raise GoDiagnostic(expr.token, f"mismatched operand types {left.type} and {right.type}")
            expr.type = left.type
            if op in ("==", "!=", "<", "<=", ">", ">=") and (left.constant is None or right.constant is None):
                if is_untyped_integer(left.type):
                    target = default_type(left.type)
                    coerce(left, target)
                    coerce(right, target)
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
                raise UnsupportedGo(expr.token, 'go.unsupported.division-panic', "dynamic division requires shared-runtime division-by-zero panic support")
        if left.constant is not None and right.constant is not None:
            a, b = left.constant, right.constant
            if op == "<<" and b > MAX_CONSTANT_BITS:
                if a != 0:
                    raise UnsupportedGo(expr.token, 'go.unsupported.limit-constant-precision', "constant exceeds implementation precision limit (4096 bits)")
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
