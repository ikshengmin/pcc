"""Backend-0 full collection in bounded stops.

A stopped-world full collection builds a node for every tracked container,
so its stop grows with the heap. This cycle does the same discovery across
many short stops and leaves the decision to a young collection:

1. build: a cursor over the allocator's cells gives every old (not young)
   tracked container a node in the tracked index, with its reference count;
2. subtract: every node's object subtracts the references it holds to other
   nodes, so a node keeps the count of its referrers outside the view;
3. mark: nodes with outside referrers, and what they reach, are reached;
4. sweep: the nodes leave the index and the unreached objects are kept as
   candidates;
5. demote: the candidates become young again, in one stop, and the caller
   runs a young collection, which decides exactly among them.

The world runs between two stops, so the counts and edges the cycle read
may be stale. That only changes which objects become candidates: a young
collection frees an object only when every reference to it comes from its
own view, whatever put the object there. A resumed interior scan stores an
ordinal, not an address in a container: insertions or removals may change
the approximate discovery, not the final decision. A stable unreachable
component still has every slot examined; a mutator cannot change it without
acquiring a reference to it first.

A node of this cycle carries _FULL_VIEW and belongs to no stopped-world
view. It lives in a block and is never recycled: untrack clears its object
(py_gc_untrack) and the passes skip it. A caller that needs a complete
stopped-world view first makes the cycle give up its nodes
(pcc_gc_backend0_full_abort).

The passes run in one of two ways. In a stop (pcc_gc_backend0_full_stop) a
mutator owns the stopped world and the tracked table. On the collector
thread (pcc_gc_backend0_full_worker_main, the concurrent feature of the
former GC2) the world runs, and the thread follows what mutators already
obey: it holds the object-graph lock for a batch, which structural
container changes take; it scans a list or an instance under that object's
container lock; it reaches an object only from its node under the node's
shard lock, which the object's untrack takes before the object can be
freed, and keeps it alive with a reference while it scans it. It never
dereferences a child. The runtime's roots and the hand-over to the young
generation still take a stop. Mutators assist with a stop of their own
when the work done is behind what their allocation asks for
(pcc_gc_backend0_full_step).
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    atomic_cas_i32,
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    atomic_store_i64,
    free,
    function_addr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memmove,
    null,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_platform_monotonic_us = extern("pcc_platform_monotonic_us", (), c_int64)
pcc_gc_record_collection_stw_pause = extern(
    "pcc_gc_record_collection_stw_pause", (c_int64, c_int64), c_void
)
pcc_gc_default_table_lock = extern("pcc_gc_default_table_lock", (), c_void)
pcc_gc_default_table_unlock = extern("pcc_gc_default_table_unlock", (), c_void)
py_gc_index_find = extern("py_gc_index_find", (c_ptr,), c_ptr)
py_gc_index_insert = extern("py_gc_index_insert", (c_ptr, c_ptr), c_int64)
py_gc_index_remove = extern("py_gc_index_remove", (c_ptr,), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_visit_object_slots_slice = extern(
    "pcc_gc_visit_object_slots_slice",
    (c_ptr, c_int64, c_int64, c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_backend0_mark_runtime_roots = extern(
    "pcc_gc_backend0_mark_runtime_roots", (), c_void
)
pcc_allocator_object_cells_enumerate_slice = extern(
    "pcc_allocator_object_cells_enumerate_slice",
    (c_ptr, c_int64, c_int64, c_int64, c_ptr, c_int64), c_int64
)
pcc_gc_managed_pointer_index_enumerate_slice = extern(
    "pcc_gc_managed_pointer_index_enumerate_slice",
    (c_ptr, c_int64, c_int64, c_int64, c_ptr, c_int64), c_int64
)
pcc_gc_granule_is_object_start = extern(
    "pcc_gc_granule_is_object_start", (c_ptr,), c_int64
)
pcc_gc_managed_pointer_index_contains = extern(
    "pcc_gc_managed_pointer_index_contains", (c_ptr,), c_int64
)
pcc_gc_young_log_note = extern("pcc_gc_young_log_note", (c_ptr,), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_container_lock = extern("pcc_container_lock", (c_ptr,), c_void)
pcc_container_unlock = extern("pcc_container_unlock", (c_ptr,), c_void)
pcc_gc_tracked_shard_index = extern("pcc_gc_tracked_shard_index", (c_ptr,), c_int64)
pcc_gc_tracked_shard_lock = extern("pcc_gc_tracked_shard_lock", (c_int64,), c_void)
pcc_gc_tracked_shard_unlock = extern("pcc_gc_tracked_shard_unlock", (c_int64,), c_void)
pcc_allocator_lock_acquire = extern("pcc_allocator_lock_acquire", (), c_void)
pcc_allocator_lock_release = extern("pcc_allocator_lock_release", (), c_void)
pcc_allocator_object_side_slot = extern(
    "pcc_allocator_object_side_slot", (c_ptr, c_int64), c_ptr
)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_thread_start = extern("pcc_thread_start", (c_ptr, c_ptr, c_ptr), c_int64)
pcc_thread_detach = extern("pcc_thread_detach", (c_ptr,), c_void)
pcc_thread_safepoint_gate = extern("pcc_thread_safepoint_gate", (), c_void)
pcc_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
py_decref = extern("py_decref", (c_ptr,), c_void)


# The tracked node of freestanding_gc_tracking.py.
_NODE_OBJECT: i64 = 0
_NODE_GC_REFS: i64 = 8
_NODE_BITS: i64 = 16
_NODE_SHARD: i64 = 20
_NODE_PREV: i64 = 24
# Full-view nodes are not in the tracked list. Its next-link word belongs
# to the gray worklist; untrack only clears _NODE_OBJECT on a full-view node.
_NODE_GRAY_NEXT: i64 = 32
_NODE_BYTES: i64 = 40
# Node bits of this cycle; the stopped-world collector owns bits 1 to 32.
_FULL_VIEW: i64 = 64
_FULL_REACHED: i64 = 128

_IMMORTAL: i64 = 1
_TRACKED: i64 = 2
_PINNED: i64 = 64

# A block of nodes: the next block, the nodes in use, then the nodes.
_BLOCK_NEXT: i64 = 0
_BLOCK_COUNT: i64 = 8
_BLOCK_FIRST: i64 = 16
_BLOCK_NODES: i64 = 1020
_BLOCK_BYTES: i64 = 40816

# The cycle's state (pcc_gc_full_state).
_STATE_BLOCKS: i64 = 0          # first block; the sweep frees from here
_STATE_TAIL: i64 = 8            # block the build fills
_STATE_BLOCK: i64 = 16          # position of the current pass: its block
_STATE_INDEX: i64 = 24          # and the next node of that block
_STATE_CELLS: i64 = 32          # cursor over object cells (four words)
_STATE_CELLS_DONE: i64 = 56
_STATE_LARGE: i64 = 64          # cursor over large objects (three words)
_STATE_LARGE_DONE: i64 = 80
_STATE_GRAY: i64 = 88           # head of reached but unscanned block-owned nodes
_STATE_GRAY_COUNT: i64 = 96
_STATE_CANDIDATES: i64 = 112    # unreached objects, as addresses only
_STATE_CANDIDATE_COUNT: i64 = 120
_STATE_CANDIDATE_CAPACITY: i64 = 128
_STATE_ROOTS_SCANNED: i64 = 136
_STATE_DISCARD: i64 = 144       # release the view without candidates
_STATE_SLOT_CURSOR: i64 = 152   # an ordinal in the current object's current layout
_STATE_MARK_NODE: i64 = 160     # block-owned node, never a retained raw slot
_STATE_BYTES: i64 = 168

_PHASE_BUILD: i64 = 1
_PHASE_SUBTRACT: i64 = 2
_PHASE_MARK: i64 = 3
_PHASE_SWEEP: i64 = 4
_PHASE_DEMOTE: i64 = 5

_BATCH: i64 = 64
_SLOT_BATCH: i64 = 128
_SLOT_DONE: i64 = -1
_SLOT_SLICE_CURSOR: i64 = 0
_SLOT_SLICE_BYTES: i64 = 16
# Outcome of a pass: out of time, complete, or at a step that needs the
# world stopped (a pass on the collector thread only).
_PASS_MORE: i64 = 0
_PASS_DONE: i64 = 1
_PASS_NEEDS_STOP: i64 = 2
# The work one allocation tick asks of the cycle (1020 tracked containers,
# sixteen node visits each): a cycle of four passes then completes while
# the tracked population turns over by a quarter.
_PACE_UNITS: i64 = 16320


@c_abi_export("pcc_gc_backend0_full_work")
def _work(units: i64) -> None:
    atomic_rmw_i64("add", global_addr("pcc_gc_full_work_done"), 0, units, "relaxed")


@c_abi_export("pcc_gc_backend0_full_find")
def _find(child, concurrent: i64):
    """The node of ``child``, or NULL. With the world running a cell's side
    word is read as it stands (its node's memory outlives the cycle's
    passes), and a hash entry under its shard's lock, which every untrack
    that changes that table holds."""
    if concurrent == 0:
        return py_gc_index_find(child)
    side = pcc_allocator_object_side_slot(child, 0)
    if ptr_is_null(side) == 0:
        return int_to_ptr(atomic_load_i64(side, 0, "acquire"))
    if pcc_gc_granule_is_object_start(child) == 1:
        # A cell of a slab without side words: none of its cells has a node.
        return null()
    shard: i64 = pcc_gc_tracked_shard_index(child)
    pcc_gc_tracked_shard_lock(shard)
    node = py_gc_index_find(child)
    pcc_gc_tracked_shard_unlock(shard)
    return node


@c_abi_export("pcc_gc_backend0_full_hold")
def _hold(node, obj) -> i64:
    """Keep ``obj`` alive for a scan with the world running. 0: it is gone
    or its release has begun. 1: this thread took a reference. 2: it is
    immortal. While the node still names the object under this lock, its
    untrack has not passed and its memory is its own."""
    shard: i64 = pcc_gc_tracked_shard_index(obj)
    pcc_gc_tracked_shard_lock(shard)
    held: i64 = 0
    if ptr_eq(load_ptr(node, _NODE_OBJECT), obj) != 0:
        if (load_i32(obj, 12) & _IMMORTAL) != 0:
            held = 2
        else:
            count: i64 = atomic_load_i64(obj, 0, "acquire")
            trying: i64 = 1
            while trying != 0:
                if count <= 0:
                    trying = 0
                else:
                    observed: i64 = atomic_cas_i64(
                        obj, 0, count, count + 1, "acq_rel", "acquire"
                    )
                    if observed == count:
                        held = 1
                        trying = 0
                    else:
                        count = observed
    pcc_gc_tracked_shard_unlock(shard)
    return held


@c_abi_export("pcc_gc_backend0_full_queue_release")
def _queue_release(obj) -> None:
    cell = malloc(16)
    if ptr_is_null(cell) != 0:
        py_decref(obj)
        return
    store_ptr(cell, 8, obj)
    head_slot = global_addr("pcc_gc_full_release_head")
    head: i64 = atomic_load_i64(head_slot, 0, "acquire")
    published: i64 = 0
    while published == 0:
        store_i64(cell, 0, head)
        observed: i64 = atomic_cas_i64(
            head_slot, 0, head, ptr_diff(cell, null()), "acq_rel", "acquire"
        )
        if observed == head:
            published = 1
        else:
            head = observed
    atomic_store_i32(global_addr("pcc_gc_backend4_auto_step_pending"), 0, 1, "release")


@c_abi_export("pcc_gc_backend0_full_release")
def _release(obj) -> None:
    """Give back _hold's reference. When it became the last one, a mutator
    releases it (pcc_gc_backend0_full_drain_releases): that may run a
    finalizer, which is the program's code."""
    count: i64 = atomic_load_i64(obj, 0, "acquire")
    trying: i64 = 1
    while trying != 0:
        if count <= 1:
            trying = 0
            _queue_release(obj)
        else:
            observed: i64 = atomic_cas_i64(
                obj, 0, count, count - 1, "acq_rel", "acquire"
            )
            if observed == count:
                trying = 0
            else:
                count = observed


@c_abi_export("pcc_gc_backend0_full_drain_releases")
def pcc_gc_backend0_full_drain_releases() -> i64:
    """Release, on the calling mutator, the references the collector thread
    left behind; the number released."""
    cell = int_to_ptr(
        atomic_rmw_i64("xchg", global_addr("pcc_gc_full_release_head"), 0, 0, "acq_rel")
    )
    released: i64 = 0
    while ptr_is_null(cell) == 0:
        following = load_ptr(cell, 0)
        py_decref(load_ptr(cell, 8))
        free(cell)
        cell = following
        released = released + 1
    return released


@c_abi_export("pcc_gc_backend0_full_time_up")
def _time_up(deadline: i64) -> i64:
    if pcc_platform_monotonic_us() >= deadline:
        return 1
    return 0


@c_abi_export("pcc_gc_backend0_full_enter")
def _enter(state, phase: i64) -> None:
    store_ptr(state, _STATE_BLOCK, load_ptr(state, _STATE_BLOCKS))
    store_i64(state, _STATE_INDEX, 0)
    store_i64(state, _STATE_SLOT_CURSOR, 0)
    store_ptr(state, _STATE_MARK_NODE, null())
    if phase == _PHASE_SWEEP:
        store_ptr(state, _STATE_GRAY, null())
        store_i64(state, _STATE_GRAY_COUNT, 0)
    store_i32(global_addr("pcc_gc_full_phase"), 0, phase)


@c_abi_export("pcc_gc_backend0_full_push")
def _push_candidate(state, item) -> None:
    """Append a candidate; without memory the cycle releases its view."""
    items_offset: i64 = _STATE_CANDIDATES
    count: i64 = load_i64(state, items_offset + 8)
    capacity: i64 = load_i64(state, items_offset + 16)
    items = load_ptr(state, items_offset)
    if count == capacity:
        wider: i64 = 1024
        if capacity > 0:
            wider = capacity * 2
        grown = malloc(wider * 8)
        if ptr_is_null(grown) != 0:
            store_i64(state, _STATE_DISCARD, 1)
            return
        if ptr_is_null(items) == 0:
            memmove(grown, items, count * 8)
            free(items)
        items = grown
        store_ptr(state, items_offset, items)
        store_i64(state, items_offset + 16, wider)
    store_ptr(items, count * 8, item)
    store_i64(state, items_offset + 8, count + 1)


@c_abi_export("pcc_gc_backend0_full_add")
def _add(state, obj) -> None:
    block = load_ptr(state, _STATE_TAIL)
    if ptr_is_null(block) != 0 or load_i64(block, _BLOCK_COUNT) >= _BLOCK_NODES:
        fresh = malloc(_BLOCK_BYTES)
        if ptr_is_null(fresh) != 0:
            store_i64(state, _STATE_DISCARD, 1)
            return
        store_ptr(fresh, _BLOCK_NEXT, null())
        store_i64(fresh, _BLOCK_COUNT, 0)
        if ptr_is_null(block) != 0:
            store_ptr(state, _STATE_BLOCKS, fresh)
        else:
            store_ptr(block, _BLOCK_NEXT, fresh)
        store_ptr(state, _STATE_TAIL, fresh)
        block = fresh
    used: i64 = load_i64(block, _BLOCK_COUNT)
    node = ptr_add(block, _BLOCK_FIRST + used * _NODE_BYTES)
    store_ptr(node, _NODE_OBJECT, obj)
    store_i64(node, _NODE_GC_REFS, load_i64(obj, 0))
    store_i32(node, _NODE_BITS, _FULL_VIEW)
    store_i32(node, _NODE_SHARD, 0)
    store_ptr(node, _NODE_PREV, null())
    store_ptr(node, _NODE_GRAY_NEXT, null())
    # 0: the object has a node already (the cell cursor started over on a
    # grown table). Below 0: no index entry could be made. Either way the
    # slot is reused and the object stays an outside referrer.
    if py_gc_index_insert(obj, node) == 1:
        store_i64(block, _BLOCK_COUNT, used + 1)


@c_abi_export("pcc_gc_backend0_full_build")
def _build(state, deadline: i64, concurrent: i64) -> i64:
    """Give the next old tracked containers a node."""
    batch = stack_alloc(_BATCH * 8)
    young: i64 = load_i32(global_addr("pcc_gc_tracking_young_bit"), 0)
    while load_i64(state, _STATE_DISCARD) == 0:
        found: i64 = 0
        if load_i64(state, _STATE_CELLS_DONE) == 0:
            if concurrent != 0:
                # A slab binds under the allocator's lock: the table and
                # its spans hold still for this part of the walk.
                pcc_allocator_lock_acquire()
            found = pcc_allocator_object_cells_enumerate_slice(
                batch, _BATCH, _TRACKED, young, ptr_add(state, _STATE_CELLS), 512
            )
            if concurrent != 0:
                pcc_allocator_lock_release()
        elif load_i64(state, _STATE_LARGE_DONE) == 0:
            if concurrent != 0:
                # The large-object index changes under no lock this thread
                # may take.
                return _PASS_NEEDS_STOP
            found = pcc_gc_managed_pointer_index_enumerate_slice(
                batch, _BATCH, _TRACKED, young, ptr_add(state, _STATE_LARGE), 512
            )
        else:
            return _PASS_DONE
        index: i64 = 0
        while index < found:
            obj = load_ptr(batch, index * 8)
            if concurrent != 0:
                # The cell may have died since the walk saw it. Its untrack
                # takes this lock, and a new object in the cell is young.
                shard: i64 = pcc_gc_tracked_shard_index(obj)
                pcc_gc_tracked_shard_lock(shard)
                flags: i64 = load_i32(obj, 12)
                if (
                    pcc_gc_granule_is_object_start(obj) == 1
                    and (flags & _TRACKED) != 0
                    and (flags & young) == 0
                ):
                    _add(state, obj)
                pcc_gc_tracked_shard_unlock(shard)
            else:
                _add(state, obj)
            index = index + 1
        _work(found + 8)
        if _time_up(deadline) != 0:
            return _PASS_MORE
    return _PASS_DONE


@c_abi_export("pcc_gc_backend0_full_subtract_slot")
def _subtract_slot(slot, role: i64, context) -> None:
    if role == 3:  # borrowed update-only metadata is not a graph edge
        return
    child = pcc_gc_load_ptr(null(), slot)
    if ptr_is_null(child) != 0 or is_tagged_int(child) != 0:
        return
    concurrent: i64 = 1
    if ptr_is_null(context) != 0:
        concurrent = 0
    node = _find(child, concurrent)
    if ptr_is_null(node) == 0 and (load_i32(node, _NODE_BITS) & _FULL_VIEW) != 0:
        store_i64(node, _NODE_GC_REFS, load_i64(node, _NODE_GC_REFS) - 1)


@c_abi_export("pcc_gc_backend0_full_scan")
def _scan(state, node, cursor: i64, marking: i64, concurrent: i64) -> i64:
    """Visit one slice of the node's current slots; return the next ordinal
    or _SLOT_DONE. Reacquire all object ownership for each slice."""
    obj = load_ptr(node, _NODE_OBJECT)
    if ptr_is_null(obj) != 0:
        return _SLOT_DONE
    context = null()
    held: i64 = 0
    locked: i64 = 0
    if concurrent != 0:
        held = _hold(node, obj)
        if held == 0:
            # Unscanned references remain outside referrers.
            return _SLOT_DONE
        context = state
        tag: i64 = load_i32(obj, 8)
        if pcc_capi_is_cext_type_tag(tag) != 0:
            if held == 1:
                _release(obj)
            return _SLOT_DONE
        if (
            tag == abi_constant("object.type.list")
            or tag == abi_constant("object.type.instance")
            or tag >= abi_constant("object.type.user_class_start")
        ):
            pcc_container_lock(obj)
            locked = 1

    visitor = function_addr("pcc_gc_backend0_full_subtract_slot")
    if marking != 0:
        visitor = function_addr("pcc_gc_backend0_full_mark_slot")
    slice_out = stack_alloc(_SLOT_SLICE_BYTES)
    handled: i64 = pcc_gc_visit_object_slots_slice(
        obj, cursor, _SLOT_BATCH, visitor, context, slice_out
    )
    following: i64 = _SLOT_DONE
    if handled != 0:
        following = load_i64(slice_out, _SLOT_SLICE_CURSOR)
    elif concurrent == 0:
        # A C extension's traverse callback has no resumable ABI. Keep its
        # existing stopped traversal; do not run it on the background worker.
        pcc_gc_visit_object_slots(obj, visitor, null())
    if locked != 0:
        pcc_container_unlock(obj)
    if held == 1:
        _release(obj)
    return following


@c_abi_export("pcc_gc_backend0_full_subtract")
def _subtract(state, deadline: i64, concurrent: i64) -> i64:
    block = load_ptr(state, _STATE_BLOCK)
    index: i64 = load_i64(state, _STATE_INDEX)
    units: i64 = 0
    while ptr_is_null(block) == 0:
        count: i64 = load_i64(block, _BLOCK_COUNT)
        while index < count:
            cursor: i64 = load_i64(state, _STATE_SLOT_CURSOR)
            next_slot: i64 = _scan(
                state, ptr_add(block, _BLOCK_FIRST + index * _NODE_BYTES),
                cursor, 0, concurrent
            )
            if next_slot == _SLOT_DONE:
                store_i64(state, _STATE_SLOT_CURSOR, 0)
                index = index + 1
            else:
                store_i64(state, _STATE_SLOT_CURSOR, next_slot)
            units = units + 1
            if (units & 31) == 0:
                _work(32)
            if (next_slot != _SLOT_DONE or (units & 31) == 0) and _time_up(deadline) != 0:
                _work(units & 31)
                store_ptr(state, _STATE_BLOCK, block)
                store_i64(state, _STATE_INDEX, index)
                return _PASS_MORE
        block = load_ptr(block, _BLOCK_NEXT)
        index = 0
    _work(units & 31)
    return _PASS_DONE


@c_abi_export("pcc_gc_backend0_full_reach")
def _reach(state, node) -> None:
    store_i32(node, _NODE_BITS, load_i32(node, _NODE_BITS) | _FULL_REACHED)
    # The node is already owned by a cycle block. One link per reached
    # node suffices, even when a single object reaches the entire heap.
    store_ptr(node, _NODE_GRAY_NEXT, load_ptr(state, _STATE_GRAY))
    store_ptr(state, _STATE_GRAY, node)
    store_i64(state, _STATE_GRAY_COUNT, load_i64(state, _STATE_GRAY_COUNT) + 1)


@c_abi_export("pcc_gc_backend0_full_mark_child")
def _mark_child(o, concurrent: i64) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    state = global_load_ptr("pcc_gc_full_state")
    if ptr_is_null(state) != 0:
        return
    node = _find(o, concurrent)
    if ptr_is_null(node) != 0:
        return
    bits: i64 = load_i32(node, _NODE_BITS)
    if (bits & _FULL_VIEW) != 0 and (bits & _FULL_REACHED) == 0:
        _reach(state, node)


@c_abi_export("pcc_gc_backend0_full_mark")
def pcc_gc_backend0_full_mark(o) -> None:
    """Reach ``o`` when it is an unreached node of the cycle. The root
    visitors arrive here through pcc_gc_backend0_mark_reachable while
    pcc_gc_full_marking is set, in a stop."""
    _mark_child(o, 0)


@c_abi_export("pcc_gc_backend0_full_mark_slot")
def _mark_slot(slot, role: i64, context) -> None:
    if role == 3:
        return
    concurrent: i64 = 1
    if ptr_is_null(context) != 0:
        concurrent = 0
    _mark_child(pcc_gc_load_ptr(null(), slot), concurrent)


@c_abi_export("pcc_gc_backend0_full_rooted")
def _rooted(node, obj, concurrent: i64) -> i64:
    """1 when the node's object has a referrer outside the view. A count of
    zero belongs to a release that stopped before its untrack: its fields
    still own references. With the world running the object is read under
    its shard's lock, while the node still names it."""
    shard: i64 = 0
    if concurrent != 0:
        shard = pcc_gc_tracked_shard_index(obj)
        pcc_gc_tracked_shard_lock(shard)
    rooted: i64 = 0
    if ptr_eq(load_ptr(node, _NODE_OBJECT), obj) != 0:
        if (
            load_i64(node, _NODE_GC_REFS) > 0
            or load_i64(obj, 0) == 0
            or (load_i32(obj, 12) & _PINNED) != 0
        ):
            rooted = 1
    if concurrent != 0:
        pcc_gc_tracked_shard_unlock(shard)
    return rooted


@c_abi_export("pcc_gc_backend0_full_mark_pass")
def _mark(state, deadline: i64, concurrent: i64) -> i64:
    """Reach the nodes with outside referrers and what they reach. One unit
    scans one gray node or examines one node as a root; the runtime's own
    roots follow once, in a stop, when both are exhausted."""
    outcome: i64 = -1
    units: i64 = 0
    while outcome < 0:
        gray: i64 = load_i64(state, _STATE_GRAY_COUNT)
        block = load_ptr(state, _STATE_BLOCK)
        scanning = load_ptr(state, _STATE_MARK_NODE)
        next_slot: i64 = _SLOT_DONE
        if ptr_is_null(scanning) == 0 or gray > 0:
            if ptr_is_null(scanning) != 0:
                gray = gray - 1
                store_i64(state, _STATE_GRAY_COUNT, gray)
                scanning = load_ptr(state, _STATE_GRAY)
                store_ptr(state, _STATE_GRAY, load_ptr(scanning, _NODE_GRAY_NEXT))
            next_slot = _scan(state, scanning, load_i64(state, _STATE_SLOT_CURSOR), 1, concurrent)
            if next_slot == _SLOT_DONE:
                store_ptr(state, _STATE_MARK_NODE, null())
                store_i64(state, _STATE_SLOT_CURSOR, 0)
            else:
                store_ptr(state, _STATE_MARK_NODE, scanning)
                store_i64(state, _STATE_SLOT_CURSOR, next_slot)
        elif ptr_is_null(block) == 0:
            index: i64 = load_i64(state, _STATE_INDEX)
            if index < load_i64(block, _BLOCK_COUNT):
                node = ptr_add(block, _BLOCK_FIRST + index * _NODE_BYTES)
                obj = load_ptr(node, _NODE_OBJECT)
                if ptr_is_null(obj) == 0 and (load_i32(node, _NODE_BITS) & _FULL_REACHED) == 0:
                    if _rooted(node, obj, concurrent) != 0:
                        _reach(state, node)
                store_i64(state, _STATE_INDEX, index + 1)
            else:
                store_ptr(state, _STATE_BLOCK, load_ptr(block, _BLOCK_NEXT))
                store_i64(state, _STATE_INDEX, 0)
        elif load_i64(state, _STATE_ROOTS_SCANNED) == 0:
            if concurrent != 0:
                # Stacks and frames are read with their threads stopped.
                outcome = _PASS_NEEDS_STOP
            else:
                store_i64(state, _STATE_ROOTS_SCANNED, 1)
                atomic_store_i32(global_addr("pcc_gc_full_marking"), 0, 1, "release")
                pcc_gc_backend0_mark_runtime_roots()
                atomic_store_i32(global_addr("pcc_gc_full_marking"), 0, 0, "release")
        else:
            outcome = _PASS_DONE
        if load_i64(state, _STATE_DISCARD) != 0:
            outcome = _PASS_DONE
        units = units + 1
        if (units & 31) == 0:
            _work(32)
        if outcome < 0 and (next_slot != _SLOT_DONE or (units & 31) == 0):
            if _time_up(deadline) != 0:
                outcome = _PASS_MORE
    _work(units & 31)
    return outcome


@c_abi_export("pcc_gc_backend0_full_sweep")
def _sweep(state, deadline: i64, concurrent: i64) -> i64:
    """Take the next nodes out of the index and keep the unreached objects.
    The view is gone when the pass is done. With the world running, a
    node leaves the index under its shard's lock, so no untrack still holds
    it when its block is freed."""
    block = load_ptr(state, _STATE_BLOCKS)
    index: i64 = load_i64(state, _STATE_INDEX)
    units: i64 = 0
    while ptr_is_null(block) == 0:
        count: i64 = load_i64(block, _BLOCK_COUNT)
        while index < count:
            node = ptr_add(block, _BLOCK_FIRST + index * _NODE_BYTES)
            obj = load_ptr(node, _NODE_OBJECT)
            if ptr_is_null(obj) == 0:
                live: i64 = 1
                if concurrent != 0:
                    shard: i64 = pcc_gc_tracked_shard_index(obj)
                    pcc_gc_tracked_shard_lock(shard)
                    if ptr_eq(load_ptr(node, _NODE_OBJECT), obj) != 0:
                        py_gc_index_remove(obj)
                        store_ptr(node, _NODE_OBJECT, null())
                    else:
                        live = 0
                    pcc_gc_tracked_shard_unlock(shard)
                else:
                    py_gc_index_remove(obj)
                    store_ptr(node, _NODE_OBJECT, null())
                if (
                    live != 0
                    and (load_i32(node, _NODE_BITS) & _FULL_REACHED) == 0
                    and load_i64(state, _STATE_DISCARD) == 0
                ):
                    _push_candidate(state, obj)
            index = index + 1
            units = units + 1
            if (units & 31) == 0:
                _work(32)
                if _time_up(deadline) != 0:
                    store_i64(state, _STATE_INDEX, index)
                    return _PASS_MORE
        following = load_ptr(block, _BLOCK_NEXT)
        free(block)
        block = following
        index = 0
        store_ptr(state, _STATE_BLOCKS, block)
        store_i64(state, _STATE_INDEX, 0)
    store_ptr(state, _STATE_TAIL, null())
    store_ptr(state, _STATE_BLOCK, null())
    atomic_store_i32(global_addr("pcc_gc_full_view_active"), 0, 0, "release")
    _work(units & 31)
    return _PASS_DONE


@c_abi_export("pcc_gc_backend0_full_demote")
def _demote(state) -> i64:
    """Make every candidate young again, all in this stop: a young
    collection that saw only part of a dead cycle would keep all of it. A
    candidate is an address: it counts only while it is a live tracked
    object, as an entry of the young log does."""
    if load_i64(state, _STATE_DISCARD) != 0:
        return 0
    young: i64 = load_i32(global_addr("pcc_gc_tracking_young_bit"), 0)
    items = load_ptr(state, _STATE_CANDIDATES)
    count: i64 = load_i64(state, _STATE_CANDIDATE_COUNT)
    demoted: i64 = 0
    index: i64 = 0
    while index < count:
        obj = load_ptr(items, index * 8)
        live: i64 = 0
        if pcc_gc_granule_is_object_start(obj) == 1:
            live = 1
        elif pcc_gc_managed_pointer_index_contains(obj) != 0:
            live = 1
        if live != 0:
            flags: i64 = load_i32(obj, 12)
            if (flags & _TRACKED) != 0 and (flags & young) == 0:
                atomic_rmw_i32("or", obj, 12, young, "acq_rel")
                pcc_gc_young_log_note(obj)
                demoted = demoted + 1
        index = index + 1
    atomic_rmw_i64(
        "add", global_addr("pcc_gc_full_candidates_found"), 0, demoted, "relaxed"
    )
    return demoted


@c_abi_export("pcc_gc_backend0_full_finish")
def _finish(state) -> None:
    # Gray links belong to the node blocks, discarded during sweep/abort.
    candidates = load_ptr(state, _STATE_CANDIDATES)
    if ptr_is_null(candidates) == 0:
        free(candidates)
    free(state)
    global_store_ptr("pcc_gc_full_state", null())
    atomic_store_i32(global_addr("pcc_gc_full_marking"), 0, 0, "release")
    atomic_store_i32(global_addr("pcc_gc_full_view_active"), 0, 0, "release")
    store_i32(global_addr("pcc_gc_full_phase"), 0, 0)


@c_abi_export("pcc_gc_backend0_full_abort")
def pcc_gc_backend0_full_abort() -> None:
    """Give up the cycle and its nodes. The caller has stopped the world
    and holds the tracked table: it builds a complete view next, which
    needs every tracked object as a node of its own."""
    state = global_load_ptr("pcc_gc_full_state")
    if ptr_is_null(state) != 0:
        return
    block = load_ptr(state, _STATE_BLOCKS)
    while ptr_is_null(block) == 0:
        count: i64 = load_i64(block, _BLOCK_COUNT)
        index: i64 = 0
        while index < count:
            obj = load_ptr(ptr_add(block, _BLOCK_FIRST + index * _NODE_BYTES), _NODE_OBJECT)
            if ptr_is_null(obj) == 0:
                py_gc_index_remove(obj)
            index = index + 1
        following = load_ptr(block, _BLOCK_NEXT)
        free(block)
        block = following
    _finish(state)


@c_abi_export("pcc_gc_backend0_full_start")
def _start() -> None:
    if load_i32(global_addr("pcc_gc_tracking_flag_mode"), 0) == 0:
        return
    if load_i32(global_addr("pcc_gc_tracking_young_bit"), 0) == 0:
        return
    state = malloc(_STATE_BYTES)
    if ptr_is_null(state) != 0:
        return
    offset: i64 = 0
    while offset < _STATE_BYTES:
        store_i64(state, offset, 0)
        offset = offset + 8
    global_store_ptr("pcc_gc_full_state", state)
    atomic_store_i64(global_addr("pcc_gc_full_work_done"), 0, 0, "relaxed")
    atomic_store_i64(global_addr("pcc_gc_full_work_due"), 0, 0, "relaxed")
    # No node exists yet, and a thread decides how it untracks without a
    # safepoint in between: every untrack after this stop sees the view.
    atomic_store_i32(global_addr("pcc_gc_full_view_active"), 0, 1, "release")
    store_i32(global_addr("pcc_gc_full_phase"), 0, _PHASE_BUILD)


@c_abi_export("pcc_gc_backend0_full_advance")
def _advance(deadline: i64, concurrent: i64) -> i64:
    """Work on the cycle until ``deadline``. 1: it advanced. 2: its
    candidates were handed to the young generation and it ended. 3 (on the
    collector thread only): its next step needs the world stopped."""
    state = global_load_ptr("pcc_gc_full_state")
    atomic_rmw_i64("add", global_addr("pcc_gc_full_slices"), 0, 1, "relaxed")
    if (
        load_i32(global_addr("py_gc_enabled"), 0) == 0
        or load_i32(global_addr("py_gc_threshold0"), 0) <= 0
    ):
        # Automatic collection is off: no candidate may reach a collection.
        store_i64(state, _STATE_DISCARD, 1)
    running: i64 = 1
    while running != 0:
        phase: i64 = load_i32(global_addr("pcc_gc_full_phase"), 0)
        if load_i64(state, _STATE_DISCARD) != 0 and phase < _PHASE_SWEEP:
            phase = _PHASE_SWEEP
            _enter(state, phase)
        advanced: i64 = _PASS_MORE
        if phase == _PHASE_BUILD:
            advanced = _build(state, deadline, concurrent)
        elif phase == _PHASE_SUBTRACT:
            advanced = _subtract(state, deadline, concurrent)
        elif phase == _PHASE_MARK:
            advanced = _mark(state, deadline, concurrent)
        elif phase == _PHASE_SWEEP:
            advanced = _sweep(state, deadline, concurrent)
        elif concurrent != 0:
            # Every candidate becomes young in one stop.
            return 3
        else:
            _demote(state)
            discarded: i64 = load_i64(state, _STATE_DISCARD)
            _finish(state)
            if discarded != 0:
                return 1
            atomic_rmw_i64("add", global_addr("pcc_gc_full_cycles"), 0, 1, "relaxed")
            # A mutator runs the young collection that decides among them.
            atomic_store_i32(global_addr("pcc_gc_full_finished"), 0, 1, "release")
            atomic_store_i32(
                global_addr("pcc_gc_backend4_auto_step_pending"), 0, 1, "release"
            )
            return 2
        if advanced == _PASS_MORE:
            running = 0
        elif advanced == _PASS_NEEDS_STOP:
            return 3
        else:
            if load_i64(state, _STATE_DISCARD) != 0 and phase < _PHASE_SWEEP:
                phase = _PHASE_SWEEP - 1
            _enter(state, phase + 1)
            if phase + 1 < _PHASE_DEMOTE and _time_up(deadline) != 0:
                running = 0
    return 1


@c_abi_export("pcc_gc_backend0_full_stop")
def _stop(begin: i64) -> i64:
    stw: i64 = pcc_stop_the_world()
    while stw != 0:
        pcc_thread_safepoint()
        stw = pcc_stop_the_world()
    start: i64 = pcc_platform_monotonic_us()
    result: i64 = 0
    # A collection whose finalizers run (world resumed, py_gc_collecting
    # set) owns the tracked table's nodes: the cycle waits for its end.
    if (
        load_i32(global_addr("py_gc_collecting"), 0) != 0
        or load_i32(global_addr("pcc_gc_tracking_materialized"), 0) != 0
    ):
        if load_i32(global_addr("pcc_gc_full_phase"), 0) != 0:
            result = 1
        pcc_resume_world()
        return result
    pcc_gc_default_table_lock()
    if begin != 0 and load_i32(global_addr("pcc_gc_full_phase"), 0) == 0:
        _start()
        if load_i32(global_addr("pcc_gc_full_phase"), 0) != 0:
            result = 1
    elif begin == 0 and load_i32(global_addr("pcc_gc_full_phase"), 0) != 0:
        budget: i64 = load_i64(global_addr("pcc_gc_full_slice_us"), 0)
        if budget < 0:
            budget = 0
        result = _advance(start + budget, 0)
    pcc_gc_default_table_unlock()
    pcc_gc_record_collection_stw_pause(start, pcc_platform_monotonic_us())
    pcc_resume_world()
    return result


@c_abi_export("pcc_gc_backend0_full_concurrent_batch")
def _concurrent_batch() -> i64:
    """One batch of the cycle with the world running, under the
    object-graph lock. Its holder cannot be stopped, so no collection
    begins, no stop of this cycle runs and nothing aborts it meanwhile.
    0: a stopped-world collection owns the tracked nodes, or no cycle
    runs; otherwise _advance's result."""
    pcc_py_gc_minor_graph_lock()
    outcome: i64 = 0
    if (
        load_i32(global_addr("pcc_gc_full_phase"), 0) != 0
        and load_i32(global_addr("py_gc_collecting"), 0) == 0
        and load_i32(global_addr("pcc_gc_tracking_materialized"), 0) == 0
    ):
        budget: i64 = load_i64(global_addr("pcc_gc_full_slice_us"), 0)
        if budget < 0:
            budget = 0
        outcome = _advance(pcc_platform_monotonic_us() + budget, 1)
        atomic_rmw_i64(
            "add", global_addr("pcc_gc_full_worker_batches"), 0, 1, "relaxed"
        )
    pcc_py_gc_minor_graph_unlock()
    return outcome


@c_abi_export("pcc_gc_backend0_full_worker_main")
def pcc_gc_backend0_full_worker_main(arg):
    """The collector thread of one cycle. It keeps nothing of the cycle
    between two batches: a stop may advance or abort the cycle while this
    thread is parked. It ends with the cycle, or after two seconds of
    waiting for a collection that owns the tracked nodes; the cycle then
    goes on in the mutators' stops."""
    waited: i64 = 0
    while load_i32(global_addr("pcc_gc_full_phase"), 0) != 0 and waited < 20000:
        outcome: i64 = _concurrent_batch()
        if outcome == 3:
            _stop(0)
            waited = 0
        elif outcome == 0:
            pcc_platform_sleep_ns(100000)
            waited = waited + 1
        else:
            waited = 0
        pcc_thread_safepoint_gate()
    atomic_store_i32(global_addr("pcc_gc_full_worker"), 0, 0, "release")
    return null()


@c_abi_export("pcc_gc_backend0_full_worker_start")
def _worker_start() -> None:
    if load_i32(global_addr("pcc_gc_full_concurrent"), 0) == 0:
        return
    if pcc_threads_enabled() == 0:
        return
    # The thread scans beside mutators: their counts must be atomic and
    # their lists and instances must change under container locks.
    if load_i32(global_addr("pcc_gc_refcount_atomic"), 0) == 0:
        return
    if load_i32(global_addr("pcc_gc_list_container_mode"), 0) == 0:
        return
    if atomic_cas_i32(
        global_addr("pcc_gc_full_worker"), 0, 0, 1, "acq_rel", "acquire"
    ) != 0:
        return
    if pcc_thread_start(
        global_addr("pcc_gc_full_worker_handle"),
        function_addr("pcc_gc_backend0_full_worker_main"),
        null(),
    ) != 0:
        atomic_store_i32(global_addr("pcc_gc_full_worker"), 0, 0, "release")
        return
    pcc_thread_detach(global_load_ptr("pcc_gc_full_worker_handle"))


@c_abi_export("pcc_gc_backend0_full_begin")
def pcc_gc_backend0_full_begin() -> i64:
    """Start a full collection; 1 when one started. Its passes then run in
    bounded stops of the mutators, or on a collector thread with the world
    running when the program is threaded and pcc_gc_full_concurrent is set."""
    if load_i32(global_addr("pcc_gc_full_phase"), 0) != 0:
        return 0
    started: i64 = _stop(1)
    if started != 0:
        _worker_start()
    return started


@c_abi_export("pcc_gc_backend0_full_step")
def pcc_gc_backend0_full_step() -> i64:
    """A mutator's part in the running cycle, once per allocation tick or
    automatic-collection request. 0: no cycle runs. Otherwise it took one
    bounded stop (2 when that ended the cycle), or, with a collector
    thread, none because the work done covers what allocation has asked
    for so far."""
    if load_i32(global_addr("pcc_gc_full_phase"), 0) == 0:
        return 0
    due: i64 = atomic_rmw_i64(
        "add", global_addr("pcc_gc_full_work_due"), 0, _PACE_UNITS, "relaxed"
    ) + _PACE_UNITS
    if atomic_load_i32(global_addr("pcc_gc_full_worker"), 0, "acquire") != 0:
        if atomic_load_i64(global_addr("pcc_gc_full_work_done"), 0, "relaxed") >= due:
            return 1
    return _stop(0)


@c_abi_export("pcc_gc_backend0_full_set_slice_us")
def pcc_gc_backend0_full_set_slice_us(microseconds: i64) -> i64:
    """Set the work budget of one stop or batch; the previous value. With 0
    each does the smallest unit of work of its phase."""
    slot = global_addr("pcc_gc_full_slice_us")
    previous: i64 = load_i64(slot, 0)
    if microseconds >= 0:
        store_i64(slot, 0, microseconds)
    return previous


@c_abi_export("pcc_gc_backend0_full_set_concurrent")
def pcc_gc_backend0_full_set_concurrent(enabled: i64) -> i64:
    """Let the next cycles of a threaded program run on a collector thread
    (1) or in the mutators' stops only (0); the previous value."""
    slot = global_addr("pcc_gc_full_concurrent")
    previous: i64 = load_i32(slot, 0)
    value: i64 = 0
    if enabled != 0:
        value = 1
    store_i32(slot, 0, value)
    return previous


@c_abi_export("pcc_gc_backend0_full_counter")
def pcc_gc_backend0_full_counter(which: i64) -> i64:
    """0: completed cycles. 1: stops and batches. 2: candidates handed to
    young collections. 3: the running cycle's phase. 4: batches of the
    collector thread. 5: 1 while a collector thread lives. 6: work done by
    the running cycle. 7: work its allocation has asked for."""
    if which == 0:
        return load_i64(global_addr("pcc_gc_full_cycles"), 0)
    if which == 1:
        return load_i64(global_addr("pcc_gc_full_slices"), 0)
    if which == 2:
        return load_i64(global_addr("pcc_gc_full_candidates_found"), 0)
    if which == 3:
        return load_i32(global_addr("pcc_gc_full_phase"), 0)
    if which == 4:
        return load_i64(global_addr("pcc_gc_full_worker_batches"), 0)
    if which == 5:
        return atomic_load_i32(global_addr("pcc_gc_full_worker"), 0, "acquire")
    if which == 6:
        return atomic_load_i64(global_addr("pcc_gc_full_work_done"), 0, "relaxed")
    if which == 7:
        return atomic_load_i64(global_addr("pcc_gc_full_work_due"), 0, "relaxed")
    return 0
