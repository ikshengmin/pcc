"""Raw object-node pool, object list, and Backend 3 young worklist."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    atomic_rmw_i64,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_eq,
    ptr_add,
    ptr_diff,
    ptr_is_null,
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

pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_allocator_record_span_first = extern(
    "pcc_allocator_record_span_first", (), c_ptr
)
pcc_allocator_record_span_next = extern(
    "pcc_allocator_record_span_next", (c_ptr,), c_ptr
)
pcc_allocator_record_span_records = extern(
    "pcc_allocator_record_span_records", (c_ptr,), c_ptr
)
pcc_allocator_record_span_cells = extern(
    "pcc_allocator_record_span_cells", (c_ptr,), c_int64
)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_gc_granule_is_object_start = extern("pcc_gc_granule_is_object_start", (c_ptr,), c_int64)


@c_abi_export("pcc_gc_object_list_head")
def pcc_gc_object_list_head() -> c_ptr:
    return global_load_ptr("pcc_gc_object_head")


@c_abi_export("pcc_gc_object_walk_records_from")
def _object_walk_records_from(span: c_ptr, cell: i64) -> c_ptr:
    """The first registered record at or after ``cell`` of ``span``, then of
    the record spans after it, or NULL."""
    found = null()
    while ptr_is_null(found) != 0 and ptr_is_null(span) == 0:
        records = pcc_allocator_record_span_records(span)
        count: i64 = pcc_allocator_record_span_cells(span)
        while ptr_is_null(found) != 0 and cell < count:
            record = ptr_add(records, cell * OBJECT_NODE_RECORD_BYTES)
            if atomic_load_i64(record, RECORD_INDEX_KEY, "acquire") != 0:
                found = record
            cell = cell + 1
        if ptr_is_null(found) != 0:
            span = pcc_allocator_record_span_next(span)
            cell = 0
    return found


@c_abi_export("pcc_gc_object_walk_first")
def pcc_gc_object_walk_first() -> c_ptr:
    """The first node of a walk over every registered object: the object
    list, then each registered cell record. Records are never freed, so a
    walk may continue from a record whose object died meanwhile."""
    head = pcc_gc_object_list_head()
    if ptr_is_null(head) == 0:
        return head
    return _object_walk_records_from(pcc_allocator_record_span_first(), 0)


@c_abi_export("pcc_gc_object_walk_next")
def pcc_gc_object_walk_next(node: c_ptr) -> c_ptr:
    """The node after ``node`` in its walk; see pcc_gc_object_walk_first."""
    span = load_ptr(node, NODE_RECORD_SPAN)
    if ptr_is_null(span) != 0:
        nxt = load_ptr(node, 16)
        if ptr_is_null(nxt) == 0:
            return nxt
        return _object_walk_records_from(pcc_allocator_record_span_first(), 0)
    cell: i64 = ptr_diff(node, pcc_allocator_record_span_records(span)) // OBJECT_NODE_RECORD_BYTES
    return _object_walk_records_from(span, cell + 1)


@c_abi_export("pcc_gc_object_list_non_slab_enumerate")
def pcc_gc_object_list_non_slab_enumerate(out: c_ptr, capacity: i64, required_flags: i64) -> i64:
    """Count/fill flagged non-slab objects from the stopped active graph.

    Successful ObjectNode publication removes these objects from the exact
    managed-pointer set. Slab objects have their own LIVE-cell enumeration;
    exact-set overlap during publication is deduplicated by the materializer.
    Return every match, writing at most capacity pointers, or -1 without STW.

    CMS joins the ordinary live-thread set before its entry, and graph-lock
    holders cannot park. A completed stop therefore excludes graph mutation,
    including scalar retirement. Pending nodes were detached at claim before
    their links were reused; never enumerate the object index or pending chain.
    Raw frees touch only retired/detached storage and remain running until a
    poll. No graph lock, allocation or callback is needed during this walk.
    """
    if pcc_thread_owns_stopped_world() == 0:
        return -1
    total: i64 = 0
    node = pcc_gc_object_list_head()
    while ptr_is_null(node) == 0:
        if load_i64(node, 32) == 0:
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) == 0 and is_tagged_int(obj) == 0:
                if ((load_i32(obj, 12) & required_flags) == required_flags
                        and pcc_gc_granule_is_object_start(obj) != 1):
                    if total < capacity:
                        store_ptr(out, total * 8, obj)
                    total = total + 1
        node = load_ptr(node, 16)
    return total


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
        load_i32(global_addr("pcc_gc_backend_selected"), 0) != 4
        and ptr_is_null(global_load_ptr("pcc_gc_backend3_promotion_head")) != 0
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


@c_abi_export("pcc_gc_object_node_set_zpage")
def pcc_gc_object_node_set_zpage(node: c_ptr, zpage_node: c_ptr) -> None:
    store_ptr(node, 48, zpage_node)


@c_abi_export("pcc_gc_object_node_gc_refs")
def pcc_gc_object_node_gc_refs(node: c_ptr) -> i64:
    return load_i64(node, 56)


@c_abi_export("pcc_gc_object_node_set_gc_refs")
def pcc_gc_object_node_set_gc_refs(node: c_ptr, value: i64) -> None:
    store_i64(node, 56, value)


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


@c_abi_export("pcc_gc_object_node_take_record")
def pcc_gc_object_node_take_record(record: c_ptr) -> c_ptr:
    """Claim a cell's record as the node of the object now in that cell.

    The caller holds the graph lock and has checked the record is not indexed
    for this object already. The cell's previous object released the record
    before its cell could be allocated again: unindexed and without leases.
    """
    if (
        load_i64(record, RECORD_INDEX_KEY) != 0
        or load_i64(record, 80) != 0
    ):
        pcc_platform_abort()
        return null()
    _clear_promotion_state(record)
    return record


@c_abi_export("pcc_gc_object_node_return_record")
def pcc_gc_object_node_return_record(record: c_ptr) -> None:
    """Return a detached cell record to its cell; nothing is freed.

    The caller holds the graph lock, has removed the record's index key and
    unlinked it. A record still keyed, leased or not a record at all aborts.
    """
    if (
        pcc_gc_object_node_is_record(record) == 0
        or load_i64(record, RECORD_INDEX_KEY) != 0
        or load_i64(record, 80) != 0
    ):
        pcc_platform_abort()
        return
    _clear_promotion_state(record)


@c_abi_export("pcc_gc_object_node_alloc")
def pcc_gc_object_node_alloc() -> c_ptr:
    head = global_load_ptr("pcc_gc_object_node_free_head")
    if ptr_is_null(head) == 0:
        global_store_ptr("pcc_gc_object_node_free_head", load_ptr(head, 16))
        count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
        if count > 0:
            store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count - 1)
        if load_i64(head, 80) != 0:
            pcc_platform_abort()
            return null()
        _clear_promotion_state(head)
        return head
    node = malloc(88)
    if ptr_is_null(node) == 0:
        store_i64(node, 80, 0)
    _clear_promotion_state(node)
    return node


@c_abi_export("pcc_gc_object_node_prepare")
def pcc_gc_object_node_prepare() -> c_ptr:
    node = malloc(88)
    if ptr_is_null(node) == 0:
        store_i64(node, 80, 0)
    return node


@c_abi_export("pcc_gc_object_node_plan_requires_prepare")
def pcc_gc_object_node_plan_requires_prepare() -> i64:
    if ptr_is_null(global_load_ptr("pcc_gc_object_node_free_head")) != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_object_node_take_prepared")
def pcc_gc_object_node_take_prepared(prepared_io: c_ptr) -> c_ptr:
    if ptr_is_null(prepared_io) != 0:
        return null()
    head = global_load_ptr("pcc_gc_object_node_free_head")
    if ptr_is_null(head) == 0:
        global_store_ptr("pcc_gc_object_node_free_head", load_ptr(head, 16))
        count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
        if count > 0:
            store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count - 1)
        if load_i64(head, 80) != 0:
            pcc_platform_abort()
            return null()
        _clear_promotion_state(head)
        return head
    node = load_ptr(prepared_io, 0)
    store_ptr(prepared_io, 0, null())
    if ptr_is_null(node) == 0 and load_i64(node, 80) != 0:
        pcc_platform_abort()
        return null()
    _clear_promotion_state(node)
    return node


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


@c_abi_export("pcc_gc_object_node_release_to_pool")
def pcc_gc_object_node_release_to_pool(node: c_ptr) -> i64:
    """Pool an already detached/index-retired node; never free under the lock.

    Return zero to transfer the full pool's overflow node to the caller's raw
    release chunk. The caller must not inspect a successfully pooled node again.
    A cell's record returns to its cell instead and is never overflow.
    """
    if ptr_is_null(node) != 0 or load_i64(node, 80) != 0:
        pcc_platform_abort()
        return 0
    if pcc_gc_object_node_is_record(node) != 0:
        _clear_promotion_state(node)
        return 1
    count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
    if count >= 8192:
        return 0
    _clear_promotion_state(node)
    store_ptr(node, 16, global_load_ptr("pcc_gc_object_node_free_head"))
    global_store_ptr("pcc_gc_object_node_free_head", node)
    store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count + 1)
    return 1


@c_abi_export("pcc_gc_object_node_finish_detached")
def pcc_gc_object_node_finish_detached(nodes: c_ptr) -> None:
    while ptr_is_null(nodes) == 0:
        node = nodes
        nodes = load_ptr(node, 16)
        if load_i64(node, 80) != 0:
            pcc_platform_abort()
            return
        store_ptr(node, 16, null())
        # Records are released before their chain is built; never free one.
        if pcc_gc_object_node_is_record(node) == 0:
            free(node)


@c_abi_export("pcc_gc_backend3_young_link_head")
def pcc_gc_backend3_young_link_head(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    head = pcc_gc_backend3_young_list_head()
    pcc_gc_object_node_set_young_prev(node, null())
    pcc_gc_object_node_set_young_next(node, head)
    if ptr_is_null(head) == 0:
        pcc_gc_object_node_set_young_prev(head, node)
    pcc_gc_backend3_young_set_head(node)


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


@c_abi_export("pcc_gc_object_known_size")
def pcc_gc_object_known_size(obj: c_ptr) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) == 0 and pcc_gc_object_node_freeing(node) == 0:
        return pcc_gc_object_node_size(node)
    return 0


@c_abi_export("pcc_gc_live_bytes_subtract")
def pcc_gc_live_bytes_subtract(size: i64) -> None:
    # Registration of an object cell adds without the graph lock
    # (pcc_gc_register_object_cell), so every update is atomic. The count
    # never goes below zero.
    if size <= 0:
        return
    slot = global_addr("pcc_gc_live_bytes")
    live: i64 = atomic_load_i64(slot, 0, "relaxed")
    while True:
        updated: i64 = 0
        if size < live:
            updated = live - size
        observed: i64 = atomic_cas_i64(slot, 0, live, updated, "relaxed", "relaxed")
        if observed == live:
            return
        live = observed
