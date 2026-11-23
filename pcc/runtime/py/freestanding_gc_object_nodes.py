"""Raw object-node pool, object list, and Backend 3 young worklist."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_rmw_i64,
    define_thread_local_i32,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_platform_abort = extern("pcc_platform_abort", (), c_void)

# Internal object-node tail: i64 foreign-address lease count at byte 80.
# This is not a PyObjectHeader field. Live promotion state never resets it.
# freeing@32: 0 active, 1 retired, negative = scalar-sweep batch token owner.
# Pending scalar nodes are detached/indexed; next@16, prev@40 and young-next@64
# belong to their retirement chain. Their first-death live debit already ran.
#
# An object-family cell's node is its record in the slab's record array
# (pcc_allocator_object_node_record_ensure), with the index key just past the node
# (freestanding_gc_index_table). A record is never pooled or freed: release
# returns it to its cell, which registers the next object allocated there.
# Records are not on the object list: word 24 of a record names its span
# (NODE_RECORD_SPAN), and walks reach records through the allocator's
# record spans. Every other object's node comes from the pool below or from
# malloc, has word 24 NULL and is on the object list.
NODE_RECORD_SPAN = 24
# The allocator's OBJECT_NODE_RECORD_BYTES.
OBJECT_NODE_RECORD_BYTES = 96
# The record's index key (freestanding_gc_index_table RECORD_INDEX_KEY).
RECORD_INDEX_KEY = 88
# The allocator's object span descriptor (freestanding_allocator): its cell
# count, its record array (SPAN_NODE_RECORDS_OFFSET) and the next span with
# records (SPAN_RECORD_NEXT_OFFSET). A walk reads them directly: every step
# of every collector pass used to call the allocator three times for them.
SPAN_CELL_COUNT = 24
SPAN_NODE_RECORDS = 48
SPAN_RECORD_NEXT = 56



@c_abi_export("pcc_gc_object_set_list_head")
def pcc_gc_object_set_list_head(head: c_ptr) -> None:
    global_store_ptr("pcc_gc_object_head", head)
    pcc_gc_object_list_revision_bump()


@c_abi_export("pcc_gc_object_list_revision_bump")
def pcc_gc_object_list_revision_bump() -> None:
    """Note that the set of walked objects changed: a node was linked or
    unlinked, or a cell record registered or retired. Resumable walks and
    remap contexts compare this revision to detect a freed (and possibly
    reused) object, so records bump it exactly as list nodes do. A record
    registers without the graph lock, so the bump is atomic; readers only
    compare it for equality.

    Only GC4's remap and retirement contexts and the promotion worklist's
    cursor read it: GC1-GC3 skip the bump, which every allocating thread
    paid on one shared line, while no promotion is queued."""
    if (
        ptr_is_null(global_load_ptr('pcc_gc_backend3_promotion_head')) != 0
    ):
        return
    atomic_rmw_i64(
        "add", global_addr("pcc_gc_object_list_revision"), 0, 1, "relaxed"
    )


@c_abi_export("pcc_gc_trace_cursor_load")
def pcc_gc_trace_cursor_load() -> c_ptr:
    return global_load_ptr("pcc_gc_trace_cursor")


@c_abi_export("pcc_gc_trace_cursor_store")
def pcc_gc_trace_cursor_store(node: c_ptr) -> None:
    global_store_ptr("pcc_gc_trace_cursor", node)


@c_abi_export("pcc_gc_backend3_young_list_head")
def pcc_gc_backend3_young_list_head() -> c_ptr:
    return global_load_ptr("pcc_gc_backend3_young_head")


@c_abi_export("pcc_gc_backend3_young_set_head")
def pcc_gc_backend3_young_set_head(node: c_ptr) -> None:
    global_store_ptr("pcc_gc_backend3_young_head", node)


@c_abi_export("pcc_gc_object_node_size")
def pcc_gc_object_node_size(node: c_ptr) -> i64:
    return load_i64(node, 8)


@c_abi_export("pcc_gc_object_node_next")
def pcc_gc_object_node_next(node: c_ptr) -> c_ptr:
    return load_ptr(node, 16)


@c_abi_export("pcc_gc_object_node_set_next")
def pcc_gc_object_node_set_next(node: c_ptr, nxt: c_ptr) -> None:
    store_ptr(node, 16, nxt)


@c_abi_export("pcc_gc_object_node_freeing")
def pcc_gc_object_node_freeing(node: c_ptr) -> i64:
    return load_i64(node, 32)


@c_abi_export("pcc_gc_object_node_set_freeing")
def pcc_gc_object_node_set_freeing(node: c_ptr, freeing: i64) -> None:
    store_i64(node, 32, freeing)


@c_abi_export("pcc_gc_object_node_prev")
def pcc_gc_object_node_prev(node: c_ptr) -> c_ptr:
    return load_ptr(node, 40)


@c_abi_export("pcc_gc_object_node_set_prev")
def pcc_gc_object_node_set_prev(node: c_ptr, prev: c_ptr) -> None:
    store_ptr(node, 40, prev)


@c_abi_export("pcc_gc_object_node_zpage")
def pcc_gc_object_node_zpage(node: c_ptr) -> c_ptr:
    return load_ptr(node, 48)


@c_abi_export("pcc_gc_object_node_young_next")
def pcc_gc_object_node_young_next(node: c_ptr) -> c_ptr:
    return load_ptr(node, 64)


@c_abi_export("pcc_gc_object_node_set_young_next")
def pcc_gc_object_node_set_young_next(node: c_ptr, nxt: c_ptr) -> None:
    store_ptr(node, 64, nxt)


@c_abi_export("pcc_gc_object_node_young_prev")
def pcc_gc_object_node_young_prev(node: c_ptr) -> c_ptr:
    return load_ptr(node, 72)


@c_abi_export("pcc_gc_object_node_set_young_prev")
def pcc_gc_object_node_set_young_prev(node: c_ptr, prev: c_ptr) -> None:
    store_ptr(node, 72, prev)


@c_abi_export("pcc_gc_object_node_clear_promotion_state")
def _clear_promotion_state(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    store_ptr(node, 64, null())
    store_ptr(node, 72, null())
    store_i64(node, 56, 0)


@c_abi_export("pcc_gc_backend3_promotion_unlink")
def _promotion_unlink(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    obj = load_ptr(node, 0)
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if (load_i32(obj, 12) & 128) != 0:
        return
    prev = load_ptr(node, 72)
    nxt = load_ptr(node, 64)
    if (
        ptr_eq(global_load_ptr("pcc_gc_backend3_promotion_head"), node) == 0
        and ptr_eq(global_load_ptr("pcc_gc_backend3_promotion_tail"), node) == 0
        and ptr_is_null(prev) != 0
        and ptr_is_null(nxt) != 0
    ):
        return
    if ptr_is_null(prev) == 0:
        store_ptr(prev, 64, nxt)
    elif ptr_eq(global_load_ptr("pcc_gc_backend3_promotion_head"), node) != 0:
        global_store_ptr("pcc_gc_backend3_promotion_head", nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 72, prev)
    elif ptr_eq(global_load_ptr("pcc_gc_backend3_promotion_tail"), node) != 0:
        global_store_ptr("pcc_gc_backend3_promotion_tail", prev)
    _clear_promotion_state(node)


@c_abi_export("pcc_gc_object_node_is_record")
def pcc_gc_object_node_is_record(node: c_ptr) -> i64:
    """1 when ``node`` is a cell record: registration names its span in
    word NODE_RECORD_SPAN, which every other node keeps NULL."""
    if ptr_is_null(node) != 0:
        return 0
    if ptr_is_null(load_ptr(node, NODE_RECORD_SPAN)) != 0:
        return 0
    return 1


@c_abi_export("pcc_gc_object_node_release")
def pcc_gc_object_node_release(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    if load_i64(node, 80) != 0:
        pcc_platform_abort()
        return
    _clear_promotion_state(node)
    if pcc_gc_object_node_is_record(node) != 0:
        return
    count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
    if count >= 8192:
        free(node)
        return
    store_ptr(node, 16, global_load_ptr("pcc_gc_object_node_free_head"))
    global_store_ptr("pcc_gc_object_node_free_head", node)
    store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count + 1)


@c_abi_export("pcc_gc_backend3_young_unlink")
def pcc_gc_backend3_young_unlink(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    prev = pcc_gc_object_node_young_prev(node)
    nxt = pcc_gc_object_node_young_next(node)
    if ptr_is_null(prev) == 0:
        pcc_gc_object_node_set_young_next(prev, nxt)
    elif ptr_eq(pcc_gc_backend3_young_list_head(), node) != 0:
        pcc_gc_backend3_young_set_head(nxt)
    else:
        pcc_gc_object_node_set_young_next(node, null())
        pcc_gc_object_node_set_young_prev(node, null())
        return
    if ptr_is_null(nxt) == 0:
        pcc_gc_object_node_set_young_prev(nxt, prev)
    pcc_gc_object_node_set_young_next(node, null())
    pcc_gc_object_node_set_young_prev(node, null())


@c_abi_export("pcc_gc_object_node_unlink")
def pcc_gc_object_node_unlink(node: c_ptr) -> None:
    if ptr_is_null(load_ptr(node, NODE_RECORD_SPAN)) == 0:
        # A cell record is not on the object list, and a walk standing on it
        # continues from its cell.
        _promotion_unlink(node)
        pcc_gc_backend3_young_unlink(node)
        pcc_gc_object_list_revision_bump()
        return
    prev = pcc_gc_object_node_prev(node)
    nxt = pcc_gc_object_node_next(node)
    if ptr_eq(pcc_gc_trace_cursor_load(), node) != 0:
        pcc_gc_trace_cursor_store(nxt)
    if ptr_eq(
        global_load_ptr("pcc_gc_backend4_reset_object_cursor"), node
    ) != 0:
        global_store_ptr("pcc_gc_backend4_reset_object_cursor", nxt)
    _promotion_unlink(node)
    pcc_gc_backend3_young_unlink(node)
    if ptr_is_null(prev) != 0:
        pcc_gc_object_set_list_head(nxt)
    else:
        pcc_gc_object_node_set_next(prev, nxt)
        pcc_gc_object_list_revision_bump()
    if ptr_is_null(nxt) == 0:
        pcc_gc_object_node_set_prev(nxt, prev)


# Each thread publishes its live-byte changes to pcc_gc_live_bytes in batches
# of at least this many bytes either way, as it publishes allocation debt
# (freestanding_gc_incremental_concurrent_scheduler): one atomic add per batch
# instead of an atomic update per registration and per free on a line every
# allocating thread and the sweeper write. The shared total lags by at most a
# batch per thread, and reads below zero while a thread's frees are published
# before another thread's allocations of the same bytes; its reader (the
# idle-step exit) only compares it with the marked bytes.
LIVE_BYTES_BATCH = 32768
define_thread_local_i32("pcc_gc_tls_live_bytes_delta", 0)


@c_abi_export("pcc_gc_live_bytes_note")
def pcc_gc_live_bytes_note(delta: i64) -> None:
    pending: i64 = load_i32(global_addr("pcc_gc_tls_live_bytes_delta"), 0) + delta
    if pending < LIVE_BYTES_BATCH and pending > 0 - LIVE_BYTES_BATCH:
        store_i32(global_addr("pcc_gc_tls_live_bytes_delta"), 0, pending)
        return
    store_i32(global_addr("pcc_gc_tls_live_bytes_delta"), 0, 0)
    atomic_rmw_i64("add", global_addr("pcc_gc_live_bytes"), 0, pending, "relaxed")


@c_abi_export("pcc_gc_live_bytes_flush")
def pcc_gc_live_bytes_flush() -> None:
    """Publish this thread's unpublished live-byte change now (telemetry
    reads, thread exit)."""
    pending: i64 = load_i32(global_addr("pcc_gc_tls_live_bytes_delta"), 0)
    if pending == 0:
        return
    store_i32(global_addr("pcc_gc_tls_live_bytes_delta"), 0, 0)
    atomic_rmw_i64("add", global_addr("pcc_gc_live_bytes"), 0, pending, "relaxed")


@c_abi_export("pcc_gc_live_bytes_subtract")
def pcc_gc_live_bytes_subtract(size: i64) -> None:
    if size <= 0:
        return
    pcc_gc_live_bytes_note(0 - size)
