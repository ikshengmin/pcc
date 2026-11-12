"""Backend 3 remembered-owner queue and budgeted overflow scanning."""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_store_i64,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    malloc,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


@c_abi_export("pcc_gc_backend3_remembered_owner_list_head")
def pcc_gc_backend3_remembered_owner_list_head() -> c_ptr:
    return global_load_ptr("pcc_gc_backend3_remembered_owner_head")


@c_abi_export("pcc_gc_backend3_remembered_owner_list_set_head")
def pcc_gc_backend3_remembered_owner_list_set_head(head: c_ptr) -> None:
    global_store_ptr("pcc_gc_backend3_remembered_owner_head", head)


@c_abi_export("pcc_gc_backend3_remember_owner")
def pcc_gc_backend3_remember_owner(owner: c_ptr, owner_flags: i64) -> None:
    if ptr_is_null(owner) != 0:
        return
    if is_tagged_int(owner) != 0:
        return
    if (owner_flags & 512) != 0:
        return
    allocation_limit: i64 = atomic_load_i64(
        global_addr("pcc_gc_backend3_remembered_owner_allocation_limit"),
        0,
        "acquire",
    )
    node = null()
    if allocation_limit != 0:
        node = malloc(16)
        if ptr_is_null(node) == 0 and allocation_limit > 0:
            atomic_store_i64(
                global_addr(
                    "pcc_gc_backend3_remembered_owner_allocation_limit"
                ),
                0,
                allocation_limit - 1,
                "release",
            )
    if ptr_is_null(node) != 0:
        store_i32(global_addr("pcc_gc_backend3_remembered_overflow"), 0, 1)
        global_store_ptr("pcc_gc_backend3_remembered_scan_cursor", null())
        store_i64(
            global_addr("pcc_gc_backend3_remembered_scan_revision"), 0, 0
        )
        atomic_rmw_i32("or", owner, 12, 512, "acq_rel")
        return
    store_ptr(node, 0, owner)
    store_ptr(node, 8, pcc_gc_backend3_remembered_owner_list_head())
    pcc_gc_backend3_remembered_owner_list_set_head(node)
    atomic_rmw_i32("or", owner, 12, 512, "acq_rel")
