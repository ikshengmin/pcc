"""Non-inline complete-object destructors with owned declaration/access rules.

N4659 [class.dtor]; Itanium ABI D1 complete-object destructor encoding. There
is no D0 delete, D2 base destruction, virtual dispatch, inline/COMDAT or unwind
claim. Scalar/pointer subobjects need no emitted destructor calls.
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
    if not outside and parser._peek().value != ";":
        raise CppDiagnostic("cpp.unsupported.inline-member-functions", "in-class/defaulted destructors require inline special-member emission", name.coord)
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
    if not outside:
        parser._expect(";")
        return declaration
    previous_member, previous_this, previous_return = parser.member_function, parser.this_symbol, parser.return_type
    parser.scope = parameter_scope
    parser.member_function, parser.this_symbol, parser.return_type = symbol, this, "void"
    try:
        body = parser._block(new_scope=False)
    finally:
        parser.scope = parent
        parser.member_function, parser.this_symbol, parser.return_type = previous_member, previous_this, previous_return
    return c_ast.FuncDef(declaration, None, body, name.coord)
