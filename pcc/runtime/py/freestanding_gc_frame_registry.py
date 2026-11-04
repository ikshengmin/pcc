"""Native frame-root registry and GC3/GC4 thread-local node storage."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_void_ptr2,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    frame_address,
    free,
    gc_backend_current,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

pcc_gc_index_slot_size = extern("pcc_gc_index_slot_size", (), c_int64)



define_thread_local_ptr_null("pcc_gc_frame_node_pool_heads")
define_thread_local_ptr_null("pcc_gc_frame_node_pool_counts")
# One cap spans every exact-size 0..16-slot bucket in this native thread.
define_thread_local_i32("pcc_gc_frame_node_pool_total", 0)


pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_gc_cycle_requested_store_release = extern("pcc_gc_cycle_requested_store_release", (c_int64,), c_void)

pcc_gc_root_slot_count_from_map = extern("pcc_gc_root_slot_count_from_map", (c_ptr,), c_int64)
pcc_gc_root_map_is_borrowed = extern("pcc_gc_root_map_is_borrowed", (c_ptr,), c_int64)
pcc_gc_root_registry_note_mutation_locked = extern(
    "pcc_gc_root_registry_note_mutation_locked", (), c_void
)
pcc_gc_frame_index_find = extern("pcc_gc_frame_index_find", (c_ptr,), c_ptr)
pcc_gc_frame_index_plan_capacity = extern(
    "pcc_gc_frame_index_plan_capacity", (c_int64,), c_int64
)
pcc_gc_frame_index_plan_commit = extern("pcc_gc_frame_index_plan_commit", (c_ptr, c_int64, c_int64), c_int64)
pcc_gc_frame_index_replace_preallocated = extern(
    "pcc_gc_frame_index_replace_preallocated", (c_ptr, c_ptr), c_ptr
)
pcc_gc_frame_index_remove = extern("pcc_gc_frame_index_remove", (c_ptr,), c_ptr)
pcc_gc_stackmap_roots_mode = extern("pcc_gc_stackmap_roots_mode", (), c_int64)
pcc_thread_stack_high = extern("pcc_thread_stack_high", (), c_int64)


@c_abi_export("pcc_gc_frame_slots_walked")
def pcc_gc_frame_slots_walked(slots) -> i64:
    """Whether the precise stack walk roots this mapped frame's slots.

    Only the ``_mapped`` frame entries ask: compiled code that carries
    stack-map records for the frame. True while a published table makes the
    walk the root source and the slots live in a caller frame on this
    registered thread's stack; the walk then visits them for every
    collector and every mutator thread, so the frame needs no registry
    node. Otherwise (no table, a heap address) the frame registers.
    pcc_gc_stackmap_walk_state records the mode's final answer.
    """
    walk: i64 = load_i32(global_addr("pcc_gc_stackmap_walk_state"), 0)
    if walk == 0 and pcc_gc_stackmap_roots_mode() == 0:
        return 0
    address: i64 = ptr_to_int(slots)
    if address <= ptr_to_int(frame_address()):
        return 0
    high: i64 = pcc_thread_stack_high()
    if high == 0 or address >= high:
        return 0
    return 1


@c_abi_export("pcc_gc_frame_roots_disabled_fast")
def pcc_gc_frame_roots_disabled_fast() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return 0
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0:
        return 0
    if load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0:
        return 0
    return 1


@c_abi_export("pcc_gc_should_track_frame_roots")
def pcc_gc_should_track_frame_roots() -> i64:
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0:
        return 1
    return load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0)


@c_abi_export("pcc_gc_frame_node_bucket")
def pcc_gc_frame_node_bucket(root_count: i64) -> i64:
    # Zero is a valid bucket for the allocator helper. Frame enter itself
    # remains allocation-free for a zero-root map.
    if root_count < 0 or root_count > 16:
        return -1
    return root_count


@c_abi_export("pcc_gc_frame_node_size")
def pcc_gc_frame_node_size(root_count: i64) -> i64:
    # The C oracle pins this mirrored prefix with a sizeof static assertion.
    return 64 + root_count * 8


@c_abi_export("pcc_gc_frame_node_pool_heads_get")
def pcc_gc_frame_node_pool_heads_get():
    heads = global_load_ptr("pcc_gc_frame_node_pool_heads")
    if ptr_is_null(heads) == 0:
        return heads
    heads = malloc(136)
    if ptr_is_null(heads) != 0:
        return heads
    memset(heads, 0, 136)
    global_store_ptr("pcc_gc_frame_node_pool_heads", heads)
    return heads


@c_abi_export("pcc_gc_frame_node_pool_counts_get")
def pcc_gc_frame_node_pool_counts_get():
    counts = global_load_ptr("pcc_gc_frame_node_pool_counts")
    if ptr_is_null(counts) == 0:
        return counts
    counts = malloc(136)
    if ptr_is_null(counts) != 0:
        return counts
    memset(counts, 0, 136)
    global_store_ptr("pcc_gc_frame_node_pool_counts", counts)
    return counts


@c_abi_export("pcc_gc_frame_node_alloc")
def pcc_gc_frame_node_alloc(root_count: i64):
    node_size: i64 = pcc_gc_frame_node_size(root_count)
    bucket: i64 = pcc_gc_frame_node_bucket(root_count)
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (backend == 3 or backend == 4) and bucket >= 0:
        heads = pcc_gc_frame_node_pool_heads_get()
        counts = pcc_gc_frame_node_pool_counts_get()
        if ptr_is_null(heads) == 0 and ptr_is_null(counts) == 0:
            offset: i64 = bucket * 8
            head = load_ptr(heads, offset)
            if ptr_is_null(head) == 0:
                nxt = load_ptr(head, 16)
                store_ptr(heads, offset, nxt)
                count: i64 = load_i64(counts, offset)
                if count > 0:
                    store_i64(counts, offset, count - 1)
                total: i64 = load_i32(
                    global_addr("pcc_gc_frame_node_pool_total"), 0
                )
                if total > 0:
                    store_i32(
                        global_addr("pcc_gc_frame_node_pool_total"),
                        0,
                        total - 1,
                    )
                memset(head, 0, node_size)
                return head
    node = malloc(node_size)
    if ptr_is_null(node) == 0:
        memset(node, 0, node_size)
    return node


@c_abi_export("pcc_gc_frame_node_create")
def pcc_gc_frame_node_create(
    frame_map,
    slots,
    root_count: i64,
    extra_flags: i64,
):
    node = pcc_gc_frame_node_alloc(root_count)
    if ptr_is_null(node) != 0:
        return node
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    store_ptr(node, 16, global_load_ptr("pcc_gc_frame_head"))
    store_ptr(node, 24, null())
    store_ptr(node, 32, null())
    store_i64(node, 40, root_count)
    store_i32(
        node,
        48,
        pcc_gc_root_map_is_borrowed(frame_map) | extra_flags,
    )
    store_ptr(node, 56, ptr_add(node, 64))
    return node


@c_abi_export("pcc_gc_frame_node_unlink")
def pcc_gc_frame_node_unlink(node) -> None:
    if ptr_is_null(node) != 0:
        return
    prev = load_ptr(node, 24)
    nxt = load_ptr(node, 16)
    if ptr_eq(
        global_load_ptr("pcc_gc_backend3_frame_root_scan_cursor"), node
    ) != 0:
        global_store_ptr("pcc_gc_backend3_frame_root_scan_cursor", nxt)
        store_i64(
            global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0, 0
        )
    if ptr_is_null(prev) != 0:
        global_store_ptr("pcc_gc_frame_head", nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 24, prev)
    store_ptr(node, 16, null())
    store_ptr(node, 24, null())
    store_ptr(node, 32, null())
    pcc_gc_root_registry_note_mutation_locked()


@c_abi_export("pcc_gc_frame_node_release")
def pcc_gc_frame_node_release(node) -> None:
    if ptr_is_null(node) != 0:
        return
    root_count: i64 = load_i64(node, 40)
    bucket: i64 = pcc_gc_frame_node_bucket(root_count)
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (backend != 3 and backend != 4) or bucket < 0:
        free(node)
        return
    heads = pcc_gc_frame_node_pool_heads_get()
    counts = pcc_gc_frame_node_pool_counts_get()
    if ptr_is_null(heads) != 0 or ptr_is_null(counts) != 0:
        free(node)
        return
    offset: i64 = bucket * 8
    count: i64 = load_i64(counts, offset)
    total: i64 = load_i32(global_addr("pcc_gc_frame_node_pool_total"), 0)
    if total >= 1024:
        free(node)
        return
    node_size: i64 = pcc_gc_frame_node_size(root_count)
    memset(node, 0, node_size)
    store_ptr(node, 16, load_ptr(heads, offset))
    store_ptr(heads, offset, node)
    store_i64(counts, offset, count + 1)
    store_i32(global_addr("pcc_gc_frame_node_pool_total"), 0, total + 1)


@c_abi_export("pcc_gc_frame_node_tls_pool_cached_count")
def pcc_gc_frame_node_tls_pool_cached_count() -> i64:
    return load_i32(global_addr("pcc_gc_frame_node_pool_total"), 0)


@c_abi_export("pcc_gc_frame_node_tls_pool_drain")
def pcc_gc_frame_node_tls_pool_drain() -> None:
    heads = global_load_ptr("pcc_gc_frame_node_pool_heads")
    counts = global_load_ptr("pcc_gc_frame_node_pool_counts")
    bucket: i64 = 0
    while bucket <= 16:
        offset: i64 = bucket * 8
        if ptr_is_null(heads) == 0:
            node = load_ptr(heads, offset)
            store_ptr(heads, offset, null())
            while ptr_is_null(node) == 0:
                nxt = load_ptr(node, 16)
                free(node)
                node = nxt
        if ptr_is_null(counts) == 0:
            store_i64(counts, offset, 0)
        bucket = bucket + 1
    if ptr_is_null(heads) == 0:
        free(heads)
        global_store_ptr("pcc_gc_frame_node_pool_heads", null())
    if ptr_is_null(counts) == 0:
        free(counts)
        global_store_ptr("pcc_gc_frame_node_pool_counts", null())
    store_i32(global_addr("pcc_gc_frame_node_pool_total"), 0, 0)


@c_abi_export("pcc_gc_note_frame_enter")
def pcc_gc_note_frame_enter(frame_map, slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0:
        return
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    root_count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
    if root_count <= 0:
        return
    # The slots become roots only once linked. A contended graph lock polls
    # for a stop before it queues, and a collector running then could move or
    # free values that only these unpublished slots hold. Stay unparkable
    # until the node is linked; the exit services any pending stop after.
    pcc_thread_no_park_enter()
    node = pcc_gc_frame_node_create(frame_map, slots, root_count, 0)
    if ptr_is_null(node) != 0:
        pcc_thread_no_park_exit()
        return
    prepared = null()
    prepared_cap: i64 = 0
    prepared_slot = stack_alloc(8)
    while True:
        pcc_py_gc_minor_graph_lock()
        required: i64 = pcc_gc_frame_index_plan_capacity(1)
        if required < 0:
            pcc_py_gc_minor_graph_unlock()
            if ptr_is_null(prepared) == 0:
                free(prepared)
            pcc_gc_frame_node_release(node)
            pcc_thread_no_park_exit()
            return
        if required > 0 and (
            ptr_is_null(prepared) != 0 or prepared_cap < required
        ):
            pcc_py_gc_minor_graph_unlock()
            if ptr_is_null(prepared) == 0:
                free(prepared)
            prepared = malloc(required * pcc_gc_index_slot_size())
            if ptr_is_null(prepared) != 0:
                pcc_gc_frame_node_release(node)
                pcc_thread_no_park_exit()
                return
            memset(prepared, 0, required * pcc_gc_index_slot_size())
            prepared_cap = required
            continue
        if required > 0:
            store_ptr(prepared_slot, 0, prepared)
            if (
                pcc_gc_frame_index_plan_commit(
                    prepared_slot, prepared_cap, 1
                )
                < 0
            ):
                pcc_py_gc_minor_graph_unlock()
                free(prepared)
                prepared = null()
                prepared_cap = 0
                continue
            prepared = load_ptr(prepared_slot, 0)
        old_head = global_load_ptr("pcc_gc_frame_head")
        store_ptr(node, 16, old_head)
        if ptr_is_null(old_head) == 0:
            store_ptr(old_head, 24, node)
        global_store_ptr("pcc_gc_frame_head", node)
        pcc_gc_root_registry_note_mutation_locked()
        duplicate = pcc_gc_frame_index_replace_preallocated(slots, node)
        if ptr_eq(duplicate, node) != 0:
            pcc_gc_frame_node_unlink(node)
            pcc_py_gc_minor_graph_unlock()
            if ptr_is_null(prepared) == 0:
                free(prepared)
            pcc_gc_frame_node_release(node)
            pcc_thread_no_park_exit()
            return
        store_ptr(node, 32, duplicate)
        # A frame enter or leave genuinely is new tracing work for an incremental
        # marker, so requesting a cycle is right for a mutator.  It is wrong for the
        # collector itself: pcc_gc_collect stops the world and drains with
        # `while pcc_gc_step(...) != 0`, and every call inside that drain enters and
        # leaves a GC frame because this runtime is compiled pcc-Python.  Each such
        # transition re-armed pcc_gc_cycle_requested, so completing a cycle
        # immediately requested the next one and the drain never terminated -- the
        # gray count was measured oscillating 0 -> 3 -> 0 forever with no mutator
        # running, both halves reporting progress.  The C runtime never hits this
        # because its collector is C and registers no GC frames.  The world is
        # stopped for the whole explicit window, so a frame transition seen there is
        # always the collector's own.
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
            pcc_gc_cycle_requested_store_release(1)
        pcc_py_gc_minor_graph_unlock()
        if ptr_is_null(prepared) == 0:
            free(prepared)
        pcc_thread_no_park_exit()
        return


@c_abi_export("pcc_gc_note_frame_enter_checked")
def pcc_gc_note_frame_enter_checked(frame_map, slots) -> i64:
    """Register fresh raw slots before the caller releases its no-park guard.

    Return 1 for a registered frame (or deliberately disabled frame tracking),
    0 for invalid input, duplicate slots, or registration failure, and -1 when
    the caller did not protect its unpublished inputs with an outer guard.
    The caller must hold no graph, container, or scheduler lock: registration
    may allocate and acquire the graph lock beneath the plain no-park guard.
    Slots belong exclusively to this activation; slots and immutable map storage
    stay valid until a successful entry is paired with leave. Failure adds no
    frame: in particular, duplicate failure must not retire the previous frame.
    No Python exception is allocated here. The caller owns failure cleanup and
    must not release that guard with unprotected live values. Legacy entry and
    generated-code ABI remain unchanged.
    """
    if pcc_thread_no_park_depth() <= 0:
        return -1
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return 0
    root_count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
    if root_count <= 0:
        return 0
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return 1
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0:
        return 1
    # Reject re-entry: an older indexed frame must never mask failure to
    # register this new activation. Its caller keeps these stack slots private.
    pcc_py_gc_minor_graph_lock()
    previous = pcc_gc_frame_index_find(slots)
    pcc_py_gc_minor_graph_unlock()
    if ptr_is_null(previous) == 0:
        return 0
    pcc_gc_note_frame_enter(frame_map, slots)
    pcc_py_gc_minor_graph_lock()
    current = pcc_gc_frame_index_find(slots)
    registered: i64 = 0
    if ptr_is_null(current) == 0:
        if (ptr_eq(load_ptr(current, 0), frame_map) != 0
                and ptr_eq(load_ptr(current, 8), slots) != 0
                and load_i64(current, 40) == root_count):
            registered = 1
    pcc_py_gc_minor_graph_unlock()
    if ptr_is_null(current) == 0 and registered == 0:
        # These private activation slots cannot legitimately acquire another
        # node between checks. Do not let cleanup retire an unrelated frame.
        pcc_platform_abort()
        return -1
    return registered


@c_abi_export("pcc_gc_note_frame_enter_lifo")
def pcc_gc_note_frame_enter_lifo(frame_map, slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0:
        return
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    root_count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
    if root_count <= 0:
        return
    # Unparkable until linked, as in pcc_gc_note_frame_enter.
    pcc_thread_no_park_enter()
    node = pcc_gc_frame_node_create(frame_map, slots, root_count, 2)
    if ptr_is_null(node) == 0:
        pcc_py_gc_minor_graph_lock()
        old_head = global_load_ptr("pcc_gc_frame_head")
        store_ptr(node, 16, old_head)
        if ptr_is_null(old_head) == 0:
            store_ptr(old_head, 24, node)
        global_store_ptr("pcc_gc_frame_head", node)
        pcc_gc_root_registry_note_mutation_locked()
        # Collector-internal frame transitions must not re-arm the cycle; see
        # the note on the first of these four sites.
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
            pcc_gc_cycle_requested_store_release(1)
        pcc_py_gc_minor_graph_unlock()
    pcc_thread_no_park_exit()


@c_abi_export("pcc_gc_note_frame_leave_lifo")
def pcc_gc_note_frame_leave_lifo(slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0 or ptr_is_null(slots) != 0:
        return
    if ptr_is_null(global_load_ptr("pcc_gc_frame_head")) != 0:
        # No registry node exists at all, so this frame (walked or not)
        # has none to unlink: skip the graph lock.
        return
    released = null()
    pcc_py_gc_minor_graph_lock()
    node = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), slots) != 0 and (load_i32(node, 48) & 2) != 0:
            pcc_gc_frame_node_unlink(node)
            # Collector-internal frame transitions must not re-arm the cycle; see the
            # note on the first of these four sites.
            if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
                pcc_gc_cycle_requested_store_release(1)
            released = node
            break
        node = load_ptr(node, 16)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_frame_node_release(released)


@c_abi_export("pcc_gc_note_frame_leave")
def pcc_gc_note_frame_leave(slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    backend: i64 = gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0 or ptr_is_null(slots) != 0:
        return
    if ptr_is_null(global_load_ptr("pcc_gc_frame_head")) != 0:
        # No registry node exists (walked frames register none).
        return
    released = null()
    pcc_py_gc_minor_graph_lock()
    if backend == 0 and ptr_is_null(global_load_ptr("pcc_gc_frame_head")) != 0:
        pcc_py_gc_minor_graph_unlock()
        return
    indexed = pcc_gc_frame_index_find(slots)
    if ptr_is_null(indexed) != 0:
        pcc_py_gc_minor_graph_unlock()
        return
    if ptr_eq(load_ptr(indexed, 8), slots) != 0:
        duplicate = load_ptr(indexed, 32)
        pcc_gc_frame_node_unlink(indexed)
        if ptr_is_null(duplicate) == 0:
            pcc_gc_frame_index_replace_preallocated(slots, duplicate)
        else:
            pcc_gc_frame_index_remove(slots)
        released = indexed
        # Collector-internal frame transitions must not re-arm the cycle; see the
        # note on the first of these four sites.
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
            pcc_gc_cycle_requested_store_release(1)
    else:
        pcc_gc_frame_index_remove(slots)
        pcc_gc_frame_index_replace_preallocated(load_ptr(indexed, 8), indexed)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_frame_node_release(released)


# Explicit activation-private registration. Unchecked legacy entries above
# keep their global duplicate/index semantics. Each arena entry embeds the
# published native-frame descriptor (40 bytes) and one range (16 bytes).
# Entry: descriptor@0, slots@40, signed_count@48, map@56, kind@64, free_next@72.
# Arena: chunks@0, free@8, active_head@16, active_count@24, visitors@32,
#        retired@40, owner@48, chunk_count@56. Chunk: next@0, used@8; entries@32.
_LOCAL_ARENA_BYTES: i64 = 64
_LOCAL_ENTRY_BYTES: i64 = 80
_LOCAL_CHUNK_ENTRIES: i64 = 256
_LOCAL_CHUNK_BYTES: i64 = 20512

pcc_thread_local_root_arena_get = extern("pcc_thread_local_root_arena_get", (), c_ptr)
pcc_thread_local_root_arena_set = extern("pcc_thread_local_root_arena_set", (c_ptr,), c_void)
pcc_thread_is_running = extern("pcc_thread_is_running", (), c_int64)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)


@c_abi_export("pcc_gc_local_frame_find")
def pcc_gc_local_frame_find(arena, slots, kind: i64):
    if ptr_is_null(arena):
        return null()
    entry = load_ptr(arena, 16)
    while ptr_is_null(entry) == 0:
        if ptr_eq(load_ptr(entry, 40), slots) and load_i64(entry, 64) == kind:
            return entry
        entry = load_ptr(entry, 0)
    return null()


@c_abi_export("pcc_gc_local_frame_take")
def pcc_gc_local_frame_take(arena):
    entry = load_ptr(arena, 8)
    if ptr_is_null(entry) == 0:
        store_ptr(arena, 8, load_ptr(entry, 72))
        return entry
    chunk = load_ptr(arena, 0)
    if ptr_is_null(chunk) or load_i64(chunk, 8) == _LOCAL_CHUNK_ENTRIES:
        created = malloc(_LOCAL_CHUNK_BYTES)
        if ptr_is_null(created):
            return null()
        store_ptr(created, 0, chunk)
        store_i64(created, 8, 0)
        store_ptr(arena, 0, created)
        store_i64(arena, 56, load_i64(arena, 56) + 1)
        chunk = created
    index: i64 = load_i64(chunk, 8)
    store_i64(chunk, 8, index + 1)
    return ptr_add(chunk, 32 + index * _LOCAL_ENTRY_BYTES)


@c_abi_export("pcc_gc_local_frame_enter")
def pcc_gc_local_frame_enter(frame_map, slots, root_count: i64, kind: i64, checked: i64) -> i64:
    if pcc_thread_no_park_depth() <= 0 or pcc_thread_is_running() == 0:
        return -1
    arena = pcc_thread_local_root_arena_get()
    created: i64 = 0
    if ptr_is_null(arena):
        arena = malloc(_LOCAL_ARENA_BYTES)
        if ptr_is_null(arena):
            return 0
        memset(arena, 0, _LOCAL_ARENA_BYTES)
        store_i64(arena, 48, pcc_current_thread_id())
        created = 1
    elif checked != 0 and ptr_is_null(pcc_gc_local_frame_find(arena, slots, kind)) == 0:
        return 0
    entry = pcc_gc_local_frame_take(arena)
    if ptr_is_null(entry):
        if created != 0:
            free(arena)
        return 0
    memset(entry, 0, _LOCAL_ENTRY_BYTES)
    store_ptr(entry, 0, load_ptr(arena, 16))
    store_ptr(entry, 8, ptr_add(entry, 40))
    store_i64(entry, 16, 1)
    store_i64(entry, 24, 1)
    store_i64(entry, 32, load_i64(arena, 48))
    store_ptr(entry, 40, slots)
    signed_count: i64 = root_count
    if pcc_gc_root_map_is_borrowed(frame_map) != 0:
        signed_count = 0 - root_count
    store_i64(entry, 48, signed_count)
    store_ptr(entry, 56, frame_map)
    store_i64(entry, 64, kind)
    store_ptr(arena, 16, entry)
    store_i64(arena, 24, load_i64(arena, 24) + 1)
    if created != 0:
        pcc_thread_local_root_arena_set(arena)
    return 1


@c_abi_export("pcc_gc_local_frame_leave")
def pcc_gc_local_frame_leave(slots, kind: i64) -> i64:
    if pcc_thread_is_running() == 0:
        return -1
    arena = pcc_thread_local_root_arena_get()
    if ptr_is_null(arena):
        return 0
    previous = null()
    entry = load_ptr(arena, 16)
    while ptr_is_null(entry) == 0:
        if ptr_eq(load_ptr(entry, 40), slots) and load_i64(entry, 64) == kind:
            following = load_ptr(entry, 0)
            if ptr_is_null(previous):
                store_ptr(arena, 16, following)
            else:
                store_ptr(previous, 0, following)
            store_ptr(entry, 40, null())
            store_i64(entry, 48, 0)
            store_i64(entry, 16, 0)
            store_i64(arena, 24, load_i64(arena, 24) - 1)
            # A visitor can hold the unlinked descriptor as its next cursor.
            # Leave its previous link intact and defer reuse until all nested
            # stopped walks return. No native cursor survives a world resume.
            free_offset: i64 = 8
            if load_i64(arena, 32) != 0:
                free_offset = 40
            store_ptr(entry, 72, load_ptr(arena, free_offset))
            store_ptr(arena, free_offset, entry)
            return 1
        previous = entry
        entry = load_ptr(entry, 0)
    return 0


@c_abi_export("pcc_gc_local_frame_contains")
def pcc_gc_local_frame_contains(arena, slot) -> i64:
    if ptr_is_null(arena) or ptr_is_null(slot):
        return 0
    entry = load_ptr(arena, 16)
    while ptr_is_null(entry) == 0:
        count: i64 = load_i64(entry, 48)
        if count < 0:
            count = 0 - count
        offset: i64 = ptr_to_int(slot) - ptr_to_int(load_ptr(entry, 40))
        if offset >= 0 and (offset & 7) == 0 and offset // 8 < count:
            return 1
        entry = load_ptr(entry, 0)
    return 0


@c_abi_export("pcc_gc_local_frame_visit_stopped")
def pcc_gc_local_frame_visit_stopped(arena, visitor, context, borrowed: i64) -> i64:
    if pcc_thread_owns_stopped_world() == 0:
        pcc_platform_abort()
        return -1
    if ptr_is_null(arena):
        return 0
    store_i64(arena, 32, load_i64(arena, 32) + 1)
    visited: i64 = 0
    entry = load_ptr(arena, 16)
    while ptr_is_null(entry) == 0:
        following = load_ptr(entry, 0)
        if load_i64(entry, 32) != load_i64(arena, 48):
            pcc_platform_abort()
            return -1
        count: i64 = load_i64(entry, 48)
        if load_i64(entry, 16) != 0 and ((count < 0 and borrowed != 0) or (count > 0 and borrowed == 0)):
            call_void_ptr2(visitor, entry, context)
            visited = visited + 1
        entry = following
    depth: i64 = load_i64(arena, 32) - 1
    store_i64(arena, 32, depth)
    if depth == 0:
        entry = load_ptr(arena, 40)
        store_ptr(arena, 40, null())
        while ptr_is_null(entry) == 0:
            following = load_ptr(entry, 72)
            store_ptr(entry, 72, load_ptr(arena, 8))
            store_ptr(arena, 8, entry)
            entry = following
    return visited


@c_abi_export("pcc_gc_local_frame_slot_count")
def pcc_gc_local_frame_slot_count(arena) -> i64:
    if ptr_is_null(arena):
        return 0
    result: i64 = 0
    entry = load_ptr(arena, 16)
    while ptr_is_null(entry) == 0:
        count: i64 = load_i64(entry, 48)
        result = result + (0 - count if count < 0 else count)
        entry = load_ptr(entry, 0)
    return result


@c_abi_export("pcc_gc_local_frame_destroy")
def pcc_gc_local_frame_destroy(arena) -> None:
    if ptr_is_null(arena):
        return
    if load_i64(arena, 24) != 0 or load_i64(arena, 32) != 0:
        pcc_platform_abort()
        return
    chunk = load_ptr(arena, 0)
    while ptr_is_null(chunk) == 0:
        following = load_ptr(chunk, 0)
        free(chunk)
        chunk = following
    free(arena)


@c_abi_export("pcc_gc_note_frame_enter_local_checked")
def pcc_gc_note_frame_enter_local_checked(frame_map, slots) -> i64:
    """Caller promises activation-private slots and holds an outer guard.

    This explicit API may own private scratch arrays allocated by a C-API
    adapter as well as compiler stack cells. The address must not be shared
    through unchecked registration, transferred to another thread, or kept
    after its enclosing activation. Persistent owners use the legacy ABI.
    """
    if pcc_thread_no_park_depth() <= 0:
        return -1
    if ptr_is_null(frame_map) or ptr_is_null(slots):
        return 0
    count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
    if count <= 0:
        return 0
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return 1
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() == 0:
        return 1
    return pcc_gc_local_frame_enter(frame_map, slots, count, 0, 1)


@c_abi_export("pcc_gc_frame_enter_local_mode")
def pcc_gc_frame_enter_local_mode(frame_map, slots, kind: i64) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    pcc_thread_no_park_enter()
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() != 0 and ptr_is_null(frame_map) == 0 and ptr_is_null(slots) == 0:
        count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
        if count > 0 and pcc_gc_local_frame_enter(frame_map, slots, count, kind, 0) != 1:
            # A void generated entry cannot report failure while releasing its
            # unpublished inputs. The checked adapter API returns failure.
            pcc_platform_abort()
    pcc_thread_no_park_exit()


@c_abi_export("pcc_gc_frame_enter_local")
def pcc_gc_frame_enter_local(frame_map, slots) -> None:
    pcc_gc_frame_enter_local_mode(frame_map, slots, 0)


@c_abi_export("pcc_gc_frame_enter_lifo_local")
def pcc_gc_frame_enter_lifo_local(frame_map, slots) -> None:
    pcc_gc_frame_enter_local_mode(frame_map, slots, 2)


@c_abi_export("pcc_gc_frame_leave_local_mode")
def pcc_gc_frame_leave_local_mode(slots, kind: i64) -> None:
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
    if ptr_is_null(slots):
        return
    gc_backend_current()
    if pcc_gc_should_track_frame_roots() != 0 and pcc_gc_local_frame_leave(slots, kind) < 0:
        pcc_platform_abort()


@c_abi_export("pcc_gc_frame_leave_local")
def pcc_gc_frame_leave_local(slots) -> None:
    pcc_gc_frame_leave_local_mode(slots, 0)


@c_abi_export("pcc_gc_frame_leave_lifo_local")
def pcc_gc_frame_leave_lifo_local(slots) -> None:
    pcc_gc_frame_leave_local_mode(slots, 2)


@c_abi_export("pcc_gc_frame_enter_local_mapped")
def pcc_gc_frame_enter_local_mapped(frame_map, slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() == 0 and pcc_gc_frame_slots_walked(slots) == 0:
        pcc_gc_frame_enter_local(frame_map, slots)


@c_abi_export("pcc_gc_frame_enter_lifo_local_mapped")
def pcc_gc_frame_enter_lifo_local_mapped(frame_map, slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() == 0 and pcc_gc_frame_slots_walked(slots) == 0:
        pcc_gc_frame_enter_lifo_local(frame_map, slots)


@c_abi_export("pcc_gc_frame_leave_local_mapped")
def pcc_gc_frame_leave_local_mapped(slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() == 0 and pcc_gc_frame_slots_walked(slots) == 0:
        pcc_gc_frame_leave_local(slots)


@c_abi_export("pcc_gc_frame_leave_lifo_local_mapped")
def pcc_gc_frame_leave_lifo_local_mapped(slots) -> None:
    if pcc_gc_frame_roots_disabled_fast() == 0 and pcc_gc_frame_slots_walked(slots) == 0:
        pcc_gc_frame_leave_lifo_local(slots)



@c_abi_export("pcc_gc_note_frame_leave_local")
def pcc_gc_note_frame_leave_local(slots) -> None:
    # Pair of checked raw adapter entry. It is intentionally not the compiler
    # frame protocol: checked failure branches do not have an active scope.
    pcc_gc_frame_leave_local_mode(slots, 0)
