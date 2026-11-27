from __future__ import annotations

"""Physical x64 MIR frame compaction after the one virtual allocation.

Root/reload cells, complete addressable objects and all actual spilled
machine homes are physical. Register-only values and proven ephemeral CAS
pairs retain nominal IDs, never physical accesses. A rebuilt stack map must
equal the old records under the exact offset remap before emission.
"""
from dataclasses import replace

from . import BackendUnavailable
from .self_backend_ir import _align_to
from .self_backend_kernel import get_indexed_function_kernel
from .self_backend_x86_mir import X86MIRValue,X86MIRFunction,X86MIRAllocation
from .self_backend_precise_stackmaps import (
    FunctionStackMapPlan,PlannedSafepoint,PlannedRootLocation,PlannedManagedReload,
)


class X86MIRFrame:
    __slots__=("original_size","original_slots","original_allocas","remaps","padding","new_size","moved")

    def __init__(self,original_size:int,original_slots:list[int],
                 original_allocas:list[tuple[int,int]],remaps:list[tuple[int,int,int]],
                 padding:list[tuple[int,int]],new_size:int,moved:bool)->None:
        self.original_size=original_size
        self.original_slots=original_slots
        self.original_allocas=original_allocas
        self.remaps=remaps
        self.padding=padding
        self.new_size=new_size
        self.moved=moved

    def remap(self,offset:int)->int:
        low_index=0
        high_index=len(self.remaps)
        while low_index<high_index:
            middle=(low_index+high_index)//2
            if self.remaps[middle][1]<offset:low_index=middle+1
            else:high_index=middle
        if low_index<len(self.remaps):
            low,high,shift=self.remaps[low_index]
            if low<offset<=high:return offset-shift
        return offset

    def restore(self,func)->None:
        from .self_backend_aarch64_frame import _refresh_legacy_slots
        kernel=get_indexed_function_kernel(func)
        for slot_id,offset in enumerate(self.original_slots):
            kernel.slot_scalars.set_unchecked(slot_id*2,offset)
        for value_id,offset in self.original_allocas:
            kernel.value_scalars.set_unchecked(value_id*8+3,offset)
        func.frame_size=self.original_size
        _refresh_legacy_slots(func,kernel)


_ORIGINAL_X86_MIR_REMAP = X86MIRFrame.remap


def plan_x86_mir_frame(func,plan,mir:X86MIRFunction,allocation:X86MIRAllocation,*,retain_original=False)->X86MIRFrame:
    kernel=get_indexed_function_kernel(func)
    old_slots=[kernel.slot_offset(index) for index in range(len(kernel.slot_scalars)//2)] if retain_original else []
    old_allocas=[]
    spans=[]
    ephemeral=set(mir.cas_pairs)
    def keep(offset,size,alignment):
        if size==0:return
        if size<0 or offset<size or offset>func.frame_size or alignment<=0:
            raise BackendUnavailable("invalid typed x64 MIR physical frame span")
        spans.append((offset-size,offset,alignment))
    for value in mir.values:
        value_id=value.value_id
        offset=kernel.alloca_offset(value_id)
        if offset>=0:
            if retain_original:old_allocas.append((value_id,offset))
            alloca_layout=kernel.type_span(kernel.alloca_type_id(value_id))
            keep(offset,alloca_layout.third,max(alloca_layout.fourth,8))
        slot_id=kernel.value_slot_id(value_id)
        if (slot_id>=0 and allocation.registers[value_id]<0
                and value_id not in ephemeral):
            slot_layout=kernel.type_layout(kernel.slot_type_id(slot_id))
            keep(value.home,slot_layout.first,slot_layout.second)
    if kernel.hidden_sret_slot_id>=0:
        raise BackendUnavailable("typed x64 MIR unexpected hidden sret")
    for offset in mir.root_offsets:
        keep(offset,8,8)

    # Merge overlapping physical spans; independently pack disjoint groups
    # preserving each group's relative bytes and strongest alignment residue.
    spans.sort()
    groups=[]
    for low,high,alignment in spans:
        if groups and low<groups[-1][1]:
            previous=groups[-1]
            groups[-1]=(previous[0],max(high,previous[1]),max(alignment,previous[2]))
        else:groups.append((low,high,alignment))
    remaps=[];padding=[];cursor=0
    for low,high,alignment in groups:
        width=high-low;residue=high%alignment
        new_high=_align_to(cursor+width-residue,alignment)+residue
        new_low=new_high-width
        if new_low>cursor:padding.append((cursor,new_low))
        remaps.append((low,high,high-new_high))
        cursor=new_high
    new_size=_align_to(cursor,16)
    if new_size>cursor:padding.append((cursor,new_size))
    layout=X86MIRFrame(func.frame_size,old_slots,old_allocas,remaps,padding,new_size,new_size!=func.frame_size)
    if not layout.moved:
        layout.moved=any(shift!=0 for _,_,shift in remaps)
    return layout


def compact_x86_mir_frame(func,plan,mir:X86MIRFunction,allocation:X86MIRAllocation)->X86MIRFrame:
    """Mutating oracle/packed fallback, not the ordinary plain MIR route."""
    from .self_backend_aarch64_frame import _refresh_legacy_slots
    layout=plan_x86_mir_frame(func,plan,mir,allocation,retain_original=True)
    kernel=get_indexed_function_kernel(func)
    for slot_id,old in enumerate(layout.original_slots):
        kernel.slot_scalars.set_unchecked(slot_id*2,layout.remap(old))
    for value_id,old in layout.original_allocas:
        kernel.value_scalars.set_unchecked(value_id*8+3,layout.remap(old))
    func.frame_size=layout.new_size
    _refresh_legacy_slots(func,kernel)
    return layout


def remapped_mir_values(mir:X86MIRFunction,layout:X86MIRFrame,allocation:X86MIRAllocation|None=None)->X86MIRFunction:
    ephemeral=set(mir.cas_pairs)
    values=[]
    for value in mir.values:
        if value.value_id in ephemeral:
            home=-1
        elif allocation is not None and allocation.registers[value.value_id]>=0:
            # A virtual register never observes its nominal old home.
            values.append(value)
            continue
        else:home=layout.remap(value.home)
        values.append(value if home==value.home else X86MIRValue(
            value.value_id,value.type_id,value.width,value.pointer,home,value.addressable,value.used))
    return X86MIRFunction(tuple(values),mir.blocks,mir.arguments,mir.edge_uses,mir.edge_copies,
                          mir.pinned,mir.cas_extracts,mir.cas_pairs,mir.root_offsets)


def _remap_location_group(layout, original, group_memo, element_memo, memoize):
    group_id = id(original) if memoize else 0
    if memoize and group_id in group_memo:
        group_entry = group_memo[group_id]
        if group_entry[0] is original:
            return group_entry[1], group_entry[2]
    locations = []
    changed = False
    for location in original:
        if type(location) is not PlannedRootLocation or type(location.offset) is not int:
            return None
        item_id = id(location) if memoize else 0
        mapped = None
        if memoize and item_id in element_memo:
            item_entry = element_memo[item_id]
            if item_entry[0] is location:
                mapped = item_entry[1]
        if mapped is None:
            offset = -layout.remap(-location.offset)
            if not -layout.new_size <= offset <= -8:
                return None
            mapped = location if offset == location.offset else PlannedRootLocation(offset, location.owned)
            if memoize:
                element_memo[item_id] = (location, mapped)
        if mapped is not location:
            changed = True
        locations.append(mapped)
    # Exact private plans already intern location tuples. Keep their sharing;
    # a custom/observed layout retains the original uncached tuple behavior.
    result = original if memoize and not changed else tuple(locations)
    if memoize:
        group_memo[group_id] = (original, result, changed)
    return result, changed


def _remap_reload_group(layout, original, group_memo, element_memo, memoize):
    group_id = id(original) if memoize else 0
    if memoize and group_id in group_memo:
        group_entry = group_memo[group_id]
        if group_entry[0] is original:
            return group_entry[1], group_entry[2]
    reloads = []
    changed = False
    for reload in original:
        if (type(reload) is not PlannedManagedReload or type(reload.source_offset) is not int
                or type(reload.destination_offset) is not int):
            return None
        item_id = id(reload) if memoize else 0
        mapped = None
        if memoize and item_id in element_memo:
            item_entry = element_memo[item_id]
            if item_entry[0] is reload:
                mapped = item_entry[1]
        if mapped is None:
            source = -layout.remap(-reload.source_offset)
            destination = -layout.remap(-reload.destination_offset)
            if not (-layout.new_size <= source <= -8 and -layout.new_size <= destination <= -8):
                return None
            if source == reload.source_offset and destination == reload.destination_offset:
                mapped = reload
            else:
                mapped = PlannedManagedReload(source, destination, reload.derived_offset)
            if memoize:
                element_memo[item_id] = (reload, mapped)
        if mapped is not reload:
            changed = True
        reloads.append(mapped)
    result = original if memoize and not changed else tuple(reloads)
    if memoize:
        group_memo[group_id] = (original, result, changed)
    return result, changed


def remap_plain_stack_map(plan:FunctionStackMapPlan,layout:X86MIRFrame)->FunctionStackMapPlan|None:
    """Immutable exact x64 plan remap; no frontend/root-state recomputation.

    The same layout preserves all complete root/reload spans. Semantic IDs,
    ownership, exception/liveness routes, labels and derived offsets are
    unchanged. Custom/packed records retain the checked rebuild oracle.

    The exact planner shares immutable groups and elements across safepoints.
    Remap each identity once under this private pure layout, retaining the
    source identity alongside the answer (no address-reuse or equal-value
    conflation). Never cache across functions or for an observed layout.
    """
    if (type(plan) is not FunctionStackMapPlan or plan.packed_records is not None
            or type(plan.records) is not tuple):
        return None
    if not layout.moved:return plan
    memoize = (type(layout) is X86MIRFrame
               and getattr(X86MIRFrame, "remap", None) is _ORIGINAL_X86_MIR_REMAP)
    location_groups: dict[int, tuple] = {}
    location_elements: dict[int, tuple] = {}
    reload_groups: dict[int, tuple] = {}
    reload_elements: dict[int, tuple] = {}
    records=[]
    for record in plan.records:
        if (type(record) is not PlannedSafepoint or type(record.locations) is not tuple
                or type(record.reloads) is not tuple):return None
        location_result = _remap_location_group(layout, record.locations, location_groups, location_elements, memoize)
        if location_result is None:return None
        reload_result = _remap_reload_group(layout, record.reloads, reload_groups, reload_elements, memoize)
        if reload_result is None:return None
        locations, locations_changed = location_result
        reloads, reloads_changed = reload_result
        records.append(replace(record, locations=locations, reloads=reloads)
                       if locations_changed or reloads_changed else record)
    frame_size=plan.frame_size-layout.original_size+layout.new_size
    if frame_size<0:return None
    return replace(plan,frame_size=frame_size,records=tuple(records))


def stack_map_remap_is_exact(old,new,layout:X86MIRFrame)->bool:
    if (old.function_name!=new.function_name or old.function_id!=new.function_id
            or old.end_label!=new.end_label or old.target!=new.target
            or old.block_entry_labels!=new.block_entry_labels
            or old.instruction_suffix_labels!=new.instruction_suffix_labels
            or old.terminator_prefix_labels!=new.terminator_prefix_labels
            or old.frame_size-new.frame_size!=layout.original_size-layout.new_size):
        return False
    records=old.diagnostic_records();other=new.diagnostic_records()
    if len(records)!=len(other):return False
    for a,b in zip(records,other,strict=True):
        if (a.safepoint_id!=b.safepoint_id or a.label!=b.label or a.kind!=b.kind
                or a.flags!=b.flags or a.exceptional_block!=b.exceptional_block
                or a.continuation_id!=b.continuation_id):
            return False
        expected=tuple((-layout.remap(-x.offset),x.owned) for x in a.locations)
        actual=tuple((x.offset,x.owned) for x in b.locations)
        if expected!=actual:return False
        expected_reload=tuple((-layout.remap(-x.source_offset),-layout.remap(-x.destination_offset),x.derived_offset) for x in a.reloads)
        actual_reload=tuple((x.source_offset,x.destination_offset,x.derived_offset) for x in b.reloads)
        if expected_reload!=actual_reload:return False
    return True
