"""Closed array-length/index constants and bounded owned aggregate storage.

These checks are not dynamic bounds-check elimination, trait solving or a
replacement for Rust const evaluation. Unsupported expressions fail explicitly.
"""
from copy import deepcopy

from .types import ArrayLength, FixedArrayType, TupleType

MAX_ARRAY_ELEMENTS = 4096


def closed_constant(expression, token):
    pending = [expression]
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        if count > 128:
            raise token.error('array constant expression exceeds implementation limit (128 nodes)')
        # Checked after typing: a name that denotes a const item is a `const`
        # node by now, while a local is still a `name` and stays rejected.
        if node.kind not in {'integer', 'bool', 'group', 'unary', 'binary', 'cast', 'const'}:
            raise token.error('array lengths and indices require closed scalar constant expressions; const generics and dynamic bounds/panic support are not implemented')
        pending.extend(node.children)


def length_value(checker, length, token):
    if isinstance(length, ArrayLength):
        from .semantics import unify
        expression = deepcopy(length.expression)
        unify('usize', checker.expression(expression), token)
        closed_constant(expression, token)
        length = checker.validate(expression)
    if type(length) is not int or length < 0:
        raise token.error('array length must be a nonnegative usize constant')
    if length > MAX_ARRAY_ELEMENTS:
        raise token.error('array length exceeds implementation limit (4096 elements)')
    return length


def storage_units(checker, ty, token):
    """Bound admitted concrete aggregate work, not a host sizeof/layout guess."""
    budget = MAX_ARRAY_ELEMENTS

    def visit(item, multiplier=1):
        nonlocal budget
        if isinstance(item, FixedArrayType):
            visit(item.element, multiplier * item.length)
        elif isinstance(item, TupleType):
            for element in item.elements:
                visit(element, multiplier)
        elif item in checker.records:
            for _, element in checker.records[item].fields:
                visit(element, multiplier)
        elif item in checker.enums and checker.enums[item].payloads:
            budget -= multiplier  # Tag; all typed private variant slots exist.
            for fields in checker.enums[item].payloads.values():
                for element in fields:
                    visit(element, multiplier)
        else:
            budget -= multiplier
        if budget < 0:
            raise token.error('array storage shape exceeds implementation limit (4096 scalar slots)')

    visit(ty)
