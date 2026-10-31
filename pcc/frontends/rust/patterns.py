"""Unit-only enums and binding-free pattern coverage, without trait shortcuts.

A bounded constructor matrix covers bool/enum/tuple cases and integer literal
partitions plus an explicit other-value class. This is not payload matching,
match ergonomics, a borrow checker, or a replacement for Rust's trait solver.
"""
from itertools import product

from .semantics import INTEGERS, bounds, resolve, unify
from .types import TupleType

MAX_PATTERN_ROWS = 512
MAX_COVERAGE_STEPS = 65536


def check_enums(checker):
    for enum in checker.enums.values():
        previous = -1
        seen = set()
        for token, expr in enum.variants:
            if token.text in enum.discriminants:
                raise token.error("duplicate enum variant")
            dtype = enum.representation or "i64"
            if expr is not None:
                if enum.representation is None:
                    # Default logical discriminants are isize, not i64. The
                    # admitted targets are both 64-bit, but an explicitly i64
                    # typed source constant must not masquerade as isize.
                    def unsuffixed(node):
                        if node.kind == "integer":
                            if any(str(node.value).endswith(ty) for ty in INTEGERS):
                                raise node.token.error("default enum discriminants require unsuffixed integer constants; isize typing is not implemented")
                        elif node.kind not in {"group", "unary", "binary"}:
                            raise node.token.error("default enum discriminants currently require unsuffixed integer constant expressions")
                        for child in node.children:
                            unsuffixed(child)
                    unsuffixed(expr)
                unify(dtype, checker.expression(expr), token)
                value = checker.validate(expr)
                if value is None:
                    raise token.error("enum discriminant must be a supported integer constant")
            else:
                value = previous + 1
            if not bounds(dtype)[0] <= value <= bounds(dtype)[1]:
                raise token.error("enum discriminant overflow")
            if value in seen:
                raise token.error("duplicate enum discriminant")
            enum.discriminants[token.text] = value
            seen.add(value)
            previous = value


def check_pattern(checker, pattern, ty):
    pattern.ty = ty
    if pattern.kind == "wildcard":
        return
    if pattern.kind == "or":
        for child in pattern.children:
            check_pattern(checker, child, ty)
    elif pattern.kind == "tuple":
        if not isinstance(ty, TupleType) or len(ty.elements) != len(pattern.children):
            raise pattern.token.error("tuple pattern shape does not match scrutinee")
        for child, element in zip(pattern.children, ty.elements):
            check_pattern(checker, child, element)
    elif pattern.kind == "unit":
        unify("()", ty, pattern.token)
    elif pattern.kind == "literal":
        unify(ty, checker.expression(pattern.value), pattern.token)
    elif pattern.kind == "variant":
        name, variant = pattern.value
        enum = checker.enums.get(name)
        if enum is None or variant not in enum.discriminants:
            raise pattern.token.error("unknown enum variant pattern")
        unify(name, ty, pattern.token)
        pattern.value = enum.discriminants[variant]
    else:
        raise pattern.token.error("unsupported pattern")


def leaf_types(checker, ty, token):
    if isinstance(ty, TupleType):
        return [item for child in ty.elements for item in leaf_types(checker, child, token)]
    if ty == "()":
        return []
    if ty in INTEGERS | {"bool"} or ty in checker.enums:
        return [ty]
    raise token.error("match currently supports bool/integer/unit-only-enum and tuples of those types")


def pattern_rows(checker, pattern):
    pattern.ty = resolve(pattern.ty)
    if pattern.kind == "literal":
        pattern.value = checker.validate(pattern.value)
    if pattern.kind in {"literal", "variant"}:
        return [(pattern.value,)]
    if pattern.kind == "unit":
        return [()]
    if pattern.kind == "wildcard":
        return [(None,) * len(leaf_types(checker, pattern.ty, pattern.token))]
    rows = []
    if pattern.kind == "or":
        for child in pattern.children:
            rows.extend(pattern_rows(checker, child))
            if len(rows) > MAX_PATTERN_ROWS:
                raise pattern.token.error("pattern matrix exceeds implementation limit")
    else:
        rows = [()]
        for child in pattern.children:
            child_rows = pattern_rows(checker, child)
            if len(rows) * len(child_rows) > MAX_PATTERN_ROWS:
                raise pattern.token.error("pattern matrix exceeds implementation limit")
            rows = [left + right for left, right in product(rows, child_rows)]
    return rows


def validate_match(checker, expr):
    columns = leaf_types(checker, expr.children[0].ty, expr.token)
    rows = []
    for pattern in expr.value:
        rows.extend(pattern_rows(checker, pattern))
        if len(rows) > MAX_PATTERN_ROWS:
            raise expr.token.error("pattern matrix exceeds implementation limit")
    budget = MAX_COVERAGE_STEPS

    def covered(matrix, types):
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise expr.token.error("match exhaustiveness exceeds implementation limit")
        if not matrix:
            return False
        if not types or any(all(value is None for value in row) for row in matrix):
            return True
        ty = types[0]
        if ty == "bool":
            constructors = (False, True)
        elif ty in checker.enums:
            constructors = tuple(checker.enums[ty].discriminants.values())
        else:
            # Every unmentioned integer behaves identically for these patterns.
            constructors = tuple({row[0] for row in matrix if row[0] is not None}) + (object(),)
        return all(covered([row[1:] for row in matrix if row[0] is None or row[0] == tag], types[1:])
                   for tag in constructors)

    if not covered(rows, columns):
        raise expr.token.error("non-exhaustive match patterns")
