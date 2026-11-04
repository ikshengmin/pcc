"""Retained function inline hints, not an owned inlining implementation.

https://doc.rust-lang.org/reference/attributes/codegen.html#the-inline-attribute
Pinned parser: rust-lang/rust 1.91.1, rustc_attr_parsing/src/attributes/inline.rs.
All three modes are advisory. Keep original attributes and the first applicable
hint; do not fabricate an alwaysinline/noinline backend contract or certification.
"""
from dataclasses import dataclass

from .attribute_metadata import meta_list, offset


@dataclass(frozen=True)
class InlineAttribute:
    token: object
    mode: str
    source_span: tuple[int, int]


@dataclass(frozen=True)
class FunctionInlineHints:
    declaration: str
    attributes: tuple[InlineAttribute, ...]
    selected_hint: str | None
    ignored_reason: str | None
    applied_by_owned_codegen: bool = False


def inline_attribute(parser, token, start):
    mode = 'hint'
    if parser.token.text == '(':
        items = meta_list(parser)
        if len(items) != 1 or items[0].name not in {'always', 'never'}:
            raise token.error('inline requires no argument or one always/never word')
        if items[0].form != 'word':
            raise token.error('nested inline metadata is not implemented; only always/never words are admitted')
        mode = items[0].name
    elif parser.token.text == '=':
        raise token.error('inline name-value metadata is not implemented')
    elif parser.token.text != ']':
        raise token.error('inline requires no argument or one always/never word')
    end = parser.expect(']')
    return InlineAttribute(token, mode, (offset(parser, start), offset(parser, end) + 1))


def function_attributes(parser):
    """Interleave existing documentation with this one finite attribute family."""
    docs, hints = [], []
    while True:
        docs.extend(parser.documentation())
        position = parser.pos
        if not (parser.token.text == '#' and parser.tokens[position + 1].text == '['
                and parser.tokens[position + 2].text == 'inline'):
            return tuple(docs), tuple(hints)
        start = parser.take()
        parser.expect('[')
        token = parser.take()
        hints.append(inline_attribute(parser, token, start))


def require_function_target(parser, attributes):
    if not attributes:
        return
    position = parser.pos
    if parser.tokens[position].text == 'unsafe':
        position += 1
    if parser.tokens[position].text == 'extern':
        position += 1
        if parser.tokens[position].kind == 'string':
            position += 1
    if parser.tokens[position].text != 'fn':
        raise attributes[0].token.error('inline attributes require an admitted function item; this target or qualifier is not implemented')


def collect_inline_hints(functions, traits):
    declarations = list(functions)
    declarations.extend(function for trait in traits for function in trait.methods)
    result = []
    for function in declarations:
        attributes = function.inline_attributes
        if not attributes:
            continue
        # A required trait method's annotation does not transfer to an impl.
        # Foreign declarations likewise have no local body to inline.
        reason = ('no_body' if function.body is None else
                  'externally_exported' if function.no_mangle else None)
        result.append(FunctionInlineHints(function.declaration_name, attributes,
            attributes[0].mode if reason is None else None, reason))
    return tuple(result)
