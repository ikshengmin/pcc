"""Enums and typed pattern coverage, without trait or borrowing shortcuts.

A bounded constructor matrix covers bool/enum/tuple cases and integer literal
partitions plus an explicit other-value class. This is not match ergonomics,
a borrow checker, or a replacement for Rust's trait solver.
"""
from .semantics import INTEGERS, bounds, resolve, unify
from .types import TupleType, TypeApplication, TypeParameter

MAX_PATTERN_ROWS = 512
MAX_COVERAGE_STEPS = 65536


def check_enums(checker, declarations=None):
    for enum in checker.enums.values() if declarations is None else declarations:
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


def contains_binding(pattern):
    return pattern.kind == "binding" or any(contains_binding(child) for child in pattern.children)


def check_pattern(checker, pattern, ty):
    pattern.ty = ty
    if pattern.kind == "wildcard":
        return
    if pattern.kind == "binding":
        if pattern.token.text in checker.scopes[-1]:
            raise pattern.token.error("duplicate binding in pattern")
        pattern.binding = checker.bind(pattern.token, ty, pattern.value)
    elif pattern.kind == "or":
        if contains_binding(pattern):
            raise pattern.token.error("bindings inside or-patterns require alternative binding unification, which is not implemented")
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
    elif pattern.kind in {"variant", "payload"}:
        name, variant = pattern.value
        if isinstance(name, TypeApplication) or name in checker.generics.definitions:
            name = checker.generics.path_type(name, pattern.token, expected=ty)
            checker.valid_type(name, pattern.token)
        enum = checker.enums.get(name)
        if enum is None or variant not in enum.discriminants:
            raise pattern.token.error("unknown enum variant pattern")
        unify(name, ty, pattern.token)
        fields = enum.payloads.get(variant)
        if pattern.kind == "payload":
            if fields is None or len(fields) != len(pattern.children):
                raise pattern.token.error("enum payload pattern shape does not match variant")
            for child, field_ty in zip(pattern.children, fields):
                check_pattern(checker, child, field_ty)
        elif fields is not None:
            raise pattern.token.error("payload variant pattern requires fields")
        pattern.variant = variant
        pattern.value = enum.discriminants[variant]
    else:
        raise pattern.token.error("unsupported pattern")


def pattern_bindings(pattern):
    if pattern.kind == "binding":
        yield pattern.binding
    for child in pattern.children:
        yield from pattern_bindings(child)


def validate_pattern(checker, pattern):
    pattern.ty = resolve(pattern.ty)
    if pattern.kind == "literal":
        pattern.value = checker.validate(pattern.value)
    if pattern.kind == "binding":
        pattern.binding.ty = resolve(pattern.binding.ty)
    for child in pattern.children:
        validate_pattern(checker, child)


def validate_match(checker, expr):
    from .syntax import Pattern

    def admitted(ty):
        if isinstance(ty, TypeParameter):
            # Unknown T admits only wildcard/binding patterns; type checking
            # already rejected concrete constructors/literals against T.
            return True
        if isinstance(ty, TupleType):
            return all(admitted(item) for item in ty.elements)
        return ty in INTEGERS | {"bool", "()"} or ty in checker.enums or ty in checker.records
    if not admitted(expr.children[0].ty):
        raise expr.token.error("match currently supports owned scalar/aggregate values, not reference matching")
    for pattern in expr.value:
        validate_pattern(checker, pattern)
    budget = MAX_COVERAGE_STEPS

    def wildcards(types):
        return tuple(Pattern("wildcard", expr.token, ty=ty) for ty in types)

    def covered(matrix, types):
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise expr.token.error("match exhaustiveness exceeds implementation limit")
        if not matrix:
            return False
        if not types:
            return True
        # Expand alternatives only at the selected column. Nested tuple/sum
        # constructors are specialized structurally, never flattened into an
        # invalid product of all enum payloads.
        expanded = []
        pending = list(matrix)
        while pending:
            row = pending.pop()
            if row[0].kind == "or":
                pending.extend((option,) + row[1:] for option in row[0].children)
            else:
                expanded.append(row)
            if len(expanded) + len(pending) > MAX_PATTERN_ROWS:
                raise expr.token.error("pattern matrix exceeds implementation limit")
        matrix = expanded
        if any(all(p.kind in {"wildcard", "binding"} for p in row) for row in matrix):
            return True
        ty = types[0]
        if isinstance(ty, TupleType):
            constructors = [("tuple", None, ty.elements)]
        elif ty in checker.enums:
            enum = checker.enums[ty]
            constructors = [("enum", name, enum.payloads.get(name, ())) for name in enum.discriminants]
        elif ty == "bool":
            constructors = [("literal", value, ()) for value in (False, True)]
        elif ty in INTEGERS:
            values = {row[0].value for row in matrix if row[0].kind == "literal"}
            constructors = [("literal", value, ()) for value in values] + [("literal", object(), ())]
        else:
            constructors = [("unit", None, ())]
        for kind, tag, fields in constructors:
            specialized = []
            for row in matrix:
                head, tail = row[0], row[1:]
                if head.kind in {"wildcard", "binding"}:
                    specialized.append(wildcards(fields) + tail)
                elif kind == "tuple" and head.kind == "tuple":
                    specialized.append(tuple(head.children) + tail)
                elif kind == "enum" and head.kind in {"variant", "payload"} and head.variant == tag:
                    specialized.append(tuple(head.children) + tail)
                elif kind == head.kind and (kind == "unit" or head.value == tag):
                    specialized.append(tail)
            if not covered(specialized, tuple(fields) + types[1:]):
                return False
        return True

    if not covered([(pattern,) for pattern in expr.value], (expr.children[0].ty,)):
        raise expr.token.error("non-exhaustive match patterns")
