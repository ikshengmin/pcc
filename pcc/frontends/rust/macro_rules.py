"""Owned macro-by-example: `macro_rules!` definitions and their expansion.

Rust Reference macros-by-example.html. A definition is a list of rules
`(matcher) => {transcriber}`; an invocation's token tree is matched against
the rules in order, and the first rule that matches the whole input is
transcribed. Matchers hold literal tokens, delimited groups, fragments
`$name:kind` and repetitions `$( ... ) sep? op` (op `*`, `+` or `?`).
Fragments are parsed by the owned parser itself on the invocation's tokens;
`expr` fragments are transcribed as one parenthesized expression, so their
precedence is kept (`$e as u64` with `v as i64` is `(v as i64) as u64`).

Hygiene is rustc's mixed-site hygiene: every token a transcriber writes
carries a mark for that expansion, fragment tokens keep their own marks, and
local variables bind and resolve by (name, mark) (semantics.local_key), while
items, fields, methods and paths ignore marks. `$crate` is `crate` (one
crate). Scoping is textual: a definition is visible after it in its module,
in blocks and child modules declared later; `#[macro_export]` and
`#[macro_use]` are explicit gaps. Expansion depth follows rustc's default
recursion_limit (128).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .syntax import RustError, Token

DELIMITERS = {'(': ')', '[': ']', '{': '}'}
FRAGMENT_KINDS = frozenset({'expr', 'ty', 'ident', 'tt', 'literal', 'pat', 'pat_param', 'path', 'block',
                            'lifetime'})
RECURSION_LIMIT = 128
MAX_EXPANSIONS = 4096
MAX_EXPANDED_TOKENS = 1 << 20


@dataclass(frozen=True)
class Fragment:
    token: Token  # the metavariable name
    kind: str


@dataclass(frozen=True)
class Variable:
    token: Token


@dataclass(frozen=True)
class Repetition:
    token: Token
    elements: tuple
    separator: Token | None
    operator: str


@dataclass(frozen=True)
class Group:
    open: Token
    close: Token
    elements: tuple


@dataclass(frozen=True)
class Matched:
    """A fragment's matched tokens."""
    kind: str
    tokens: tuple


@dataclass(frozen=True)
class MacroRules:
    token: Token
    rules: tuple  # (matcher elements, transcriber elements)


def closing(tokens, index):
    """Index of the delimiter closing tokens[index]."""
    stack = [DELIMITERS[tokens[index].text]]
    position = index + 1
    while position < len(tokens):
        text = tokens[position].text
        if tokens[position].kind == 'eof':
            break
        if text in DELIMITERS and tokens[position].kind == 'punct':
            stack.append(DELIMITERS[text])
        elif text in (')', ']', '}') and tokens[position].kind == 'punct':
            if text != stack.pop():
                raise tokens[position].error(f'mismatched closing delimiter {text!r}')
            if not stack:
                return position
        position += 1
    raise tokens[index].error(f'unclosed delimiter {tokens[index].text!r}')


def parse_elements(tokens, *, matcher):
    """Matcher or transcriber elements of a delimited body (without its delimiters)."""
    elements, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        if token.text == '$' and index + 1 < len(tokens):
            following = tokens[index + 1]
            if following.text == '(':
                end = closing(tokens, index + 1)
                inner = parse_elements(tokens[index + 2:end], matcher=matcher)
                index = end + 1
                separator = None
                if index < len(tokens) and tokens[index].text not in ('*', '+', '?'):
                    separator = tokens[index]
                    if separator.text in DELIMITERS or separator.text == '$':
                        raise separator.error('expected a repetition operator `*`, `+` or `?`')
                    index += 1
                if index >= len(tokens) or tokens[index].text not in ('*', '+', '?'):
                    raise token.error('expected one of `*`, `+` or `?` after a macro repetition')
                operator = tokens[index].text
                if operator == '?' and separator is not None:
                    raise separator.error('the `?` macro repetition operator does not take a separator')
                elements.append(Repetition(token, tuple(inner), separator, operator))
                index += 1
                continue
            if following.kind in ('name', 'keyword'):
                if following.text == 'crate' and not matcher:
                    elements.append(replace(following, text='crate', kind='keyword'))
                    index += 2
                    continue
                if matcher:
                    if index + 3 >= len(tokens) or tokens[index + 2].text != ':':
                        raise following.error(f'missing fragment specifier for `${following.text}`')
                    kind = tokens[index + 3]
                    if kind.text not in FRAGMENT_KINDS:
                        if kind.text in ('stmt', 'item', 'vis', 'meta'):
                            raise kind.error(f'the `{kind.text}` macro fragment is not implemented')
                        raise kind.error(f'invalid fragment specifier `{kind.text}`')
                    elements.append(Fragment(following, kind.text))
                    index += 4
                    continue
                elements.append(Variable(following))
                index += 2
                continue
        if token.text in DELIMITERS and token.kind == 'punct':
            end = closing(tokens, index)
            elements.append(Group(token, tokens[end], tuple(parse_elements(tokens[index + 1:end],
                                                                           matcher=matcher))))
            index = end + 1
            continue
        elements.append(token)
        index += 1
    return elements


def variables(elements):
    names = set()
    for element in elements:
        if isinstance(element, (Fragment, Variable)):
            names.add(element.token.text)
        elif isinstance(element, Repetition):
            names |= variables(element.elements)
        elif isinstance(element, Group):
            names |= variables(element.elements)
    return names


def define(parser):
    """`macro_rules! name { rules }` at the parser (after `macro_rules`)."""
    keyword = parser.take()
    parser.expect('!')
    name = parser.name()
    opener = parser.token
    if opener.text not in DELIMITERS:
        raise opener.error('expected `{`, `(` or `[` after a macro_rules! name')
    end = closing(parser.tokens, parser.pos)
    body = parser.tokens[parser.pos + 1:end]
    parser.pos = end + 1
    if opener.text != '{':
        parser.expect(';')
    rules, index = [], 0
    while index < len(body):
        if body[index].text not in DELIMITERS:
            raise body[index].error('expected a delimited macro matcher')
        matcher_end = closing(body, index)
        matcher = parse_elements(body[index + 1:matcher_end], matcher=True)
        index = matcher_end + 1
        if index >= len(body) or body[index].text != '=>':
            raise (body[index] if index < len(body) else name).error('expected `=>` after a macro matcher')
        index += 1
        if index >= len(body) or body[index].text not in DELIMITERS:
            raise (body[index] if index < len(body) else name).error('expected a delimited macro transcriber')
        transcriber_end = closing(body, index)
        transcriber = parse_elements(body[index + 1:transcriber_end], matcher=False)
        rules.append((tuple(matcher), tuple(transcriber)))
        index = transcriber_end + 1
        if index < len(body):
            if body[index].text != ';':
                raise body[index].error('expected `;` between macro rules')
            index += 1
    if not rules:
        raise name.error('a macro_rules! definition needs at least one rule')
    parser.macros[name.text] = MacroRules(name, tuple(rules))
    return keyword


class _Matcher:
    def __init__(self, parser, tokens):
        self.parser = parser
        self.tokens = tokens

    def sequence(self, elements, position, end, bindings):
        for element in elements:
            if isinstance(element, Token):
                if position >= end or self.tokens[position].text != element.text:
                    return None
                position += 1
            elif isinstance(element, Group):
                if position >= end or self.tokens[position].text != element.open.text:
                    return None
                close = closing(self.tokens, position)
                if close >= end or self.sequence(element.elements, position + 1, close, bindings) != close:
                    return None
                position = close + 1
            elif isinstance(element, Fragment):
                after = self.fragment(element.kind, position, end)
                if after is None:
                    return None
                bindings[element.token.text] = Matched(element.kind, tuple(self.tokens[position:after]))
                position = after
            else:
                position = self.repetition(element, position, end, bindings)
                if position is None:
                    return None
        return position

    def repetition(self, element, position, end, bindings):
        iterations = []
        while True:
            start = position
            if iterations and element.separator is not None:
                if position >= end or self.tokens[position].text != element.separator.text:
                    break
                position += 1
            trial = {}
            after = self.sequence(element.elements, position, end, trial)
            if after is None or after == position:
                position = start
                break
            iterations.append(trial)
            position = after
            if element.operator == '?':
                break
        if element.operator == '+' and not iterations:
            return None
        for name in variables(element.elements):
            bindings[name] = [iteration.get(name) for iteration in iterations]
        return position

    def fragment(self, kind, position, end):
        if position >= end:
            return None
        token = self.tokens[position]
        if kind == 'ident':
            return position + 1 if token.kind in ('name', 'keyword') and token.text != '_' else None
        if kind == 'lifetime':
            return position + 1 if token.kind == 'lifetime' else None
        if kind == 'tt':
            if token.text in DELIMITERS and token.kind == 'punct':
                return closing(self.tokens, position) + 1
            return None if token.text in (')', ']', '}') else position + 1
        if kind == 'literal':
            if token.text == '-':
                position += 1
                if position >= end:
                    return None
                token = self.tokens[position]
            literal = token.kind in ('integer', 'float', 'string', 'char', 'byte', 'byte_string') or token.text in ('true', 'false')
            return position + 1 if literal else None
        if kind == 'block':
            return closing(self.tokens, position) + 1 if token.text == '{' else None
        sub = sub_parser(self.parser, self.tokens[position:end])
        try:
            if kind == 'expr':
                sub.expression()
            elif kind == 'ty':
                sub.type()
            elif kind in ('pat', 'pat_param'):
                sub.pattern(alternatives=kind == 'pat')
            else:  # path
                if sub.token.kind not in ('name', 'keyword'):
                    return None
                sub.take()
                while sub.token.text == '::':
                    sub.take()
                    sub.take()
        except RustError:
            return None
        return position + sub.pos if sub.pos else None


def sub_parser(parser, tokens):
    """The parser over `tokens` (with an end marker), sharing its state."""
    child = object.__new__(type(parser))
    child.__dict__ = parser.__dict__.copy()
    last = tokens[-1] if tokens else parser.token
    child.tokens = list(tokens) + [Token('<eof>', 'eof', last.line, last.column, source_path=last.source_path)]
    child.pos = 0
    return child


def _lookup(bindings, name, indices):
    value = bindings[name]
    for index in indices:
        if not isinstance(value, list):
            break
        value = value[index]
    return value


def transcribe(elements, bindings, mark, indices, output, token):
    for element in elements:
        if isinstance(element, Token):
            output.append(replace(element, hygiene=mark))
        elif isinstance(element, Group):
            output.append(replace(element.open, hygiene=mark))
            transcribe(element.elements, bindings, mark, indices, output, token)
            output.append(replace(element.close, hygiene=mark))
        elif isinstance(element, Variable):
            name = element.token.text
            if name not in bindings:
                raise element.token.error(f'unknown macro variable `{name}`')
            value = _lookup(bindings, name, indices)
            if isinstance(value, list):
                raise element.token.error(f'variable `{name}` is still repeating at this depth')
            if value is None:
                raise element.token.error(f'variable `{name}` is not bound in this repetition')
            if value.kind == 'expr':
                first = value.tokens[0]
                output.append(Token('(', 'punct', first.line, first.column, source_path=first.source_path))
                output.extend(value.tokens)
                last = value.tokens[-1]
                output.append(Token(')', 'punct', last.line, last.column, source_path=last.source_path))
            else:
                output.extend(value.tokens)
        else:
            names = [name for name in sorted(variables(element.elements)) if name in bindings]
            lengths = {}
            for name in names:
                value = _lookup(bindings, name, indices)
                if isinstance(value, list):
                    lengths[name] = len(value)
            if not lengths:
                raise element.token.error('attempted to repeat an expression containing no syntax variables '
                                          'matched as repeating at this depth')
            if len(set(lengths.values())) > 1:
                (first, count), (second, other) = list(lengths.items())[:2]
                raise element.token.error(f'meta-variable `{first}` repeats {count} times, but `{second}` '
                                          f'repeats {other} times')
            for index in range(next(iter(lengths.values()))):
                if index and element.separator is not None:
                    output.append(replace(element.separator, hygiene=mark))
                transcribe(element.elements, bindings, mark, indices + (index,), output, token)


def expand(parser, name):
    """Expand the invocation `name ! (input)` at the parser (positioned after
    the name); returns the transcribed tokens and leaves the parser after the
    invocation's closing delimiter."""
    definition = parser.macros[name.text]
    parser.expect('!')
    opener = parser.token
    if opener.text not in DELIMITERS:
        raise opener.error('expected one of `(`, `[` or `{` after a macro name')
    end = closing(parser.tokens, parser.pos)
    tokens = parser.tokens[:end]
    start = parser.pos + 1
    parser.pos = end + 1
    state = parser.macro_state
    depth = state['depths'].get(name.hygiene, 0) + 1
    if depth > RECURSION_LIMIT:
        raise name.error(f'recursion limit reached while expanding `{name.text}!` (recursion_limit {RECURSION_LIMIT})')
    state['count'] += 1
    if state['count'] > MAX_EXPANSIONS:
        raise name.error(f'macro expansion exceeds implementation limit ({MAX_EXPANSIONS} expansions)')
    matcher = _Matcher(parser, tokens)
    for elements, transcriber in definition.rules:
        bindings = {}
        if matcher.sequence(elements, start, end, bindings) == end:
            mark = state['count']
            state['depths'][mark] = depth
            output = []
            transcribe(transcriber, bindings, mark, (), output, name)
            state['tokens'] += len(output)
            if state['tokens'] > MAX_EXPANDED_TOKENS:
                raise name.error('macro expansion exceeds implementation limit (1Mi tokens)')
            parser.macro_expansions.append(MacroRulesExpansion(name, tuple(output)))
            return output
    raise (parser.tokens[start] if start < end else name).error(
        f'no rules expected this token in macro call `{name.text}!`')


@dataclass(frozen=True)
class MacroRulesExpansion:
    """A recorded expansion (diagnostics and source maps)."""
    token: Token
    tokens: tuple

    @property
    def name(self):
        return self.token.text


def splice(parser, start, tokens):
    """Replace the parser's tokens from `start` to its position with `tokens`
    and continue parsing at `start`."""
    parser.tokens = parser.tokens[:start] + list(tokens) + parser.tokens[parser.pos:]
    parser.pos = start
