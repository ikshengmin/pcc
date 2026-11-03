"""Named static casts with owned cv-void-pointer to object-pointer semantics.

N4659 [expr.static.cast]/13: preserve the pointer value, with destination
pointee cv at least the source void pointee cv. The current type model owns
const only; volatile retains its declaration diagnostic. No object access,
construction, alignment assertion, base adjustment or reinterpretation owner
is introduced. Arithmetic casts retain the existing conversion path.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.declarators import DeclaratorParser
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ReferenceType,
    const_qualified,
    contains_array,
    contains_function,
    pointer_type,
    unqualified,
)


def convert_void_pointer(parser, dtype, expression, coord):
    from pcc.frontends.cpp.parser import Expression

    if parser.parsing_template or parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.static-cast',
                            'pointer static_cast in template patterns/replay is not implemented', coord)
    target, source = unqualified(dtype), unqualified(expression.dtype)
    if contains_function(target) or contains_function(source):
        raise CppDiagnostic('cpp.unsupported.function-pointers',
                            'function-pointer static_cast requires its own conversion rules', coord)
    if (not pointer_type(target) or not pointer_type(source)
            or unqualified(source.pointee) != 'void'
            or unqualified(target.pointee) == 'void'
            or isinstance(target.pointee, ReferenceType)):
        raise CppDiagnostic('cpp.unsupported.static-cast',
                            'this pointer static_cast requires cv void* and an object-pointer target', coord)
    if const_qualified(source.pointee) and not const_qualified(target.pointee):
        raise CppDiagnostic('cpp.invalid-conversion',
                            'static_cast from void* cannot remove pointee const qualification', coord)
    # Incomplete object types are allowed: converting the address neither
    # needs a layout nor reads the pointed-to object. The cast evaluates the
    # source once and preserves its pointer value.
    parser._require_value(expression)
    typename = c_ast.Typename(None, [], parser._scalar_type(None, target, coord), coord)
    node = c_ast.Cast(typename, expression.node, coord)
    if contains_array(target):
        # Generic C Cast projection erases array pointees. The existing typed
        # declaration owner rebuilds the complete pointer/array shape, at any
        # depth, before returning the value. This matters even for a discarded
        # *cast: an erased void** could otherwise fetch a pointer-sized value
        # from an array object. A builtin pointer temporary needs no class
        # lifetime action or cleanup; only its initializer holds the operand.
        name = parser._fresh_projection_name('__pcc_cpp_static_cast_')
        temporary = parser._decl(name, target, coord, node)
        node = c_ast.StmtExpr(c_ast.Compound([temporary, c_ast.ID(name, coord)], coord), coord)
    # [expr.const]/2 excludes conversion from void* to an object pointer in
    # C++17 core constant expressions. Do not mint a static-address proof.
    return Expression(node, target, constant_fact=not_constant(
        target, 'conversion from void* to an object pointer', coord))


def parse(parser, token):
    parser._expect('<')
    dtype = parser._type(check_lowering=False)
    declaration = DeclaratorParser(
        parser.cursor, parser._identifier,
        parse_parameters=parser._sizeof_parameter_syntax,
        parse_array_bound=parser._array_bound,
    ).parse_abstract()
    dtype = parser._alias_declarator_type(declaration, dtype)
    parser._check_value_type(dtype, token)
    parser._expect('>')
    parser._expect('(')
    expression = parser._expression()
    parser._expect(')')
    return parser._explicit_arithmetic_cast(dtype, expression, token.coord, 'static_cast')
