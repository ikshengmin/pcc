"""Built-in `#[derive(..)]` on structs and enums.

Rust Reference attributes/derive.html and the std derive documentation
(retrieved 2026-11-13); error codes observed from rustc 1.92.0. The nine
built-in derives are declared on the item and validated as rustc does:
required companion traits (Copy needs Clone, Eq needs PartialEq, PartialOrd
needs PartialEq, Ord needs Eq and PartialOrd) and every field type must
implement the derived trait. Their effects in PCC:

* Copy: the type is copied instead of moved.
* Clone: `x.clone()` is a non-moving copy. PCC's admitted types own no heap
  storage, so the bitwise copy is exactly the field-wise derived clone.
* PartialEq: `==`/`!=` compare structurally (enums: variant, then the active
  payload only) without moving either operand.
* Eq, PartialOrd, Ord, Hash, Debug, Default: declared and validated; every
  use of them (ordering operators, hashing, formatting, `default()`) is still
  an explicit gap, so none can silently succeed.

Derive macros from other crates (`thiserror::Error`, `serde::Serialize`)
are procedural macros and remain explicit gaps, as do derives on generic
data declarations (their impls carry trait bounds) and `Default` on enums
(it needs a `#[default]` variant).
"""
from __future__ import annotations

from dataclasses import dataclass

from .attribute_metadata import meta_list, offset

BUILTIN_DERIVES = ('Copy', 'Clone', 'PartialEq', 'Eq', 'PartialOrd', 'Ord', 'Hash', 'Debug', 'Default')
_REQUIRES = {'Copy': ('Clone',), 'Eq': ('PartialEq',), 'PartialOrd': ('PartialEq',), 'Ord': ('Eq', 'PartialOrd')}


@dataclass(frozen=True)
class DeriveAttribute:
    token: object  # the derived path's first token
    name: str      # the path as written, `::`-joined
    source_span: tuple[int, int]


def derive_attribute(parser, attribute, start):
    """`#[derive(Path, ...)]`; the parser is left after the closing `]`."""
    items = meta_list(parser, paths=True)
    for item in items:
        if item.form != 'word':
            raise item.token.error('expected a path to a derive macro')
    end = parser.expect(']')
    return [DeriveAttribute(item.token, item.name, item.source_span) for item in items]


def implements(checker, ty, trait):
    """Whether a field type implements a derivable trait in PCC's type universe."""
    from .types import STATIC_STR, FixedArrayType, TupleType
    if ty in checker.records or ty in checker.enums:
        return trait in checker.declared_derives.get(ty, ())
    if isinstance(ty, TupleType):
        return len(ty.elements) <= 12 and all(implements(checker, item, trait) for item in ty.elements)
    if isinstance(ty, FixedArrayType):
        if trait == 'Default' and ty.length > 32:
            return False
        return implements(checker, ty.element, trait)
    from .semantics import INTEGERS
    return ty in INTEGERS | {'bool', '()', STATIC_STR}


def validate_derives(checker):
    """Record each ADT's derived traits after checking them as rustc does."""
    # Generic declarations have left checker.records by now; check them here.
    for declaration in checker.generics.definitions.values():
        if declaration.derives and declaration.type_parameters:
            raise declaration.derives[0].token.error(
                'derives on generic data declarations need trait-bounded impls, which are not implemented')
    declarations = list(checker.records.items()) + list(checker.enums.items())
    checker.declared_derives = {}
    for name, declaration in declarations:
        seen = []
        for derive in declaration.derives:
            if derive.name not in BUILTIN_DERIVES:
                if '::' in derive.name:
                    raise derive.token.error(f'derive macro `{derive.name}` comes from another crate; '
                                             'procedural-macro expansion is not implemented')
                raise derive.token.error(f'cannot find derive macro `{derive.name}` in this scope '
                                         '(PCC implements only the built-in derives)')
            if derive.name in seen:
                raise derive.token.error(f'conflicting implementations of trait `{derive.name}` (rustc E0119)')
            seen.append(derive.name)
        checker.declared_derives[name] = frozenset(seen)
    for name, declaration in declarations:
        derived = checker.declared_derives[name]
        if not derived:
            continue
        token = {derive.name: derive.token for derive in declaration.derives}
        for trait in sorted(derived, key=[derive.name for derive in declaration.derives].index):
            for required in _REQUIRES.get(trait, ()):
                if required not in derived:
                    raise token[trait].error(f'deriving `{trait}` requires `{name}: {required}` (rustc E0277)')
        if 'Default' in derived and name in checker.enums:
            raise token['Default'].error('`#[derive(Default)]` on an enum needs a `#[default]` variant, '
                                         'which is not implemented')
        if name in checker.records:
            field_types = list(checker.fields(name))
        else:
            field_types = [ty for fields in checker.enums[name].payloads.values() for ty in fields]
        # Deterministic, rustc-like order: Copy's field check (E0204) first,
        # then the other derives as written.
        written = [derive.name for derive in declaration.derives]
        for trait in sorted(derived, key=lambda name: (name != 'Copy', written.index(name))):
            for ty in field_types:
                if not implements(checker, ty, trait):
                    code = 'E0204' if trait == 'Copy' else 'E0369' if trait == 'PartialEq' else 'E0277'
                    raise token[trait].error(f'field type {ty} does not implement `{trait}` (rustc {code})')


def equality_gap(checker, ty):
    """None when `==` on `ty` is structural equality PCC lowers, else why not."""
    from .types import STATIC_STR, FixedArrayType, TupleType
    if ty == STATIC_STR:
        return "comparing string contents needs core str equality, which is not implemented"
    if ty in checker.records or ty in checker.enums:
        if 'PartialEq' not in checker.declared_derives.get(ty, ()):
            return f"{ty} does not implement PartialEq (rustc E0369)"
        fields = (checker.fields(ty) if ty in checker.records
                  else [t for items in checker.enums[ty].payloads.values() for t in items])
        return next((gap for gap in (equality_gap(checker, item) for item in fields) if gap), None)
    if isinstance(ty, TupleType):
        return next((gap for gap in (equality_gap(checker, item) for item in ty.elements) if gap), None)
    if isinstance(ty, FixedArrayType):
        return equality_gap(checker, ty.element)
    from .semantics import INTEGERS
    if ty in INTEGERS | {'bool', '()'}:
        return None
    return f"{ty} has no structural equality here"


@dataclass(frozen=True)
class HelperAttribute:
    """An inert attribute owned by a procedural derive, kept as written.

    `#[error(..)]`, `#[from]`, `#[source]`, `#[serde(..)]`: only the derive
    macro gives them meaning, and that derive is itself an explicit semantic
    gap, so they are retained (never interpreted) and parsing continues.
    """
    token: object
    name: str
    tokens: tuple


def has_procedural_derive(derives):
    return any(derive.name not in BUILTIN_DERIVES for derive in derives)


def helper_attribute(parser, first):
    """Consume one attribute whose first path token was taken, through `]`."""
    start = parser.pos - 1
    name = first.text
    while parser.accept('::'):
        name += '::' + parser.name().text
    while parser.token.text != ']':
        if parser.token.kind == 'eof':
            raise first.error('unterminated attribute')
        if parser.token.text in {'(', '[', '{'}:
            parser.skip_token_tree()
        else:
            parser.take()
    parser.expect(']')
    return HelperAttribute(first, name, tuple(parser.tokens[start:parser.pos]))


def helper_attributes(parser, allowed):
    """Field/variant attributes: retained helpers when a procedural derive
    owns them; otherwise left for the ordinary parser to diagnose."""
    found = []
    while allowed and parser.token.text == '#' and parser.tokens[parser.pos + 1].text == '[':
        parser.take()
        parser.take()
        found.append(helper_attribute(parser, parser.take()))
    return found
