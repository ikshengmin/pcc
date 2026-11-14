"""Non-inline static data members of non-template classes.

N4659 [class.static.data], [basic.def]/2.3, [basic.link]/4, [basic.lookup.unqual]/13.
https://timsong-cpp.github.io/cppwp/n4659/class.static.data
A declaration in the class is not a definition: it declares one object with
static storage duration and the class's linkage, named `C::m` (Itanium
`_ZN1C1mE`), accessible under the member's access. Exactly one namespace-scope
definition `T C::m = init;` may follow; its initializer is looked up and
access-checked as if in a member function of C. Reads, writes and addresses
are those of an ordinary namespace object. The initializer reuses the
namespace-object static initialization owner (constants, null, addresses,
string literals); dynamic initialization stays explicit.

A member may be an array of scalar elements declared with its bound; the
definition may omit that major bound ([basic.link]/10) and initializes it
with explicit braces whose elements are those same static constants
([dcl.init.aggr]), omitted elements and a missing initializer zero-filled.

Explicit gaps: in-class initializers (constant and inline members), arrays
of unknown bound in the class and of class type, brace elision and string
initialization of arrays, class-type members, unions, local and
unnamed-namespace classes, class templates, and several declarators in one
declaration.
Original implementation; no upstream compiler source is copied.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.members import mangle_member
from pcc.frontends.cpp.arrays import const_array_elements
from pcc.frontends.cpp.objects import static_array_initializer, static_initializer, zero_initializer
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import same_type
from pcc.frontends.cpp.types import array_type, unqualified


def _gap(message, coord):
    raise CppDiagnostic("cpp.unsupported.static-data-members", message, coord)


def _check_type(parser, dtype, coord):
    leaf = dtype
    while array_type(leaf):
        if leaf.bound is None:
            _gap("static data member arrays of unknown bound require completion by the definition", coord)
        leaf = leaf.element
    if is_class_type(leaf):
        _gap("static data members of class type require static initialization and lifetime semantics", coord)
    parser._check_object_type(dtype, coord)


def parse_static_data_member(parser, name, dtype, storage, member_definition, owner):
    if member_definition is not None:
        return _declare(parser, name, dtype, storage, member_definition)
    return _define(parser, name, dtype, storage, owner)


def _declare(parser, name, dtype, storage, definition):
    coord = name.coord
    if storage is None or storage.value != "static":
        raise CppDiagnostic("cpp.invalid-member-storage", "a data member cannot be declared extern",
                            storage.coord if storage is not None else coord)
    owner = definition.dtype
    if owner.is_union:
        _gap("static data members of unions are not implemented", coord)
    if getattr(owner, "internal_linkage", False):
        _gap("static data members of internal-linkage classes require ODR-use tracking", coord)
    if getattr(parser, "class_template_instance", None) is not None or parser.parsing_template:
        _gap("static data members of class templates require per-specialization definitions", coord)
    _check_type(parser, dtype, coord)
    if parser._peek().value in ("=", "{"):
        _gap("in-class initializers of static data members (constant or inline members) are not implemented",
             parser._peek().coord)
    if parser._peek().value == ",":
        raise CppDiagnostic("cpp.unsupported.init-declarator-list",
                            "multiple object declarators require separate point-of-declaration processing",
                            parser._peek().coord)
    parser._expect(";")
    if name.value in definition.scope.names or name.value == owner.name:
        raise CppDiagnostic("cpp.redeclaration", "static data member conflicts with a member", coord)
    symbol = Symbol(name.value, "value", coord, dtype=dtype)
    symbol.c_name = mangle_member(owner, name.value, ())[:-1]  # `_ZN..E`, no parameter list
    symbol.class_owner = owner
    symbol.access = definition.access
    symbol.static_data_member = True
    symbol.defined = False
    symbol.internal_linkage = False
    definition.scope.declare(symbol)
    declaration = parser._decl(symbol.c_name, dtype, coord)
    declaration.storage = ["extern"]
    symbol.object_declaration = declaration
    parser.namespace_objects.append(symbol)
    return declaration


def _define(parser, name, dtype, storage, owner):
    coord = name.coord
    if storage is not None:
        raise CppDiagnostic("cpp.invalid-member-storage",
                            "a static data member definition cannot repeat a storage class", storage.coord)
    symbol = owner.scope.names.get(name.value)
    if symbol is None or not getattr(symbol, "static_data_member", False):
        raise CppDiagnostic("cpp.no-matching-member",
                            "definition does not match a declared static data member", coord)
    if (array_type(dtype) and dtype.bound is None and array_type(symbol.dtype)
            and same_type(symbol.dtype.element, dtype.element)):
        # [basic.link]/10: the definition may omit the declared major bound.
        dtype = symbol.dtype
    if not same_type(unqualified(symbol.dtype), unqualified(dtype)) or not same_type(symbol.dtype, dtype):
        raise CppDiagnostic("cpp.conflicting-object-type",
                            "static data member definition has a different type", coord)
    if symbol.defined:
        raise CppDiagnostic("cpp.redefinition", "static data member has more than one definition", coord)
    # [basic.lookup.unqual]/13: names after the declarator are looked up, and
    # access-checked, as in a member function of the class.
    previous = parser.scope
    parser.scope = Scope(owner.scope)
    try:
        if array_type(dtype):
            proof = None
            if parser._peek().value in ("=", "{"):
                init, dtype = static_array_initializer(parser, dtype, coord)
            elif const_array_elements(dtype):
                raise CppDiagnostic("cpp.const-initializer", "const array elements require initialization", coord)
            else:
                # Zero-initialized by the shared C aggregate owner.
                init = c_ast.InitList([], coord)
        elif parser._peek().value in ("=", "{"):
            init, proof = static_initializer(parser, dtype, coord)
        else:
            init, proof = zero_initializer(parser, dtype, coord), None
    finally:
        parser.scope = previous
    if parser._peek().value == ",":
        raise CppDiagnostic("cpp.unsupported.init-declarator-list",
                            "multiple object declarators require separate point-of-declaration processing",
                            parser._peek().coord)
    parser._expect(";")
    symbol.defined = True
    symbol.initialization_complete = True
    if proof is not None and dtype != unqualified(dtype):
        symbol.constant_fact = proof
    definition = parser._decl(symbol.c_name, dtype, coord)
    definition.init = init
    definition.storage = []
    return definition


def object_member_access(parser, expression, operator, symbol, member):
    """`x.m` / `p->m` naming a static data member ([expr.ref]/4.1).

    The object expression is still evaluated (discarded); the result is the
    static object itself, an lvalue: `*(base, &C::m)`.
    """
    if operator == ".":
        if not expression.lvalue:
            raise CppDiagnostic("cpp.unsupported.class-temporaries",
                                "class temporary materialization is not implemented", member.coord)
        if not expression.discarded_known:
            raise CppDiagnostic("cpp.unsupported.discarded-class-expression",
                                "class glvalue discarded evaluation is not modeled", member.coord)
        discarded = expression.discarded_node
    else:
        discarded = expression.node
    from pcc.frontends.cpp.parser import Expression
    node = c_ast.ID(symbol.c_name, member.coord)
    if discarded is not None:
        address = c_ast.UnaryOp("&", node, member.coord)
        node = c_ast.UnaryOp("*", c_ast.ExprList([discarded, address], member.coord), member.coord)
    return Expression(node, symbol.dtype, True, symbol, decltype_entity=symbol.dtype)
