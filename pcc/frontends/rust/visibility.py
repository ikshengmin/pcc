"""Restricted visibility: `pub(crate)`, `pub(self)`, `pub(super)`, `pub(in path)`.

Rust Reference visibility-and-privacy.html#pubin-path-pubcrate-pubsuper-and-pubself.
A visibility is `False` (private: the owning module and its descendants),
`True` (unrestricted `pub`) or a `VisibilityRestriction`. A restriction names
the module whose subtree may see the item; it must be the owning module or one
of its ancestors (rustc E0742). Edition 2018 and later require `pub(in path)`
paths to start with `crate`, `self` or `super`; edition 2015 reads them from
the crate root. Use aliases are not followed in restriction paths.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisibilityRestriction:
    token: object  # the `pub` token, for diagnostics
    path: tuple    # `crate`, `self`, `super` or the `in` path segments
    explicit_in: bool = False

    @property
    def spelling(self):
        inner = '::'.join(segment.text for segment in self.path)
        return 'pub(' + ('in ' if self.explicit_in else '') + inner + ')'


def parse_visibility(parser):
    """`pub`, a restricted `pub(...)`, or nothing (False).

    `pub (` also starts a parenthesized type in a tuple field, so only the
    restricted forms rustc recognizes are consumed: `pub(crate)`, `pub(self)`,
    `pub(super)` and `pub(in path)`.
    """
    if not parser.accept('pub'):
        return False
    pub = parser.tokens[parser.pos - 1]
    if parser.token.text != '(':
        return True
    first, second = parser.tokens[parser.pos + 1], parser.tokens[parser.pos + 2]
    if first.text in {'crate', 'self', 'super'} and second.text == ')':
        parser.take()
        path, explicit_in = (parser.take(),), False
    elif first.text == 'in':
        parser.take()
        parser.take()
        path, explicit_in = [], True
        while True:
            if parser.token.text in {'crate', 'self', 'super'}:
                path.append(parser.take())
            else:
                path.append(parser.name())
            if len(path) > 32:
                raise pub.error('visibility path exceeds implementation limit (32 segments)')
            if not parser.accept('::'):
                break
        path = tuple(path)
    else:
        return True
    parser.expect(')')
    restriction = VisibilityRestriction(pub, path, explicit_in)
    parser.visibility_restrictions.append(restriction)
    return restriction


def restriction_components(restriction, owner, edition):
    """Module components a restriction admits, checked against `owner`."""
    path = restriction.path
    first = path[0].text
    if first == 'crate':
        current, rest = (), path[1:]
    elif first == 'self':
        current, rest = tuple(owner), path[1:]
    elif first == 'super':
        current, rest = tuple(owner), list(path)
        while rest and rest[0].text == 'super':
            if not current:
                raise rest[0].error('too many leading super path segments')
            current, rest = current[:-1], rest[1:]
    elif edition >= 2018:
        raise path[0].error('relative paths are not supported in visibilities in 2018 edition or later; '
                            'start the path with crate, self or super')
    else:
        current, rest = (), path
    for segment in rest:
        if segment.text in {'crate', 'self', 'super'}:
            raise segment.error('nonleading self/crate/super visibility path segments are not allowed')
        current = current + (segment.text,)
    if tuple(owner[:len(current)]) != current:
        raise restriction.token.error(
            f'visibilities can only be restricted to ancestor modules ({restriction.spelling})')
    return current


def scope_components(visibility, owner, edition):
    """None for unrestricted `pub`; else the components whose subtree sees it."""
    if visibility is True:
        return None
    if not visibility:
        return tuple(owner)
    return restriction_components(visibility, owner, edition)


def within(components, scope):
    return scope is None or tuple(components[:len(scope)]) == tuple(scope)
