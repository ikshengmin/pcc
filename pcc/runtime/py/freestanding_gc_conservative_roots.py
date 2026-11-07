"""GC4 prototype roots from frozen mutator words and proven object extents.

Index the stopped mutators' words, then query collector-owned extents. Sorting
the whole heap at every mark made tracing-only collection pay O(heap log heap)
even when the stacks contained very few distinct roots. An arbitrary word is
compared as an integer, never dereferenced. Overlapping payload extents retain
every possible owner. The world and graph stay stopped throughout the scan.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    free,
    global_addr,
    global_load_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_is_null,
    ptr_eq,
    ptr_to_int,
    stack_alloc,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_forwarding_index_find = extern("pcc_gc_forwarding_index_find", (c_ptr,), c_ptr)
pcc_gc_object_is_address_pinned = extern("pcc_gc_object_is_address_pinned", (c_ptr,), c_int64)

pcc_gc_object_node_is_active = extern("pcc_gc_object_node_is_active", (c_ptr,), c_int64)
pcc_gc_mark_root_gray_if_known = extern("pcc_gc_mark_root_gray_if_known", (c_ptr,), c_void)
pcc_thread_visit_conservative_words = extern(
    "pcc_thread_visit_conservative_words", (c_ptr, c_ptr), c_int64
)
pcc_thread_world_stop_epoch_held = extern("pcc_thread_world_stop_epoch_held", (), c_int64)
pcc_thread_snapshot_stopped_world_owner = extern(
    "pcc_thread_snapshot_stopped_world_owner", (), c_int64
)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)

_INTERVAL_START: i64 = 0
_INTERVAL_END: i64 = 8
_INTERVAL_OWNER: i64 = 16
_INTERVAL_PREFIX_END: i64 = 24
_INTERVAL_BYTES: i64 = 32
_SCAN_ENTRIES: i64 = 0
_SCAN_COUNT: i64 = 8
_SCAN_BYTES: i64 = 16

# Frozen-word capture record. A NULL data pointer selects the counting pass;
# the second pass must fit exactly the capacity counted from the same snapshots.
_WORDS_DATA: i64 = 0
_WORDS_COUNT: i64 = 8
_WORDS_CAPACITY: i64 = 16
_WORDS_CONTEXT_BYTES: i64 = 24
_WORD_BYTES: i64 = 8


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


@c_abi_export("pcc_gc_conservative_node_has_live_extent")
def _node_has_live_extent(node) -> i64:
    if pcc_gc_object_node_is_active(node) != 0:
        return 1
    # GC3's source stops owning mark work at copy commit, but NoRC retains
    # that indexed physical allocation until its canonical target retires.
    if (load_i32(global_addr("pcc_gc_backend_selected"), 0) != 3
            or load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0):
        return 0
    obj = load_ptr(node, 0)
    if ptr_is_null(obj) != 0 or (load_i32(obj, 12) & (2048 | 524288)) != 2048:
        return 0
    return 1 if ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0 else 0


@c_abi_export("pcc_gc_conservative_pin_owner_chain")
def _pin_owner_chain(node, mark: i64) -> None:
    owner = load_ptr(node, 0)
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if ((backend != 3 and backend != 4)
            or load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0):
        if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
            store_i64(node, 56, load_i64(node, 56) | 1)
        else:
            store_i64(node, 56, 1)
        if mark != 0:
            pcc_gc_mark_root_gray_if_known(owner)
        return
    # An old raw address pins every physical owner along a moving chain.
    # Shade its canonical target directly: the generic root helper follows
    # only one edge, which may still name another forwarding shell.
    remaining: i64 = load_i32(global_addr("pcc_gc_forwarding_population"), 0) + 1
    while remaining > 0:
        current = pcc_gc_object_index_find(owner)
        if ptr_is_null(current) != 0:
            pcc_platform_abort()
            return
        store_i64(current, 56, load_i64(current, 56) | 1)
        edge = pcc_gc_forwarding_index_find(owner)
        if ptr_is_null(edge) != 0:
            if mark != 0:
                pcc_gc_mark_root_gray_if_known(owner)
            return
        target = load_ptr(edge, 8)
        if ptr_is_null(target) != 0 or ptr_eq(target, owner) != 0:
            pcc_platform_abort()
            return
        owner = target
        remaining -= 1
    pcc_platform_abort()


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
            if _node_has_live_extent(node) != 0:
                _pin_owner_chain(node, 1)


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


@c_abi_export("pcc_gc_conservative_record_word")
def _record_word(word, context) -> None:
    value: i64 = ptr_to_int(word)
    # No valid positive extent can include INT64_MAX: its exclusive end
    # must also fit in i64. Keep unaligned interior addresses and tagged words.
    if value <= 0 or value == 9223372036854775807:
        return
    count: i64 = load_i64(context, _WORDS_COUNT)
    words = load_ptr(context, _WORDS_DATA)
    if ptr_is_null(words) == 0:
        if count >= load_i64(context, _WORDS_CAPACITY):
            pcc_platform_abort()
            return
        store_i64(words, count * _WORD_BYTES, value)
    if count >= 9223372036854775807 // _WORD_BYTES:
        pcc_platform_abort()
        return
    store_i64(context, _WORDS_COUNT, count + 1)


@c_abi_export("pcc_gc_conservative_word_sift")
def _word_sift(words, root: i64, count: i64) -> None:
    value: i64 = load_i64(words, root * _WORD_BYTES)
    while root < count // 2:
        child: i64 = root * 2 + 1
        if child + 1 < count and load_i64(words, child * _WORD_BYTES) < load_i64(
            words, (child + 1) * _WORD_BYTES
        ):
            child = child + 1
        child_value: i64 = load_i64(words, child * _WORD_BYTES)
        if value >= child_value:
            break
        store_i64(words, root * _WORD_BYTES, child_value)
        root = child
    store_i64(words, root * _WORD_BYTES, value)


@c_abi_export("pcc_gc_conservative_sort_words")
def _sort_words(words, count: i64) -> i64:
    if count <= 1:
        return count
    root: i64 = count // 2
    while root > 0:
        root = root - 1
        _word_sift(words, root, count)
    remaining: i64 = count
    while remaining > 1:
        remaining = remaining - 1
        largest: i64 = load_i64(words, 0)
        store_i64(words, 0, load_i64(words, remaining * _WORD_BYTES))
        store_i64(words, remaining * _WORD_BYTES, largest)
        _word_sift(words, 0, remaining)
    unique: i64 = 1
    previous: i64 = load_i64(words, 0)
    index: i64 = 1
    while index < count:
        value: i64 = load_i64(words, index * _WORD_BYTES)
        if value != previous:
            store_i64(words, unique * _WORD_BYTES, value)
            unique = unique + 1
            previous = value
        index = index + 1
    return unique


@c_abi_export("pcc_gc_conservative_extent_has_word")
def _extent_has_word(words, count: i64, base, size: i64) -> i64:
    start: i64 = ptr_to_int(base)
    if start <= 0 or size <= 0 or size > 9223372036854775807 - start:
        pcc_platform_abort()
        return 0
    low: i64 = 0
    high: i64 = count
    while low < high:
        middle: i64 = low + (high - low) // 2
        if load_i64(words, middle * _WORD_BYTES) < start:
            low = middle + 1
        else:
            high = middle
    if low < count and load_i64(words, low * _WORD_BYTES) < start + size:
        return 1
    return 0


@c_abi_export("pcc_gc_scan_conservative_thread_roots")
def _scan_thread_roots(mark: i64) -> None:
    if pcc_thread_world_stop_epoch_held() < 0:
        pcc_platform_abort()
        return
    # The owner's own frames since it stopped the world are roots as well.
    if pcc_thread_snapshot_stopped_world_owner() < 0:
        pcc_platform_abort()
        return
    context = stack_alloc(_WORDS_CONTEXT_BYTES)
    store_ptr(context, _WORDS_DATA, null())
    store_i64(context, _WORDS_COUNT, 0)
    store_i64(context, _WORDS_CAPACITY, 0)
    visited: i64 = pcc_thread_visit_conservative_words(_record_word, context)
    if visited < 0:
        pcc_platform_abort()
        return
    capacity: i64 = load_i64(context, _WORDS_COUNT)
    words = malloc(capacity * _WORD_BYTES if capacity > 0 else _WORD_BYTES)
    if ptr_is_null(words):
        pcc_platform_abort()
        return
    store_ptr(context, _WORDS_DATA, words)
    store_i64(context, _WORDS_COUNT, 0)
    store_i64(context, _WORDS_CAPACITY, capacity)
    captured: i64 = pcc_thread_visit_conservative_words(_record_word, context)
    if captured != visited or load_i64(context, _WORDS_COUNT) != capacity:
        free(words)
        pcc_platform_abort()
        return
    count: i64 = _sort_words(words, capacity)
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    count_free: i64 = load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0)
    gc3_count_free: i64 = 1 if backend == 3 and count_free != 0 else 0
    chain_pin_cut: i64 = gc3_count_free
    if (backend == 4 and count_free != 0
            and load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0):
        chain_pin_cut = 1
    if chain_pin_cut != 0:
        # Clear the old cut before any source can pin a later node in its
        # forwarding chain. Inactive source pins are cycle-local as well.
        # A GC4 heap without forwarding edges keeps the single-pass refresh.
        reset = global_load_ptr("pcc_gc_object_head")
        while ptr_is_null(reset) == 0:
            if _node_has_live_extent(reset) != 0:
                store_i64(reset, 56, load_i64(reset, 56) & ~1)
            reset = load_ptr(reset, 16)
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        if _node_has_live_extent(node) != 0:
            # A stopped-world relocation step needs CURRENT raw addresses,
            # including objects allocated since the last completed mark.
            # Retire old conservative pins without changing marking colors.
            if mark == 0 and chain_pin_cut == 0:
                if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
                    store_i64(node, 56, load_i64(node, 56) & ~1)
                else:
                    store_i64(node, 56, 0)
            rooted: i64 = _extent_has_word(words, count, load_ptr(node, 0), load_i64(node, 8))
            if gc3_count_free != 0 and pcc_gc_object_is_address_pinned(load_ptr(node, 0)) != 0:
                rooted = 1
            owner = load_ptr(node, 48)
            if ptr_is_null(owner) == 0:
                span = load_ptr(owner, 64)
                while ptr_is_null(span) == 0:
                    if _extent_has_word(words, count, load_ptr(span, 8), load_i64(span, 16)) != 0:
                        rooted = 1
                    span = load_ptr(span, 40)
            if rooted != 0:
                # A raw source address retains its bytes and shades the
                # canonical target; source metadata itself is not a root.
                _pin_owner_chain(node, mark)
        node = load_ptr(node, 16)
    free(words)


@c_abi_export("pcc_gc_gray_conservative_thread_roots")
def pcc_gc_gray_conservative_thread_roots() -> None:
    _scan_thread_roots(1)


@c_abi_export("pcc_gc_pin_conservative_thread_roots")
def pcc_gc_pin_conservative_thread_roots() -> None:
    # The caller owns the stopped world. The graph lock excludes collector
    # metadata changes while resolving frozen words to object/payload owners.
    pcc_py_gc_minor_graph_lock()
    _scan_thread_roots(0)
    pcc_py_gc_minor_graph_unlock()
