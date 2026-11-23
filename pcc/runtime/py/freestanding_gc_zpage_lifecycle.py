"""Backend 4 ZPage cache, unlink, and owner-removal lifecycle."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_node_freeing = extern(
    "pcc_gc_object_node_freeing", (c_ptr,), c_int64
)
pcc_gc_object_node_zpage = extern(
    "pcc_gc_object_node_zpage", (c_ptr,), c_ptr
)
pcc_gc_zpage_owner_index_find = extern(
    "pcc_gc_zpage_owner_index_find", (c_ptr,), c_ptr
)


@c_abi_export("pcc_gc_backend4_zpage_find")
def pcc_gc_backend4_zpage_find(owner):
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return null()
    obj_node = pcc_gc_object_index_find(owner)
    if ptr_is_null(obj_node) == 0:
        if pcc_gc_object_node_freeing(obj_node) == 0:
            znode = pcc_gc_object_node_zpage(obj_node)
            if ptr_is_null(znode) == 0:
                return znode
    return pcc_gc_zpage_owner_index_find(owner)


@c_abi_export("pcc_gc_backend4_zpage_remove_payload_span_base")
def pcc_gc_backend4_zpage_remove_payload_span_base(owner_node, base) -> i64:
    if ptr_is_null(owner_node) != 0 or ptr_is_null(base) != 0:
        return 0
    removed: i64 = 0
    prev = null()
    node = load_ptr(owner_node, 64)
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 40)
        if ptr_eq(load_ptr(node, 8), base) == 0:
            prev = node
            node = nxt
            continue
        if ptr_is_null(prev) != 0:
            store_ptr(owner_node, 64, nxt)
        else:
            store_ptr(prev, 40, nxt)
        page = load_ptr(node, 32)
        size: i64 = load_i64(node, 16)
        offset: i64 = load_i64(node, 24)
        if ptr_is_null(page) == 0 and size > 0 and offset >= 0:
            used: i64 = load_i64(page, 8)
            if used >= size:
                store_i64(page, 8, used - size)
            else:
                store_i64(page, 8, 0)
        free(node)
        removed = removed + 1
        node = nxt
    return removed
