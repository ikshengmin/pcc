"""Pointer const-qualification casts, N4659 [expr.const.cast], [conv.qual].

Keep every pointer/array level, bound and nominal terminal type. This is not
reinterpret_cast, a base-class adjustment, an int*/void* conversion, or a
claim to implement volatile/reference/member-pointer lifetime semantics.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import unmodeled
from pcc.frontends.cpp.declarators import DeclaratorParser
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ArrayType, ConstType, PointerType, ReferenceType, function_type,
    pointer_type, unqualified,
)


def qualification_shape(dtype):
    """The complete P_i/U shape, ignoring only cv at each represented level."""
    if isinstance(dtype, ConstType):
        return qualification_shape(dtype.base)
    if isinstance(dtype, PointerType):
        return ('pointer', qualification_shape(dtype.pointee))
    if isinstance(dtype, ArrayType):
        return ('array', dtype.bound, qualification_shape(dtype.element))
    return ('terminal', dtype)


def convert(parser, dtype, expression, coord):
    from pcc.frontends.cpp.parser import Expression
    if parser.parsing_template or parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.const-cast',
                            'const_cast in template patterns/replay is not implemented', coord)
    if isinstance(dtype, ReferenceType):
        raise CppDiagnostic('cpp.unsupported.references',
                            'reference const_cast requires reference-result/lifetime semantics', coord)
    target = unqualified(dtype)
    expression = parser._decay_array(expression)
    source = unqualified(expression.dtype)
    if not pointer_type(target) or not pointer_type(source):
        raise CppDiagnostic('cpp.invalid-const-cast',
                            'this const_cast requires pointer operands and a pointer target', coord)
    if function_type(target.pointee) or function_type(source.pointee):
        raise CppDiagnostic('cpp.invalid-const-cast',
                            'const_cast cannot convert function pointers', coord)
    if qualification_shape(source) != qualification_shape(target):
        raise CppDiagnostic('cpp.invalid-const-cast',
                            'const_cast may change cv qualifications, not pointer/array shape or terminal type', coord)
    # Incomplete pointees are deliberately permitted: no layout, pointer
    # arithmetic, dereference, or object copy is performed by this conversion.
    parser._require_value(expression)
    typename = c_ast.Typename(None, [], parser._scalar_type(None, target, coord), coord)
    node = c_ast.Cast(typename, expression.node, coord)
    return Expression(node, target, constant_fact=unmodeled(
        target, 'const_cast constant-address evaluation is not implemented', coord))


def parse(parser, token):
    parser._expect('<')
    dtype = parser._type(check_lowering=False)
    declaration = DeclaratorParser(
        parser.cursor, parser._identifier,
        parse_parameters=parser._sizeof_parameter_syntax,
        parse_array_bound=parser._array_bound,
    ).parse_abstract()
    dtype = parser._alias_declarator_type(declaration, dtype)
    parser._expect('>')
    parser._expect('(')
    expression = parser._expression()
    parser._expect(')')
    return convert(parser, dtype, expression, token.coord)
