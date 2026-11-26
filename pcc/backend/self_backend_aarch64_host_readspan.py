"""CPython-only exact single-run snapshot proof for typed AArch64 passes.

Keep class/method identity introspection out of the native hot context
module's type inference. The caller's native raw flag excludes this route.
"""
from __future__ import annotations

from .self_backend_aarch64_fragments import AArch64EmissionFragments
from .self_backend_value_arena import CompilerIntArena
from . import self_backend_value_arena as _readspan_arena_module
from .self_backend_value_arena import CompilerRecordSpanArena as _ReadSpanRecordArena
from .self_backend_value_arena import CompilerInt2 as _ReadSpanInt2

_READSPAN_ARENA_CLASS = CompilerIntArena
_READSPAN_ARENA_METHODS = tuple(
    (name, getattr(CompilerIntArena, name, None)) for name in (
        "__init__", "__len__", "append", "append2", "get_unchecked",
        "get2_unchecked", "get3_unchecked", "truncate", "extend_from", "close",
    )
)
_READSPAN_FRAGMENT_METHODS = tuple(
    (name, getattr(AArch64EmissionFragments, name, None))
    for name in ("copy_record_ids", "_require_open")
)
_READSPAN_SPAN_METHODS = tuple(
    (name, getattr(_ReadSpanRecordArena, name, None))
    for name in ("copy_ids", "_root", "_require_open")
)
_READSPAN_INT2 = getattr(_readspan_arena_module, "CompilerInt2", None)
_READSPAN_INT3 = getattr(_readspan_arena_module, "CompilerInt3", None)
_READSPAN_MALLOC = getattr(_readspan_arena_module, "malloc", None)


def _copy_exact_host_span_slice(ctx, span, ids):
    from . import self_backend_aarch64_typed_pass_context as _ctx_module
    TypedPassContext = _ctx_module.TypedPassContext
    # Exact single-run traversal would first allocate a temporary slice,
    # extend a fresh arena list, then copy that list before closing it.
    # Prove the same source shape/methods and scalar bounds first, and
    # create one independent snapshot directly. Unusual inputs retain the
    # authoritative traversal and its diagnostics/callback ordering.
    if (type(ctx) is not TypedPassContext
            or getattr(TypedPassContext, "__getattribute__", None) is not object.__getattribute__
            or getattr(_READSPAN_ARENA_CLASS, "__getattribute__", None) is not object.__getattribute__
            or getattr(_READSPAN_ARENA_CLASS, "__setattr__", None) is not object.__setattr__
            or getattr(AArch64EmissionFragments, "__getattribute__", None) is not object.__getattribute__
            or getattr(_ReadSpanRecordArena, "__getattribute__", None) is not object.__getattribute__
            or type(ctx.verify_each) is not bool or ctx.verify_each
            or type(ids) is not _READSPAN_ARENA_CLASS
            or CompilerIntArena is not _READSPAN_ARENA_CLASS
            or getattr(_ctx_module, "CompilerIntArena", None) is not _READSPAN_ARENA_CLASS
            or getattr(_ctx_module, "AArch64EmissionFragments", None) is not AArch64EmissionFragments
            or getattr(_readspan_arena_module, "CompilerIntArena", None) is not _READSPAN_ARENA_CLASS
            or getattr(_readspan_arena_module, "CompilerInt2", None) is not _READSPAN_INT2
            or getattr(_readspan_arena_module, "CompilerInt3", None) is not _READSPAN_INT3
            or getattr(_readspan_arena_module, "malloc", None) is not _READSPAN_MALLOC
            or type(ctx.fragments) is not AArch64EmissionFragments
            or type(ctx.fragments.records) is not _READSPAN_ARENA_CLASS
            or type(span) is not type(_ReadSpanInt2(0, 0))
            or type(span.first) is not int or type(span.second) is not int):
        return None
    fragments = ctx.fragments
    spans = fragments.spans
    record_length = fragments.records._length
    if (type(record_length) is not int or record_length < 0 or record_length % 4
            or type(fragments.records._address) is not int or fragments.records._address != 0
            or type(ids._length) is not int or ids._length != 0):
        return None
    limit = record_length // 4
    if (type(spans) is not _ReadSpanRecordArena
            or type(spans.generation) is not int or span.second != spans.generation
            or type(fragments.closed) is not bool or fragments.closed
            or type(spans.closed) is not bool or spans.closed):
        return None
    for name, original in _READSPAN_ARENA_METHODS:
        if getattr(_READSPAN_ARENA_CLASS, name, None) is not original:
            return None
    for name, original in _READSPAN_FRAGMENT_METHODS:
        if getattr(AArch64EmissionFragments, name, None) is not original or name in fragments.__dict__:
            return None
    for name, original in _READSPAN_SPAN_METHODS:
        if getattr(_ReadSpanRecordArena, name, None) is not original or name in spans.__dict__:
            return None
    for arena in (ids, spans.spans, spans.nodes, spans.runs):
        if (type(arena) is not _READSPAN_ARENA_CLASS
                or type(arena._address) is not int or arena._address != 0
                or type(arena._closed) is not bool or arena._closed
                or type(arena._values) is not list or type(arena._length) is not int
                or arena._length != len(arena._values)):
            return None
    header = span.first * 2
    if header < 0 or header + 1 >= spans.spans._length:
        return None
    root = spans.spans._values[header]
    length = spans.spans._values[header + 1]
    if type(root) is not int or type(length) is not int:
        return None
    node = root * 3
    if node < 0 or node + 2 >= spans.nodes._length:
        return None
    kind = spans.nodes._values[node]
    start = spans.nodes._values[node + 1]
    count = spans.nodes._values[node + 2]
    if (type(kind) is not int or type(start) is not int or type(count) is not int
            or kind not in (-2, -3) or start < 0 or count < 1
            or start + count > spans.runs._length
            or count != length
            or ids._values is spans.runs._values
            or ids._values is spans.nodes._values
            or ids._values is spans.spans._values):
        return None
    values = spans.runs._values
    end = start + count
    for position in range(start, end):
        record_id = values[position]
        if type(record_id) is not int or record_id < 0 or record_id >= limit:
            # Do not allocate or compare a custom payload before the
            # original copy/validation route gets its chance to fail.
            return None
    return values[start:end]
