"""Owned standard-array iteration contracts, not user-name trait dispatch.

Only externally checked, concrete arrays with recursively empty destruction
are admitted. The proof is independent of Copy: a non-Copy record still moves.
Unknown generic destruction, custom iterators and Drop are not erased here.
"""
from dataclasses import dataclass
from enum import Enum

from .types import (
    INTEGER_TYPES, FixedArrayType, SharedReference, TupleType, TypeApplication,
    TypeParameter,
)


class StandardIterator(Enum):
    OWNED_ARRAY = 'core::array::IntoIter'


@dataclass(frozen=True)
class OwnedArrayIteration:
    array_type: FixedArrayType
    identity: StandardIterator = StandardIterator.OWNED_ARRAY


def prove_no_drop(checker, ty, token, visiting=()):
    """Require a concrete supported value graph, not merely a Copy heuristic.

    User trait implementations (including Drop) are rejected by the parser.
    Revisit this proof when that admission boundary changes; there is no
    general needs_drop/trait solver or destructor-glue implementation here.
    """
    if isinstance(ty, TypeParameter):
        raise token.error('for iteration with unknown generic element destruction is not implemented')
    if isinstance(ty, SharedReference):
        raise token.error('for array reference elements require additional iterator lifetime support')
    if isinstance(ty, FixedArrayType):
        prove_no_drop(checker, ty.element, token, visiting)
        return
    if isinstance(ty, TupleType):
        for element in ty.elements:
            prove_no_drop(checker, element, token, visiting)
        return
    if isinstance(ty, TypeApplication):
        checker.valid_type(ty, token)
    if ty in INTEGER_TYPES | {'bool', '()'}:
        return
    if ty in visiting:
        raise token.error('recursive for element destruction is not implemented')
    if ty in checker.records:
        fields = tuple(field for _, field in checker.records[ty].fields)
    elif ty in checker.enums:
        fields = tuple(field for payload in checker.enums[ty].payloads.values() for field in payload)
    else:
        raise token.error('for element destruction is not implemented for this type')
    for field in fields:
        prove_no_drop(checker, field, token, visiting + (ty,))


def validate_iteration(checker, expression):
    array = expression.children[0].ty
    checker.valid_type(array, expression.token)
    prove_no_drop(checker, array.element, expression.token)
    expression.value = OwnedArrayIteration(array)
