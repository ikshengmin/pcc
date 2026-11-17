"""Opt-in, read-only coordinator allocator counters, not an RSS measurement."""

import os
import sys

from pcc.extern import c_int64, extern

_live_requested = extern("pcc_allocator_live_requested_bytes", (), c_int64)
_live_usable = extern("pcc_allocator_live_usable_bytes", (), c_int64)
_mapped = extern("pcc_allocator_mapped_bytes", (), c_int64)
_reclaimable_raw = extern("pcc_allocator_reclaimable_slab_bytes", (), c_int64)


def coordinator_memory_phase(phase: str) -> None:
    """Never collect, trim, expose addresses, or replace compilation errors.

    Counts are process-local relaxed snapshots, not a transaction across
    threads. Reclaimable bytes cover fully-free raw slabs only, not objects.
    CPython has no equivalent native allocator and reports unavailable.
    """
    try:
        if os.environ.get("PCC_PROFILE_COORDINATOR_MEMORY", "") != "1":
            return
        if phase not in (
            "closure-before", "closure-single-returned", "closure-single-admitted",
            "closure-multi-returned", "init-order-returned",
            "exports-before", "exports-returned", "manifests-ready",
        ):
            return
        try:
            requested = _live_requested()
            usable = _live_usable()
            mapped = _mapped()
            reclaimable = _reclaimable_raw()
        except NotImplementedError:
            print("pcc coordinator memory phase=" + phase + " status=unavailable",
                  file=sys.stderr, flush=True)
            return
        print("pcc coordinator memory phase=" + phase
              + " live_requested_bytes=" + str(requested)
              + " live_usable_bytes=" + str(usable)
              + " mapped_bytes=" + str(mapped)
              + " reclaimable_raw_slab_bytes=" + str(reclaimable),
              file=sys.stderr, flush=True)
    except Exception:
        # Ordinary diagnostic failures are nonfatal; cancellation still propagates.
        pass
