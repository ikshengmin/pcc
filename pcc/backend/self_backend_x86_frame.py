"""Initialize only unowned bytes of the fixed x86-64 local frame.

The target-neutral layout leaves alignment gaps between allocated spans and
before its final 16-byte boundary. Conservative snapshots must not retain a
previous activation's pointers through bytes that no current value owns.
Keep every nominal SSA slot, complete addressable alloca and mapped/reload
cell, including slots whose accesses the later register pass removes. This
is not liveness analysis, frame compaction or removal of machine roots.
"""
from __future__ import annotations

from . import BackendUnavailable
from .self_backend_kernel import get_indexed_function_kernel
from .self_backend_ir import ParsedFunction
from .self_backend_precise_stackmaps import (
    FunctionStackMapPlan,
    PlannedManagedReload,
    PlannedRootLocation,
    PlannedSafepoint,
)


def _plain_plan_cells(func, plan) -> set[int] | None:
    """Collect a pure immutable plan's cell union, or retain legacy reads.

    Do not memoize custom records, iterators, roots, or numeric objects:
    their reads can mutate later roots or the function's validation bound.
    """
    if (type(func) is not ParsedFunction or type(func.frame_size) is not int
            or type(plan) is not FunctionStackMapPlan
            or type(plan.records) is not tuple):
        return None
    # A fixed small number of cells per record already has linear storage.
    # Avoid building identity/set tables for that common, inexpensive shape.
    # This bound selects an algorithm; it never omits a cell or validation.
    large_groups = False
    for record in plan.records:
        if type(record) is not PlannedSafepoint:
            return None
        if type(record.locations) is not tuple or type(record.reloads) is not tuple:
            return None
        if len(record.locations) > 8 or len(record.reloads) > 8:
            large_groups = True
            break
    if not large_groups:
        return None
    cells: set[int] = set()
    seen: dict[int, tuple[PlannedRootLocation, ...]] = {}
    for record in plan.records:
        if type(record) is not PlannedSafepoint:
            return None
        locations = record.locations
        reloads = record.reloads
        if type(locations) is not tuple or type(reloads) is not tuple:
            return None
        identity = id(locations)
        if identity not in seen or seen[identity] is not locations:
            for location in locations:
                if type(location) is not PlannedRootLocation:
                    return None
                offset = location.offset
                if type(offset) is not int:
                    return None
                cells.add(offset)
            # Keep the tuple alive for the lifetime of its identity key.
            seen[identity] = locations
        for reload in reloads:
            if type(reload) is not PlannedManagedReload:
                return None
            if (type(reload.source_offset) is not int
                    or type(reload.destination_offset) is not int):
                return None
            cells.add(reload.source_offset)
            cells.add(reload.destination_offset)
    return cells


def fixed_frame_padding(func, plan) -> list[tuple[int, int]]:
    """Return unowned spans as positive distances below the frame pointer.

    A range (low, high) denotes [FP-high, FP-low). Platform saves/scratch,
    incoming arguments and dynamic call/alloca storage are outside the fixed
    local-frame range and never enter this complement.
    """
    kernel = get_indexed_function_kernel(func)
    spans: list[tuple[int, int]] = []

    def keep(offset: int, size: int) -> None:
        if size == 0:
            return
        if size < 0 or offset < size or offset > func.frame_size:
            raise BackendUnavailable("invalid x86 fixed frame span")
        spans.append((offset - size, offset))

    for slot_id in range(len(kernel.slot_scalars) // 2):
        layout = kernel.type_layout(kernel.slot_type_id(slot_id))
        keep(kernel.slot_offset(slot_id), layout.first)
    for value_id in range(len(kernel.value_names)):
        offset = kernel.alloca_offset(value_id)
        if offset >= 0:
            layout = kernel.type_span(kernel.alloca_type_id(value_id))
            keep(offset, layout.third)
    if kernel.hidden_sret_slot_id >= 0:
        keep(kernel.slot_offset(kernel.hidden_sret_slot_id), 8)

    # Mapped and reload cells remain storage without an ordinary SSA access.
    # Keep both representations without projecting the packed plan.
    if plan is not None:
        packed = plan.packed_records
        if packed is not None:
            for index in range(len(packed.location_scalars) // 2):
                location = packed.location_scalars.get2_unchecked(index)
                keep(-location.first, 8)
            for index in range(len(packed.reload_scalars) // 3):
                reload = packed.reload_scalars.get3_unchecked(index)
                keep(-reload.first, 8)
                keep(-reload.second, 8)
        else:
            cells = _plain_plan_cells(func, plan)
            if cells is not None:
                for offset in cells:
                    keep(-offset, 8)
            else:
                for record in plan.records:
                    for location in record.locations:
                        keep(-location.offset, 8)
                    for reload in record.reloads:
                        keep(-reload.source_offset, 8)
                        keep(-reload.destination_offset, 8)

    padding: list[tuple[int, int]] = []
    cursor = 0
    for low, high in sorted(spans):
        if low > cursor:
            padding.append((cursor, low))
        cursor = max(cursor, high)
    if cursor < func.frame_size:
        padding.append((cursor, func.frame_size))
    return padding


def emit_fixed_frame_padding(padding) -> list[str]:
    """Write precisely the gaps without borrowing any incoming register."""
    lines: list[str] = []
    for low, high in padding:
        offset = high
        while offset > low:
            if offset % 8 == 0 and offset - low >= 8:
                width, size = 8, "QWORD"
            elif offset % 4 == 0 and offset - low >= 4:
                width, size = 4, "DWORD"
            elif offset % 2 == 0 and offset - low >= 2:
                width, size = 2, "WORD"
            else:
                width, size = 1, "BYTE"
            lines.append(f"  mov {size} PTR [rbp - {offset}], 0")
            offset -= width
    return lines
