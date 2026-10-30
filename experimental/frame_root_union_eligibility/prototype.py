"""Research-only preflight; no production dispatcher or global patching.

This is executable source, not a performance-qualified replacement. A caller
must state a policy. No policy threshold is a production default or inferred
from the held experiment's timings.
"""
from __future__ import annotations

from dataclasses import dataclass

from pcc.backend.self_backend_precise_stackmaps import (
    PlannedManagedReload,
    PlannedRootLocation,
    PlannedSafepoint,
)


@dataclass(frozen=True)
class Sharing:
    first_use: tuple[bool, ...]
    records: int
    location_items: int
    unique_location_items: int
    unique_nonempty_groups: int


def scan_location_sharing(records) -> Sharing | None:
    """Read exact record/tuple metadata only, never root/reload properties.

    O(records) work, O(nonempty groups + records) auxiliary space. Strong tuple
    references plus `is` prevent a stale/reused/moved address from being a false
    hit. A moving id can only increase the unique-item estimate, conservatively
    decreasing the estimated savings. Empty tuples need no identity cache.
    """
    if type(records) is not tuple or len(records) < 2:
        return None
    seen: dict[int, tuple] = {}
    first_use: list[bool] = []
    location_items = 0
    unique_location_items = 0
    groups = 0
    for record in records:
        if type(record) is not PlannedSafepoint:
            return None
        locations = record.locations
        if type(locations) is not tuple:
            return None
        count = len(locations)
        location_items += count
        first = False
        if count:
            identity = id(locations)
            previous = None
            if identity in seen:
                previous = seen[identity]
            if previous is not locations:
                first = True
                seen[identity] = locations
                unique_location_items += count
                groups += 1
        first_use.append(first)
    return Sharing(tuple(first_use), len(records), location_items,
                   unique_location_items, groups)


def _original_records(records) -> dict[int, bool]:
    offsets: dict[int, bool] = {}
    for record in records:
        for location in record.locations:
            offsets[-location.offset] = True
        for reload in record.reloads:
            offsets[-reload.source_offset] = True
    return offsets


def _validated_union(records, first_use: tuple[bool, ...]) -> dict[int, bool] | None:
    """Reuse preflight identity decisions, preserving first-insertion order.

    Every skipped group was validated at its first appearance. Any opaque root,
    reload, or offset declines before invoking its callback. The already-read
    prefix then contains only ordinary frozen fields and builtin int operations,
    so replaying the original loop has not replayed user code.
    """
    offsets: dict[int, bool] = {}
    index = 0
    for record in records:
        if first_use[index]:
            for location in record.locations:
                if (type(location) is not PlannedRootLocation
                        or type(location.offset) is not int):
                    return None
                offsets[-location.offset] = True
        reloads = record.reloads
        if type(reloads) is not tuple:
            return None
        for reload in reloads:
            if (type(reload) is not PlannedManagedReload
                    or type(reload.source_offset) is not int):
                return None
            offsets[-reload.source_offset] = True
        index += 1
    return offsets


def slot_offsets(plan, *, minimum_saved_items: int,
                 minimum_saved_items_per_record: int) -> dict[int, bool]:
    """Experimental dispatch with explicit, unqualified structural policy.

    A policy can reject a plan without inspecting individual roots. It does not
    eliminate preflight cost on rootless or unique plans, and the metadata/type
    scan itself has no measured break-even. Imports occur when this experimental
    module loads, not once per call; this differs intentionally from held v1.
    """
    if (type(minimum_saved_items) is not int or minimum_saved_items < 0
            or type(minimum_saved_items_per_record) is not int
            or minimum_saved_items_per_record < 0):
        raise ValueError("sharing policy must contain nonnegative ordinary ints")
    offsets: dict[int, bool] = {}
    if plan is None:
        return offsets
    packed = plan.packed_records
    if packed is not None:
        # Identical packed traversal; do not re-read the plan's getter.
        locations = packed.location_scalars
        index = 0
        count = len(locations)
        while index < count:
            offsets[-locations.get_unchecked(index)] = True
            index += 2
        reloads = packed.reload_scalars
        index = 0
        count = len(reloads)
        while index < count:
            offsets[-reloads.get_unchecked(index)] = True
            index += 3
        return offsets
    records = plan.records
    sharing = scan_location_sharing(records)
    if sharing is not None:
        saved = sharing.location_items - sharing.unique_location_items
        if (saved > 0 and saved >= minimum_saved_items
                and saved >= minimum_saved_items_per_record * sharing.records):
            ordinary = _validated_union(records, sharing.first_use)
            if ordinary is not None:
                return ordinary
    return _original_records(records)
