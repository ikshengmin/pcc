"""Complete-object destructors with owned declaration/access rules.

N4659 [class.dtor]; Itanium ABI D1 complete-object destructor encoding. There
is no D0 delete, D2 base destruction, virtual dispatch or unwind claim.
In-class bodies use the existing completed-class external-ODR method owner.
Explicit member D1 calls share the normal-exit cleanup owner; implicit D1
synthesis, unknown effects and exception cleanup remain explicit boundaries.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.declarators import QualifiedName, _scope_separator
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.members import mangle_member
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import PointerType, unqualified


def _qualified_name(parser):
    global_scope = _scope_separator(parser.cursor) is not None
    tokens = [parser._identifier()]
    while _scope_separator(parser.cursor) is not None:
        if parser._accept("~"):
            name = parser._identifier()
            return QualifiedName(tokens, global_scope), name
        tokens.append(parser._identifier())
    raise CppDiagnostic("cpp.invalid-destructor", "out-of-class destructor requires qualified ~class-name", parser._peek().coord)


def destructor_ahead(parser):
    with parser.cursor.tentative():
        try:
            _qualified_name(parser)
            return True
        except CppDiagnostic:
            return False


def parse_destructor(parser, definition=None):
    parent = parser.scope
    outside = definition is None
    if outside:
        qualifier, name = _qualified_name(parser)
        target = parent.resolve_name(qualifier)
        if target is None or target.kind != "type" or not is_class_type(target.dtype):
            raise CppDiagnostic("cpp.invalid-destructor", "destructor owner must name a class", name.coord)
        owner = unqualified(target.dtype)
        require_complete(owner, name.coord)
        definition = owner.definition
        enclosing = owner.declaration_scope
        while enclosing is not None and enclosing is not parent:
            enclosing = enclosing.parent
        if enclosing is None:
            raise CppDiagnostic("cpp.member-definition-namespace", "destructor definition must be in an enclosing namespace", name.coord)
    else:
        parser._expect("~")
        name = parser._identifier()
        owner = definition.dtype
    if name.value != owner.name:
        raise CppDiagnostic("cpp.invalid-destructor", "destructor name must match its class", name.coord)
    parameter_scope = Scope(definition.scope)
    parser.scope = parameter_scope
    try:
        parser._expect("(")
        params = parser._parameter_clause()
        parser._expect(")")
    finally:
        parser.scope = parent
    if params:
        raise CppDiagnostic("cpp.destructor-parameters", "destructors take no arguments", name.coord)
    if parser._peek().value in ("const", "volatile"):
        raise CppDiagnostic("cpp.destructor-cv", "destructors cannot have cv-qualifiers", parser._peek().coord)
    if parser._peek().value in ("noexcept", "throw"):
        raise CppDiagnostic("cpp.unsupported.exceptions", "destructor exception specifications and unwinding are not implemented", parser._peek().coord)
    inline_definition = not outside and parser._peek().value == "{"
    if not outside and not inline_definition and parser._peek().value != ";":
        raise CppDiagnostic("cpp.unsupported.inline-member-functions", "defaulted/deleted destructors require special-member rules", name.coord)
    if outside and parser._peek().value != "{":
        raise CppDiagnostic("cpp.member-definition-required", "out-of-class destructor requires a body", name.coord)
    previous = definition.destructor
    if outside:
        if previous is None:
            raise CppDiagnostic("cpp.no-matching-member", "destructor definition has no matching declaration", name.coord)
        if previous.defined:
            raise CppDiagnostic("cpp.redefinition", "destructor is already defined", name.coord)
        symbol = previous
        symbol.defined = True
    else:
        if previous is not None:
            raise CppDiagnostic("cpp.redeclaration", "destructor cannot be redeclared in its class", name.coord)
        symbol = Symbol("~" + owner.name, "function", name.coord, dtype="void", parameters=())
        symbol.class_owner, symbol.access = owner, definition.access
        symbol.owner = definition.scope
        symbol.destructor = True
        symbol.method_static = False
        symbol.method_const = False
        symbol.internal_linkage = owner.internal_linkage
        # Empty member-name encoding is the fixed suffix 0Ev. Keep the same
        # namespace/type prefix as other members but encode complete-object D1.
        symbol.c_name = mangle_member(owner, "", ())[:-3] + "D1Ev"
        definition.destructor = symbol
    parser.function_projection_names.add(symbol.c_name)
    this = Symbol("this", "value", name.coord, dtype=PointerType(owner),
                  c_name=parser._local_projection_name("this"))
    declaration = parser._decl(symbol.c_name, "void", name.coord)
    declaration.type = c_ast.FuncDecl(c_ast.ParamList([
        parser._decl(this.c_name, this.dtype, name.coord)], name.coord), declaration.type, name.coord)
    if symbol.internal_linkage:
        declaration.storage = ["static"]
    if inline_definition:
        if owner.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.class-linkage",
                                "in-class destructors of internal-linkage classes require distinct definition ownership", name.coord)
        from pcc.frontends.cpp.inline_constructors import collect_body
        from pcc.frontends.cpp.inline_methods import DeferredMethod
        symbol.definition_linkage = "external_odr"
        definition.inline_methods.append(DeferredMethod(
            symbol, "void", parameter_scope, [], declaration, this,
            collect_body(parser), name.coord,
        ))
        return declaration
    if not outside:
        parser._expect(";")
        return declaration
    return bind_destructor_body(parser, definition, symbol, parameter_scope, declaration, this, name.coord)


def member_destructors(definition, scope=None):
    """Select declared member D1s in construction order, without synthesis."""
    from pcc.frontends.cpp.member_initialization import requires_destruction
    selected = []
    for member in definition.members:
        if not requires_destruction(member.dtype):
            continue
        owner = unqualified(member.dtype)
        destructor = owner.definition.destructor if is_class_type(owner) else None
        if destructor is None:
            raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                                "implicit member destruction requires special-member synthesis", member.coord)
        if scope is not None:
            scope.check_member_access(destructor, member.coord)
        selected.append((member, destructor))
    return selected


def _closed_destruction_body(body, cleanup_destructors):
    """Prove a finite chain of bound bodies, not merely absence of throw syntax.

    Only exact cleanup-call nodes emitted by our lifetime owner may recurse
    into a previously proven D1. A name, declaration, user call or unknown
    function effect cannot establish this proof.
    """
    pending = [body]
    while pending:
        node = pending.pop()
        if isinstance(node, c_ast.FuncCall):
            selected = cleanup_destructors.get(node)
            if selected is None or not getattr(selected, "closed_destruction_body", False):
                return False
        pending.extend(child for _name, child in node.children())
    return True


def bind_destructor_body(parser, definition, symbol, parameter_scope, declaration, this, coord):
    """Body locals die before members on both fallthrough and early return."""
    selected = member_destructors(definition, parameter_scope)
    previous_calls = parser.lifetimes.cleanup_destructors
    parser.lifetimes.cleanup_destructors = {}
    parser.lifetimes.enter()
    try:
        parser.lifetimes.register_members(this, selected)
        function = parser._bind_function_body(
            symbol, "void", parameter_scope, [], declaration, this, definition, coord,
        )
        function.body.block_items.extend(parser.lifetimes.current_cleanup(coord))
        closed = _closed_destruction_body(function.body, parser.lifetimes.cleanup_destructors)
    finally:
        parser.lifetimes.leave()
        parser.lifetimes.cleanup_destructors = previous_calls
    if selected and not closed:
        raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                            "unknown destruction effects require member exception cleanup", coord)
    symbol.closed_destruction_body = closed
    from pcc.frontends.cpp.constructors import call_free_body
    symbol.closed_normal_body = call_free_body(function.body)
    return function
