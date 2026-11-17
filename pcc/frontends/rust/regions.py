"""Signature regions: rustc's lifetime elision over the region slots of a type.

https://doc.rust-lang.org/reference/lifetime-elision.html#lifetime-elision-in-functions
A value has up to two region slots (stage 6): an outer one for a reference or
a data type with one lifetime parameter (`&'a T`, `W<'a>`), and an inner one
for a reference to such a data type (`&'s W<'a>`: outer 's, inner 'a).
Regions nested deeper, inside tuples, arrays or type arguments, are not
modeled (slot_count is None) and keep explicit boundaries.

Each slot of a parameter has a region: its written lifetime, or a fresh
anonymous one when elided (a data type's lifetime may be elided in a path).
Lifetime arguments are erased from normalized types, so names come from the
types as written (Function.written_signature, and the impl's written owner
for `Self`). An elided output slot takes `self`'s outer region for a
`&self`/`&mut self` method, else the region of the only input lifetime
position (rustc counts positions, not names); otherwise it is rustc E0106.
These are the function's universal regions.
"""
from __future__ import annotations

STATIC = "'static"


def written(lifetime):
    return lifetime if lifetime not in (None, "'_") else None


def data_lifetime(ty, checker):
    """Whether a normalized type is a data type with a lifetime parameter."""
    declaration = checker.generics.definitions.get(ty) if isinstance(ty, str) else None
    return declaration is not None and bool(declaration.lifetime_parameters)


def slot_count(ty, checker):
    """Region slots of a normalized type: 0, 1 or 2, or None when its regions
    are nested beyond what the owned model represents."""
    from .call_inference import partial
    from .reference_admission import has_regions
    from .types import SharedReference
    ty = partial(ty)
    if isinstance(ty, SharedReference):
        inner = slot_count(ty.referent, checker)
        return None if inner is None or inner > 1 else 1 + inner
    if data_lifetime(ty, checker):
        return 1
    return None if has_regions(ty, checker) else 0


def slot_names(normalized, written_ty, owner, fresh, checker):
    """Region names of a normalized type's slots, read from the written type."""
    from .call_inference import partial
    from .types import SharedReference, TypeApplication
    normalized = partial(normalized)
    if written_ty == "Self":
        written_ty = owner
    if isinstance(normalized, SharedReference):
        lifetime = written(written_ty.lifetime) if isinstance(written_ty, SharedReference) else None
        referent = written_ty.referent if isinstance(written_ty, SharedReference) else None
        return [lifetime or fresh()] + slot_names(normalized.referent, referent, owner, fresh, checker)
    if data_lifetime(normalized, checker):
        lifetime = None
        if isinstance(written_ty, TypeApplication) and written_ty.lifetimes:
            lifetime = written(written_ty.lifetimes[0])
        return [lifetime or fresh()]
    return []


def signature_regions(fn, checker):
    """(per-parameter slot regions, output slot regions) of fn."""
    params_written, result_written = fn.written_signature or (
        tuple(ty for _, ty, _ in fn.params), fn.result)
    owner = fn.implementation.owner if fn.implementation is not None else None
    counter = iter(range(1 << 30))

    def fresh():
        return f"'_{next(counter)}"

    regions = tuple(tuple(slot_names(ty, written_ty, owner, fresh, checker))
                    for (_, ty, _), written_ty in zip(fn.params, params_written))
    elided = []

    def output():
        elided.append(None)
        return None

    out = slot_names(fn.result, result_written, owner, output, checker)
    if elided:
        from .call_inference import partial
        from .types import SharedReference
        if fn.params and fn.params[0][0].text == "self" and isinstance(partial(fn.params[0][1]), SharedReference):
            source = regions[0][0]  # &self / &mut self: its reference's region
        else:
            # rustc counts input lifetime positions, not distinct names.
            inputs = [region for slots in regions for region in slots]
            if len(inputs) != 1:
                raise fn.token.error("missing lifetime specifier: an elided output needs one input or self "
                                     "lifetime (E0106)")
            source = inputs[0]
        out = [source if region is None else region for region in out]
    return regions, tuple(out)
