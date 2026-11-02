"""Original-token completed-class binding for ordinary in-class method bodies.

N4659 [class.mem], [class.mfct], [class.dtor], [basic.def.odr]. Constructor
initialization is owned separately; ordinary complete-object destructors share
this token/ODR owner but retain their own body/return rules. Local static/TLS
guards and exception unwinding remain boundaries. No source token is rewritten.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic


class DeferredMethod:
    def __init__(self, symbol, result_type, parameter_scope, parameter_symbols, declaration,
                 this, tokens, coord):
        self.symbol = symbol
        self.result_type = result_type
        self.parameter_scope = parameter_scope
        self.parameter_symbols = parameter_symbols
        self.declaration = declaration
        self.this = this
        self.tokens = tokens
        self.coord = coord
        self.definition_linkage = 'external_odr'
        self.function = None


def bind_methods(parser, definition):
    if not definition.inline_methods:
        return
    from pcc.frontends.cpp.inline_constructors import publish_definitions
    from pcc.frontends.cpp.parser import CppToken, TokenCursor
    original_cursor = parser.cursor
    try:
        for pending in definition.inline_methods:
            parser.cursor = TokenCursor(pending.tokens + (CppToken('EOF', '', pending.coord),))
            if getattr(pending.symbol, 'destructor', False):
                from pcc.frontends.cpp.destructors import bind_destructor_body
                pending.function = bind_destructor_body(
                    parser, definition, pending.symbol, pending.parameter_scope,
                    pending.declaration, pending.this, pending.coord,
                )
            else:
                pending.function = parser._bind_function_body(
                    pending.symbol, pending.result_type, pending.parameter_scope, pending.parameter_symbols,
                    pending.declaration, pending.this, definition, pending.coord,
                )
            if parser._peek().kind != 'EOF':
                raise CppDiagnostic('cpp.unsupported.syntax', 'unexpected in-class method body tail', parser._peek().coord)
            pending.symbol.defined = True
    finally:
        parser.cursor = original_cursor
    publish_definitions(parser, definition, definition.inline_methods)
