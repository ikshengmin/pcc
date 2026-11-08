"""Address-sharded tracked lists with a merged collector/cold-snapshot view.

Every object address selects one of 32 shards without a provenance lookup.
Each shard also owns its fallback primary index; allocator side words retain
their existing exact-cell checks. The all-table owner joins every list and
pool into the legacy globals; nested callbacks use those globals until the
outermost unlock repartitions the nodes.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_store_i32,
    atomic_store_i64,
    free,
    gc_backend_current,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    logical_shift_right_i64,
    malloc,
    null,
    ptr_add,
    ptr_diff,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_current_native_thread_token = extern(
    "pcc_current_native_thread_token", (), c_ptr
)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_gc_index_insert = extern("py_gc_index_insert", (c_ptr, c_ptr), c_int64)
py_gc_index_remove = extern("py_gc_index_remove", (c_ptr,), c_ptr)
pcc_allocator_object_cells_enumerate = extern(
    "pcc_allocator_object_cells_enumerate", (c_ptr, c_int64, c_int64), c_int64
)
pcc_gc_managed_pointer_index_enumerate = extern(
    "pcc_gc_managed_pointer_index_enumerate", (c_ptr, c_int64, c_int64), c_int64
)
pcc_gc_object_list_non_slab_enumerate = extern(
    "pcc_gc_object_list_non_slab_enumerate", (c_ptr, c_int64, c_int64), c_int64
)

_SHARD_COUNT: i64 = 32
_SHARD_STRIDE: i64 = 128
_SHARD_LOCK: i64 = 0
_SHARD_HEAD: i64 = 8
_SHARD_POOL: i64 = 16
_SHARD_POOL_COUNT: i64 = 24
# A primary-index record {slots, capacity, count, used}, owned by index_table.
# The record occupies bytes 32..63 and is not merged with the tracked list.
_SHARD_PRIMARY_INDEX: i64 = 32
_NODE_OBJECT: i64 = 0
_NODE_GC_REFS: i64 = 8
_NODE_REACHABLE: i64 = 16
_NODE_SHARD: i64 = 20
_NODE_PREV: i64 = 24
_NODE_NEXT: i64 = 32
_NODE_BYTES: i64 = 40
_POOL_LIMIT: i64 = 4096
_TRACKED: i64 = 2


@c_abi_export("pcc_gc_tracked_shard_address")
def _tracked_shard_address(index: i64) -> c_ptr:
    base = global_addr("pcc_gc_tracked_shards")
    alignment: i64 = (
        _SHARD_STRIDE - (ptr_diff(base, null()) & (_SHARD_STRIDE - 1))
    ) & (_SHARD_STRIDE - 1)
    return ptr_add(base, alignment + index * _SHARD_STRIDE)


@c_abi_export("pcc_gc_tracked_shard_index")
def _tracked_shard_index(obj: c_ptr) -> i64:
    # This only selects a lock/table; it is not evidence of managed-pointer
    # provenance and never permits a header read or bypasses index checks.
    bits: i64 = ptr_diff(obj, null())
    mixed: i64 = (
        logical_shift_right_i64(bits, 4)
        ^ logical_shift_right_i64(bits, 11)
        ^ logical_shift_right_i64(bits, 19)
    )
    return mixed & (_SHARD_COUNT - 1)


@c_abi_export("pcc_gc_tracked_primary_index_cells")
def _tracked_primary_index_cells(obj: c_ptr) -> c_ptr:
    """Primary-index record for an address, under its shard or all locks."""
    shard = _tracked_shard_address(_tracked_shard_index(obj))
    return ptr_add(shard, _SHARD_PRIMARY_INDEX)


@c_abi_export("pcc_gc_tracked_table_owned_by_current")
def _tracked_table_owned_by_current() -> i64:
    owner: i64 = atomic_load_i64(
        global_addr("pcc_gc_table_lock_owner_token"), 0, "acquire"
    )
    if owner == 0:
        return 0
    if owner == ptr_diff(pcc_current_native_thread_token(), null()):
        return 1
    return 0


@c_abi_export("pcc_gc_flag_only_tracking")
def _flag_only_tracking() -> i64:
    """Tracking is the header flag alone: flag mode with no nodes built.

    While pcc_gc_tracked_materialize's nodes exist (a stopped-world
    collection or gc-module snapshot on this thread), track and untrack keep
    them exact through the node path instead.
    """
    if atomic_load_i32(global_addr("pcc_gc_tracking_flag_mode"), 0, "acquire") == 0:
        return 0
    if atomic_load_i32(global_addr("pcc_gc_tracking_materialized"), 0, "acquire") != 0:
        return 0
    return 1


@c_abi_export("pcc_gc_tracked_shard_lock")
def _tracked_shard_lock(index: i64) -> None:
    shard = _tracked_shard_address(index)
    acquired: i64 = 0
    while acquired == 0:
        # Defensive raw-thread registration may park, so enter before taking
        # the word. A failed attempt holds nothing when exit/poll runs.
        pcc_thread_no_park_enter()
        if atomic_cas_i32(shard, _SHARD_LOCK, 0, 1, "acq_rel", "acquire") == 0:
            acquired = 1
        else:
            pcc_thread_no_park_exit()
            pcc_thread_safepoint()


@c_abi_export("pcc_gc_tracked_shard_unlock")
def _tracked_shard_unlock(index: i64) -> None:
    atomic_store_i32(_tracked_shard_address(index), _SHARD_LOCK, 0, "release")
    pcc_thread_no_park_exit()


@c_abi_export("pcc_gc_default_table_lock")
def pcc_gc_default_table_lock() -> None:
    if _tracked_table_owned_by_current() != 0:
        depth = global_addr("pcc_gc_table_lock_depth")
        store_i64(depth, 0, load_i64(depth, 0) + 1)
        return
    acquired: i64 = 0
    while acquired == 0:
        pcc_thread_no_park_enter()
        prefix: i64 = 0
        while prefix < _SHARD_COUNT:
            shard = _tracked_shard_address(prefix)
            if atomic_cas_i32(
                shard, _SHARD_LOCK, 0, 1, "acq_rel", "acquire"
            ) != 0:
                break
            prefix = prefix + 1
        if prefix == _SHARD_COUNT:
            acquired = 1
        else:
            while prefix > 0:
                prefix = prefix - 1
                atomic_store_i32(
                    _tracked_shard_address(prefix), _SHARD_LOCK, 0, "release"
                )
            pcc_thread_no_park_exit()
            pcc_thread_safepoint()
    merged = null()
    pooled = null()
    index: i64 = 0
    while index < _SHARD_COUNT:
        shard = _tracked_shard_address(index)
        head = load_ptr(shard, _SHARD_HEAD)
        store_ptr(shard, _SHARD_HEAD, null())
        if ptr_is_null(head) == 0:
            tail = head
            while ptr_is_null(load_ptr(tail, _NODE_NEXT)) == 0:
                tail = load_ptr(tail, _NODE_NEXT)
            store_ptr(tail, _NODE_NEXT, merged)
            if ptr_is_null(merged) == 0:
                store_ptr(merged, _NODE_PREV, tail)
            merged = head
        pool = load_ptr(shard, _SHARD_POOL)
        store_ptr(shard, _SHARD_POOL, null())
        store_i32(shard, _SHARD_POOL_COUNT, 0)
        if ptr_is_null(pool) == 0:
            tail = pool
            while ptr_is_null(load_ptr(tail, _NODE_NEXT)) == 0:
                tail = load_ptr(tail, _NODE_NEXT)
            store_ptr(tail, _NODE_NEXT, pooled)
            pooled = pool
        index = index + 1
    global_store_ptr("py_gc_head", merged)
    global_store_ptr("pcc_gc_tracked_node_pool", pooled)
    store_i64(global_addr("pcc_gc_table_lock_depth"), 0, 1)
    atomic_store_i64(
        global_addr("pcc_gc_table_lock_owner_token"), 0,
        ptr_diff(pcc_current_native_thread_token(), null()), "release",
    )


@c_abi_export("pcc_gc_default_table_unlock")
def pcc_gc_default_table_unlock() -> None:
    if _tracked_table_owned_by_current() == 0:
        pcc_platform_abort()
        return
    depth = global_addr("pcc_gc_table_lock_depth")
    nesting: i64 = load_i64(depth, 0)
    if nesting > 1:
        store_i64(depth, 0, nesting - 1)
        return
    if nesting != 1:
        pcc_platform_abort()
        return
    pcc_gc_default_drain_deferred_nodes()
    node = global_load_ptr("py_gc_head")
    global_store_ptr("py_gc_head", null())
    while ptr_is_null(node) == 0:
        following = load_ptr(node, _NODE_NEXT)
        index: i64 = load_i32(node, _NODE_SHARD)
        if index < 0 or index >= _SHARD_COUNT:
            pcc_platform_abort()
            return
        shard = _tracked_shard_address(index)
        head = load_ptr(shard, _SHARD_HEAD)
        store_ptr(node, _NODE_PREV, null())
        store_ptr(node, _NODE_NEXT, head)
        if ptr_is_null(head) == 0:
            store_ptr(head, _NODE_PREV, node)
        store_ptr(shard, _SHARD_HEAD, node)
        node = following
    node = global_load_ptr("pcc_gc_tracked_node_pool")
    global_store_ptr("pcc_gc_tracked_node_pool", null())
    while ptr_is_null(node) == 0:
        following = load_ptr(node, _NODE_NEXT)
        # A pool node no longer has an object. Use its own stable address.
        index = logical_shift_right_i64(ptr_diff(node, null()), 4) & (_SHARD_COUNT - 1)
        shard = _tracked_shard_address(index)
        store_i32(node, _NODE_SHARD, index)
        store_ptr(node, _NODE_NEXT, load_ptr(shard, _SHARD_POOL))
        store_ptr(shard, _SHARD_POOL, node)
        store_i32(shard, _SHARD_POOL_COUNT, load_i32(shard, _SHARD_POOL_COUNT) + 1)
        node = following
    store_i64(depth, 0, 0)
    atomic_store_i64(global_addr("pcc_gc_table_lock_owner_token"), 0, 0, "release")
    index = _SHARD_COUNT
    while index > 0:
        index = index - 1
        atomic_store_i32(
            _tracked_shard_address(index), _SHARD_LOCK, 0, "release"
        )
    pcc_thread_no_park_exit()


@c_abi_export("pcc_gc_default_unlink_tracked_node")
def pcc_gc_default_unlink_tracked_node(node: c_ptr) -> None:
    if ptr_is_null(node):
        return
    previous = load_ptr(node, _NODE_PREV)
    next_node = load_ptr(node, _NODE_NEXT)
    if ptr_is_null(previous) == 0:
        store_ptr(previous, _NODE_NEXT, next_node)
    elif _tracked_table_owned_by_current() != 0:
        global_store_ptr("py_gc_head", next_node)
    else:
        shard = _tracked_shard_address(load_i32(node, _NODE_SHARD))
        store_ptr(shard, _SHARD_HEAD, next_node)
    if ptr_is_null(next_node) == 0:
        store_ptr(next_node, _NODE_PREV, previous)
    store_ptr(node, _NODE_PREV, null())
    store_ptr(node, _NODE_NEXT, null())
    atomic_rmw_i32("sub", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel")


@c_abi_export("pcc_gc_default_drain_deferred_nodes")
def pcc_gc_default_drain_deferred_nodes() -> None:
    node = global_load_ptr("pcc_gc_deferred_node_free_head")
    global_store_ptr("pcc_gc_deferred_node_free_head", null())
    while ptr_is_null(node) == 0:
        next_node = load_ptr(node, _NODE_NEXT)
        free(node)
        node = next_node


@c_abi_export("pcc_gc_tracked_node_pool_take")
def _tracked_node_pool_take(index: i64, merged: i64) -> c_ptr:
    shard = _tracked_shard_address(index)
    if merged != 0:
        node = global_load_ptr("pcc_gc_tracked_node_pool")
    else:
        node = load_ptr(shard, _SHARD_POOL)
    if ptr_is_null(node) == 0:
        if merged != 0:
            global_store_ptr("pcc_gc_tracked_node_pool", load_ptr(node, _NODE_NEXT))
        else:
            store_ptr(shard, _SHARD_POOL, load_ptr(node, _NODE_NEXT))
            store_i32(shard, _SHARD_POOL_COUNT, load_i32(shard, _SHARD_POOL_COUNT) - 1)
        atomic_rmw_i32(
            "sub", global_addr("pcc_gc_tracked_node_pool_count"), 0, 1, "acq_rel"
        )
        return node
    return malloc(_NODE_BYTES)


@c_abi_export("pcc_gc_tracked_node_recycle")
def _tracked_node_recycle(node: c_ptr) -> None:
    # One shared atomic budget preserves the existing total limit, rather
    # than multiplying the retained memory by the number of shards.
    count_slot = global_addr("pcc_gc_tracked_node_pool_count")
    count: i64 = atomic_load_i32(count_slot, 0, "acquire")
    reserved: i64 = 0
    while reserved == 0:
        if count >= _POOL_LIMIT:
            free(node)
            return
        observed: i64 = atomic_cas_i32(
            count_slot, 0, count, count + 1, "acq_rel", "acquire"
        )
        if observed == count:
            reserved = 1
        else:
            count = observed
    store_ptr(node, _NODE_OBJECT, null())
    store_ptr(node, _NODE_PREV, null())
    if _tracked_table_owned_by_current() != 0:
        store_ptr(node, _NODE_NEXT, global_load_ptr("pcc_gc_tracked_node_pool"))
        global_store_ptr("pcc_gc_tracked_node_pool", node)
    else:
        shard = _tracked_shard_address(load_i32(node, _NODE_SHARD))
        store_ptr(node, _NODE_NEXT, load_ptr(shard, _SHARD_POOL))
        store_ptr(shard, _SHARD_POOL, node)
        store_i32(shard, _SHARD_POOL_COUNT, load_i32(shard, _SHARD_POOL_COUNT) + 1)


@c_abi_export("pcc_gc_tracked_node_pool_cached_count")
def pcc_gc_tracked_node_pool_cached_count() -> i64:
    pcc_gc_default_table_lock()
    count: i64 = atomic_load_i32(
        global_addr("pcc_gc_tracked_node_pool_count"), 0, "acquire"
    )
    pcc_gc_default_table_unlock()
    return count


@c_abi_export("pcc_gc_tracked_node_pool_drain")
def pcc_gc_tracked_node_pool_drain() -> None:
    pcc_gc_default_table_lock()
    node = global_load_ptr("pcc_gc_tracked_node_pool")
    global_store_ptr("pcc_gc_tracked_node_pool", null())
    atomic_store_i32(global_addr("pcc_gc_tracked_node_pool_count"), 0, 0, "release")
    while ptr_is_null(node) == 0:
        next_node = load_ptr(node, _NODE_NEXT)
        free(node)
        node = next_node
    pcc_gc_default_table_unlock()


@c_abi_export("py_gc_track")
def py_gc_track(obj: c_ptr) -> None:
    if ptr_is_null(obj):
        return
    if is_tagged_int(obj):
        return
    if pcc_threads_enabled() != 0 and gc_backend_current() == 4:
        return
    # The old shard entry admitted raw newcomers before touching the header.
    # Flag-only tracking needs that boundary too. An admitted caller's ID
    # lookup is TLS-only; a newcomer may wait for STW, so select the current
    # representation only AFTER it returns. No safepoint splits flag/count.
    if pcc_current_thread_id() == 0:
        pcc_platform_abort()
        return
    if _flag_only_tracking() != 0:
        # One atomic: the previous flags say whether this call tracked it,
        # so concurrent trackers count the object once.
        previous: i64 = atomic_rmw_i32("or", obj, 12, _TRACKED, "acq_rel")
        if (previous & _TRACKED) == 0:
            _tracked_growth(atomic_rmw_i32(
                "add", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel"
            ) + 1)
        return
    merged: i64 = _tracked_table_owned_by_current()
    index: i64 = _tracked_shard_index(obj)
    if merged == 0:
        _tracked_shard_lock(index)
        # First-allocation freeze can take every shard and enable flag mode
        # while this caller waits. Recheck under the acquired no-park lease:
        # publishing a permanent node now would outlive flag-only untrack.
        if _flag_only_tracking() != 0:
            previous: i64 = atomic_rmw_i32("or", obj, 12, _TRACKED, "acq_rel")
            tracked: i64 = 0
            if (previous & _TRACKED) == 0:
                tracked = atomic_rmw_i32(
                    "add", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel"
                ) + 1
            _tracked_shard_unlock(index)
            if (previous & _TRACKED) == 0:
                _tracked_growth(tracked)
            return
    # This lock owns TRACKED and membership, not color/pin/lifetime bits.
    flags: i64 = load_i32(obj, 12)
    if (flags & _TRACKED) != 0:
        if merged == 0:
            _tracked_shard_unlock(index)
        return
    node = _tracked_node_pool_take(index, merged)
    if ptr_is_null(node):
        if merged == 0:
            _tracked_shard_unlock(index)
        return
    store_i32(node, _NODE_SHARD, index)
    inserted: i64 = py_gc_index_insert(obj, node)
    if inserted == 0:
        _tracked_node_recycle(node)
        atomic_rmw_i32("or", obj, 12, _TRACKED, "acq_rel")
        if merged == 0:
            _tracked_shard_unlock(index)
        return
    if inserted < 0:
        _tracked_node_recycle(node)
        if merged == 0:
            _tracked_shard_unlock(index)
        return
    store_ptr(node, _NODE_OBJECT, obj)
    store_i64(node, _NODE_GC_REFS, 0)
    store_i32(node, _NODE_REACHABLE, 0)
    store_ptr(node, _NODE_PREV, null())
    shard = _tracked_shard_address(index)
    if merged != 0:
        head = global_load_ptr("py_gc_head")
    else:
        head = load_ptr(shard, _SHARD_HEAD)
    store_ptr(node, _NODE_NEXT, head)
    if ptr_is_null(head) == 0:
        store_ptr(head, _NODE_PREV, node)
    if merged != 0:
        global_store_ptr("py_gc_head", node)
    else:
        store_ptr(shard, _SHARD_HEAD, node)
    tracked: i64 = atomic_rmw_i32(
        "add", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel"
    ) + 1
    atomic_rmw_i32("or", obj, 12, _TRACKED, "acq_rel")
    if merged == 0:
        _tracked_shard_unlock(index)
    _tracked_growth(tracked)


@c_abi_export("pcc_gc_tracked_growth")
def _tracked_growth(tracked: i64) -> None:
    # Phase 1 retains the exact shared count and the existing growth trigger.
    # Only GC0's cycle collector is scheduled by tracked-object growth. The
    # tracing collectors (GC3 included) run whole-heap marks paced by
    # allocation debt on the shared mark worker; a full stopped-world mark
    # every threshold0 tracked objects dominated GC3's run time.
    backend: i64 = gc_backend_current()
    if backend != 0:
        return
    threshold: i64 = load_i32(global_addr("py_gc_threshold0"), 0)
    if threshold <= 0 or load_i32(global_addr("py_gc_enabled"), 0) == 0:
        return
    live: i64 = atomic_load_i64(
        global_addr("pcc_gc_tracked_after_collect"), 0, "relaxed"
    )
    growth: i64 = threshold
    if live > growth:
        growth = live
    if tracked - live >= growth:
        atomic_store_i32(
            global_addr("pcc_gc_backend4_auto_step_pending"), 0, 1, "release"
        )


@c_abi_export("py_gc_untrack")
def py_gc_untrack(obj: c_ptr) -> None:
    if ptr_is_null(obj):
        return
    if is_tagged_int(obj):
        return
    if pcc_threads_enabled() != 0 and gc_backend_current() == 4:
        return
    # Admission can wait across materialization/dematerialization. Never
    # retain a representation decision made before that wait.
    if pcc_current_thread_id() == 0:
        pcc_platform_abort()
        return
    if _flag_only_tracking() != 0:
        previous: i64 = atomic_rmw_i32("and", obj, 12, ~_TRACKED, "acq_rel")
        if (previous & _TRACKED) != 0:
            atomic_rmw_i32("sub", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel")
        return
    merged: i64 = _tracked_table_owned_by_current()
    index: i64 = _tracked_shard_index(obj)
    if merged == 0:
        _tracked_shard_lock(index)
        # A first freeze may have removed this object's node while we waited.
        # The flag/count transition still belongs to this call exactly once.
        if _flag_only_tracking() != 0:
            previous: i64 = atomic_rmw_i32("and", obj, 12, ~_TRACKED, "acq_rel")
            if (previous & _TRACKED) != 0:
                atomic_rmw_i32("sub", global_addr("py_gc_tracked_count"), 0, 1, "acq_rel")
            _tracked_shard_unlock(index)
            return
    flags: i64 = load_i32(obj, 12)
    if (flags & _TRACKED) == 0:
        if merged == 0:
            _tracked_shard_unlock(index)
        return
    node = py_gc_index_remove(obj)
    if ptr_is_null(node) == 0:
        pcc_gc_default_unlink_tracked_node(node)
        if merged != 0:
            store_ptr(node, _NODE_OBJECT, null())
            store_ptr(node, _NODE_PREV, null())
            deferred = global_load_ptr("pcc_gc_deferred_node_free_head")
            store_ptr(node, _NODE_NEXT, deferred)
            global_store_ptr("pcc_gc_deferred_node_free_head", node)
        else:
            _tracked_node_recycle(node)
    atomic_rmw_i32("and", obj, 12, ~_TRACKED, "acq_rel")
    if merged == 0:
        _tracked_shard_unlock(index)


@c_abi_export("pcc_gc_tracked_materialize")
def pcc_gc_tracked_materialize() -> i64:
    """Acquire a complete stopped-world tracked view, or borrow an outer one.

    The caller owns the stopped world and whole table. Return the actual
    node count, or -1 on failure (0 is also returned for ordinary tracking).
    A pre-existing view is borrowed without rebuilding its nodes. Callers
    record the pre-call flag/materialized state to distinguish an acquired
    empty view from a borrow; only an acquired view may be dematerialized.
    Neither construction nor rollback changes object flags or tracked count.
    """
    if atomic_load_i32(global_addr("pcc_gc_tracking_flag_mode"), 0, "acquire") == 0:
        return 0
    if atomic_load_i32(global_addr("pcc_gc_tracking_materialized"), 0, "acquire") != 0:
        count: i64 = 0
        node = global_load_ptr("py_gc_head")
        while ptr_is_null(node) == 0:
            count = count + 1
            node = load_ptr(node, _NODE_NEXT)
        return count
    small_cap: i64 = pcc_allocator_object_cells_enumerate(null(), 0, _TRACKED)
    large_cap: i64 = pcc_gc_managed_pointer_index_enumerate(null(), 0, _TRACKED)
    tracing_cap: i64 = 0
    if load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0:
        tracing_cap = pcc_gc_object_list_non_slab_enumerate(null(), 0, _TRACKED)
    # Count/fill runs under STW. Reject invalid sizes or changed enumeration
    # rather than clamp away tracked objects and publish a partial snapshot.
    if small_cap < 0 or large_cap < 0 or tracing_cap < 0:
        return -1
    if small_cap > 1152921504606846975 - large_cap:
        return -1
    capacity: i64 = small_cap + large_cap
    if capacity > 1152921504606846975 - tracing_cap:
        return -1
    capacity = capacity + tracing_cap
    if capacity == 0:
        atomic_store_i32(global_addr("pcc_gc_tracking_materialized"), 0, 1, "release")
        return 0
    objects = malloc(capacity * 8)
    if ptr_is_null(objects):
        return -1
    small: i64 = pcc_allocator_object_cells_enumerate(objects, small_cap, _TRACKED)
    if small != small_cap:
        free(objects)
        return -1
    large: i64 = pcc_gc_managed_pointer_index_enumerate(
        ptr_add(objects, small * 8), large_cap, _TRACKED
    )
    if large != large_cap:
        free(objects)
        return -1
    tracing: i64 = 0
    if load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0:
        tracing = pcc_gc_object_list_non_slab_enumerate(
            ptr_add(objects, (small + large) * 8), tracing_cap, _TRACKED
        )
    if tracing != tracing_cap:
        free(objects)
        return -1
    atomic_store_i32(global_addr("pcc_gc_tracking_materialized"), 0, 1, "release")
    added: i64 = 0
    index: i64 = 0
    while index < small + large + tracing:
        obj = load_ptr(objects, index * 8)
        shard: i64 = _tracked_shard_index(obj)
        node = _tracked_node_pool_take(shard, 1)
        if ptr_is_null(node):
            free(objects)
            pcc_gc_tracked_dematerialize(1)
            return -1
        store_i32(node, _NODE_SHARD, shard)
        inserted: i64 = py_gc_index_insert(obj, node)
        if inserted < 0:
            _tracked_node_recycle(node)
            free(objects)
            pcc_gc_tracked_dematerialize(1)
            return -1
        if inserted == 0:
            # The enumeration sources may overlap; the existing node already
            # represents this object, so a duplicate is not an allocation error.
            _tracked_node_recycle(node)
        else:
            store_ptr(node, _NODE_OBJECT, obj)
            store_i64(node, _NODE_GC_REFS, 0)
            store_i32(node, _NODE_REACHABLE, 0)
            store_ptr(node, _NODE_PREV, null())
            head = global_load_ptr("py_gc_head")
            store_ptr(node, _NODE_NEXT, head)
            if ptr_is_null(head) == 0:
                store_ptr(head, _NODE_PREV, node)
            global_store_ptr("py_gc_head", node)
            added = added + 1
        index = index + 1
    free(objects)
    return added


@c_abi_export("pcc_gc_tracked_dematerialize")
def pcc_gc_tracked_dematerialize(acquired: i64) -> None:
    """Release only a successfully acquired view, preserving flags/count.

    Nodes of objects that died meanwhile were already unlinked by untrack.
    Their deferred storage remains owned by the outermost table unlock, not
    by this view: a collector can still hold those dead-node pointers.
    """
    if acquired <= 0:
        return
    if atomic_load_i32(global_addr("pcc_gc_tracking_materialized"), 0, "acquire") == 0:
        return
    node = global_load_ptr("py_gc_head")
    global_store_ptr("py_gc_head", null())
    while ptr_is_null(node) == 0:
        next_node = load_ptr(node, _NODE_NEXT)
        obj = load_ptr(node, _NODE_OBJECT)
        if ptr_is_null(obj) == 0:
            py_gc_index_remove(obj)
        _tracked_node_recycle(node)
        node = next_node
    atomic_store_i32(global_addr("pcc_gc_tracking_materialized"), 0, 0, "release")


@c_abi_export("pcc_gc_tracking_enter_flag_mode")
def pcc_gc_tracking_enter_flag_mode() -> None:
    """Switch once, when GC0 or a count-free nonmoving tracer is fixed.

    Objects tracked earlier (process images register before the collector
    is fixed) keep their flag but lose their nodes now, so no node outlives
    an object whose later death only clears the flag.
    """
    if atomic_load_i32(global_addr("pcc_gc_tracking_flag_mode"), 0, "acquire") != 0:
        return
    pcc_gc_default_table_lock()
    atomic_store_i32(global_addr("pcc_gc_tracking_materialized"), 0, 1, "release")
    pcc_gc_tracked_dematerialize(1)
    # Publish only after old nodes/index entries are gone and materialized is
    # clear. A fast reader's acquire must never see flag mode before teardown.
    atomic_store_i32(global_addr("pcc_gc_tracking_flag_mode"), 0, 1, "release")
    pcc_gc_default_table_unlock()
