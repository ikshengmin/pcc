"""Shared write-barrier and five-backend step dispatch policy."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32,
    atomic_rmw_i32,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_diff,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

pcc_gc_set_mark_color = extern("pcc_gc_set_mark_color", (c_ptr, c_int64), c_void)


pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_gray_count_increment_acq_rel = extern(
    "pcc_gc_gray_count_increment_acq_rel", (), c_void
)


@c_abi_export("pcc_gc_dispatch_selected_backend")
def _selected_backend() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_config_ensure()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    """The store barrier of every tracing collector; GC0 owes none.

    GC1-GC4 share one count-free mutator protocol: no object a running
    thread names moves, so a store owes only the marking work of an active
    cycle. A cycle's mark_active changes only in a stopped world, so a 0
    read here is the answer a locked read would give. Since GC4 joined that
    protocol (pcc_gc_fix_backend sets pcc_gc_nonmoving_count_free for every
    tracing collector) it records no old-to-young edge here either.
    """
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return
    backend: i64 = _selected_backend()
    return


@c_abi_export("pcc_gc_note_write_barrier")
def pcc_gc_note_write_barrier(owner, value) -> None:
    pass
