"""Signature regions: rustc's lifetime elision for reference parameters and results.

https://doc.rust-lang.org/reference/lifetime-elision.html#lifetime-elision-in-functions
Each top-level reference parameter has a region: its written lifetime, or a
fresh anonymous one when elided. An elided output lifetime takes `self`'s
region for a `&self`/`&mut self` method, else the region of the only input
lifetime position (rustc counts positions, not names); otherwise it is
rustc E0106. These are the function's universal
regions; the move checker requires a returned value's origins to be the
output region (or 'static) and keeps parameter loans live to the exit.
Regions inside aggregates and type arguments are not modeled here.
"""
from __future__ import annotations

STATIC = "'static"


def written(lifetime):
    return lifetime if lifetime not in (None, "'_") else None


def signature_regions(fn):
    """(per-parameter region or None, output region or None) of fn."""
    from .call_inference import partial
    from .types import SharedReference
    regions, anonymous = [], 0
    for _, ty, _ in fn.params:
        ty = partial(ty)
        if isinstance(ty, SharedReference):
            name = written(ty.lifetime)
            if name is None:
                name, anonymous = f"'_{anonymous}", anonymous + 1
            regions.append(name)
        else:
            regions.append(None)
    result = partial(fn.result)
    if not isinstance(result, SharedReference):
        return tuple(regions), None
    out = written(result.lifetime)
    if out is None:
        if fn.params and fn.params[0][0].text == "self" and regions and regions[0] is not None:
            out = regions[0]
        else:
            # rustc counts input lifetime positions, not distinct names.
            inputs = [region for region in regions if region is not None]
            if len(inputs) != 1:
                raise fn.token.error("missing lifetime specifier: an elided output needs one input or self "
                                     "lifetime (E0106)")
            out = inputs[0]
    return tuple(regions), out
