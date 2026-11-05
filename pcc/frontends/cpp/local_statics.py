"""Constant-initialized block statics, N4659 [basic.start.static], [stmt.dcl].

Only integral/enum '=' constants and zero initialization are admitted. The
shared C declaration owner supplies persistent internal storage, not a runtime
guard. Dynamic initialization, destruction, inline/ODR sharing and switch-body
declaration hoisting stay gated.
"""

from pcc.frontends.cpp.constants import KNOWN, converted_fact
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import ConstType, ReferenceType, is_enum_type


def declare(parser, *, for_init=False):
    keyword = parser._expect('static')
    # A constructor can be bound lazily while a calling function is active.
    # Its member owner, not the caller's current_function, owns this storage.
    owner = parser.member_function or parser.current_function
    if (owner is None or getattr(owner, 'definition_linkage', None) == 'external_odr'
            or parser.parsing_template or parser.template_function is not None
            or parser.instantiating_template or getattr(parser, 'class_template_method', None) is not None):
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local statics require a non-template, non-inline function definition', keyword.coord)
    if parser.switch_contexts:
        # The shared C switch owner rejects initialized declarations crossed
        # by dispatch. Do not erase that protection or reach a backend error.
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local statics in switch bodies require declaration hoisting', keyword.coord)
    if parser._peek().value in ('static', 'extern'):
        raise CppDiagnostic('cpp.invalid-storage-specifier',
                            'multiple storage class specifiers', parser._peek().coord)
    dtype = parser._type(storage_specifiers=[keyword])
    name, dtype = parser._declarator(dtype, allow_class_element=True)
    if isinstance(dtype, ReferenceType):
        raise CppDiagnostic('cpp.unsupported.references',
                            'static reference initialization is not implemented', name.coord)
    if not (parser.types.integral(dtype) or is_enum_type(dtype)):
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local statics currently require integral or enum objects', name.coord)
    parser._check_object_type(dtype, name.coord)
    # C's static symbol identity contains the function and projected name.
    # Sibling C++ blocks may reuse a spelling but must not share one object.
    symbol = parser.scope.declare(Symbol(
        name.value, 'value', name.coord, dtype=dtype,
        c_name=parser._fresh_projection_name('__pcc_cpp_static_'),
    ))
    proof = None
    if parser._accept('='):
        if parser._peek().value == '{':
            raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                                'local static list initialization is not implemented', name.coord)
        expression = parser._expression()
        parser._require_value(expression)
        parser._convert(expression, dtype, name.coord)
        proof = converted_fact(parser._constant_fact(expression), dtype, parser.types, name.coord)
        if proof.status != KNOWN:
            raise CppDiagnostic('cpp.unsupported.dynamic-initialization',
                                'a local static initializer requires a modeled target constant', name.coord)
        initializer = parser._enum_constant_expression(dtype, proof.value, name.coord).node
    elif parser._peek().value in ('{', '('):
        raise CppDiagnostic('cpp.unsupported.static-initialization-analysis',
                            'local statics currently require an equals initializer', name.coord)
    else:
        if isinstance(dtype, ConstType):
            raise CppDiagnostic('cpp.const-initializer', 'const objects require an initializer', name.coord)
        initializer = parser._enum_constant_expression(dtype, 0, name.coord).node
    if parser._peek().value == ',':
        code = 'cpp.unsupported.for-declaration-list' if for_init else 'cpp.unsupported.init-declarator-list'
        raise CppDiagnostic(code, 'multiple static init-declarators are not implemented', parser._peek().coord)
    parser._expect(';')
    symbol.initialization_complete = True
    symbol.jump_entry = 'static'
    if isinstance(dtype, ConstType) and proof is not None:
        symbol.constant_fact = proof
    declaration = parser._decl(symbol.c_name, dtype, name.coord, initializer)
    declaration.storage = ['static']
    return declaration
