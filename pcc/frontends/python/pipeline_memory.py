"""Opt-in, read-only coordinator allocator counters, not an RSS measurement."""

import os
import sys

from pcc.extern import c_int64, extern
from pcc.unsafe import load_i64, stack_alloc

_live_requested = extern("pcc_allocator_live_requested_bytes", (), c_int64)
_live_usable = extern("pcc_allocator_live_usable_bytes", (), c_int64)
_mapped = extern("pcc_allocator_mapped_bytes", (), c_int64)
_reclaimable_raw = extern("pcc_allocator_reclaimable_slab_bytes", (), c_int64)
_dict_storage_snapshot = extern("pcc_gc_live_dict_storage_snapshot", (c_int64,), c_int64)


def _coordinator_dict_storage_profile() -> None:
    """One cold, opt-in, stopped-world snapshot at manifests-ready.

    Bins are capacity:dicts:raw_blocks:requested:usable (bytes), excluding
    constructing or terminal dictionaries. No claim about mapped or RSS.
    """
    prefix = "pcc coordinator dict storage phase=manifests-ready"
    try:
        # Application stack_alloc returns an integer address, not a managed
        # pointer; the native snapshot receives it through c_int64.
        out = stack_alloc(1792)
        status = _dict_storage_snapshot(out)
        if status == 0:
            bins = ""
            capacity = 4
            lane = 0
            while lane < 56:
                offset = lane * 32
                count = load_i64(out, offset)
                if count != 0:
                    if bins != "":
                        bins = bins + ","
                    bins = bins + (str(capacity) + ":" + str(count)
                                   + ":" + str(load_i64(out, offset + 8))
                                   + ":" + str(load_i64(out, offset + 16))
                                   + ":" + str(load_i64(out, offset + 24)))
                capacity = capacity * 2
                lane = lane + 1
            print(prefix + " status=ok bins=" + bins, file=sys.stderr, flush=True)
            return
    except Exception:
        # As with the existing memory counters, never turn a diagnostic
        # allocation or unavailable native ABI into a compilation failure.
        pass
    print(prefix + " status=unavailable", file=sys.stderr, flush=True)


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
        if phase == "manifests-ready":
            _coordinator_dict_storage_profile()
    except Exception:
        # Ordinary diagnostic failures are nonfatal; cancellation still propagates.
        pass
