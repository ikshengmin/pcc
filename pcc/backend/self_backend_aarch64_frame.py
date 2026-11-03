"""Finalize physical AArch64 slots after register allocation.

The target-neutral slots are candidates, not all physical storage. Keep the
whole addressable objects and every root/reload cell, then pack the spans still
used by the final scalar emitter. Slot identities and types do not change.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import BackendUnavailable
from .self_backend_aarch64_darwin_regalloc import _value_needs_slot
from .self_backend_ir import (
    AllocaInfo,
    SlotInfo,
    _align_to,
    parsed_function_publish_alloca_slot,
    parsed_function_publish_value_slot,
)
from .self_backend_kernel import get_indexed_function_kernel


@dataclass
class PhysicalFrameLayout:
    original_size: int
    original_slots: list[int]
    original_allocas: list[tuple[int, int]]
    original_reloads: list[int]
    padding: list[tuple[int, int]]
    moved: bool

    def restore(self, func) -> None:
        """A reused prepared module retains its target-neutral input layout."""
        kernel = get_indexed_function_kernel(func)
        for slot_id, offset in enumerate(self.original_slots):
            kernel.slot_scalars.set_unchecked(slot_id * 2, offset)
        for value_id, offset in self.original_allocas:
            kernel.value_scalars.set_unchecked(value_id * 8 + 3, offset)
        func.frame_size = self.original_size
        func.aarch64_reload_slot_offsets = self.original_reloads
        _refresh_legacy_slots(func, kernel)


def _refresh_legacy_slots(func, kernel) -> None:
    if func.indexed_slot_projection:
        return
    for value_id, name in enumerate(kernel.value_names):
        slot_id = kernel.value_slot_id(value_id)
        if slot_id >= 0:
            parsed_function_publish_value_slot(func, name, SlotInfo(
                kernel.slot_offset(slot_id),
                kernel.type_desc(kernel.slot_type_id(slot_id)),
            ))
        offset = kernel.alloca_offset(value_id)
        if offset >= 0:
            parsed_function_publish_alloca_slot(func, name, AllocaInfo(
                offset, kernel.type_desc(kernel.alloca_type_id(value_id)),
            ))
    if kernel.hidden_sret_slot_id >= 0:
        slot_id = kernel.hidden_sret_slot_id
        func.hidden_sret_slot = SlotInfo(
            kernel.slot_offset(slot_id), kernel.type_desc(kernel.slot_type_id(slot_id)),
        )


def compact_aarch64_frame(func, plan) -> PhysicalFrameLayout:
    """Remap the final physical layout, retaining unmaterialized slot IDs.

    Unused scalar slots keep their nominal offsets just as in the existing
    frameless proof: no emitted access may use them. Any shared slot needed by
    one value remains physical for all aliases. The caller must rebuild the
    stackmap plan after this function and must not rerun register allocation.
    """
    kernel = get_indexed_function_kernel(func)
    slot_count = len(kernel.slot_scalars) // 2
    old_slots = [kernel.slot_offset(index) for index in range(slot_count)]
    old_allocas = []
    spans: list[tuple[int, int, int]] = []

    def keep(offset: int, size: int, alignment: int) -> None:
        if size == 0:
            return
        if offset < size or offset > func.frame_size or alignment <= 0:
            raise BackendUnavailable("invalid physical frame span")
        spans.append((offset - size, offset, alignment))

    for value_id in range(len(kernel.value_names)):
        offset = kernel.alloca_offset(value_id)
        if offset >= 0:
            old_allocas.append((value_id, offset))
            layout = kernel.type_span(kernel.alloca_type_id(value_id))
            keep(offset, layout.third, max(layout.fourth, 8))
        slot_id = kernel.value_slot_id(value_id)
        if slot_id >= 0 and _value_needs_slot(func, kernel, value_id):
            layout = kernel.type_layout(kernel.slot_type_id(slot_id))
            keep(old_slots[slot_id], layout.first, layout.second)
    if kernel.hidden_sret_slot_id >= 0:
        keep(old_slots[kernel.hidden_sret_slot_id], 8, 8)

    packed = plan.packed_records
    if packed is not None:
        for index in range(len(packed.location_scalars) // 2):
            location = packed.location_scalars.get2_unchecked(index)
            keep(-location.first, 8, 8)
        for index in range(len(packed.reload_scalars) // 3):
            reload = packed.reload_scalars.get3_unchecked(index)
            keep(-reload.first, 8, 8)
            keep(-reload.second, 8, 8)
    else:
        for record in plan.records:
            for location in record.locations:
                keep(-location.offset, 8, 8)
            for reload in record.reloads:
                keep(-reload.source_offset, 8, 8)
                keep(-reload.destination_offset, 8, 8)

    # Positive distances from FP: [low, high) is one physical storage span.
    # Merge overlaps, not adjacency, so independent objects can be repacked.
    spans.sort()
    groups: list[tuple[int, int, int]] = []
    for low, high, alignment in spans:
        if groups and low < groups[-1][1]:
            previous = groups[-1]
            groups[-1] = (previous[0], max(high, previous[1]),
                          max(alignment, previous[2]))
        else:
            groups.append((low, high, alignment))
    remaps: list[tuple[int, int, int]] = []
    padding: list[tuple[int, int]] = []
    cursor = 0
    for low, high, alignment in groups:
        width = high - low
        residue = high % alignment
        new_high = _align_to(cursor + width - residue, alignment) + residue
        new_low = new_high - width
        if new_low > cursor:
            padding.append((cursor, new_low))
        remaps.append((low, high, high - new_high))
        cursor = new_high
    new_size = _align_to(cursor, 16)
    if new_size > cursor:
        padding.append((cursor, new_size))

    def remap(offset: int) -> int:
        low_index = 0
        high_index = len(remaps)
        while low_index < high_index:
            middle = (low_index + high_index) // 2
            if remaps[middle][1] < offset:
                low_index = middle + 1
            else:
                high_index = middle
        if low_index < len(remaps):
            low, high, shift = remaps[low_index]
            if low < offset <= high:
                return offset - shift
        return offset

    result = PhysicalFrameLayout(
        func.frame_size, old_slots, old_allocas,
        list(func.aarch64_reload_slot_offsets), padding, new_size != func.frame_size,
    )
    for slot_id, offset in enumerate(old_slots):
        new_offset = remap(offset)
        kernel.slot_scalars.set_unchecked(slot_id * 2, new_offset)
        result.moved = result.moved or new_offset != offset
    for value_id, offset in old_allocas:
        new_offset = remap(offset)
        kernel.value_scalars.set_unchecked(value_id * 8 + 3, new_offset)
        result.moved = result.moved or new_offset != offset
    func.frame_size = new_size
    _refresh_legacy_slots(func, kernel)
    return result
