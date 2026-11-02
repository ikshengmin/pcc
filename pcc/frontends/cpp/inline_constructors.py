"""Completed-class binding for in-class complete-object constructor definitions.

Original lexer tokens and parameter Symbols survive the delayed parse. The C
projection retains an explicit external-ODR definition requirement; absent the
shared definition-linkage contract, no translation unit is returned for emission.
This does not implement base/delegating constructors or exception unwinding.
"""
from copy import deepcopy

from pcc.frontends.cpp.diagnostics import CppDiagnostic


class DeferredConstructor:
    def __init__(self, symbol, parameter_scope, declaration, this, tokens, coord):
        self.symbol = symbol
        self.parameter_scope = parameter_scope
        self.declaration = declaration
        self.this = this
        self.tokens = tokens
        self.coord = coord
        self.definition_linkage = 'external_odr'
        self.function = None


def _balanced(parser, opening):
    """Consume one balanced original token region, without interpreting names."""
    pairs = {'(': ')', '[': ']', '{': '}'}
    parser._expect(opening)
    stack = [pairs[opening]]
    while stack:
        token = parser.cursor.take()
        if token.kind == 'EOF':
            raise CppDiagnostic('cpp.unsupported.syntax', 'unterminated in-class definition', token.coord)
        if token.value in pairs:
            stack.append(pairs[token.value])
        elif token.value in (')', ']', '}'):
            if token.value != stack.pop():
                raise CppDiagnostic('cpp.unsupported.syntax', 'unbalanced in-class definition', token.coord)


def collect_body(parser):
    """Retain a complete original function body, including its delimiters."""
    start = parser.cursor.position
    _balanced(parser, '{')
    return tuple(parser.cursor.tokens[start:parser.cursor.position])


def collect_definition(parser, owner):
    """Capture the supported mem-initializer grammar followed by its actual body.

    A braced mem-initializer is not mistaken for the function body. This narrow
    collector only establishes token boundaries; binding/conversion/access and
    duplicate initializers still belong to the common constructor semantic pass.
    """
    start = parser.cursor.position
    if parser._peek().value in ('try', 'noexcept', 'throw'):
        raise CppDiagnostic('cpp.unsupported.exceptions',
                            'constructor exception contracts and function-try-blocks require unwinding',
                            parser._peek().coord)
    if parser._accept(':'):
        while True:
            name = parser._identifier()
            if name.value == owner.name:
                raise CppDiagnostic('cpp.unsupported.delegating-constructors',
                                    'delegation requires constructor lifetime integration', name.coord)
            if parser._peek().value == '{':
                raise CppDiagnostic('cpp.unsupported.constructor-list-initialization',
                                    'braced member initialization requires direct-list conversion rules',
                                    parser._peek().coord)
            _balanced(parser, '(')
            if not parser._accept(','):
                break
    _balanced(parser, '{')
    return tuple(parser.cursor.tokens[start:parser.cursor.position])


def bind_definitions(parser, definition):
    if not definition.inline_constructors:
        return
    from pcc.frontends.cpp.constructors import bind_constructor_body
    from pcc.frontends.cpp.parser import CppToken, TokenCursor
    original_cursor = parser.cursor
    try:
        for pending in definition.inline_constructors:
            parser.cursor = TokenCursor(pending.tokens + (CppToken('EOF', '', pending.coord),))
            pending.function = bind_constructor_body(
                parser, definition, pending.symbol, pending.parameter_scope,
                pending.declaration, pending.this, pending.coord,
            )
            if parser._peek().kind != 'EOF':
                raise CppDiagnostic('cpp.unsupported.syntax', 'unexpected constructor definition tail', parser._peek().coord)
            pending.symbol.defined = True
    finally:
        parser.cursor = original_cursor
    publish_definitions(parser, definition, definition.inline_constructors)


def publish_definitions(parser, definition, pending_definitions):
    """Project only supported ODR definitions after all class prototypes."""
    try:
        from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    except ModuleNotFoundError as error:
        if error.name != 'pcc.backend.definition_linkage':
            raise
        supported = False
    else:
        supported = supports_external_odr_function_definitions(parser.types.target_triple)
    # Never emit this definition as an ordinary strong global when the shared
    # AST/codegen contract has not landed. There is no semantic-only bypass flag.
    for pending in pending_definitions:
        if not supported or not hasattr(pending.declaration, 'definition_linkage'):
            raise CppDiagnostic('cpp.unsupported.inline-member-functions',
                                'in-class definition requires shared external-ODR definition linkage',
                                pending.coord)
        # All prototypes must precede every delayed body. The C future-type
        # table alone cannot carry declaration metadata such as unsigned return
        # semantics. Only the body declaration owns the ODR definition marker.
        pending.function.decl = deepcopy(pending.declaration)
        pending.function.decl.definition_linkage = pending.definition_linkage
        definition.method_declarations.append(pending.function)
