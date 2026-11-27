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
_managed_size_snapshot = extern("pcc_gc_live_managed_size_snapshot", (c_int64, c_int64), c_int64)


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


def _coordinator_managed_size_profile() -> None:
    """Cold allocator LIVE object-slab sizes at manifests-ready.

    Bins: rounded request/class upper bound:count:requested:usable:
    minimum hypothetical 16-byte-aligned payload bytes. Excludes raw/large
    mappings, reserved/free cells and slab metadata; not RSS. Independent
    snapshot from the dictionary table receipt immediately before this.
    """
    prefix = "pcc coordinator managed slab sizes phase=manifests-ready"
    try:
        out = stack_alloc(1184)
        status = _managed_size_snapshot(out, 1184)
        if status == 0:
            bins = ""
            upper = 16
            lane = 0
            while lane < 37:
                offset = lane * 32
                count = load_i64(out, offset)
                if count != 0:
                    if bins != "":
                        bins = bins + ","
                    bins = bins + (str(upper) + ":" + str(count)
                                   + ":" + str(load_i64(out, offset + 8))
                                   + ":" + str(load_i64(out, offset + 16))
                                   + ":" + str(load_i64(out, offset + 24)))
                if lane < 31:
                    upper = upper + 16
                elif lane == 31:
                    upper = 1024
                else:
                    upper = upper * 2
                lane = lane + 1
            print(prefix + " status=ok bins=" + bins, file=sys.stderr, flush=True)
            return
    except Exception:
        # Diagnostic failure must not become compilation failure.
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
            _coordinator_managed_size_profile()
    except Exception:
        # Ordinary diagnostic failures are nonfatal; cancellation still propagates.
        pass


# Cold isolated-worker telemetry. These are the stable scalar runtime getters;
# no audit, logging, collector configuration, heap walk or reset is enabled.
_worker_gc_metric = extern("pcc_gc_telemetry", (c_int64,), c_int64)
_WORKER_GC_COUNTER_IDS = (5, 32, 33, 119, 120, 0, 1, 2, 3, 6, 7, 121)
# Allocations and stores (IDs 0/1) are sums of i64 metric shards;
# only load barriers/safepoints (IDs 2/3) use signed i32 storage.
_WORKER_GC_WIDE_COUNT = 7
_WORKER_GC_DELTA_NAMES = (
    "work_steps", "recorded_pause_count", "recorded_pause_us",
    "stw_count", "stw_sum_us", "allocations", "write_barriers",
    "read_barriers", "safepoints",
)
_WORKER_GC_GAUGE_NAMES = (
    "debt_bytes_end", "max_recorded_pause_us_since_reset_end",
    "max_stw_us_since_reset_end",
)
_WORKER_GC_PHASES = ("ast", "infer", "frontend_generate")


def _worker_gc_emit(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class WorkerGcPhaseTiming:
    """One opt-in actual worker phase at a time, never per emitted operation.

    Native getters publish caller-local batched counters. Their reads and the
    diagnostic's own Python allocations are inclusive and non-atomic. A
    nondecreasing 32-bit event counter cannot exclude a full wrap. Recorded
    pauses/STW are not all mutator GC/RC cost. Host GC is not a substitute.
    """

    def __init__(self):
        self.active_index = -1
        self.active_phase = ""
        self.start_values = []

    def _snapshot(self):
        if sys.implementation.name != "pcc":
            return []
        values = []
        for metric in _WORKER_GC_COUNTER_IDS:
            values.append(_worker_gc_metric(metric))
        return values

    def begin(self, index: int, phase: str) -> None:
        try:
            self.active_index = -1
            self.active_phase = ""
            self.start_values = []
            if index < 0 or phase not in _WORKER_GC_PHASES:
                return
            self.active_index = index
            self.active_phase = phase
            self.start_values = self._snapshot()
        except Exception:
            # Optional phase diagnostics must not alter worker outcomes.
            pass

    def complete(self, index: int, phase: str) -> None:
        try:
            matched = self.active_index == index and self.active_phase == phase
            start = self.start_values
            self.active_index = -1
            self.active_phase = ""
            self.start_values = []
            if index < 0 or phase not in _WORKER_GC_PHASES:
                return
            prefix = ("pcc worker gc phase pid=" + str(os.getpid())
                      + " index=" + str(index) + " phase=" + phase)
            line = prefix + " gc_available=0"
            if matched and len(start) == len(_WORKER_GC_COUNTER_IDS):
                try:
                    end = self._snapshot()
                    if len(end) == len(_WORKER_GC_COUNTER_IDS):
                        line = self._format_delta(prefix, start, end)
                except Exception:
                    pass
            # One best-effort write: a partially failed sink must not be
            # retried or mistaken for a valid completed diagnostic row.
            _worker_gc_emit(line)
        except Exception:
            pass

    def _format_delta(self, prefix: str, start, end) -> str:
        wide_valid = True
        event_valid = True
        for i in range(len(_WORKER_GC_DELTA_NAMES)):
            valid = start[i] >= 0 and end[i] >= start[i]
            if i < _WORKER_GC_WIDE_COUNT:
                wide_valid = wide_valid and valid
            else:
                event_valid = event_valid and valid and end[i] <= 2147483647
        parts = [
            prefix + " gc_available=1 gc_backend=0",
            "gc_scope=process gc_snapshot=non_atomic",
            "gc_recorded_work=inclusive gc_snapshot_overhead=inclusive",
            "gc_wide_counter_bits=64 gc_event_counter_bits=32",
            "gc_event_full_wrap_excluded=0",
            "gc_wide_delta_ok=" + str(int(wide_valid)),
            "gc_event_delta_ok=" + str(int(event_valid)),
        ]
        for i in range(len(_WORKER_GC_DELTA_NAMES)):
            if ((i < _WORKER_GC_WIDE_COUNT and wide_valid)
                    or (i >= _WORKER_GC_WIDE_COUNT and event_valid)):
                parts.append("gc_" + _WORKER_GC_DELTA_NAMES[i] + "_delta="
                             + str(end[i] - start[i]))
        for i in range(len(_WORKER_GC_GAUGE_NAMES)):
            value = end[len(_WORKER_GC_DELTA_NAMES) + i]
            if value >= 0:
                parts.append("gc_" + _WORKER_GC_GAUGE_NAMES[i] + "=" + str(value))
        return " ".join(parts)
