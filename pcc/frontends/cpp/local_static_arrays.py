"""Constant-initialized block arrays, N4659 [dcl.init.aggr], [basic.start.static].

The caller has checked the local-static owner, including inline/template and
switch boundaries. Existing array Sema owns bounds, explicit braces and
narrowing. Only typed integral/enum constants reach shared C static storage;
no runtime expression or dynamic-initialization guard is projected.
"""

from pcc.frontends.cpp.arrays import (
    const_array_elements, deduce_list_bound, lower_array_initializer,
)
from pcc.frontends.cpp.constants import KNOWN, converted_fact
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.initializers import parse_initializer_list
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import array_type, is_enum_type


def declare(parser, name, dtype, *, for_init=False):
    leaf = dtype
    while array_type(leaf):
        leaf = leaf.element
    if not (parser.types.integral(leaf) or is_enum_type(leaf)):
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local static arrays require integral or enum elements', name.coord)
    # Do not complete an unknown bound before its initializer is parsed. This
    # also leaves a typedef's shared unknown-bound type unchanged.
    parser._check_object_type(dtype.element if dtype.bound is None else dtype, name.coord)
    symbol = parser.scope.declare(Symbol(
        name.value, 'value', name.coord, dtype=dtype,
        c_name=parser._fresh_projection_name('__pcc_cpp_static_'),
    ))

    def constant_element(expression, target, coord):
        parser._require_value(expression)
        parser._convert(expression, target, coord)
        proof = converted_fact(parser._constant_fact(expression), target, parser.types, coord)
        if proof.status != KNOWN:
            raise CppDiagnostic('cpp.unsupported.dynamic-initialization',
                                'a local static array initializer requires modeled target constants', coord)
        return parser._enum_constant_expression(target, proof.value, coord).node

    equals = parser._accept('=')
    if parser._peek().value == '{':
        initializer = parse_initializer_list(parser)
        if dtype.bound is None:
            dtype = deduce_list_bound(dtype, initializer, parser.types)
            parser._check_object_type(dtype, name.coord)
            symbol.dtype = dtype
        initializer = lower_array_initializer(dtype, initializer, parser.types, constant_element)
    elif equals is not None or parser._peek().value == '(':
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local static arrays require explicit brace initialization', name.coord)
    else:
        parser._check_object_type(dtype, name.coord)
        if const_array_elements(dtype):
            raise CppDiagnostic('cpp.const-initializer', 'const array elements require initialization', name.coord)
        # Shared static storage zero-initializes a missing initializer without
        # expanding one explicit initializer node per declared element.
        initializer = None
    if parser._peek().value == ',':
        code = 'cpp.unsupported.for-declaration-list' if for_init else 'cpp.unsupported.init-declarator-list'
        raise CppDiagnostic(code, 'multiple static init-declarators are not implemented', parser._peek().coord)
    parser._expect(';')
    symbol.initialization_complete = True
    symbol.jump_entry = 'static'
    # Const array elements do not turn the aggregate into an integral constant
    # expression: no fabricated scalar fact or initializer expression is saved.
    declaration = parser._decl(symbol.c_name, dtype, name.coord, initializer)
    declaration.storage = ['static']
    return declaration
