"""Complete-object construction with the existing record layout and C ABI.

N4659 [class.ctor], [class.base.init], [dcl.init], [over.match.ctor].
Out-of-class bodies and delayed in-class bodies share scalar/pointer member
initialization. In-class definitions additionally require the shared external-ODR
linkage contract before emission. No base-object C2, unwind, copy, or dynamic
initialization semantics are synthesized.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.declarators import DeclaratorParser, QualifiedName, parse_qualified_name
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.members import mangle_member
from pcc.frontends.cpp.sema import Scope, Symbol, resolve_overload
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, contains_function, is_enum_type, unqualified


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


def parse_constructor(parser, definition=None, *, explicit=False):
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
    params, parameter_symbols, default_arguments = [], [], []
    try:
        parser._expect("(")
        parser._parameter_clause(params, parameter_symbols, defaults=default_arguments)
        parser._expect(")")
    finally:
        parser.scope = parent
    if outside and any(default_arguments):
        raise CppDiagnostic("cpp.unsupported.default-argument-context",
                            "out-of-class constructor default additions require special-member declaration rules", name.coord)
    if parser._peek().value in ("const", "volatile"):
        raise CppDiagnostic("cpp.constructor-cv", "constructors cannot have cv-qualifiers", parser._peek().coord)
    for _, dtype in params:
        if contains_function(dtype):
            raise CppDiagnostic("cpp.unsupported.function-pointers", "constructor callback parameters require function-value semantics", name.coord)
        if is_class_type(dtype):
            raise CppDiagnostic("cpp.unsupported.class-value-abi", "constructor class-by-value arguments require copy semantics", name.coord)
        plain = unqualified(dtype)
        pointee = plain
        while isinstance(pointee, (PointerType, ReferenceType)):
            pointee = unqualified(pointee.referent if isinstance(pointee, ReferenceType) else pointee.pointee)
        if is_class_type(pointee) and pointee.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.class-linkage", "constructor signatures with internal-linkage class types require ABI support", name.coord)
        if is_enum_type(pointee) and pointee.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.enum-linkage", "constructor signatures with internal-linkage enum types require ABI support", name.coord)
    inline_definition = not outside and parser._peek().value != ";"
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
        symbol.constructor_explicit = explicit
        symbol.method_static = False
        symbol.method_const = False
        symbol.internal_linkage = owner.internal_linkage
        symbol.c_name = _complete_name(owner, signature)
        group.overloads.append(symbol)
    from pcc.frontends.cpp.default_arguments import merge
    merge(parser, symbol, default_arguments, definition.scope)
    parser.function_projection_names.add(symbol.c_name)
    for parameter in parameter_symbols:
        if parameter is not None:
            parameter.c_name = parser._local_projection_name(parameter.name)
    declaration, this = _declaration(parser, symbol, params, parameter_symbols, outside or inline_definition)
    if inline_definition:
        if owner.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.class-linkage",
                                "in-class constructors of internal-linkage classes require distinct definition ownership", name.coord)
        from pcc.frontends.cpp.inline_constructors import DeferredConstructor, collect_definition
        tokens = collect_definition(parser, owner)
        symbol.definition_linkage = "external_odr"
        definition.inline_constructors.append(DeferredConstructor(
            symbol, parameter_scope, declaration, this, tokens, name.coord,
        ))
        return declaration
    if not outside:
        parser._expect(";")
        return declaration
    return bind_constructor_body(parser, definition, symbol, parameter_scope, declaration, this, name.coord)


def parse_member_initializers(parser, definition):
    """Bind initializer names/expressions in the active constructor scope.

    The caller may retain typed Pattern expressions; this pass does not emit
    stores or decide a dependent member's eventual initialization conversion.
    """
    owner = definition.dtype
    initializers = {}
    if parser._accept(":"):
        while True:
            member = parser._identifier()
            if member.value == owner.name:
                raise CppDiagnostic("cpp.unsupported.delegating-constructors", "delegation requires constructor lifetime integration", member.coord)
            target = definition.scope.lookup(member.value, member.coord)
            if (target is not None and target.kind == "type"
                    and unqualified(target.dtype) is owner):
                raise CppDiagnostic("cpp.unsupported.delegating-constructors",
                                    "delegation requires constructor lifetime integration", member.coord)
            if target is None or target.kind != "member" or target.class_owner is not owner:
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
    return initializers


def lower_member_initializers(parser, definition, this, initializers):
    """Initialize concrete fields in declaration order, without zeroing omissions."""
    stores = []
    for member in definition.members:
        if member not in initializers and getattr(member, 'default_initializer_tokens', None) is not None:
            from pcc.frontends.cpp.default_member_initializers import value as default_value
            value = default_value(member, c_ast.ID(this.c_name, member.coord))
        elif member not in initializers:
            if isinstance(member.dtype, ConstType):
                raise CppDiagnostic("cpp.const-initializer", "const data member requires initialization", member.coord)
            # Default initialization of a scalar leaves it indeterminate.
            continue
        else:
            expression = initializers[member]
            value = (c_ast.Constant("int", "0", member.coord) if expression is None else
                     parser._convert(expression, member.dtype, member.coord, contextual=True))
        field = c_ast.StructRef(c_ast.ID(this.c_name, member.coord), "->",
                               c_ast.ID(member.c_name, member.coord), member.coord)
        # This is construction, not C++ assignment to a live const object.
        stores.append(c_ast.Assignment("=", field, value, member.coord))
    return stores


def bind_constructor_body(parser, definition, symbol, parameter_scope, declaration, this, coord):
    """Bind a body in its original parameter scope against a completed class."""
    parent = parser.scope
    owner = definition.dtype
    previous_member, previous_this, previous_return = parser.member_function, parser.this_symbol, parser.return_type
    parser.scope = parameter_scope
    parser.member_function, parser.this_symbol, parser.return_type = symbol, this, "void"
    try:
        initializers = parse_member_initializers(parser, definition)
        stores = lower_member_initializers(parser, definition, this, initializers)
        body = parser._block(new_scope=False)
        body.block_items = stores + body.block_items
    finally:
        parser.scope = parent
        parser.member_function, parser.this_symbol, parser.return_type = previous_member, previous_this, previous_return
    symbol.closed_normal_body = call_free_body(body)
    return c_ast.FuncDef(declaration, None, body, coord)



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
    direct_list = False
    copy_initialization = False
    if parser._accept("("):
        if _function_declaration_ahead(parser):
            raise CppDiagnostic("cpp.unsupported.block-function-declarations", "function declaration ambiguity is not object construction", parser._peek().coord)
        while True:
            expression = parser._expression()
            # A class lvalue may bind a selected reference parameter without
            # loading/copying its aggregate value. Only constructor selection
            # opts into that address-only path; ordinary function calls retain
            # their separate class-value/ADL capability boundary.
            parser._require_value(expression, allow_class=True,
                                  object_address=is_class_type(expression.dtype))
            arguments.append(parser._decay_array(expression))
            if parser._accept(")"):
                break
            parser._expect(",")
    elif parser._peek().value == "{":
        from pcc.frontends.cpp.constructor_lists import parse_arguments
        arguments = parse_arguments(parser)
        direct_list = True
    elif parser._accept("="):
        copy_initialization = True
        if parser._peek().value == "{":
            from pcc.frontends.cpp.constructor_lists import parse_arguments
            arguments = parse_arguments(parser)
            direct_list = True
        else:
            expression = parser._expression()
            parser._require_value(expression, allow_class=True)
            if is_class_type(expression.dtype):
                raise CppDiagnostic("cpp.unsupported.class-initialization",
                                    "class copy/materialization requires separate lifetime semantics", expression.node.coord)
            arguments = [parser._decay_array(expression)]
    address = c_ast.UnaryOp("&", c_ast.ID(symbol.c_name, symbol.coord), symbol.coord)
    call = constructor_call(parser, dtype, arguments, address, symbol.coord,
                            direct_list=direct_list, copy_initialization=copy_initialization)
    parser._expect(";")
    symbol.initialization_complete = True
    symbol.jump_entry = "initialized"
    return [parser._decl(symbol.c_name, symbol.dtype, symbol.coord),
            call]


def call_free_body(body):
    """A bounded proof, not a noexcept inference for arbitrary external calls."""
    pending = [body]
    while pending:
        node = pending.pop()
        if isinstance(node, c_ast.FuncCall):
            return False
        pending.extend(child for _name, child in node.children())
    return True


def constructor_call(parser, dtype, arguments, address, coord, require_closed=False, *,
                     direct_list=False, copy_initialization=False):
    """Select the existing C1 at an already-evaluated complete-object address."""
    group = dtype.definition.constructors
    if (len(arguments) == 1 and unqualified(arguments[0].dtype) is dtype
            and not any(is_copy_constructor(candidate, dtype) for candidate in group.overloads)):
        # A same-class argument can select an implicitly declared copy
        # constructor even when no spelled constructor is viable. Do not
        # mistake the incomplete candidate set for a no-viable diagnostic.
        raise CppDiagnostic("cpp.unsupported.class-initialization",
                            "implicit copy constructor declaration/definition requires special-member semantics", coord)
    if copy_initialization and not direct_list:
        from pcc.frontends.cpp.constructor_lists import converting_candidates
        group = converting_candidates(group)
    if direct_list or copy_initialization:
        from pcc.frontends.cpp.constructor_lists import require_simple_candidates
        require_simple_candidates(group, coord)
    selected = resolve_overload(group,
                                [arg.dtype for arg in arguments], parser.types, coord,
                                [arg.null_pointer for arg in arguments], [arg.lvalue or arg.assignment_result for arg in arguments])
    parser.scope.check_member_access(selected, coord)
    from pcc.frontends.cpp.class_template_members import request_body
    request_body(parser, selected, coord)
    if require_closed and not getattr(selected, 'closed_normal_body', False):
        raise CppDiagnostic('cpp.unsupported.placement-constructor-effects',
                            'unknown constructor effects need exception/cleanup integration', coord)
    if direct_list:
        # Narrowing is checked only after overload selection, never used to
        # discard a better candidate in favor of a non-narrowing alternative.
        from pcc.frontends.cpp.constructor_lists import check_arguments, sequence_arguments
        check_arguments(parser, arguments, selected.parameters)
        if copy_initialization and getattr(selected, "constructor_explicit", False):
            # The chosen explicit candidate makes this form ill-formed; do
            # not demand temporary materialization merely to diagnose it.
            raise CppDiagnostic("cpp.explicit-copy-list", "copy-list-initialization selected an explicit constructor", coord)
    from pcc.frontends.cpp.default_arguments import expand
    arguments = expand(parser, selected, arguments, coord)
    preparation = []
    if direct_list:
        # Only the supplied initializer-list clauses were narrowing-checked
        # above. Defaults were copy-initialized at their declaration, and are
        # not source-list clauses. Evaluating them after the supplied clauses
        # is a permitted implementation order, not a C++ ordering guarantee.
        preparation, converted = sequence_arguments(parser, arguments, selected.parameters)
    else:
        converted = [convert_constructor_argument(parser, arg, target, coord)
                     for arg, target in zip(arguments, selected.parameters)]
    call = c_ast.FuncCall(c_ast.ID(selected.c_name, coord), c_ast.ExprList([address] + converted, coord), coord)
    if preparation:
        return c_ast.StmtExpr(c_ast.Compound(preparation + [call], coord), coord)
    return call


def convert_constructor_argument(parser, expression, dtype, coord):
    """Borrow compatible class lvalues only at a selected C1 reference slot."""
    if isinstance(dtype, ReferenceType) and is_class_type(dtype.referent):
        from pcc.frontends.cpp.references import bind
        return bind(parser, expression, dtype, coord, allow_class=True)
    return parser._convert(expression, dtype, coord)


def is_copy_constructor(symbol, owner):
    """N4659 [class.copy.ctor]/1: a same-class reference plus only defaults."""
    if not symbol.parameters:
        return False
    first = symbol.parameters[0]
    if not isinstance(first, ReferenceType) or unqualified(first.referent) is not owner:
        return False
    defaults = getattr(symbol, "default_arguments", ())
    return len(symbol.parameters) == 1 or (
        len(defaults) == len(symbol.parameters) and all(defaults[1:]))
