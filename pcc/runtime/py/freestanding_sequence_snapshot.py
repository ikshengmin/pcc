"""Prepared list-to-tuple snapshot commit; no allocation or finish while locked."""

__pcc_freestanding__ = True

from pcc import (
    i64,
)
from pcc.extern import (
    c_abi_export,
    c_int64,
    c_ptr,
    c_void,
    extern,
)
from pcc.unsafe import (
    abi_constant,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    ptr_add,
    ptr_is_null,
    store_i64,
)

pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_copy_ptr_lease_commit_locked = extern(
    "pcc_gc_copy_ptr_lease_commit_locked", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)

_SEQUENCE_SNAPSHOT_PLAN_BYTES: i64 = 128


@c_abi_export("pcc_list_snapshot_commit_slots")
def pcc_list_snapshot_commit_slots(
    source_slot: c_ptr, tuple_slot: c_ptr, length: i64,
    plans: c_ptr, tokens: c_ptr,
) -> i64:
    """Commit one consistent list version into a prepared empty tuple.

    Both containing objects are owned/address-leased. Every destination item
    is zero initialized and traced by tuple.len, every pointer plan was
    initialized before locking, and every token word starts at zero. Status
    -2 is a no-write size mismatch requiring a fresh preparation; -1 is an
    invalid shape/copy failure; 0 succeeds. Partial copies on -1 remain owned
    by the tuple. The caller finishes all plans and releases all transferred
    child leases outside the lock, before clearing or retiring that tuple.
    """
    status: i64 = -1
    pcc_py_gc_minor_graph_lock()
    source = load_ptr(source_slot, 0)
    output = load_ptr(tuple_slot, 0)
    if ptr_is_null(source) == 0 and ptr_is_null(output) == 0:
        if is_tagged_int(source) == 0 and is_tagged_int(output) == 0:
            if load_i32(source, abi_constant("object.header.type_tag_offset")) == abi_constant("object.type.list") and load_i32(output, abi_constant("object.header.type_tag_offset")) == abi_constant("object.type.tuple"):
                if load_i64(output, abi_constant("object.tuple.length_offset")) == length and length >= 0:
                    status = -2
                    if load_i64(source, abi_constant("object.list.length_offset")) == length:
                        items = load_ptr(source, abi_constant("object.list.items_offset"))
                        status = 0
                        if length > 0 and ptr_is_null(items) != 0:
                            status = -1
                        index: i64 = 0
                        while index < length and status == 0:
                            source_field = ptr_add(items, index * abi_constant("object.pointer.size"))
                            destination = ptr_add(output, abi_constant("object.tuple.items_offset") + index * abi_constant("object.pointer.size"))
                            if ptr_is_null(load_ptr(source_field, 0)) != 0:
                                status = -1
                            else:
                                token: i64 = pcc_gc_copy_ptr_lease_commit_locked(
                                    ptr_add(plans, index * _SEQUENCE_SNAPSHOT_PLAN_BYTES),
                                    output, destination, source_field,
                                )
                                if token < 0:
                                    status = -1
                                else:
                                    store_i64(tokens, index * abi_constant("object.pointer.size"), token)
                            index += 1
    pcc_py_gc_minor_graph_unlock()
    return status
