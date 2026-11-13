"""Built-in expression macros `env!` and `option_env!` over an owned environment.

Rust std documentation, macros `env` and `option_env` (retrieved 2026-11-13),
with argument rules and messages observed from rustc 1.92.0. rustc reads its
process environment; PCC never does. `CompileEnvironment` is an explicit
input: defined variables, plus names that are knowingly unavailable (such as
`CARGO_PKG_README`, which Cargo derives from package files) so that using
them is an explicit error instead of a wrong "not defined".

Both macros are evaluated at parse time. The resulting `&'static str` (or
`Option<&'static str>`) has no runtime representation yet, so the expansion
is recorded on the parser and the use then stops at that explicit gap.
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
        raise token.error(f'runtime string values are not implemented; env!({name!r}) expanded to a '
                          "`&'static str` literal with no runtime representation")
    raise token.error(f"option_env!({name!r}) yields Option<&'static str>; the core Option model and "
                      'runtime string values are not implemented')
