"""Single-level object-pointer reinterpretation, N4659 [expr.reinterpret.cast].

Reuse the existing address representation without granting an access, alignment
or lifetime proof. Const may be added but not removed. Nested pointer/array,
reference, function and integer conversions retain separate capability gates.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.declarators import DeclaratorParser
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ReferenceType,
    array_type,
    const_qualified,
    contains_function,
    pointer_type,
    unqualified,
)


def convert(parser, dtype, expression, coord):
    from pcc.frontends.cpp.parser import Expression

    if parser.parsing_template or parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.pointer-templates',
                            'reinterpret_cast in template patterns/replay is not implemented', coord)
    if isinstance(dtype, ReferenceType):
        raise CppDiagnostic('cpp.unsupported.references',
                            'reference reinterpret_cast requires reference-result semantics', coord)
    target = unqualified(dtype)
    expression = parser._decay_array(expression)
    source = unqualified(expression.dtype)
    if contains_function(target) or contains_function(source):
        raise CppDiagnostic('cpp.unsupported.function-pointers',
                            'function-pointer reinterpret_cast requires its own conversion rules', coord)
    if target == 'nullptr' or source == 'nullptr' and pointer_type(target):
        raise CppDiagnostic('cpp.invalid-reinterpret-cast',
                            'reinterpret_cast cannot convert nullptr_t to a pointer or produce nullptr_t', coord)
    if not pointer_type(target) or not pointer_type(source):
        # Keep the preexisting syntax frontier for forms whose integer or
        # other representation mapping is not supplied by this owner.
        raise CppDiagnostic('cpp.unsupported.syntax',
                            'this reinterpret_cast requires object pointers on both sides', coord)
    if pointer_type(target.pointee) or pointer_type(source.pointee):
        raise CppDiagnostic('cpp.unsupported.multilevel-pointers',
                            'reinterpret_cast across nested pointer qualifications is not implemented', coord)
    if array_type(target.pointee) or array_type(source.pointee):
        raise CppDiagnostic('cpp.unsupported.syntax',
                            'reinterpret_cast of pointer-to-array representations is not implemented', coord)
    if const_qualified(source.pointee) and not const_qualified(target.pointee):
        raise CppDiagnostic('cpp.invalid-reinterpret-cast',
                            'reinterpret_cast cannot remove pointee const qualification', coord)
    # Pointees may be incomplete: conversion alone does not require layout or
    # access their storage. Existing object-pointer representation is unchanged,
    # including typed nulls and the address of a pointer-interconvertible object.
    parser._require_value(expression)
    typename = c_ast.Typename(None, [], parser._scalar_type(None, target, coord), coord)
    node = c_ast.Cast(typename, expression.node, coord)
    # [expr.const]/2 excludes reinterpret_cast even when the operand itself
    # carries an address/null constant fact. Never mint a static-address proof.
    return Expression(node, target, constant_fact=not_constant(
        target, 'reinterpret_cast is not a C++17 core constant expression', coord))


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
