"""Reference admission boundaries.

Shared and mutable references in bodies, parameters and `&self`/`&mut self`
receivers are admitted by owned analysis (stages 4-5 of README "Replacing the
rustc admission gate"): sema rejects explicit lifetimes and receiver types,
reference returns and references in aggregates with explicit boundaries, the
move checker owns shared and mutable loans, and
reject_unowned_reference_shapes re-validates the subset after sema. Presence
detection (contains_references) still decides which checked inputs leave moves
to the actual check and gates the module-only analysis API. Walking all
declaration/type/expression fields also covers unused functions, nested type
applications and implicit method autoref declarations, never token text.
"""
from dataclasses import fields, is_dataclass

from .syntax import ConstItem, Expr, RustError, StaticItem
from .types import SLICE_GAP, SharedReference, SliceType, is_static_str


REFERENCE_CHECK_REQUIRED = (
    "reference-bearing Rust source requires an actual source/options-bound "
    "CheckedRustSource; use check_rust_source or compile_rust_checked; "
    "experimental_rustc_checked=True is not reference certification"
)


def contains_references(*roots):
    """Inspect owned syntax/semantic structure, never token substrings or names."""
    pending = list(roots)
    seen = set()
    while pending:
        value = pending.pop()
        if value is None or isinstance(value, (str, bytes, int, bool)):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if is_static_str(value):
            continue  # Literal data only; see types.STATIC_STR.
        if isinstance(value, (ConstItem, StaticItem)):
            # A const item's `&str` is `&'static str` (elision in a const).
            pending.append(value.expression)
            if not is_static_str(value.ty, elided_is_static=True):
                pending.append(value.ty)
            continue
        if isinstance(value, SharedReference):
            return True
        if isinstance(value, Expr) and value.kind in {"borrow", "deref"}:
            return True
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (tuple, list)):
            pending.extend(value)
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, field.name) for field in fields(value))
    return False


def first_slice(*roots):
    """The first slice type in the structure, reported before the generic
    reference boundary because no check could admit it."""
    pending, seen = list(roots), set()
    while pending:
        value = pending.pop()
        if value is None or isinstance(value, (str, bytes, int, bool)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, SliceType):
            return value
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, (tuple, list)):
            pending.extend(value)
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, item.name) for item in fields(value))
    return None


REFERENCE_RETURN_GAP = ("reference returns and aggregate reference storage require lifetime analysis, "
                        "which is not implemented")


def has_reference(ty) -> bool:
    """Whether a (resolved) type contains a reference anywhere."""
    from .call_inference import partial
    from .types import FixedArrayType, TupleType, TypeApplication
    ty = partial(ty)
    if isinstance(ty, SharedReference):
        return True
    if isinstance(ty, TupleType):
        return any(has_reference(item) for item in ty.elements)
    if isinstance(ty, FixedArrayType):
        return has_reference(ty.element)
    if isinstance(ty, TypeApplication):
        return any(has_reference(item) for item in ty.arguments)
    return False


def reject_unowned_reference_shapes(checker):
    """Fail closed after sema: owned loans (stages 4-5) cover references in
    bodies and parameters only, never in results or aggregate storage, even if
    a semantic transformation synthesized one."""
    for function in checker.functions:
        if has_reference(function.result):
            raise function.token.error(REFERENCE_RETURN_GAP)
    for record in checker.records.values():
        for token, ty in record.fields:
            if has_reference(ty):
                raise token.error(REFERENCE_RETURN_GAP)
    for enum in checker.enums.values():
        for payload in enum.payloads.values():
            if any(has_reference(ty) for ty in payload):
                raise enum.token.error(REFERENCE_RETURN_GAP)


def reject_slices(*roots):
    slice_type = first_slice(*roots)
    if slice_type is not None and slice_type.token is not None:
        raise slice_type.token.error(SLICE_GAP)


def reject_uncertified_references(*roots):
    slice_type = first_slice(*roots)
    if slice_type is not None and slice_type.token is not None:
        raise slice_type.token.error(SLICE_GAP)
    if contains_references(*roots):
        raise RustError(REFERENCE_CHECK_REQUIRED)
