"""The bounded source expansion of std's println! and eprintln!, not their I/O.

rust-lang/rust@1.95.0/library/std/src/macros.rs: the empty arm prints a LF;
the general arm passes format_args_nl! to the defining crate's _print/_eprint.
Keep that hygienic target and the ordered literal pieces/Display captures.
No Rust fmt::Arguments layout, stdio lock, panic, or host write is invented.
"""
from __future__ import annotations

from dataclasses import dataclass

from .builtin_macros import MacroExpansion


@dataclass(frozen=True)
class StdOutputExpansion(MacroExpansion):
    # A canonical library owner, not a Rust path resolved in the caller.
    callee: tuple[str, str, str]
    format_token: object
    literal_pieces: tuple[str, ...]
    # A format plan is not evaluated, borrowed, or formatted by Python.
    captures: tuple[str, ...]
    source_span: tuple[int, int]
    module_scope: tuple[int, ...]

    def missing_runtime(self):
        return self.token.error(
            f'{self.name}! requires the owned {"::".join(self.callee)} and fmt::Arguments '
            'runtime (stream locking and write-error panic); std output is not implemented')


def _literal_format(token):
    # fmt grammar admitted here: literal text, escaped braces, and an exact
    # implicit ASCII identifier with default Display. Other valid format
    # grammar is explicitly outside the owner, never interpreted by str.format.
    from .syntax import _KEYWORDS
    value = token.literal_value
    parts, captures, text = [], [], []
    i = 0
    while i < len(value):
        char = value[i]
        if char not in '{}':
            text.append(char)
            i += 1
        elif i + 1 < len(value) and value[i + 1] == char:
            text.append(char)
            i += 2
        elif char == '}':
            raise token.error("invalid format string: unmatched '}'")
        else:
            end = value.find('}', i + 1)
            if end < 0:
                raise token.error("invalid format string: unclosed '{'")
            name = value[i + 1:end]
            if not (name.isascii() and name.isidentifier() and name != '_' and name not in _KEYWORDS):
                raise token.error('only implicit plain identifier Display captures are implemented; '
                                  'positional arguments and format specifications are not supported')
            parts.append(''.join(text)); text.clear()
            captures.append(name)
            i = end + 1
    text.append('\n')
    parts.append(''.join(text))
    return tuple(parts), tuple(captures)


def expand_std_output(parser, token):
    from .attribute_metadata import offset
    from .syntax import Expr
    parser.expect('!')
    opener = parser.take()
    closer = {'(': ')', '[': ']', '{': '}'}.get(opener.text)
    if closer is None:
        raise opener.error(f'expected delimited input after {token.text}!')
    literal = None
    if parser.token.text == closer:
        parts, captures, args = ('\n',), (), ()
    else:
        literal = parser.token
        if literal.kind != 'string':
            raise literal.error(f'{token.text}! requires a format string literal; '
                                'format strings produced by other macros are not implemented')
        parser.take()
        if parser.accept(',') and parser.token.text != closer:
            raise parser.token.error('explicit positional and named format arguments are not implemented')
        parts, captures = _literal_format(literal)
        args = (literal.literal_value,)
    close = parser.expect(closer)
    plan = StdOutputExpansion(
        token, token.text, args, None,
        ('std', 'io', '_print' if token.text == 'println' else '_eprint'),
        literal, parts, captures, (offset(parser, token), offset(parser, close) + 1), parser._macro_scope)
    parser.macro_expansions.append(plan)
    return Expr('std_output', token, plan)


def require_unshadowed_output(parser, first):
    # Path-based use bindings are module-wide, even if they follow the call.
    # No import may silently be treated as an implicit std macro. Our resolver
    # lacks a macro namespace, so even a potentially value-only alias rejects.
    # A glob can import a macro: the parser cannot assume its target has only
    # value/type declarations before the supplied modules have been resolved.
    if not parser.uses:
        return
    # A brace-terminal self import is known to import only a TYPE binding;
    # unlike a simple import, it cannot shadow a macro of the same name.
    globs = any(item.glob is not None for item in parser.uses)
    names = {item.binding.text for item in parser.uses
             if item.glob is None and item.group_self is None}
    for plan in parser.macro_expansions[first:]:
        if globs and (not isinstance(plan, StdOutputExpansion) or plan.module_scope == parser._macro_scope):
            raise plan.token.error(f'{plan.name}! with a glob import needs owned macro namespace resolution')
        if (isinstance(plan, StdOutputExpansion) and plan.module_scope == parser._macro_scope
                and plan.name in names):
            raise plan.token.error(f'{plan.name}! has a same-name import; explicit macro namespace '
                                   'resolution is required before selecting the std prelude')
