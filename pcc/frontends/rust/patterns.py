"""Enums and typed pattern coverage, without trait or borrowing shortcuts.

A bounded constructor matrix covers bool/enum/tuple cases and integer literal
partitions plus an explicit other-value class. This is not match ergonomics,
a borrow checker, or a replacement for Rust's trait solver.
"""
from .semantics import INTEGERS, InferredType, bounds, resolve, unify
from .syntax import Enumeration
from .types import FixedArrayType, TupleType, TypeApplication, TypeParameter

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
                                raise node.token.error("default enum discriminants currently require unsuffixed integer constants; typed discriminant expressions are not implemented")
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


BINDING_KINDS = frozenset({"binding", "at"})


def contains_binding(pattern):
    return pattern.kind in BINDING_KINDS or any(contains_binding(child) for child in pattern.children)


# Standard prelude tuple variants (std::prelude::v1).
PRELUDE_VARIANTS = {"Some": "Option", "Ok": "Result", "Err": "Result"}


def check_pattern(checker, pattern, ty, *, allow_inferred=False):
    pattern.ty = ty
    if pattern.kind == "wildcard":
        return
    if pattern.kind in BINDING_KINDS:
        if pattern.token.text in checker.scopes[-1]:
            raise pattern.token.error("duplicate binding in pattern")
        pattern.binding = checker.bind(pattern.token, ty, pattern.value)
        if pattern.kind == "at":
            # `x @ p`: p is checked against the same scrutinee type and its
            # own bindings share this pattern's scope (E0416 on reuse).
            check_pattern(checker, pattern.children[0], ty, allow_inferred=allow_inferred)
    elif pattern.kind == "or":
        if contains_binding(pattern):
            raise pattern.token.error("bindings inside or-patterns require alternative binding unification, which is not implemented")
        for child in pattern.children:
            check_pattern(checker, child, ty, allow_inferred=allow_inferred)
    elif pattern.kind == "tuple":
        from .call_inference import partial
        ty = partial(ty)
        if allow_inferred and isinstance(ty, InferredType):
            # The pattern contributes only shape, never guessed element types.
            # Keep fresh slots shared with its scrutinee and later arm uses.
            elements = tuple(InferredType(child.token, description="tuple pattern element")
                             for child in pattern.children)
            ty = unify(ty, TupleType(elements), pattern.token)
        if not isinstance(ty, TupleType) or len(ty.elements) != len(pattern.children):
            raise pattern.token.error("tuple pattern shape does not match scrutinee")
        for child, element in zip(pattern.children, ty.elements):
            check_pattern(checker, child, element, allow_inferred=allow_inferred)
    elif pattern.kind == "unit":
        unify("()", ty, pattern.token)
    elif pattern.kind == "literal":
        unify(ty, checker.expression(pattern.value), pattern.token)
    elif pattern.kind in {"variant", "payload"}:
        name, variant = pattern.value
        if name is None:
            if variant in PRELUDE_VARIANTS:
                raise pattern.token.error(
                    f"prelude {PRELUDE_VARIANTS[variant]} variant patterns require the core "
                    "Option/Result model, which is not implemented")
            raise pattern.token.error(
                "unqualified tuple-struct/variant patterns require value-namespace pattern "
                "resolution, which is not implemented; qualify the enum variant")
        if isinstance(name, TypeApplication) or name in checker.generics.definitions or name == "Self" or checker.module_resolution is not None:
            name = checker.generics.path_type(
                name, pattern.token, expected=ty, allow_inferred=allow_inferred)
            checker.valid_type(name, pattern.token)
        enum = checker.generics.definitions.get(
            name.name if isinstance(name, TypeApplication) else name)
        if not isinstance(enum, Enumeration) or variant not in enum.discriminants:
            raise pattern.token.error("unknown enum variant pattern")
        unify(name, ty, pattern.token)
        fields = checker.generics.specialized_payload(name, variant)
        named = enum.named_payloads.get(variant)
        if pattern.named_fields is not None:
            convert_named_pattern(pattern, variant, named, fields)
        elif pattern.kind == "payload" and not pattern.named and named is not None:
            raise pattern.token.error(f"expected tuple struct or tuple variant, found struct variant `{variant}` (rustc E0164)")
        if pattern.kind == "payload":
            if fields is None or len(fields) != len(pattern.children):
                raise pattern.token.error("enum payload pattern shape does not match variant")
            for child, field_ty in zip(pattern.children, fields):
                check_pattern(checker, child, field_ty, allow_inferred=allow_inferred)
        elif fields is not None:
            raise pattern.token.error("payload variant pattern requires fields")
        pattern.variant = variant
        pattern.value = enum.discriminants[variant]
    else:
        raise pattern.token.error("unsupported pattern")


def binding_patterns(pattern):
    if pattern.kind in BINDING_KINDS:
        yield pattern
    for child in pattern.children:
        yield from binding_patterns(child)


def convert_named_pattern(pattern, variant, named, fields):
    """`V { a, b: p, .. }` -> the positional payload pattern it denotes."""
    from .syntax import Pattern
    written, rest = pattern.named_fields
    names = [token.text for token in named] if named is not None else []
    if fields is not None and named is None and written:
        raise pattern.token.error('numbered field patterns on tuple variants are not implemented')
    positional = [None] * len(names)
    for token, child in zip(written, pattern.children):
        if token.text not in names:
            raise token.error(f'variant `{variant}` does not have a field named `{token.text}` (rustc E0026)')
        index = names.index(token.text)
        if positional[index] is not None:
            raise token.error(f'field `{token.text}` bound multiple times in the pattern (rustc E0025)')
        positional[index] = child
    missing = [names[index] for index, child in enumerate(positional) if child is None]
    if missing and not rest:
        raise pattern.token.error(f'pattern does not mention field `{missing[0]}` (rustc E0027)')
    pattern.named_fields = None
    if fields is None:
        pattern.kind, pattern.children = "variant", []
    elif named is None:
        pattern.children = [Pattern("wildcard", pattern.token) for _ in fields]
    else:
        pattern.children = [child if child is not None else Pattern("wildcard", pattern.token)
                            for child in positional]


def pattern_bindings(pattern):
    for item in binding_patterns(pattern):
        yield item.binding


def validate_pattern(checker, pattern):
    pattern.ty = resolve(pattern.ty)
    if pattern.kind == "literal":
        pattern.value = checker.validate(pattern.value)
    if pattern.kind in BINDING_KINDS:
        pattern.binding.ty = resolve(pattern.binding.ty)
    for child in pattern.children:
        validate_pattern(checker, child)


def validate_pattern_scrutinee(checker, ty, token):
    """Shared finite owned-value domain; no reference match ergonomics."""
    from .generics import children
    pending = [ty]
    while pending:
        item = pending.pop()
        if isinstance(item, FixedArrayType):
            raise token.error("array match coverage and destructuring are not implemented")
        pending.extend(children(item))

    def admitted(ty):
        if isinstance(ty, TypeParameter):
            # Unknown T admits only wildcard/binding patterns; type checking
            # already rejected concrete constructors/literals against T.
            return True
        if isinstance(ty, TupleType):
            return all(admitted(item) for item in ty.elements)
        return ty in INTEGERS | {"bool", "()"} or ty in checker.enums or ty in checker.records
    if not admitted(ty):
        raise token.error("match currently supports owned scalar/aggregate values, not reference matching")


def validate_match(checker, expr):
    from .syntax import Pattern

    validate_pattern_scrutinee(checker, expr.children[0].ty, expr.token)
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
            elif row[0].kind == "at":
                # `x @ p` matches exactly the values p matches.
                pending.append((row[0].children[0],) + row[1:])
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
