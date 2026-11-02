"""Declaration-bound ordinary default arguments, N4659 [dcl.fct.default].

Defaults are not function types or ABI parameters. Namespace expressions bind
at their declaration point; class expressions bind in completed class scope.
Calls clone typed bound expressions, never reparse names in the caller scope.
"""
from copy import deepcopy

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope
from pcc.frontends.cpp.types import unqualified


class DefaultArgument:
    def __init__(self, tokens, dtype, scope, parameters, coord):
        self.tokens = tuple(tokens)
        self.dtype = dtype
        self.scope = scope
        self.parameters = tuple(parameters)
        self.coord = coord
        self.function = None
        self.expression = None
        self.binding = False
        self.declaration_index = None


def collect(parser, dtype):
    """Retain the exact assignment-expression token interval, no rewriting."""
    token = parser._expect('=')
    if parser.parsing_template or parser.template_function is not None:
        raise CppDiagnostic('cpp.unsupported.default-argument-context',
                            'template default arguments are not implemented', token.coord)
    if parser._peek().value == '{':
        raise CppDiagnostic('cpp.unsupported.default-argument-context',
                            'braced default arguments require list-initialization semantics', token.coord)
    tokens, closings = [], []
    pairs = {'(':')', '[':']', '{':'}'}
    while closings or parser._peek().value not in (',', ')'):
        current = parser.cursor.take()
        if current.kind == 'EOF' or current.value == ';' and not closings:
            raise CppDiagnostic('cpp.invalid-default-argument', 'unterminated default argument', current.coord)
        if current.value in pairs:
            closings.append(pairs[current.value])
        elif current.value in (')',']','}'):
            if not closings or closings.pop() != current.value:
                raise CppDiagnostic('cpp.invalid-default-argument', 'unbalanced default argument', current.coord)
        tokens.append(current)
    if not tokens:
        raise CppDiagnostic('cpp.invalid-default-argument', 'default argument needs an expression', token.coord)
    # Copy only the parameter names visible at this declaration point. Later
    # parameters must not hide outer names when a class default is replayed.
    scope = Scope(parser.scope.parent)
    scope.names.update(parser.scope.names)
    record = DefaultArgument(tokens, dtype, scope, tuple(scope.names.values()), token.coord)
    if scope.parent.kind != 'class':
        # This also precedes introduction of the function's own declarator name.
        bind(parser, record)
    return record


def bind(parser, record):
    if record.scope.parent.kind == 'class' and not record.scope.parent.class_type.definition.complete:
        raise CppDiagnostic('cpp.unsupported.default-argument-context',
                            'default arguments required before class completion are not implemented', record.coord)
    if record.expression is not None:
        return record.expression
    if record.binding:
        raise CppDiagnostic('cpp.unsupported.default-argument-context',
                            'recursive default-argument binding is not implemented', record.coord)
    from pcc.frontends.cpp.parser import CppToken, Expression, TokenCursor
    from pcc.frontends.cpp.constants import unmodeled
    previous = (parser.cursor, parser.scope, parser.member_function, parser.this_symbol,
                getattr(parser, 'default_argument_binding', None), parser.unevaluated_depth)
    record.binding = True
    try:
        parser.cursor = TokenCursor(record.tokens + (CppToken('EOF', '', record.coord),))
        parser.scope = record.scope
        parser.member_function = record.function if getattr(record.function, 'class_owner', None) else None
        parser.this_symbol = None
        parser.default_argument_binding = record
        # Each declaration has its own potentially-evaluated context, even
        # when binding is requested by a call inside another default's sizeof.
        parser.unevaluated_depth = 0
        expression = parser._expression()
        if parser._peek().kind != 'EOF':
            raise CppDiagnostic('cpp.unsupported.default-argument-context',
                                'default expression requires unimplemented grammar', parser._peek().coord)
        parser._require_value(expression)
        node = parser._convert(expression, record.dtype, record.coord)
        dtype = unqualified(record.dtype)
        record.expression = Expression(node, dtype, constant_fact=unmodeled(
            dtype, 'default argument is evaluated at its call site', record.coord))
        return record.expression
    finally:
        record.binding = False
        (parser.cursor, parser.scope, parser.member_function, parser.this_symbol,
         parser.default_argument_binding, parser.unevaluated_depth) = previous


def merge(parser, symbol, records, scope):
    """Accumulate defaults only for the same declaration scope and signature."""
    prior = tuple(getattr(symbol, 'default_arguments', (None,) * len(symbol.parameters)))
    if not any(records) and not any(prior):
        return
    if (getattr(symbol, 'default_argument_scope', scope) is not scope
            or symbol.owner is not scope or len(symbol.declaration_scopes) > 1):
        raise CppDiagnostic('cpp.unsupported.default-argument-scopes',
                            'default arguments across different redeclaration scopes are not implemented', symbol.coord)
    merged = list(prior)
    for index, record in enumerate(records):
        if record is None:
            continue
        if merged[index] is not None:
            raise CppDiagnostic('cpp.default-argument-redefinition',
                                'a default argument cannot be specified twice', record.coord)
        merged[index] = record
        record.function = symbol
    seen = False
    for record in merged:
        if record is None and seen:
            raise CppDiagnostic('cpp.default-argument-trailing',
                                'a parameter after a default argument requires a default', symbol.coord)
        seen |= record is not None
    symbol.default_arguments = tuple(merged)
    symbol.default_argument_scope = scope
    if scope.kind == 'class' and not scope.class_type.definition.complete:
        pending = scope.class_type.definition.default_arguments
        for record in records:
            if record is not None:
                record.declaration_index = len(pending)
                pending.append(record)
    else:
        for record in records:
            if record is not None:
                bind(parser, record)


def bind_class(parser, definition):
    for record in definition.default_arguments:
        bind(parser, record)


def expand(parser, symbol, arguments, coord):
    if len(arguments) >= len(symbol.parameters):
        return list(arguments)
    if parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.default-argument-context',
                            'default arguments at template call sites are not implemented', coord)
    from pcc.frontends.cpp.parser import Expression
    result = list(arguments)
    for record in symbol.default_arguments[len(arguments):]:
        if record is None:
            raise CppDiagnostic('cpp.no-viable-overload', 'missing required argument', coord)
        active = getattr(parser, 'default_argument_binding', None)
        forward_default = (active is not None and record.scope.parent is active.scope.parent
                           and record.scope.parent.kind == 'class'
                           and active.declaration_index is not None
                           and record.declaration_index is not None
                           and record.declaration_index > active.declaration_index)
        bound = bind(parser, record)
        if forward_default:
            # CWG 361 leaves forward use of another class member's default
            # unsettled. Validate the declaration independently, but do not
            # silently admit the forward-use extension as C++17 support.
            raise CppDiagnostic('cpp.unsupported.default-argument-context',
                                'forward use of a later class default argument is not implemented', coord)
        # Keep semantic dtype identity; deepcopy only the projected C AST.
        result.append(Expression(deepcopy(bound.node), bound.dtype,
                                 constant_fact=bound.constant_fact))
    return result
