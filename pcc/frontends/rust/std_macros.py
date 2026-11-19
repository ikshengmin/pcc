"""The core/std macros the owned parser expands (Rust 1.92 definitions).

Formatting macros (`format_args!`, `format!`, `write!`, `writeln!`, and the
messages of `panic!`, `unreachable!`, `todo!`, `unimplemented!`, `assert!`
and its relatives): the format string is parsed at expansion time with
std::fmt's grammar (https://doc.rust-lang.org/std/fmt/#syntax) and checked as
rustc's format_args! does: unmatched braces, unknown traits, positional and
named argument references (including `name$`/`N$` widths and `.*`), unused
arguments, positional arguments after named ones, and implicit captures of
plain identifiers (2021+). The result is one `format_args` node whose
arguments are borrowed, never moved.

`assert!(c, ..)` is `if !c { panic!(..) }`; `assert_eq!`/`assert_ne!`
compare two operands evaluated once; the `debug_` forms are that code under
`if cfg!(debug_assertions)`: always type- and borrow-checked, emitted only
when debug assertions are on (an explicit CfgOptions choice). `panic!` and
its relatives diverge. `vec!` is alloc's Vec constructor. `cfg!(pred)` is a
bool evaluated by the owned cfg configuration; `compile_error!` reports its
message. Every runtime these need (panics, core::fmt, alloc) is a semantic
gap until its plan step; expansion and checking are complete here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .syntax import Expr, RustError, Token

TRAITS = {'': 'Display', '?': 'Debug', 'x?': 'Debug', 'X?': 'Debug', 'x': 'LowerHex', 'X': 'UpperHex',
          'o': 'Octal', 'b': 'Binary', 'e': 'LowerExp', 'E': 'UpperExp', 'p': 'Pointer'}
CORE_MACROS = frozenset({'format_args', 'write', 'writeln', 'panic', 'unreachable', 'todo', 'unimplemented',
                         'assert', 'assert_eq', 'assert_ne', 'debug_assert', 'debug_assert_eq', 'debug_assert_ne',
                         'cfg', 'compile_error'})
STD_MACROS = frozenset({'format', 'vec'})
DEFAULT_MESSAGES = {'unreachable': 'internal error: entered unreachable code', 'todo': 'not yet implemented',
                    'unimplemented': 'not implemented'}


@dataclass(frozen=True)
class Count:
    """A width or precision: a literal, or an argument index (`N$`, `name$`, `*`)."""
    value: int
    argument: bool


@dataclass(frozen=True)
class Placeholder:
    argument: int
    trait: str
    fill: str | None = None
    align: str | None = None
    sign: str | None = None
    alternate: bool = False
    zero: bool = False
    width: Count | None = None
    precision: Count | None = None


@dataclass(frozen=True)
class FormatString:
    """Literal pieces and placeholders; arguments are the node's children."""
    pieces: tuple
    names: tuple  # the argument name of each child, None for positional


def _is_identifier(text):
    return bool(text) and (text[0].isalpha() or text[0] == '_') and all(c.isalnum() or c == '_' for c in text) \
        and text != '_'


class _FormatParser:
    """std::fmt's format string grammar over one string literal."""

    def __init__(self, token, text):
        self.token, self.text, self.index = token, text, 0

    def error(self, message):
        return self.token.error(f'invalid format string: {message}')

    def parse(self):
        pieces, literal = [], []
        while self.index < len(self.text):
            char = self.text[self.index]
            if char == '{':
                if self.text.startswith('{{', self.index):
                    literal.append('{')
                    self.index += 2
                    continue
                if literal:
                    pieces.append(''.join(literal))
                    literal = []
                self.index += 1
                pieces.append(self.placeholder())
            elif char == '}':
                if self.text.startswith('}}', self.index):
                    literal.append('}')
                    self.index += 2
                    continue
                raise self.error('unmatched `}` found')
            else:
                literal.append(char)
                self.index += 1
        if literal:
            pieces.append(''.join(literal))
        return pieces

    def take_while(self, predicate):
        start = self.index
        while self.index < len(self.text) and predicate(self.text[self.index]):
            self.index += 1
        return self.text[start:self.index]

    def argument(self):
        """An integer, an identifier, or None (the next positional argument)."""
        if self.index < len(self.text) and self.text[self.index].isdigit():
            return int(self.take_while(str.isdigit))
        word = self.take_while(lambda c: c.isalnum() or c == '_')
        return word or None

    def placeholder(self):
        self.take_while(str.isspace)
        argument = self.argument()
        self.take_while(str.isspace)
        spec = {}
        if self.index < len(self.text) and self.text[self.index] == ':':
            self.index += 1
            spec = self.spec()
        self.take_while(str.isspace)
        if self.index >= len(self.text):
            raise self.error('expected `}` but string was terminated')
        if self.text[self.index] != '}':
            raise self.error(f"expected `}}`, found `{self.text[self.index]}`")
        self.index += 1
        return (argument, spec)

    def count(self):
        """A width/precision: `N`, `N$`, `name$` (a written count or argument)."""
        start = self.index
        digits = self.take_while(str.isdigit)
        if digits:
            if self.index < len(self.text) and self.text[self.index] == '$':
                self.index += 1
                return ('argument', int(digits))
            return ('literal', int(digits))
        word = self.take_while(lambda c: c.isalnum() or c == '_')
        if word and self.index < len(self.text) and self.text[self.index] == '$':
            self.index += 1
            return ('argument', word)
        self.index = start  # not a count: the format trait follows
        return None

    def spec(self):
        text = self.text
        spec = {}
        if self.index + 1 < len(text) and text[self.index + 1] in '<^>' and text[self.index] != '}':
            spec['fill'], spec['align'] = text[self.index], text[self.index + 1]
            self.index += 2
        elif self.index < len(text) and text[self.index] in '<^>':
            spec['align'] = text[self.index]
            self.index += 1
        if self.index < len(text) and text[self.index] in '+-':
            spec['sign'] = text[self.index]
            self.index += 1
        if self.index < len(text) and text[self.index] == '#':
            spec['alternate'] = True
            self.index += 1
        if self.index < len(text) and text[self.index] == '0' and not text[self.index:].startswith('0$'):
            spec['zero'] = True
            self.index += 1
        width = self.count()
        if width is not None:
            spec['width'] = width
        if self.index < len(text) and text[self.index] == '.':
            self.index += 1
            if self.index < len(text) and text[self.index] == '*':
                self.index += 1
                spec['precision'] = ('star', None)
            else:
                precision = self.count()
                if precision is None:
                    raise self.error('expected a precision after `.`')
                spec['precision'] = precision
        trait = self.take_while(lambda c: c.isalnum() or c == '_' or c == '?')
        if trait not in TRAITS:
            raise self.token.error(f'unknown format trait `{trait}`')
        spec['trait'] = trait
        return spec


def macro_arguments(parser, closer):
    """Comma-separated arguments up to `closer`: (name token or None, Expr)."""
    arguments = []
    while parser.token.text != closer:
        name = None
        if (parser.token.kind == 'name' and parser.tokens[parser.pos + 1].text == '='
                and parser.tokens[parser.pos + 2].text != '='):
            name = parser.take()
            parser.take()
        arguments.append((name, parser.expression()))
        if parser.token.text != closer:
            parser.expect(',')
    parser.expect(closer)
    return arguments


def format_arguments(parser, token, format_token, arguments, *, newline=False, implicit_captures=True):
    """The `format_args` node of a literal format string and its arguments."""
    if format_token.kind != 'string':
        raise format_token.error('format argument must be a string literal')
    seen_named = False
    positional, named = [], {}
    for name, expression in arguments:
        if name is None:
            if seen_named:
                raise expression.token.error('positional arguments cannot follow named arguments')
            positional.append(expression)
        else:
            seen_named = True
            if name.text in named:
                raise name.error(f'duplicate argument named `{name.text}`')
            named[name.text] = expression
    text = format_token.literal_value + ('\n' if newline else '')
    raw_pieces = _FormatParser(format_token, text).parse()
    children = list(positional) + list(named.values())
    names = [None] * len(positional) + list(named)
    used = [False] * len(children)
    next_positional = 0

    def reference(argument, *, implicit=True):
        nonlocal next_positional
        if argument is None:
            index = next_positional
            next_positional += 1
            if index >= len(positional):
                raise format_token.error(_count_message(next_positional, len(positional)))
            used[index] = True
            return index
        if isinstance(argument, int):
            if argument >= len(positional):
                raise format_token.error(f'invalid reference to positional argument {argument} '
                                         f'({_there_are(len(positional))})')
            used[argument] = True
            return argument
        if argument in named:
            index = names.index(argument)
            used[index] = True
            return index
        if not implicit or not implicit_captures or not _is_identifier(argument):
            raise format_token.error(f'there is no argument named `{argument}`')
        # A captured identifier is an implicit named argument (format_args_capture).
        if argument in names:
            index = names.index(argument)
        else:
            capture = Token(argument, 'name', format_token.line, format_token.column,
                            source_path=format_token.source_path, hygiene=format_token.hygiene)
            children.append(Expr('name', capture, argument))
            names.append(argument)
            used.append(True)
            index = len(children) - 1
        return index

    def count(value):
        if value is None:
            return None
        kind, item = value
        if kind == 'literal':
            return Count(item, False)
        if kind == 'star':
            return Count(reference(None), True)
        return Count(reference(item), True)

    pieces = []
    for piece in raw_pieces:
        if isinstance(piece, str):
            pieces.append(piece)
            continue
        argument, spec = piece
        precision = spec.get('precision')
        star = precision is not None and precision[0] == 'star'
        precision_count = count(precision) if star else None  # `.*` takes its argument first
        index = reference(argument)
        width = count(spec.get('width'))
        if not star:
            precision_count = count(precision)
        pieces.append(Placeholder(index, TRAITS[spec.get('trait', '')], spec.get('fill'), spec.get('align'),
                                  spec.get('sign'), spec.get('alternate', False), spec.get('zero', False),
                                  width, precision_count))
    unused = [index for index, flag in enumerate(used) if not flag]
    if unused:
        index = unused[0]
        what = 'named argument never used' if names[index] is not None else 'argument never used'
        if len(unused) > 1:
            what = 'multiple unused formatting arguments'
        raise children[index].token.error(what)
    return Expr('format_args', token, FormatString(tuple(pieces), tuple(names)), children)


def _there_are(count):
    return 'there are no arguments' if count == 0 else ('there is 1 argument' if count == 1
                                                          else f'there are {count} arguments')


def _count_message(wanted, have):
    noun = 'positional argument' if wanted == 1 else 'positional arguments'
    return f'{wanted} {noun} in format string, but {_there_are(have)}'


def _delimited(parser, token):
    parser.expect('!')
    opener = parser.take()
    closer = {'(': ')', '[': ']', '{': '}'}.get(opener.text)
    if closer is None:
        raise opener.error(f'expected one of `(`, `[` or `{{` after `{token.text}!`')
    return closer


def _format_from(parser, token, closer, *, newline=False, required=True):
    """A format string literal and its arguments up to `closer`."""
    if parser.token.text == closer:
        if required:
            raise parser.token.error('requires at least a format string argument')
        parser.expect(closer)
        return format_arguments(parser, token, Token('""', 'string', token.line, token.column,
                                                     literal_value='', source_path=token.source_path),
                                [], newline=newline)
    format_token = parser.take()
    if format_token.kind != 'string':
        raise format_token.error('format argument must be a string literal')
    if parser.token.text != closer:
        parser.expect(',')
    return format_arguments(parser, token, format_token, macro_arguments(parser, closer), newline=newline)


def _panic(parser, token, closer, default=None):
    """A diverging `panic` node with its message (a format_args node)."""
    if parser.token.text == closer:
        parser.expect(closer)
        message = default if default is not None else 'explicit panic'
        literal = Token(repr(message), 'string', token.line, token.column, literal_value=message,
                        source_path=token.source_path)
        return Expr('panic', token, token.text, [format_arguments(parser, token, literal, [])])
    if parser.crate_policy.edition < 2021 and token.text == 'panic':
        raise token.error('panic! with arguments in editions before 2021 (a non-format payload) is not implemented')
    message = _format_from(parser, token, closer)
    if default is not None:
        # unreachable!/todo!/unimplemented!("..") prefix their default message.
        message.value = FormatString((default + ': ',) + message.value.pieces, message.value.names)
    return Expr('panic', token, token.text, [message])


def _assertion(parser, token, closer):
    """`assert!(cond $(, fmt)?)` -> `if !cond { panic }`."""
    condition = parser.expression()
    if parser.token.text == closer:
        parser.expect(closer)
        literal = Token('"assertion failed"', 'string', token.line, token.column,
                        literal_value='assertion failed', source_path=token.source_path)
        failure = Expr('panic', token, 'assert', [format_arguments(parser, token, literal, [])])
    else:
        parser.expect(',')
        failure = Expr('panic', token, 'assert', [_format_from(parser, token, closer)])
    negated = Expr('unary', token, '!', [Expr('group', condition.token, children=[condition])])
    return Expr('if', token, children=[negated, Expr('block', token, 0, [failure])])


def _comparison(parser, token, closer, op):
    left = parser.expression()
    parser.expect(',')
    right = parser.expression()
    children = [left, right]
    if parser.token.text != closer:
        parser.expect(',')
        if parser.token.text != closer:
            children.append(_format_from(parser, token, closer))
        else:
            parser.expect(closer)
    else:
        parser.expect(closer)
    return Expr('assert_compare', token, op, children)


def debug_assertions(parser, token):
    configuration = parser._configuration
    if configuration is None:
        raise token.error(f'{token.text}! needs cfg(debug_assertions): an explicit CfgOptions.debug_assertions '
                          'choice (compile_rust cfg=...)')
    if configuration.options.debug_assertions is None:
        raise token.error('cfg(debug_assertions) requires an explicit CfgOptions.debug_assertions choice')
    return bool(configuration.options.debug_assertions)


def expand(parser, token):
    """Expand the core/std macro `token` (the parser is after its name)."""
    name = token.text
    if name == 'cfg':
        from .attribute_metadata import meta_list
        parser.expect('!')
        items = meta_list(parser, cfg_literals=True, paths=True)
        if len(items) != 1:
            raise token.error('cfg! requires exactly one configuration predicate')
        configuration = parser.cfg_configuration(token, 'cfg!')
        return Expr('bool', token, bool(configuration.predicate(items[0])))
    closer = _delimited(parser, token)
    if name == 'compile_error':
        message = parser.take()
        if message.kind != 'string':
            raise message.error('compile_error! takes a string literal')
        raise message.error(message.literal_value)
    if name == 'format_args':
        node = _format_from(parser, token, closer)
    elif name == 'format':
        node = Expr('format', token, None, [_format_from(parser, token, closer)])
    elif name in ('write', 'writeln'):
        destination = parser.expression()
        if parser.token.text == closer:
            if name == 'write':
                raise parser.token.error('requires a destination and format arguments')
            parser.expect(closer)
            message = format_arguments(parser, token, Token('""', 'string', token.line, token.column,
                                                            literal_value='', source_path=token.source_path),
                                       [], newline=True)
        else:
            parser.expect(',')
            message = _format_from(parser, token, closer, newline=name == 'writeln')
        node = Expr('write_fmt', token, name, [destination, message])
    elif name == 'panic':
        node = _panic(parser, token, closer)
    elif name in DEFAULT_MESSAGES:
        node = _panic(parser, token, closer, DEFAULT_MESSAGES[name])
    elif name in ('assert', 'debug_assert'):
        node = _assertion(parser, token, closer)
    elif name in ('assert_eq', 'assert_ne', 'debug_assert_eq', 'debug_assert_ne'):
        node = _comparison(parser, token, closer, '==' if name.endswith('eq') else '!=')
    elif name == 'vec':
        node = _vec(parser, token, closer)
    else:
        raise token.error(f'the standard macro `{name}!` is not implemented')
    if name.startswith('debug_'):
        node = Expr('debug_assert', token, debug_assertions(parser, token), [node])
    parser.macro_expansions.append(StandardExpansion(token))
    return node


def _vec(parser, token, closer):
    if parser.token.text == closer:
        parser.expect(closer)
        return Expr('vec_macro', token, 'list', [])
    first = parser.expression()
    if parser.accept(';'):
        length = parser.expression()
        parser.expect(closer)
        return Expr('vec_macro', token, 'repeat', [first, length])
    elements = [first]
    while parser.accept(','):
        if parser.token.text == closer:
            break
        elements.append(parser.expression())
    parser.expect(closer)
    return Expr('vec_macro', token, 'list', elements)


@dataclass(frozen=True)
class StandardExpansion:
    token: Token

    @property
    def name(self):
        return self.token.text


def expands(parser, name):
    """Whether the owned parser expands core/std macro `name` here."""
    if not parser.core_prelude:
        return False
    if name in CORE_MACROS:
        return True
    return name in STD_MACROS and parser.crate_policy.implicit_library_import == 'std'


__all__ = ['expand', 'expands', 'format_arguments', 'FormatString', 'Placeholder', 'Count', 'RustError']
