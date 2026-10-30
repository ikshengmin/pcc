"""Executable historical reference and held unconditional root-union experiment.

The reference is from frame-registers blob e63322c5f37b497a98a59c386ce402a8949f3369.
The held helper is from source SHA256 2d3114e1f776670b686cc24d27d5a2703757f8be622ea5c6f4ef36bb8a638aef.
Only function names and the relative import spelling are adapted for this package.
Neither helper is installed into production by importing this module.
"""
from __future__ import annotations

def reference_slot_offsets(plan) -> dict[int, bool]:
    """Frame offsets the collector reads or a reload copies from."""
    offsets: dict[int, bool] = {}
    if plan is None:
        return offsets
    packed = plan.packed_records
    # Frame offsets are negative: [rbp - N] is offset -N.
    if packed is not None:
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
    for record in plan.records:
        for location in record.locations:
            offsets[-location.offset] = True
        for reload in record.reloads:
            offsets[-reload.source_offset] = True
    return offsets


def _ordinary_stack_map_slot_offsets(records) -> dict[int, bool] | None:
    """Union shared immutable location groups once, or decline the fast path.

    A declined prefix has read only ordinary frozen fields and integer
    offsets: the caller can repeat the original loop without replaying user
    callbacks. Keep the tuple itself, not just its address, in every cache
    entry. A moving collector or an address collision can only cause a miss;
    identity must match before any location reads are skipped.
    """
    from pcc.backend.self_backend_precise_stackmaps import (
        PlannedManagedReload,
        PlannedRootLocation,
        PlannedSafepoint,
    )

    offsets: dict[int, bool] = {}
    seen_locations: dict[int, tuple] = {}
    for record in records:
        if type(record) is not PlannedSafepoint:
            return None
        locations = record.locations
        if type(locations) is not tuple:
            return None
        if locations:
            identity = id(locations)
            previous = None
            if identity in seen_locations:
                previous = seen_locations[identity]
            if previous is not locations:
                for location in locations:
                    if (type(location) is not PlannedRootLocation
                            or type(location.offset) is not int):
                        return None
                    offsets[-location.offset] = True
                seen_locations[identity] = locations
        reloads = record.reloads
        if type(reloads) is not tuple:
            return None
        for reload in reloads:
            if (type(reload) is not PlannedManagedReload
                    or type(reload.source_offset) is not int):
                return None
            offsets[-reload.source_offset] = True
    return offsets


def held_slot_offsets(plan) -> dict[int, bool]:
    """Frame offsets the collector reads or a reload copies from."""
    offsets: dict[int, bool] = {}
    if plan is None:
        return offsets
    packed = plan.packed_records
    # Frame offsets are negative: [rbp - N] is offset -N.
    if packed is not None:
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
    if type(records) is tuple and len(records) > 1:
        ordinary = _ordinary_stack_map_slot_offsets(records)
        if ordinary is not None:
            return ordinary
    for record in records:
        for location in record.locations:
            offsets[-location.offset] = True
        for reload in record.reloads:
            offsets[-reload.source_offset] = True
    return offsets
