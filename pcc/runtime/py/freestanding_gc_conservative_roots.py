"""GC4 prototype roots from frozen mutator words and proven object extents.

The interval index contains only collector-owned nodes and payload descriptors.
An arbitrary stack word is compared as an integer; it is never dereferenced.
Overlapping payload extents retain every possible owner. The collector holds
the world and object graph throughout index construction and visitation.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    free,
    global_load_ptr,
    load_i64,
    load_ptr,
    malloc,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

pcc_gc_object_node_is_active = extern("pcc_gc_object_node_is_active", (c_ptr,), c_int64)
pcc_gc_mark_root_gray_if_known = extern("pcc_gc_mark_root_gray_if_known", (c_ptr,), c_void)
pcc_thread_visit_conservative_words = extern(
    "pcc_thread_visit_conservative_words", (c_ptr, c_ptr), c_int64
)
pcc_thread_world_stop_epoch_held = extern("pcc_thread_world_stop_epoch_held", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)

_INTERVAL_START: i64 = 0
_INTERVAL_END: i64 = 8
_INTERVAL_OWNER: i64 = 16
_INTERVAL_PREFIX_END: i64 = 24
_INTERVAL_BYTES: i64 = 32
_SCAN_ENTRIES: i64 = 0
_SCAN_COUNT: i64 = 8
_SCAN_BYTES: i64 = 16


@c_abi_export("pcc_gc_conservative_interval_swap")
def _interval_swap(entries, left: i64, right: i64) -> None:
    first = ptr_add(entries, left * _INTERVAL_BYTES)
    second = ptr_add(entries, right * _INTERVAL_BYTES)
    offset: i64 = 0
    while offset < _INTERVAL_BYTES:
        value: i64 = load_i64(first, offset)
        store_i64(first, offset, load_i64(second, offset))
        store_i64(second, offset, value)
        offset = offset + 8


@c_abi_export("pcc_gc_conservative_interval_sift")
def _interval_sift(entries, root: i64, count: i64) -> None:
    while root < count // 2:
        child: i64 = root * 2 + 1
        if child + 1 < count and load_i64(entries, child * _INTERVAL_BYTES) < load_i64(
            entries, (child + 1) * _INTERVAL_BYTES
        ):
            child = child + 1
        if load_i64(entries, root * _INTERVAL_BYTES) >= load_i64(entries, child * _INTERVAL_BYTES):
            return
        _interval_swap(entries, root, child)
        root = child


@c_abi_export("pcc_gc_conservative_sort_intervals")
def pcc_gc_conservative_sort_intervals(entries, count: i64) -> None:
    root: i64 = count // 2
    while root > 0:
        root = root - 1
        _interval_sift(entries, root, count)
    remaining: i64 = count
    while remaining > 1:
        remaining = remaining - 1
        _interval_swap(entries, 0, remaining)
        _interval_sift(entries, 0, remaining)
    high: i64 = 0
    index: i64 = 0
    while index < count:
        entry = ptr_add(entries, index * _INTERVAL_BYTES)
        end: i64 = load_i64(entry, _INTERVAL_END)
        if end > high:
            high = end
        store_i64(entry, _INTERVAL_PREFIX_END, high)
        index = index + 1


@c_abi_export("pcc_gc_conservative_mark_word")
def pcc_gc_conservative_mark_word(word, context) -> None:
    candidate: i64 = ptr_to_int(word)
    entries = load_ptr(context, _SCAN_ENTRIES)
    count: i64 = load_i64(context, _SCAN_COUNT)
    if count <= 0 or candidate <= 0:
        return
    if candidate < load_i64(entries, _INTERVAL_START) or candidate >= load_i64(
        entries, (count - 1) * _INTERVAL_BYTES + _INTERVAL_PREFIX_END
    ):
        return
    low: i64 = 0
    high: i64 = count
    while low < high:
        middle: i64 = low + (high - low) // 2
        if load_i64(entries, middle * _INTERVAL_BYTES + _INTERVAL_START) <= candidate:
            low = middle + 1
        else:
            high = middle
    index: i64 = low
    while index > 0:
        index = index - 1
        entry = ptr_add(entries, index * _INTERVAL_BYTES)
        if load_i64(entry, _INTERVAL_PREFIX_END) <= candidate:
            return
        if load_i64(entry, _INTERVAL_END) > candidate:
            node = load_ptr(entry, _INTERVAL_OWNER)
            if pcc_gc_object_node_is_active(node) != 0:
                # Count-free GC4 no longer uses gc_refs for RC subtraction.
                # It becomes the conservative pin for this mark/relocate cycle.
                store_i64(node, 56, 1)
                pcc_gc_mark_root_gray_if_known(load_ptr(node, 0))


@c_abi_export("pcc_gc_conservative_append_interval")
def _append_interval(entries, index: i64, node, base, size: i64) -> i64:
    start: i64 = ptr_to_int(base)
    if start <= 0 or size <= 0 or size > 9223372036854775807 - start:
        pcc_platform_abort()
        return index
    entry = ptr_add(entries, index * _INTERVAL_BYTES)
    store_i64(entry, _INTERVAL_START, start)
    store_i64(entry, _INTERVAL_END, start + size)
    store_ptr(entry, _INTERVAL_OWNER, node)
    store_i64(entry, _INTERVAL_PREFIX_END, 0)
    return index + 1


@c_abi_export("pcc_gc_gray_conservative_thread_roots")
def pcc_gc_gray_conservative_thread_roots() -> None:
    if pcc_thread_world_stop_epoch_held() < 0:
        pcc_platform_abort()
        return
    count: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_object_node_is_active(node) != 0:
            count = count + 1
            owner = load_ptr(node, 48)
            if ptr_is_null(owner) == 0:
                span = load_ptr(owner, 64)
                while ptr_is_null(span) == 0:
                    count = count + 1
                    span = load_ptr(span, 40)
        node = load_ptr(node, 16)
    if count > 9223372036854775807 // _INTERVAL_BYTES:
        pcc_platform_abort()
        return
    entries = malloc(count * _INTERVAL_BYTES if count > 0 else _INTERVAL_BYTES)
    if ptr_is_null(entries):
        pcc_platform_abort()
        return
    index: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_object_node_is_active(node) != 0:
            index = _append_interval(entries, index, node, load_ptr(node, 0), load_i64(node, 8))
            owner = load_ptr(node, 48)
            if ptr_is_null(owner) == 0:
                span = load_ptr(owner, 64)
                while ptr_is_null(span) == 0:
                    index = _append_interval(entries, index, node, load_ptr(span, 8), load_i64(span, 16))
                    span = load_ptr(span, 40)
        node = load_ptr(node, 16)
    pcc_gc_conservative_sort_intervals(entries, count)
    context = stack_alloc(_SCAN_BYTES)
    store_ptr(context, _SCAN_ENTRIES, entries)
    store_i64(context, _SCAN_COUNT, count)
    visited: i64 = pcc_thread_visit_conservative_words(pcc_gc_conservative_mark_word, context)
    free(entries)
    if visited < 0:
        pcc_platform_abort()
