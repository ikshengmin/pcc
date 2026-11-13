"""Reference presence is not a borrow proof: require the actual-check token.

This is an admission boundary only, not another partial borrow checker. Walking
all declaration/type/expression fields also covers unused functions, nested type
applications and implicit method autoref declarations. Unsupported aliases or
reference syntax still fail in the owned parser/type checker; they never reach
pointer lowering through a caller-attestation flag.
"""
from dataclasses import fields, is_dataclass

from .syntax import ConstItem, Expr, RustError
from .types import SharedReference, is_static_str


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
        if isinstance(value, ConstItem):
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


def reject_uncertified_references(*roots):
    if contains_references(*roots):
        raise RustError(REFERENCE_CHECK_REQUIRED)
