"""Stopped, sparse disposal graph for the layered tracing sweep.

Only the sealed candidate vector is indexed. A plan borrows its allocator
nodes until the owning sweep retires that vector. It owns no managed roots.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    free, logical_shift_right_i64, malloc, memcpy, memset, null, ptr_eq, ptr_is_null,
    ptr_to_int, stack_alloc, store_i64, store_ptr, load_i64, load_ptr,
)

__pcc_freestanding__ = True

pcc_thread_world_stop_epoch_held = extern(
    "pcc_thread_world_stop_epoch_held", (), c_int64
)
pcc_gc_tracing_is_sweep_candidate = extern(
    "pcc_gc_tracing_is_sweep_candidate", (c_ptr,), c_int64
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_visit_object_slots_slice = extern(
    "pcc_gc_visit_object_slots_slice",
    (c_ptr, c_int64, c_int64, c_ptr, c_ptr, c_ptr), c_int64
)

_WORD = 8
_ENTRY_KEY = 0
_ENTRY_ID = 8
_ENTRY_BYTES = 16
_VERTEX_INCOMING = 0
_VERTEX_FIRST = 8
_VERTEX_BYTES = 16
_EDGE_CHILD = 0
_EDGE_NEXT = 8
_EDGE_BYTES = 16

_PLAN_KEYS = 0
_PLAN_MASK = 8
_PLAN_VERTICES = 16
_PLAN_EDGES = 24
_PLAN_EDGE_COUNT = 32
_PLAN_EDGE_CAPACITY = 40
_PLAN_NODES = 48
_PLAN_ORDER = 56
_PLAN_OFFSETS = 64
_PLAN_LAYER_COUNT = 72
_PLAN_PEELED = 80
_PLAN_OWNER = 88
_PLAN_INVALID = 96
_PLAN_BYTES = 104

_SLICE_NEXT = 0
_SLICE_EXAMINED = 8
_SLICE_BYTES = 16
_SLICE_SLOTS = 64
_ROLE_OWNED_VALUE = 1
_ROLE_OWNED_TYPE = 2
_ROLE_DERIVED = 3
_MAX_CANDIDATES = 16777216
_MAX_EDGES = 134217728


@c_abi_export("pcc_gc_callback_layers_key_slot")
def _key_slot(keys, mask: i64, key) -> i64:
    bits: i64 = logical_shift_right_i64(ptr_to_int(key), 3)
    bits = (bits ^ logical_shift_right_i64(bits, 17)) * 6364136223846793005
    slot: i64 = (bits ^ logical_shift_right_i64(bits, 32)) & mask
    while True:
        old = load_ptr(keys, slot * _ENTRY_BYTES + _ENTRY_KEY)
        if ptr_is_null(old) != 0 or ptr_eq(old, key) != 0:
            return slot * _ENTRY_BYTES
        slot = (slot + 1) & mask


@c_abi_export("pcc_gc_callback_layers_note_edge")
def _note_edge(slot, role: i64, plan) -> None:
    if role == _ROLE_DERIVED:
        return
    if role != _ROLE_OWNED_VALUE and role != _ROLE_OWNED_TYPE:
        store_i64(plan, _PLAN_INVALID, 1)
        return
    key = load_ptr(slot, 0)
    if ptr_is_null(key) != 0:
        return
    keys = load_ptr(plan, _PLAN_KEYS)
    lookup: i64 = _key_slot(keys, load_i64(plan, _PLAN_MASK), key)
    if ptr_is_null(load_ptr(keys, lookup + _ENTRY_KEY)) != 0:
        return
    count: i64 = load_i64(plan, _PLAN_EDGE_COUNT)
    capacity: i64 = load_i64(plan, _PLAN_EDGE_CAPACITY)
    edges = load_ptr(plan, _PLAN_EDGES)
    if count >= capacity:
        if capacity >= _MAX_EDGES:
            store_i64(plan, _PLAN_INVALID, 1)
            return
        grown: i64 = capacity * 2 if capacity > 0 else 64
        next_edges = malloc(grown * _EDGE_BYTES)
        if ptr_is_null(next_edges) != 0:
            store_i64(plan, _PLAN_INVALID, 1)
            return
        if ptr_is_null(edges) == 0:
            memcpy(next_edges, edges, count * _EDGE_BYTES)
            free(edges)
        store_ptr(plan, _PLAN_EDGES, next_edges)
        store_i64(plan, _PLAN_EDGE_CAPACITY, grown)
        edges = next_edges
    child: i64 = load_i64(keys, lookup + _ENTRY_ID)
    parent: i64 = load_i64(plan, _PLAN_OWNER)
    vertices = load_ptr(plan, _PLAN_VERTICES)
    from_edge: i64 = parent * _VERTEX_BYTES + _VERTEX_FIRST
    incoming: i64 = child * _VERTEX_BYTES + _VERTEX_INCOMING
    store_i64(edges, count * _EDGE_BYTES + _EDGE_CHILD, child)
    store_i64(edges, count * _EDGE_BYTES + _EDGE_NEXT, load_i64(vertices, from_edge))
    store_i64(vertices, from_edge, count + 1)
    store_i64(vertices, incoming, load_i64(vertices, incoming) + 1)
    store_i64(plan, _PLAN_EDGE_COUNT, count + 1)


@c_abi_export("pcc_gc_callback_layers_release")
def pcc_gc_callback_layers_release(plan) -> None:
    if ptr_is_null(plan) != 0:
        return
    free(load_ptr(plan, _PLAN_KEYS))
    free(load_ptr(plan, _PLAN_VERTICES))
    free(load_ptr(plan, _PLAN_EDGES))
    free(load_ptr(plan, _PLAN_ORDER))
    free(load_ptr(plan, _PLAN_OFFSETS))
    free(plan)


@c_abi_export("pcc_gc_callback_layers_build")
def pcc_gc_callback_layers_build(nodes, count: i64, stop_epoch: i64):
    if count <= 0 or count > _MAX_CANDIDATES or ptr_is_null(nodes) != 0:
        return null()
    if stop_epoch < 0 or pcc_thread_world_stop_epoch_held() != stop_epoch:
        return null()
    plan = malloc(_PLAN_BYTES)
    if ptr_is_null(plan) != 0:
        return null()
    memset(plan, 0, _PLAN_BYTES)
    capacity: i64 = 2
    while capacity < count * 2:
        capacity *= 2
    keys = malloc(capacity * _ENTRY_BYTES)
    vertices = malloc(count * _VERTEX_BYTES)
    order = malloc(count * _WORD)
    offsets = malloc((count + 1) * _WORD)
    store_ptr(plan, _PLAN_KEYS, keys)
    store_ptr(plan, _PLAN_VERTICES, vertices)
    store_ptr(plan, _PLAN_NODES, nodes)
    store_ptr(plan, _PLAN_ORDER, order)
    store_ptr(plan, _PLAN_OFFSETS, offsets)
    if (ptr_is_null(keys) != 0 or ptr_is_null(vertices) != 0
            or ptr_is_null(order) != 0 or ptr_is_null(offsets) != 0):
        pcc_gc_callback_layers_release(plan)
        return null()
    memset(keys, 0, capacity * _ENTRY_BYTES)
    memset(vertices, 0, count * _VERTEX_BYTES)
    mask: i64 = capacity - 1
    store_i64(plan, _PLAN_MASK, mask)
    i: i64 = 0
    while i < count:
        node = load_ptr(nodes, i * _WORD)
        if pcc_gc_object_node_is_active(node) == 0:
            pcc_gc_callback_layers_release(plan)
            return null()
        obj = load_ptr(node, 0)
        key_slot: i64 = _key_slot(keys, mask, obj)
        if (pcc_gc_tracing_is_sweep_candidate(obj) == 0
                or ptr_is_null(load_ptr(keys, key_slot + _ENTRY_KEY)) == 0):
            pcc_gc_callback_layers_release(plan)
            return null()
        store_ptr(keys, key_slot + _ENTRY_KEY, obj)
        store_i64(keys, key_slot + _ENTRY_ID, i)
        i += 1
    i = 0
    progress = stack_alloc(_SLICE_BYTES)
    while i < count:
        store_i64(plan, _PLAN_OWNER, i)
        cursor: i64 = 0
        while cursor >= 0:
            ok: i64 = pcc_gc_visit_object_slots_slice(
                load_ptr(load_ptr(nodes, i * _WORD), 0), cursor, _SLICE_SLOTS,
                _note_edge, plan, progress,
            )
            next_cursor: i64 = load_i64(progress, _SLICE_NEXT)
            examined: i64 = load_i64(progress, _SLICE_EXAMINED)
            if (ok == 0 or load_i64(plan, _PLAN_INVALID) != 0
                    or examined < 0 or examined > _SLICE_SLOTS
                    or (next_cursor != -1 and next_cursor <= cursor)):
                pcc_gc_callback_layers_release(plan)
                return null()
            cursor = next_cursor
        i += 1
    queued: i64 = 0
    i = 0
    while i < count:
        if load_i64(vertices, i * _VERTEX_BYTES + _VERTEX_INCOMING) == 0:
            store_i64(order, queued * _WORD, i)
            queued += 1
        i += 1
    edges = load_ptr(plan, _PLAN_EDGES)
    cursor = 0
    layers: i64 = 0
    store_i64(offsets, 0, 0)
    while cursor < queued:
        end: i64 = queued
        while cursor < end:
            parent: i64 = load_i64(order, cursor * _WORD)
            store_i64(vertices, parent * _VERTEX_BYTES + _VERTEX_INCOMING, -layers - 1)
            edge: i64 = load_i64(vertices, parent * _VERTEX_BYTES + _VERTEX_FIRST)
            while edge > 0:
                offset: i64 = (edge - 1) * _EDGE_BYTES
                child: i64 = load_i64(edges, offset + _EDGE_CHILD)
                incoming: i64 = child * _VERTEX_BYTES + _VERTEX_INCOMING
                left: i64 = load_i64(vertices, incoming) - 1
                store_i64(vertices, incoming, left)
                if left == 0:
                    store_i64(order, queued * _WORD, child)
                    queued += 1
                edge = load_i64(edges, offset + _EDGE_NEXT)
            cursor += 1
        layers += 1
        store_i64(offsets, layers * _WORD, end)
    # The positive-indegree residue contains cycles and their downstream
    # objects. Their current callback order is deliberately not in this plan.
    store_i64(plan, _PLAN_LAYER_COUNT, layers)
    store_i64(plan, _PLAN_PEELED, queued)
    return plan


@c_abi_export("pcc_gc_callback_layers_count")
def pcc_gc_callback_layers_count(plan) -> i64:
    return load_i64(plan, _PLAN_LAYER_COUNT)


@c_abi_export("pcc_gc_callback_layers_range")
def pcc_gc_callback_layers_range(plan, layer: i64) -> i64:
    return load_i64(load_ptr(plan, _PLAN_OFFSETS), layer * _WORD)


@c_abi_export("pcc_gc_callback_layers_node")
def pcc_gc_callback_layers_node(plan, position: i64):
    i: i64 = load_i64(load_ptr(plan, _PLAN_ORDER), position * _WORD)
    return load_ptr(load_ptr(plan, _PLAN_NODES), i * _WORD)


@c_abi_export("pcc_gc_callback_layers_contains")
def pcc_gc_callback_layers_contains(plan, target, layer: i64) -> i64:
    if ptr_is_null(target) != 0:
        return 0
    keys = load_ptr(plan, _PLAN_KEYS)
    slot: i64 = _key_slot(keys, load_i64(plan, _PLAN_MASK), target)
    if ptr_is_null(load_ptr(keys, slot + _ENTRY_KEY)) != 0:
        return 0
    i: i64 = load_i64(keys, slot + _ENTRY_ID)
    return 1 if load_i64(load_ptr(plan, _PLAN_VERTICES), i * _VERTEX_BYTES + _VERTEX_INCOMING) == -layer - 1 else 0
