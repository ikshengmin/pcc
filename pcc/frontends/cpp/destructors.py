"""Complete-object destructors with owned declaration/access rules.

N4659 [class.dtor]; Itanium ABI D1 complete-object destructor encoding. There
is no D0 delete, D2 base destruction or unwind claim. A destructor is virtual
if declared so or if its base's destructor is (virtual_functions.py); the
destructor of a polymorphic class stores its own table pointer on entry.
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


def parse_parameters(parser, definition, name, *, check_lowering=True):
    """The same destructor declaration rules apply to primary Patterns and D1s."""
    if name.value != definition.dtype.name:
        raise CppDiagnostic("cpp.invalid-destructor", "destructor name must match its class", name.coord)
    parent = parser.scope
    parameter_scope = Scope(definition.scope)
    parser.scope = parameter_scope
    try:
        parser._expect("(")
        params = parser._parameter_clause(check_lowering=check_lowering)
        parser._expect(")")
    finally:
        parser.scope = parent
    if params:
        raise CppDiagnostic("cpp.destructor-parameters", "destructors take no arguments", name.coord)
    if parser._peek().value in ("const", "volatile"):
        raise CppDiagnostic("cpp.destructor-cv", "destructors cannot have cv-qualifiers", parser._peek().coord)
    if parser._peek().value in ("noexcept", "throw"):
        raise CppDiagnostic("cpp.unsupported.exceptions", "destructor exception specifications and unwinding are not implemented", parser._peek().coord)
    return parameter_scope


def declare_destructor(definition, name, *, encode_name=True):
    """Retain one declaration identity; primary Patterns have no concrete ABI yet."""
    if definition.destructor is not None:
        raise CppDiagnostic("cpp.redeclaration", "destructor cannot be redeclared in its class", name.coord)
    owner = definition.dtype
    symbol = Symbol("~" + owner.name, "function", name.coord, dtype="void", parameters=())
    symbol.class_owner, symbol.access = owner, definition.access
    symbol.owner = definition.scope
    symbol.destructor = True
    symbol.destructor_user_provided = True
    symbol.destructor_trivial = False
    symbol.method_static = symbol.method_const = False
    symbol.internal_linkage = owner.internal_linkage
    if encode_name:
        # Empty member-name encoding is the fixed suffix 0Ev. Keep the same
        # namespace/type prefix as other members but encode complete-object D1.
        symbol.c_name = mangle_member(owner, "", ())[:-3] + "D1Ev"
    definition.destructor = symbol
    return symbol


def declaration(parser, symbol):
    """Project a concrete complete-object D1 and its non-const this parameter."""
    coord = symbol.coord
    this = Symbol("this", "value", coord, dtype=PointerType(symbol.class_owner),
                  c_name=parser._local_projection_name("this"))
    result = parser._decl(symbol.c_name, "void", coord)
    result.type = c_ast.FuncDecl(c_ast.ParamList([
        parser._decl(this.c_name, this.dtype, coord)], coord), result.type, coord)
    if symbol.internal_linkage:
        result.storage = ["static"]
    return result, this


def parse_virt_specifiers(parser):
    """[class.mem]: an in-class destructor declarator may end with override/final."""
    specifiers = []
    while parser._peek().kind == "ID" and parser._peek().value in ("override", "final"):
        token = parser.cursor.take()
        if token.value in specifiers:
            raise CppDiagnostic("cpp.duplicate-virt-specifier", "a virt-specifier may occur only once", token.coord)
        specifiers.append(token.value)
    return tuple(specifiers)


def parse_destructor(parser, definition=None, *, virtual_token=None):
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
    parameter_scope = parse_parameters(parser, definition, name)
    specifiers = () if outside else parse_virt_specifiers(parser)
    defaulted = parser._peek().value == "="
    if defaulted:
        if outside:
            raise CppDiagnostic("cpp.unsupported.inline-member-functions",
                                "later destructor defaulting requires user-provided special-member semantics", name.coord)
        parser._expect("=")
        if parser._peek().value != "default":
            raise CppDiagnostic("cpp.unsupported.inline-member-functions",
                                "deleted destructor declarations require special-member rules", parser._peek().coord)
        parser._expect("default")
        parser._expect(";")
    inline_definition = not outside and parser._peek().value == "{"
    if not outside and not defaulted and not inline_definition and parser._peek().value != ";":
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
        symbol = declare_destructor(definition, name)
    parser.function_projection_names.add(symbol.c_name)
    function_declaration, this = declaration(parser, symbol)
    if not outside:
        from pcc.frontends.cpp.virtual_functions import declare_destructor as declare_virtual_destructor
        declare_virtual_destructor(definition, symbol, function_declaration, virtual_token, specifiers,
                                   defaulted, name.coord)
    if defaulted:
        from pcc.frontends.cpp.default_destructors import defer_definition
        defer_definition(definition, symbol, parameter_scope, function_declaration, this)
        return function_declaration
    if inline_definition:
        if owner.internal_linkage:
            raise CppDiagnostic("cpp.unsupported.class-linkage",
                                "in-class destructors of internal-linkage classes require distinct definition ownership", name.coord)
        from pcc.frontends.cpp.inline_constructors import collect_body
        from pcc.frontends.cpp.inline_methods import DeferredMethod
        symbol.definition_linkage = "external_odr"
        definition.inline_methods.append(DeferredMethod(
            symbol, "void", parameter_scope, [], function_declaration, this,
            collect_body(parser), name.coord,
        ))
        return function_declaration
    if not outside:
        parser._expect(";")
        return function_declaration
    return bind_destructor_body(parser, definition, symbol, parameter_scope, function_declaration, this, name.coord)


def member_destructors(definition, scope=None, *, parser=None):
    """Select declared member D1s in construction order, without synthesis."""
    from pcc.frontends.cpp.member_initialization import requires_destruction
    selected = []
    from pcc.frontends.cpp.inheritance import base_slot
    slot = base_slot(definition)
    if slot is not None:
        # [class.dtor]/9: the base subobject is destroyed after every member.
        base_definition = slot.dtype.definition
        destructor = base_definition.destructor
        if destructor is not None and scope is not None:
            # Named from the derived class: its implicit object is the derived this.
            scope.check_named_member_access(destructor, slot.coord)
            from pcc.frontends.cpp.default_destructors import require_usable
            require_usable(destructor, slot.coord)
        elif scope is not None:
            from pcc.frontends.cpp.default_destructors import require_implicit_usable
            require_implicit_usable(base_definition, slot.coord)
        if requires_destruction(slot.dtype):
            if destructor is None:
                raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                                    "implicit base destruction requires special-member synthesis", slot.coord)
            if parser is not None:
                request_body(parser, destructor, slot.coord)
            selected.append((slot, destructor))
    for member in definition.members:
        owner = unqualified(member.dtype)
        destructor = owner.definition.destructor if is_class_type(owner) else None
        # Even a trivial declared D1 is potentially invoked, so its access
        # and deletion constraints cannot disappear with an omitted call.
        if destructor is not None and scope is not None:
            check_usable(scope, destructor, member.coord)
        elif is_class_type(owner) and scope is not None:
            from pcc.frontends.cpp.default_destructors import require_implicit_usable
            require_implicit_usable(owner.definition, member.coord)
        if not requires_destruction(member.dtype):
            continue
        if destructor is None:
            raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                                "implicit member destruction requires special-member synthesis", member.coord)
        if parser is not None:
            request_body(parser, destructor, member.coord)
        selected.append((member, destructor))
    return selected


def check_usable(scope, symbol, coord):
    scope.check_member_access(symbol, coord)
    from pcc.frontends.cpp.default_destructors import require_usable
    require_usable(symbol, coord)


def request_body(parser, symbol, coord):
    from pcc.frontends.cpp.default_destructors import request_definition
    request_definition(parser, symbol, coord)
    from pcc.frontends.cpp.class_template_members import request_body as request_template_body
    request_template_body(parser, symbol, coord)


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


def complete_body(parser, definition, symbol, scope, this, coord, bind_body):
    """One cleanup/proof owner for parsed bodies and concretely replayed Patterns."""
    selected = member_destructors(definition, scope, parser=parser)
    previous_calls = parser.lifetimes.cleanup_destructors
    parser.lifetimes.cleanup_destructors = {}
    parser.lifetimes.enter()
    try:
        parser.lifetimes.register_members(this, selected)
        body = bind_body()
        if definition.polymorphic:
            # [class.cdtor]/4: a derived destructor left this subobject's
            # table pointer at its own table; restore the class's before the body.
            from pcc.frontends.cpp.virtual_functions import vptr_store
            body.block_items.insert(0, vptr_store(definition, this, coord))
        body.block_items.extend(parser.lifetimes.current_cleanup(coord))
        closed = _closed_destruction_body(body, parser.lifetimes.cleanup_destructors)
    finally:
        parser.lifetimes.leave()
        parser.lifetimes.cleanup_destructors = previous_calls
    # Every destructor admitted here has the implicit non-throwing exception
    # specification ([except.spec]/8: explicit specifications are rejected
    # above and no member or base destructor may throw), so an exception
    # leaving the body calls std::terminate ([except.terminate]) and needs no
    # member cleanup path. The unproven body stays recorded as not closed for
    # owners that use the proof.
    symbol.closed_destruction_body = closed
    from pcc.frontends.cpp.constructors import call_free_body
    symbol.closed_normal_body = call_free_body(body)
    return body


def bind_destructor_body(parser, definition, symbol, parameter_scope, declaration, this, coord):
    """Body locals die before members on both fallthrough and early return."""
    body = complete_body(
        parser, definition, symbol, parameter_scope, this, coord,
        lambda: parser._bind_function_body(
            symbol, "void", parameter_scope, [], declaration, this, definition, coord,
        ).body,
    )
    return c_ast.FuncDef(declaration, None, body, coord)
