"""Bound scalar default member initializers: N4659 [class.mem], [class.base.init].

Original tokens are parsed after class completion, before leaving its declaration
point. Lowered receiver nodes retain identity; substitution is never by spelling.
No implicit constructor symbol, inline ownership or exception machinery is added.
"""
from copy import deepcopy

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import PointerType, unqualified


def unsupported(coord):
    raise CppDiagnostic('cpp.unsupported.default-member-initializer-path',
                        'this initialization path does not yet consume default member initializers', coord)


def has_initializers(definition):
    from pcc.frontends.cpp.classes import is_class_type
    return any(getattr(member, 'default_initializer_tokens', None) is not None
               or (is_class_type(member.dtype) and has_initializers(unqualified(member.dtype).definition))
               for member in definition.members)


def collect(parser):
    """Retain a declaration tail intact; semantic parsing decides its grammar."""
    tokens, closings = [], []
    pairs = {'(': ')', '[': ']', '{': '}'}
    while closings or parser._peek().value != ';':
        token = parser.cursor.take()
        if token.kind == 'EOF':
            raise CppDiagnostic('cpp.invalid-default-member-initializer', 'unterminated initializer', token.coord)
        if token.value in pairs:
            closings.append(pairs[token.value])
        elif token.value in (')', ']', '}'):
            if not closings or closings.pop() != token.value:
                raise CppDiagnostic('cpp.invalid-default-member-initializer', 'unbalanced initializer', token.coord)
        tokens.append(token)
    return tuple(tokens)


def bind(parser, definition):
    """Bind in completed class scope, not the scope of any consuming constructor."""
    from pcc.frontends.cpp.parser import CppToken, TokenCursor
    owner = definition.dtype
    from pcc.frontends.cpp.classes import is_class_type
    if has_initializers(definition) and any(is_class_type(member.dtype) for member in definition.members):
        unsupported(owner.coord)
    if not any(getattr(member, 'default_initializer_tokens', None) is not None for member in definition.members):
        return
    previous = (parser.cursor, parser.scope, parser.member_function, parser.this_symbol,
                getattr(parser, 'default_initializer_receivers', None))
    context = Symbol('<default-member-initializer>', 'function', owner.coord, dtype='void')
    context.class_owner = owner
    context.method_static = context.method_const = False
    this = Symbol('this', 'value', owner.coord, dtype=PointerType(owner),
                  c_name=parser._local_projection_name('dmi_this'))
    try:
        parser.scope, parser.member_function, parser.this_symbol = definition.scope, context, this
        for member in definition.members:
            tokens = getattr(member, 'default_initializer_tokens', None)
            if tokens is None:
                continue
            parser.cursor = TokenCursor(tokens + (CppToken('EOF', '', member.coord),))
            parser.default_initializer_receivers = []
            expression = parser._expression()
            if parser._peek().kind != 'EOF':
                # No guessed split at commas: template argument commas belong
                # to the real parser. Multiple declarators remain a named gap.
                unsupported(parser._peek().coord)
            parser._require_value(expression)
            member.default_initializer_node = parser._convert(expression, member.dtype, member.coord)
            member.default_initializer_receivers = tuple(parser.default_initializer_receivers)
    finally:
        (parser.cursor, parser.scope, parser.member_function, parser.this_symbol,
         parser.default_initializer_receivers) = previous


def value(member, receiver):
    """Clone the bound tree and replace only recorded receiver node identities."""
    replacements = {id(node): deepcopy(receiver) for node in member.default_initializer_receivers}
    return deepcopy(member.default_initializer_node, replacements)


def initialize_local(parser, symbol):
    """Default-initialize an automatic complete object; omit unspecified stores."""
    from pcc.frontends.cpp.classes import default_initialize
    default_initialize(symbol.dtype, symbol.coord)
    parser._expect(';')
    receiver = c_ast.UnaryOp('&', c_ast.ID(symbol.c_name, symbol.coord), symbol.coord)
    statements = [parser._decl(symbol.c_name, symbol.dtype, symbol.coord)]
    for member in unqualified(symbol.dtype).definition.members:
        if getattr(member, 'default_initializer_tokens', None) is None:
            continue
        field = c_ast.StructRef(deepcopy(receiver), '->', c_ast.ID(member.c_name, member.coord), member.coord)
        statements.append(c_ast.Assignment('=', field, value(member, receiver), member.coord))
    symbol.initialization_complete = True
    symbol.jump_entry = 'initialized'
    return statements
