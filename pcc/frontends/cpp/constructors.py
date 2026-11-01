"""Complete-object construction with the existing record layout and C ABI.

N4659 [class.ctor], [class.base.init], [dcl.init], [over.match.ctor].
Only non-inline out-of-class definitions, automatic complete objects, and
scalar/pointer subobjects are projected. No base-object C2, destructor, unwind,
copy, inline-ODR, or dynamic-initialization semantics are synthesized.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.declarators import DeclaratorParser, QualifiedName, parse_qualified_name
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.members import mangle_member
from pcc.frontends.cpp.sema import Scope, Symbol, resolve_overload
from pcc.frontends.cpp.types import ConstType, PointerType, is_enum_type, unqualified


def constructor_ahead(parser):
    """Recognize an actual qualified injected-class name, not a return type."""
    with parser.cursor.tentative():
        try:
            name = parse_qualified_name(parser.cursor, parser._identifier)
        except CppDiagnostic:
            return False
        if len(name.parts) < 2 or parser._peek().value != "(":
            return False
        owner = parser.scope.resolve_name(QualifiedName(name.tokens[:-1], name.global_scope, name.coord))
        return (owner is not None and owner.kind == "type" and is_class_type(owner.dtype)
                and name.parts[-1] == unqualified(owner.dtype).name)


def _complete_name(owner, parameters):
    # An empty ordinary member name gives a unique final `0E` separator;
    # replace only that member encoding, retaining the common type substitutions.
    encoded = mangle_member(owner, "", parameters)
    position = len(mangle_member(owner, "", ())) - 3
    return encoded[:position] + "C1E" + encoded[position + 2:]


def _declaration(parser, symbol, params, parameter_symbols, defining):
    coord = symbol.coord
    this = Symbol("this", "value", coord, dtype=PointerType(symbol.class_owner),
                  c_name=parser._local_projection_name("this"))
    arguments = [parser._decl(this.c_name, this.dtype, coord)]
    for index, (name, dtype) in enumerate(params):
        projected = (parameter_symbols[index].c_name if name is not None else
                     "__pcc_cpp_unnamed_" + str(index) if defining else None)
        arguments.append(parser._decl(projected, dtype, name.coord if name else coord))
    declaration = parser._decl(symbol.c_name, "void", coord)
    declaration.type = c_ast.FuncDecl(c_ast.ParamList(arguments, coord), declaration.type, coord)
    if symbol.internal_linkage:
        declaration.storage = ["static"]
    return declaration, this


def parse_constructor(parser, definition=None):
    parent = parser.scope
    outside = definition is None
    name = parse_qualified_name(parser.cursor, parser._identifier)
    if outside:
        target = parent.resolve_name(QualifiedName(name.tokens[:-1], name.global_scope, name.coord))
        owner = unqualified(target.dtype)
        require_complete(owner, name.coord)
        definition = owner.definition
        enclosing = owner.declaration_scope
        while enclosing is not None and enclosing is not parent:
            enclosing = enclosing.parent
        if enclosing is None:
            raise CppDiagnostic("cpp.member-definition-namespace", "constructor definition must be in an enclosing namespace", name.coord)
    else:
        owner = definition.dtype
        if name.global_scope or len(name.parts) != 1:
            raise CppDiagnostic("cpp.invalid-constructor", "in-class constructor names must be unqualified", name.coord)
    parameter_scope = Scope(definition.scope)
    parser.scope = parameter_scope
    params, parameter_symbols = [], []
    try:
        parser._expect("(")
        parser._parameter_clause(params, parameter_symbols)
        parser._expect(")")
    finally:
        parser.scope = parent
    if parser._peek().value in ("const", "volatile"):
        raise CppDiagnostic("cpp.constructor-cv", "constructors cannot have cv-qualifiers", parser._peek().coord)
    for _, dtype in params:
        if is_class_type(dtype):
            raise CppDiagnostic("cpp.unsupported.class-value-abi", "constructor class-by-value arguments require copy semantics", name.coord)
        plain = unqualified(dtype)
        pointee = unqualified(plain.pointee) if isinstance(plain, PointerType) else plain
        if is_class_type(pointee) and pointee.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.class-linkage", "constructor signatures with internal-linkage class types require ABI support", name.coord)
        if is_enum_type(pointee) and pointee.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.enum-linkage", "constructor signatures with internal-linkage enum types require ABI support", name.coord)
    if not outside and parser._peek().value != ";":
        raise CppDiagnostic("cpp.unsupported.inline-member-functions", "in-class constructor bodies require inline/ODR emission", name.coord)
    if outside and parser._peek().value not in (":", "{"):
        raise CppDiagnostic("cpp.member-definition-required", "out-of-class constructor declaration requires a body", name.coord)
    signature = tuple(unqualified(dtype) for _, dtype in params)
    group = definition.constructors
    previous = next((item for item in group.overloads if item.parameters == signature), None)
    if outside:
        if previous is None:
            raise CppDiagnostic("cpp.no-matching-member", "constructor definition does not match a declaration", name.coord)
        if previous.defined:
            raise CppDiagnostic("cpp.redefinition", "constructor is already defined", name.coord)
        symbol = previous
        symbol.defined = True
    else:
        if previous is not None:
            raise CppDiagnostic("cpp.redeclaration", "constructor cannot be redeclared within its class", name.coord)
        symbol = Symbol(owner.name, "function", name.coord, dtype="void", parameters=signature)
        symbol.class_owner, symbol.access = owner, definition.access
        symbol.owner = definition.scope
        symbol.constructor = True
        symbol.method_static = False
        symbol.method_const = False
        symbol.internal_linkage = owner.internal_linkage
        symbol.c_name = _complete_name(owner, signature)
        group.overloads.append(symbol)
    parser.function_projection_names.add(symbol.c_name)
    for parameter in parameter_symbols:
        if parameter is not None:
            parameter.c_name = parser._local_projection_name(parameter.name)
    declaration, this = _declaration(parser, symbol, params, parameter_symbols, outside)
    if not outside:
        parser._expect(";")
        return declaration
    previous_member, previous_this, previous_return = parser.member_function, parser.this_symbol, parser.return_type
    parser.scope = parameter_scope
    parser.member_function, parser.this_symbol, parser.return_type = symbol, this, "void"
    try:
        initializers = {}
        if parser._accept(":"):
            while True:
                member = parser._identifier()
                if member.value == owner.name:
                    raise CppDiagnostic("cpp.unsupported.delegating-constructors", "delegation requires constructor lifetime integration", member.coord)
                target = definition.scope.names.get(member.value)
                if target is None or target.kind != "member":
                    raise CppDiagnostic("cpp.invalid-member-initializer", "initializer must name a direct non-static data member", member.coord)
                if target in initializers:
                    raise CppDiagnostic("cpp.duplicate-member-initializer", "data member initialized more than once", member.coord)
                if parser._peek().value != "(":
                    raise CppDiagnostic("cpp.unsupported.constructor-list-initialization", "braced member initialization requires direct-list conversion rules", parser._peek().coord)
                parser._expect("(")
                expression = None if parser._peek().value == ")" else parser._expression()
                parser._expect(")")
                initializers[target] = expression
                if not parser._accept(","):
                    break
        stores = []
        for member in definition.members:
            if member not in initializers:
                if isinstance(member.dtype, ConstType):
                    raise CppDiagnostic("cpp.const-initializer", "const data member requires initialization", member.coord)
                # Default initialization of a scalar leaves it indeterminate.
                continue
            expression = initializers[member]
            value = (c_ast.Constant("int", "0", member.coord) if expression is None else
                     parser._convert(expression, member.dtype, member.coord, contextual=True))
            field = c_ast.StructRef(c_ast.ID(this.c_name, member.coord), "->",
                                   c_ast.ID(member.c_name, member.coord), member.coord)
            # This is construction, not C++ assignment to a live const object.
            stores.append(c_ast.Assignment("=", field, value, member.coord))
        body = parser._block(new_scope=False)
        body.block_items = stores + body.block_items
    finally:
        parser.scope = parent
        parser.member_function, parser.this_symbol, parser.return_type = previous_member, previous_this, previous_return
    return c_ast.FuncDef(declaration, None, body, name.coord)



def _function_declaration_ahead(parser):
    """Prefer real parameter-declaration syntax in the most-vexing parse.

    This is syntax-only lookahead, never parameter installation or expression
    evaluation. In particular int(42) is an expression but int(x) can declare
    a parameter even if an outer object x is visible. Shared declarator syntax
    recognizes named and abstract nested/function forms before any lowering.
    """
    if parser._peek().value != ")" and not parser._is_type(parser._peek()):
        return False

    def array_bound_tokens():
        # Tentative declaration syntax retains the bound tokens; it does not
        # evaluate a constant expression, instantiate templates or make up an
        # array extent. Only the real branch may perform those semantic steps.
        tokens, closings = [], []
        opening = {"(": ")", "[": "]", "{": "}"}
        while closings or parser._peek().value != "]":
            token = parser.cursor.take()
            if token.kind == "EOF":
                raise CppDiagnostic("cpp.invalid-declarator", "unterminated array bound", token.coord)
            if token.value in opening:
                closings.append(opening[token.value])
            elif token.value in (")", "]", "}"):
                if not closings or closings.pop() != token.value:
                    raise CppDiagnostic("cpp.invalid-declarator", "unbalanced array bound", token.coord)
            tokens.append(token)
        return tuple(tokens)

    def parameter_clause():
        if parser._peek().value == ")":
            return ()
        while True:
            parser._type(check_lowering=False)
            start = parser.cursor.position
            syntax = DeclaratorParser(parser.cursor, parser._identifier,
                                      parse_parameters=parameter_clause,
                                      parse_array_bound=array_bound_tokens)
            try:
                syntax.parse()
                if parser._peek().value not in (",", ")", "="):
                    raise CppDiagnostic("cpp.invalid-declarator", "incomplete parameter declarator", parser._peek().coord)
            except CppDiagnostic:
                parser.cursor.position = start
                syntax.parse_abstract()
            if parser._peek().value == "=":
                # A complete parameter declarator followed by its default
                # argument introducer establishes the declaration branch.
                # Leave every default-expression token for the real parser;
                # this unsupported branch is never emitted as construction.
                return ()
            if parser._peek().value == ")":
                return ()
            parser._expect(",")

    with parser.cursor.tentative():
        try:
            parameter_clause()
            return parser._peek().value in (")", "=")
        except CppDiagnostic:
            # A semantic/capability failure is not proof of a declaration.
            # The real expression branch must diagnose its own unavailable
            # forms rather than inheriting speculative parser side effects.
            return False


def construct_local(parser, symbol):
    """Keep storage in its lexical block, followed by one complete-object call."""
    dtype = unqualified(symbol.dtype)
    arguments = []
    if parser._accept("("):
        if _function_declaration_ahead(parser):
            raise CppDiagnostic("cpp.unsupported.block-function-declarations", "function declaration ambiguity is not object construction", parser._peek().coord)
        while True:
            expression = parser._expression()
            parser._require_value(expression)
            arguments.append(parser._decay_array(expression))
            if parser._accept(")"):
                break
            parser._expect(",")
    elif parser._peek().value in ("=", "{"):
        raise CppDiagnostic("cpp.unsupported.constructor-list-initialization", "copy/list construction requires its separate overload and conversion rules", parser._peek().coord)
    selected = resolve_overload(dtype.definition.constructors,
                                [arg.dtype for arg in arguments], parser.types, symbol.coord,
                                [arg.null_pointer for arg in arguments])
    parser.scope.check_member_access(selected, symbol.coord)
    converted = [c_ast.UnaryOp("&", c_ast.ID(symbol.c_name, symbol.coord), symbol.coord)]
    converted += [parser._convert(arg, target, symbol.coord)
                  for arg, target in zip(arguments, selected.parameters)]
    parser._expect(";")
    symbol.initialization_complete = True
    symbol.jump_entry = "initialized"
    return [parser._decl(symbol.c_name, symbol.dtype, symbol.coord),
            c_ast.FuncCall(c_ast.ID(selected.c_name, symbol.coord), c_ast.ExprList(converted, symbol.coord), symbol.coord)]
