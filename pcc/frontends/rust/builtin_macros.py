"""Core expression macros: `env!` / `option_env!` and `matches!`.

`matches!(expression, pattern $(if guard)? $(,)?)` is defined by core as
`match expression { pattern => true, _ => false }`; it is expanded into
exactly that match (core/src/macros/mod.rs, Rust 1.92.0). A guard needs match
guards, which are not implemented, so it stays an explicit gap.

Rust std documentation, macros `env` and `option_env` (retrieved 2026-11-13),
with argument rules and messages observed from rustc 1.92.0. rustc reads its
process environment; PCC never does. `CompileEnvironment` is an explicit
input: defined variables, plus names that are knowingly unavailable (such as
`CARGO_PKG_README`, which Cargo derives from package files) so that using
them is an explicit error instead of a wrong "not defined".

Both macros are evaluated at parse time and every expansion is recorded on
the parser. `env!` yields a string literal expression (`&'static str`);
`option_env!` yields `Option<&'static str>`, which stops at the explicit gap
of the missing core Option model.
"""
from __future__ import annotations

from dataclasses import dataclass

from .syntax import RustError

_DELIMITERS = {'(': ')', '[': ']', '{': '}'}


@dataclass(frozen=True)
class CompileEnvironment:
    """Explicit compile-time environment for env!/option_env!."""
    variables: tuple[tuple[str, str], ...] = ()
    unavailable: tuple[tuple[str, str], ...] = ()  # (name, reason)

    @classmethod
    def of(cls, variables=None, unavailable=None):
        def pairs(mapping, what):
            items = tuple(sorted((mapping or {}).items()))
            if any(type(k) is not str or type(v) is not str for k, v in items):
                raise RustError(f'compile-time environment {what} must map strings to strings')
            return items
        environment = cls(pairs(variables, 'variables'), pairs(unavailable, 'unavailable reasons'))
        overlap = {k for k, _ in environment.variables} & {k for k, _ in environment.unavailable}
        if overlap:
            raise RustError(f'compile-time environment names {sorted(overlap)} are both defined and unavailable')
        return environment

    def lookup(self, name, token):
        """The value of `name`, or None when it is not defined."""
        for key, reason in self.unavailable:
            if key == name:
                raise token.error(f'environment variable {name!r} is unavailable in the explicit '
                                  f'compile-time environment: {reason}')
        return dict(self.variables).get(name)


@dataclass(frozen=True)
class MacroExpansion:
    token: object
    name: str
    arguments: tuple[str, ...]
    value: str | None


def _arguments(parser, token):
    opener = parser.take()
    closer = _DELIMITERS.get(opener.text)
    if closer is None:
        raise opener.error(f'expected one of `(`, `[` or `{{` after {token.text}!')
    values = []
    while not parser.accept(closer):
        argument = parser.token
        if argument.kind == 'name' and parser.tokens[parser.pos + 1].text == '!':
            raise argument.error(f'{token.text}! arguments produced by other macros (such as concat!) '
                                 'require eager macro expansion, which is not implemented')
        if argument.kind != 'string':
            raise argument.error('expected string literal')
        values.append(parser.take().literal_value)
        if len(values) > 2:
            raise argument.error(f'`{token.text}!()` takes 1 or 2 arguments')
        if parser.accept(closer):
            break
        parser.expect(',')
    return tuple(values)


def expand_expression_macro(parser, token):
    """Evaluate `env!(..)` / `option_env!(..)` at `token` (the macro name)."""
    parser.expect('!')
    arguments = _arguments(parser, token)
    if token.text == 'env' and len(arguments) not in {1, 2}:
        raise token.error('`env!()` takes 1 or 2 arguments')
    if token.text == 'option_env' and len(arguments) != 1:
        raise token.error('option_env! takes 1 argument')
    environment = parser.environment
    if environment is None:
        raise token.error(f'{token.text}! requires an explicit compile-time environment '
                          '(compile_rust env=...); the host process environment is never read')
    name = arguments[0]
    value = environment.lookup(name, token)
    if value is None and token.text == 'env':
        raise token.error(arguments[1] if len(arguments) == 2
                          else f'environment variable `{name}` not defined at compile time')
    expansion = MacroExpansion(token, token.text, arguments, value)
    parser.macro_expansions.append(expansion)
    if token.text == 'env':
        from .syntax import Expr
        return Expr('string', token, value)
    raise token.error(f"option_env!({name!r}) yields Option<&'static str>; the core Option model and "
                      'runtime string values are not implemented')


def expand_matches(parser, token):
    """`matches!(value, pattern $(if guard)?)` -> `match value { pattern $(if guard)? => true, _ => false }`."""
    from .syntax import Expr, Pattern
    parser.expect('!')
    opener = parser.take()
    closer = _DELIMITERS.get(opener.text)
    if closer is None:
        raise opener.error('expected one of `(`, `[` or `{` after matches!')
    if parser.token.text == closer:
        raise parser.token.error('matches! requires a value and a pattern')
    scrutinee = parser.expression()
    parser.expect(',')
    pattern = parser.pattern()
    guard_token = parser.token
    guard = parser.expression() if parser.accept('if') else None
    parser.accept(',')
    parser.expect(closer)
    expansion = MacroExpansion(token, 'matches', (), None)
    parser.macro_expansions.append(expansion)
    selected = Expr('bool', token, True)
    if guard is not None:
        selected = Expr('match_guard', guard_token, children=[guard, selected])
    return Expr('match', token, [pattern, Pattern('wildcard', token)],
                [scrutinee, selected, Expr('bool', token, False)])
