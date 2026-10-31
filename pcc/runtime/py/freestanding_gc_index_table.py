"""Freestanding pointer indexes shared by all production GC backends.

The tables are open-addressed arrays of 16-byte slots::

    key: ptr, node: ptr

NULL keys mark empty slots. Keys are never NULL on insertion and backward-
shift deletion leaves no tombstones, so a separate occupancy byte is redundant.
The relocation-member index uses the value word as an i64 multiplicity;
rehashing and backward-shift deletion preserve that word without dereferencing it.

They depend on ``pcc.unsafe`` raw memory operations, the freestanding
allocator ABI and the tracking kernel's address-selected primary-index
storage. The production pcc-Python archive links this module.

The primary (tracked-object) index is split by key provenance: an exact
object-family allocator cell keeps its node in that slab's side array
(``pcc_allocator_object_side_slot``), a constant-time slot, and only other
keys -- large or raw allocations, static objects -- use that address's shard
hash table. A key's provenance never changes while it is live, so each key
lives in exactly one of the two places. Primary find/insert/remove callers
must hold the address's tracking shard or all tracking locks, or establish
quiescence; a returned node may not outlive that protection without a
separate lifetime guarantee. These functions do not acquire another lock.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_store_i64,
    calloc,
    define_global_i64,
    define_global_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    logical_shift_right_i64,
    null,
    wrapping_mul_i64,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True


pcc_allocator_object_side_slot = extern(
    "pcc_allocator_object_side_slot", (c_ptr, c_int64), c_ptr
)
pcc_allocator_granule_object_slot = extern(
    "pcc_allocator_granule_object_slot", (c_ptr,), c_ptr
)
pcc_gc_tracked_primary_index_cells = extern(
    "pcc_gc_tracked_primary_index_cells", (c_ptr,), c_ptr
)

# Offsets within the 32-byte primary-index record supplied by tracking.
_PRIMARY_SLOTS: i64 = 0
_PRIMARY_CAPACITY: i64 = 8
_PRIMARY_COUNT: i64 = 16
_PRIMARY_USED: i64 = 24

define_global_ptr_null("pcc_py_gc_object_slots")
define_global_i64("pcc_py_gc_object_cap", 0)
define_global_i64("pcc_py_gc_object_count", 0)
define_global_i64("pcc_py_gc_object_used", 0)

# Key-only provenance set for managed objects that deliberately have no GC
# object node (backend 0, graph leaves, and explicitly registered runtime
# objects).  A slot is either NULL or one exact managed pointer; writers and
# the full lookup hold the common GC graph lock, so lookup never needs to
# inspect the candidate's header or guess from its virtual address.
define_global_ptr_null("pcc_py_gc_managed_pointer_slots")
define_global_i64("pcc_py_gc_managed_pointer_cap", 0)
define_global_i64("pcc_py_gc_managed_pointer_count", 0)

# Indexes read without the graph lock (see _index_published_cell) publish
# each table with its capacity as one immutable 16-byte record {slots, cap},
# and retire the table and record they replace: a list of 16-byte nodes
# {next, block}, freed by pcc_gc_index_reclaim_retired.
define_global_ptr_null("pcc_py_gc_object_published")
define_global_ptr_null("pcc_py_gc_managed_pointer_published")
define_global_ptr_null("pcc_py_gc_forwarding_published")
define_global_ptr_null("pcc_py_gc_forwarding_target_published")
define_global_ptr_null("pcc_py_gc_index_retired_tables")

define_global_ptr_null("pcc_py_gc_forwarding_slots")
define_global_i64("pcc_py_gc_forwarding_cap", 0)
define_global_i64("pcc_py_gc_forwarding_count", 0)
define_global_i64("pcc_py_gc_forwarding_used", 0)

define_global_ptr_null("pcc_py_gc_forwarding_target_slots")
define_global_i64("pcc_py_gc_forwarding_target_cap", 0)
define_global_i64("pcc_py_gc_forwarding_target_count", 0)
define_global_i64("pcc_py_gc_forwarding_target_used", 0)

define_global_ptr_null("pcc_py_gc_identity_slots")
define_global_i64("pcc_py_gc_identity_cap", 0)
define_global_i64("pcc_py_gc_identity_count", 0)
define_global_i64("pcc_py_gc_identity_used", 0)

define_global_ptr_null("pcc_py_gc_frame_slots")
define_global_i64("pcc_py_gc_frame_cap", 0)
define_global_i64("pcc_py_gc_frame_count", 0)
define_global_i64("pcc_py_gc_frame_used", 0)

define_global_ptr_null("pcc_py_gc_zpage_owner_slots")
define_global_i64("pcc_py_gc_zpage_owner_cap", 0)
define_global_i64("pcc_py_gc_zpage_owner_count", 0)
define_global_i64("pcc_py_gc_zpage_owner_used", 0)

define_global_ptr_null("pcc_py_gc_zpage_page_slots")
define_global_i64("pcc_py_gc_zpage_page_cap", 0)
define_global_i64("pcc_py_gc_zpage_page_count", 0)
define_global_i64("pcc_py_gc_zpage_page_used", 0)

# Exact multiplicities for the GC4 relocation list, using each existing
# 16-byte slot's value word. Selector admission can create duplicate list
# entries after a read barrier clears the candidate hint, so membership is
# not a boolean. count/used still count distinct keys, not list entries.
# These raw internal APIs require the GC graph lock: note follows one
# committed insertion, forget follows one committed unlink, and clear is
# allowed only after the caller proves the whole relocation list empty.
# Allocation failure, overflow, or inconsistent bookkeeping makes the epoch
# incomplete. Queries then require the original list walk until an explicit
# empty-list clear; a later successful operation cannot repair missing history.
define_global_ptr_null("pcc_py_gc_relocation_member_slots")
define_global_i64("pcc_py_gc_relocation_member_cap", 0)
define_global_i64("pcc_py_gc_relocation_member_count", 0)
define_global_i64("pcc_py_gc_relocation_member_used", 0)
define_global_i64("pcc_py_gc_relocation_member_incomplete", 0)


# Conservative owner superset for GC4 remembered slots. A key may remain after
# slot/card deletion or retarget; forget is allowed only after a callback-free
# list walk proves that owner absent. All calls require the GC graph lock.
# Reentrant allocator callbacks cannot inspect a half-published table: busy
# queries scan the list, and a nested mutation taints the epoch until an
# authoritative empty-list clear. Neither object headers nor slot addresses
# establish negative membership.
define_global_ptr_null("pcc_py_gc_remembered_member_slots")
define_global_i64("pcc_py_gc_remembered_member_cap", 0)
define_global_i64("pcc_py_gc_remembered_member_count", 0)
define_global_i64("pcc_py_gc_remembered_member_used", 0)
define_global_i64("pcc_py_gc_remembered_member_incomplete", 0)
define_global_i64("pcc_py_gc_remembered_member_busy", 0)

# Exact owner set for GC4 store-buffer entries in both queues. note follows
# an enqueue, forget follows removal of every entry of that owner, and clear
# follows emptying both queues. Allocation failure (or an entry dropped by a
# failed medium flush) makes the epoch incomplete; queries then walk the
# queues until the next clear. All calls hold the GC graph lock. A pcc1 GC4
# compile walked an 8,384-entry store buffer on every object free and
# counted its distinct owners quadratically on every enqueue.
define_global_ptr_null("pcc_py_gc_store_buffer_member_slots")
define_global_i64("pcc_py_gc_store_buffer_member_cap", 0)
define_global_i64("pcc_py_gc_store_buffer_member_count", 0)
define_global_i64("pcc_py_gc_store_buffer_member_used", 0)
define_global_i64("pcc_py_gc_store_buffer_member_incomplete", 0)
# GC4 store-buffer entries by owner (an i64 multiplicity in the value word)
# and by recorded slot (presence). Both mirror the two store-buffer queues; a
# path that cannot keep them exact marks them incomplete, and callers walk
# the queues until the next clear.
define_global_ptr_null("pcc_py_gc_store_buffer_owner_slots")
define_global_i64("pcc_py_gc_store_buffer_owner_cap", 0)
define_global_i64("pcc_py_gc_store_buffer_owner_count", 0)
define_global_i64("pcc_py_gc_store_buffer_owner_used", 0)
define_global_ptr_null("pcc_py_gc_store_buffer_slot_slots")
define_global_i64("pcc_py_gc_store_buffer_slot_cap", 0)
define_global_i64("pcc_py_gc_store_buffer_slot_count", 0)
define_global_i64("pcc_py_gc_store_buffer_slot_used", 0)
define_global_i64("pcc_py_gc_store_buffer_index_incomplete", 0)



@c_abi_export("pcc_gc_index_py_hash_ptr")
def pcc_gc_index_py_hash_ptr(key: c_ptr) -> i64:
    # Fibonacci multiply: consecutive (bump-allocated) addresses previously
    # landed in consecutive slots and linear probing degenerated into giant
    # primary clusters (find_slot dominated GC3/GC4 profiles). Keep bit-exact
    # with py_gc_index_hash_ptr in src/py_gc_index_table.c.
    # -7046029254386353131 == 0x9E3779B97F4A7C15 as a two's-complement i64.
    value: i64 = logical_shift_right_i64(ptr_diff(key, null()), 3)
    value = wrapping_mul_i64(value, -7046029254386353131)
    return value ^ logical_shift_right_i64(value, 32)


@c_abi_export("pcc_gc_index_published_cell")
def _index_published_cell(slots_cell: c_ptr) -> c_ptr:
    """The publication cell of an index read without the graph lock, else NULL.

    The GC4 load barrier (pcc_gc_load_ptr -> pcc_gc_backend4_slot_needs_resolve)
    looks up the forwarding and object indexes without the lock, and
    pcc_gc_pointer_is_managed probes the object and managed-pointer indexes
    with pcc_gc_index_py_seen. Their tables are published with their
    capacity in one record and retired, not freed, when replaced.
    """
    if ptr_eq(slots_cell, global_addr("pcc_py_gc_object_slots")) != 0:
        return global_addr("pcc_py_gc_object_published")
    if ptr_eq(slots_cell, global_addr("pcc_py_gc_managed_pointer_slots")) != 0:
        return global_addr("pcc_py_gc_managed_pointer_published")
    if ptr_eq(slots_cell, global_addr("pcc_py_gc_forwarding_slots")) != 0:
        return global_addr("pcc_py_gc_forwarding_published")
    if ptr_eq(slots_cell, global_addr("pcc_py_gc_forwarding_target_slots")) != 0:
        return global_addr("pcc_py_gc_forwarding_target_published")
    return null()


@c_abi_export("pcc_gc_index_retire")
def _index_retire(block) -> None:
    """Keep ``block`` until the holder of a stopped world frees it."""
    if ptr_is_null(block) != 0:
        return
    node = calloc(1, 16)
    if ptr_is_null(node) != 0:
        # A block that cannot be retired leaks: freeing it could free memory
        # a reader is probing.
        return
    store_ptr(node, 0, global_load_ptr("pcc_py_gc_index_retired_tables"))
    store_ptr(node, 8, block)
    global_store_ptr("pcc_py_gc_index_retired_tables", node)


@c_abi_export("pcc_gc_index_table_publish")
def _index_table_publish(slots_cell: c_ptr, cap_cell: c_ptr, slots, cap: i64) -> None:
    """Install ``slots`` and ``cap``; the caller holds the graph lock.

    An index read without the lock also gets a new record {slots, cap}: a
    reader loads the record once, so it never pairs a table with another
    table's capacity (which would probe the wrong slots or past the end).
    The record it replaces is retired with the replaced table. Without a
    record (no memory) readers see no table and take the locked lookup.
    """
    store_ptr(slots_cell, 0, slots)
    store_i64(cap_cell, 0, cap)
    published_cell = _index_published_cell(slots_cell)
    if ptr_is_null(published_cell) != 0:
        return
    record = null()
    if ptr_is_null(slots) == 0 and cap > 0:
        record = calloc(1, 16)
        if ptr_is_null(record) == 0:
            store_ptr(record, 0, slots)
            store_i64(record, 8, cap)
    old_record = load_ptr(published_cell, 0)
    atomic_store_i64(published_cell, 0, ptr_diff(record, null()), "release")
    _index_retire(old_record)


@c_abi_export("pcc_gc_index_table_release")
def _index_table_release(slots_cell: c_ptr, table) -> None:
    """Free a replaced table, or retire it when unlocked readers may hold it."""
    if ptr_is_null(table) != 0:
        return
    if ptr_is_null(_index_published_cell(slots_cell)) != 0:
        free(table)
        return
    _index_retire(table)


@c_abi_export("pcc_gc_index_py_seen")
def pcc_gc_index_py_seen(published_cell: c_ptr, key: c_ptr, stride: i64) -> i64:
    """1 when ``key`` is in the index, read without the graph lock; 0 = unknown.

    For an index published through ``published_cell`` (_index_published_cell).
    ``stride`` is the slot size: 16 for key/node tables, 8 for the
    managed-pointer set. A 1 is exact: a slot only ever holds a key that was
    inserted, written with one 8-byte store, so finding ``key`` means it was
    present when the slot was read (a concurrent removal is the free of an
    object the caller still references, which no caller may race). A 0
    decides nothing: a backward-shift removal may move a key past the probe,
    so callers then take the locked lookup. The probe ends at an empty slot,
    which a table at most half full always has (a retired table is no longer
    written), and never runs past one lap.

    This module is freestanding, so its functions carry no implicit stop
    polls (pipeline.py): no thread parks between loading a record here and
    its last slot read. Every thread a stopped world parked or exited is
    therefore outside this probe, which is what lets the world's holder free
    retired tables (pcc_gc_index_reclaim_retired).
    """
    key_bits: i64 = ptr_diff(key, null())
    if key_bits == 0 or (key_bits & 1) != 0:
        return 0
    record_bits: i64 = atomic_load_i64(published_cell, 0, "acquire")
    if record_bits == 0:
        return 0
    record = int_to_ptr(record_bits)
    slots = load_ptr(record, 0)
    cap: i64 = load_i64(record, 8)
    mask: i64 = cap - 1
    index: i64 = pcc_gc_index_py_hash_ptr(key) & mask
    probes: i64 = 0
    while probes < cap:
        candidate: i64 = atomic_load_i64(slots, index * stride, "relaxed")
        if candidate == key_bits:
            return 1
        if candidate == 0:
            return 0
        index = (index + 1) & mask
        probes = probes + 1
    return 0


@c_abi_export("pcc_gc_index_py_find_published")
def pcc_gc_index_py_find_published(published_cell: c_ptr, key: c_ptr) -> c_ptr:
    """pcc_gc_index_py_find for an index that callers also read without the lock.

    The table and capacity come from one published record (see
    pcc_gc_index_py_seen). An unlocked caller can still pair a key with a
    node word that a concurrent backward-shift removal is moving; under the
    lock the answer is exact.
    """
    if ptr_is_null(key) or is_tagged_int(key):
        return null()
    record_bits: i64 = atomic_load_i64(published_cell, 0, "acquire")
    if record_bits == 0:
        return null()
    record = int_to_ptr(record_bits)
    slots = load_ptr(record, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, load_i64(record, 8), key)
    if result < 0:
        return null()
    return load_ptr(slots, result * 16 + 8)


@c_abi_export("pcc_gc_index_reclaim_retired")
def pcc_gc_index_reclaim_retired() -> i64:
    """Free the retired tables; returns how many.

    Only the holder of a stopped world may call this (see
    pcc_gc_reclaim_retired_index_tables): every other mutator is parked or
    exited, so none is inside an unlocked probe, and none can load a retired
    table again because its replacement was published before it was retired.
    """
    freed: i64 = 0
    node = global_load_ptr("pcc_py_gc_index_retired_tables")
    global_store_ptr("pcc_py_gc_index_retired_tables", null())
    while ptr_is_null(node) == 0:
        older = load_ptr(node, 0)
        free(load_ptr(node, 8))
        free(node)
        freed = freed + 1
        node = older
    return freed


@c_abi_export("pcc_gc_index_slot_size")
def pcc_gc_index_slot_size() -> i64:
    """Bytes per opaque index slot, also used by preallocated GC plans."""
    return 16


@c_abi_export("pcc_gc_index_py_next_pow2")
def pcc_gc_index_py_next_pow2(value: i64) -> i64:
    if value < 8:
        return 8
    power: i64 = 1
    while power < value:
        power = power * 2
    return power


@c_abi_export("pcc_gc_index_py_rehash_capacity")
def pcc_gc_index_py_rehash_capacity(
    cap: i64, count: i64, minimum: i64
) -> i64:
    desired: i64 = (count + 1) * 4
    # At the half-full growth boundary, rounding 4 * (count + 1)
    # up to a power of two quadruples the old capacity. A doubled table
    # already restores quarter occupancy and preserves lookup headroom.
    if count + 1 > logical_shift_right_i64(cap, 1):
        desired = (count + 1) * 2
    if desired < minimum:
        desired = minimum
    compact: i64 = pcc_gc_index_py_next_pow2(desired)
    if count + 1 > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > compact:
            return grown
        return compact
    if compact < cap:
        return compact
    return cap


@c_abi_export("pcc_gc_index_py_find_slot")
def pcc_gc_index_py_find_slot(slots: c_ptr, cap: i64, key: c_ptr) -> i64:
    # Non-negative means occupied; negative encodes ``-insertion_index - 1``.
    # Backward-shift deletion keeps probe chains gap-free, so slot state is
    # only 0 (empty) or 1 (occupied): churn workloads no longer accumulate
    # tombstones that force same-capacity rehashes (calloc+memset dominated
    # the GC4 longrun profile) or lengthen probe runs.
    mask: i64 = cap - 1
    index: i64 = pcc_gc_index_py_hash_ptr(key) & mask
    while True:
        offset: i64 = index * 16
        candidate = load_ptr(slots, offset)
        if ptr_is_null(candidate) != 0:
            return -index - 1
        if ptr_eq(candidate, key):
            return index
        index = (index + 1) & mask


@c_abi_export("pcc_gc_index_py_rehash_slots")
def pcc_gc_index_py_rehash_slots(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    used_cell: c_ptr,
    requested_cap: i64,
) -> i64:
    new_cap: i64 = pcc_gc_index_py_next_pow2(requested_cap)
    new_slots = calloc(new_cap, 16)
    if ptr_is_null(new_slots):
        return -1

    old_slots = load_ptr(slots_cell, 0)
    old_cap: i64 = load_i64(cap_cell, 0)
    new_used: i64 = 0
    if ptr_is_null(old_slots) == 0:
        index: i64 = 0
        while index < old_cap:
            old_offset: i64 = index * 16
            if (
                ptr_is_null(load_ptr(old_slots, old_offset)) == 0
            ):
                key = load_ptr(old_slots, old_offset)
                result: i64 = pcc_gc_index_py_find_slot(new_slots, new_cap, key)
                new_index: i64 = -result - 1
                new_offset: i64 = new_index * 16
                store_ptr(new_slots, new_offset, key)
                store_ptr(
                    new_slots,
                    new_offset + 8,
                    load_ptr(old_slots, old_offset + 8),
                )
                new_used = new_used + 1
            index = index + 1

    _index_table_publish(slots_cell, cap_cell, new_slots, new_cap)
    store_i64(used_cell, 0, new_used)
    _index_table_release(slots_cell, old_slots)
    return 0


@c_abi_export("pcc_gc_index_py_find")
def pcc_gc_index_py_find(
    slots_cell: c_ptr, cap_cell: c_ptr, key: c_ptr, reject_tagged: i64
) -> c_ptr:
    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots) or ptr_is_null(key):
        return null()
    if reject_tagged != 0 and is_tagged_int(key):
        return null()
    result: i64 = pcc_gc_index_py_find_slot(slots, load_i64(cap_cell, 0), key)
    if result < 0:
        return null()
    return load_ptr(
        slots,
        result * 16 + 8,
    )


@c_abi_export("pcc_gc_index_py_insert")
def pcc_gc_index_py_insert(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    reject_tagged: i64,
    require_node: i64,
    initial_cap: i64,
) -> i64:
    if ptr_is_null(key):
        return -1
    if reject_tagged != 0 and is_tagged_int(key):
        return -1
    if require_node != 0 and ptr_is_null(node):
        return -1

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        return 0

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset: i64 = index * 16
    if ptr_is_null(load_ptr(slots, offset)) != 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i64(count_cell, 0, count + 1)
    return 1


@c_abi_export("pcc_gc_index_py_upsert")
def pcc_gc_index_py_upsert(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    reject_tagged: i64,
    initial_cap: i64,
) -> i64:
    if ptr_is_null(key) or ptr_is_null(node):
        return -1
    if reject_tagged != 0 and is_tagged_int(key):
        return -1

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        store_ptr(
            slots,
            result * 16 + 8,
            node,
        )
        return 0

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset: i64 = index * 16
    if ptr_is_null(load_ptr(slots, offset)) != 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i64(count_cell, 0, count + 1)
    return 1


@c_abi_export("pcc_gc_index_py_replace_raw")
def pcc_gc_index_py_replace_raw(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    initial_cap: i64,
) -> c_ptr:
    if ptr_is_null(key) or ptr_is_null(node):
        return node

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return node
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        offset: i64 = result * 16
        old = load_ptr(slots, offset + 8)
        store_ptr(slots, offset + 8, node)
        return old

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return node
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset = index * 16
    if ptr_is_null(load_ptr(slots, offset)) != 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i64(count_cell, 0, count + 1)
    return null()


@c_abi_export("pcc_gc_index_py_remove")
def pcc_gc_index_py_remove(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    reject_tagged: i64,
) -> c_ptr:
    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots) or ptr_is_null(key):
        return null()
    if reject_tagged != 0 and is_tagged_int(key):
        return null()
    # An empty index cannot hold the key; every object free probes the
    # identity index this way, and almost no program registers identities.
    if load_i64(count_cell, 0) <= 0:
        return null()
    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result < 0:
        return null()
    node = load_ptr(slots, result * 16 + 8)
    # Backward-shift deletion: close the probe-chain gap instead of writing
    # a tombstone. An entry at ``probe`` (home slot ``home``) may fill the
    # hole iff its home lies cyclically outside (hole, probe], i.e.
    # (probe - home) mod cap >= (probe - hole) mod cap. Loop cost is the
    # probe-run length past the hole (short at <=50% load with the
    # Fibonacci hash). No engine caller iterates slots across removes, so
    # moving entries here is safe for every index instance.
    mask: i64 = cap - 1
    hole: i64 = result
    probe: i64 = result
    done: i64 = 0
    while done == 0:
        probe = (probe + 1) & mask
        probe_off: i64 = probe * 16
        if ptr_is_null(load_ptr(slots, probe_off)) != 0:
            done: i64 = 1
        else:
            probe_key = load_ptr(slots, probe_off)
            home: i64 = pcc_gc_index_py_hash_ptr(probe_key) & mask
            if ((probe - home) & mask) >= ((probe - hole) & mask):
                hole_off: i64 = hole * 16
                store_ptr(slots, hole_off, probe_key)
                store_ptr(
                    slots,
                    hole_off + 8,
                    load_ptr(slots, probe_off + 8),
                )
                hole = probe
    clear_off: i64 = hole * 16
    store_ptr(slots, clear_off, null())
    store_ptr(slots, clear_off + 8, null())
    store_i64(count_cell, 0, load_i64(count_cell, 0) - 1)
    store_i64(used_cell, 0, load_i64(used_cell, 0) - 1)
    return node


@c_abi_export("pcc_gc_index_py_clear")
def pcc_gc_index_py_clear(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
) -> None:
    old_slots = load_ptr(slots_cell, 0)
    _index_table_publish(slots_cell, cap_cell, null(), 0)
    store_i64(count_cell, 0, 0)
    store_i64(used_cell, 0, 0)
    _index_table_release(slots_cell, old_slots)


@c_abi_export("pcc_gc_managed_pointer_find_slot")
def _managed_pointer_find_slot(slots, cap: i64, key) -> i64:
    # Non-negative means occupied; negative encodes ``-insertion_index - 1``.
    mask: i64 = cap - 1
    index: i64 = pcc_gc_index_py_hash_ptr(key) & mask
    while True:
        candidate = load_ptr(slots, index * 8)
        if ptr_is_null(candidate) != 0:
            return -index - 1
        if ptr_eq(candidate, key) != 0:
            return index
        index = (index + 1) & mask


@c_abi_export("pcc_gc_managed_pointer_rehash")
def _managed_pointer_rehash(requested_cap: i64) -> i64:
    new_cap: i64 = pcc_gc_index_py_next_pow2(requested_cap)
    new_slots = calloc(new_cap, 8)
    if ptr_is_null(new_slots) != 0:
        return -1
    slots_cell = global_addr("pcc_py_gc_managed_pointer_slots")
    old_slots = load_ptr(slots_cell, 0)
    old_cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    index: i64 = 0
    while index < old_cap:
        key = load_ptr(old_slots, index * 8)
        if ptr_is_null(key) == 0:
            result: i64 = _managed_pointer_find_slot(new_slots, new_cap, key)
            store_ptr(new_slots, (-result - 1) * 8, key)
        index = index + 1
    _index_table_publish(
        slots_cell, global_addr("pcc_py_gc_managed_pointer_cap"), new_slots, new_cap
    )
    _index_table_release(slots_cell, old_slots)
    return 0


@c_abi_export("pcc_gc_managed_pointer_index_seen")
def pcc_gc_managed_pointer_index_seen(obj) -> i64:
    """1 when ``obj`` is in the managed-pointer set, without the graph lock.

    0 = unknown; see pcc_gc_index_py_seen.
    """
    return pcc_gc_index_py_seen(
        global_addr("pcc_py_gc_managed_pointer_published"), obj, 8
    )


@c_abi_export("pcc_gc_object_index_seen")
def pcc_gc_object_index_seen(obj) -> i64:
    """1 when ``obj`` has a GC1-4 object node, without the graph lock.

    0 = unknown; see pcc_gc_index_py_seen.
    """
    return pcc_gc_index_py_seen(
        global_addr("pcc_py_gc_object_published"), obj, 16
    )


@c_abi_export("pcc_gc_managed_pointer_index_contains")
def pcc_gc_managed_pointer_index_contains(obj) -> i64:
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0 or ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj) != 0:
        return 0
    result: i64 = _managed_pointer_find_slot(
        slots,
        load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0),
        obj,
    )
    if result >= 0:
        return 1
    return 0


@c_abi_export("pcc_gc_managed_pointer_index_insert")
def pcc_gc_managed_pointer_index_insert(obj) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return -1
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0:
        if _managed_pointer_rehash(256) != 0:
            return -1
        slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    result: i64 = _managed_pointer_find_slot(slots, cap, obj)
    if result >= 0:
        return 0
    count: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0)
    if count + 1 > logical_shift_right_i64(cap, 1):
        if _managed_pointer_rehash(cap * 2) != 0:
            return -1
        slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
        cap = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
        result = _managed_pointer_find_slot(slots, cap, obj)
    store_ptr(slots, (-result - 1) * 8, obj)
    store_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0, count + 1)
    return 1


@c_abi_export("pcc_gc_managed_pointer_index_remove")
def pcc_gc_managed_pointer_index_remove(obj) -> i64:
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0 or ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    hole: i64 = _managed_pointer_find_slot(slots, cap, obj)
    if hole < 0:
        return 0
    mask: i64 = cap - 1
    probe: i64 = hole
    done: i64 = 0
    while done == 0:
        probe = (probe + 1) & mask
        key = load_ptr(slots, probe * 8)
        if ptr_is_null(key) != 0:
            done: i64 = 1
        else:
            home: i64 = pcc_gc_index_py_hash_ptr(key) & mask
            if ((probe - home) & mask) >= ((probe - hole) & mask):
                store_ptr(slots, hole * 8, key)
                hole = probe
    store_ptr(slots, hole * 8, null())
    count: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0)
    store_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0, count - 1)
    return 1


@c_abi_export("pcc_gc_managed_pointer_index_enumerate")
def pcc_gc_managed_pointer_index_enumerate(out, capacity: i64, required_flags: i64) -> i64:
    """Collect the registered objects outside object slabs (large objects and
    other per-object registrations) whose header flags hold every bit of
    ``required_flags``. GC0 registers its large objects here; the tracing
    collectors index every object in their own object index instead.

    Same contract as pcc_allocator_object_cells_enumerate: at most
    ``capacity`` pointers go to ``out``, the return value counts every match,
    and the caller has stopped the world. Removal back-shifts entries rather
    than leaving tombstones, so every non-null slot is a registered object.
    """
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    total: i64 = 0
    index: i64 = 0
    while index < cap:
        obj = load_ptr(slots, index * 8)
        if ptr_is_null(obj) == 0:
            if (load_i32(obj, 12) & required_flags) == required_flags:
                if total < capacity:
                    store_ptr(out, total * 8, obj)
                total = total + 1
        index = index + 1
    return total


@c_abi_export("py_gc_index_find")
def py_gc_index_find(obj: c_ptr) -> c_ptr:
    side = pcc_allocator_object_side_slot(obj, 0)
    if ptr_is_null(side) == 0:
        return load_ptr(side, 0)
    # Not an object cell, or its slab never tracked anything: in the latter
    # case its shard hash table cannot hold the key either and the probe misses.
    cells = pcc_gc_tracked_primary_index_cells(obj)
    return pcc_gc_index_py_find(
        ptr_add(cells, _PRIMARY_SLOTS),
        ptr_add(cells, _PRIMARY_CAPACITY),
        obj,
        1,
    )


@c_abi_export("py_gc_index_insert")
def py_gc_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj):
        return -1
    side = pcc_allocator_object_side_slot(obj, 1)
    if ptr_is_null(side) == 0:
        if ptr_is_null(load_ptr(side, 0)) == 0:
            return 0
        # A side word uses NULL for "not tracked", so it cannot hold one.
        if ptr_is_null(node):
            return -1
        store_ptr(side, 0, node)
        return 1
    # An object cell whose slab side array could not be mapped fails like a
    # hash growth failure; it must never land in the hash table, where
    # lookups through a later side array would not see it.
    if ptr_is_null(pcc_allocator_granule_object_slot(obj)) == 0:
        return -1
    cells = pcc_gc_tracked_primary_index_cells(obj)
    return pcc_gc_index_py_insert(
        ptr_add(cells, _PRIMARY_SLOTS),
        ptr_add(cells, _PRIMARY_CAPACITY),
        ptr_add(cells, _PRIMARY_COUNT),
        ptr_add(cells, _PRIMARY_USED),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("py_gc_index_remove")
def py_gc_index_remove(obj: c_ptr) -> c_ptr:
    side = pcc_allocator_object_side_slot(obj, 0)
    if ptr_is_null(side) == 0:
        node = load_ptr(side, 0)
        store_ptr(side, 0, null())
        return node
    cells = pcc_gc_tracked_primary_index_cells(obj)
    return pcc_gc_index_py_remove(
        ptr_add(cells, _PRIMARY_SLOTS),
        ptr_add(cells, _PRIMARY_CAPACITY),
        ptr_add(cells, _PRIMARY_COUNT),
        ptr_add(cells, _PRIMARY_USED),
        obj,
        1,
    )


@c_abi_export("pcc_gc_object_index_find")
def pcc_gc_object_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find_published(
        global_addr("pcc_py_gc_object_published"), obj
    )


@c_abi_export("pcc_gc_object_index_insert")
def pcc_gc_object_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
        obj,
        node,
        1,
        1,
        16384,
    )


@c_abi_export("pcc_gc_object_index_plan_capacity")
def pcc_gc_object_index_plan_capacity(extra: i64) -> i64:
    if extra <= 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_object_cap"), 0)
    count: i64 = load_i64(global_addr("pcc_py_gc_object_count"), 0)
    used: i64 = load_i64(global_addr("pcc_py_gc_object_used"), 0)
    if cap > 0 and used + extra <= logical_shift_right_i64(cap, 1):
        return 0
    wanted: i64 = count + extra
    desired: i64 = wanted * 4
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        desired = wanted * 2
    if desired < 16384:
        desired = 16384
    capacity: i64 = pcc_gc_index_py_next_pow2(desired)
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > capacity:
            capacity = grown
    return capacity


@c_abi_export("pcc_gc_object_index_plan_commit")
def pcc_gc_object_index_plan_commit(
    prepared_slots_io: c_ptr, prepared_cap: i64, extra: i64
) -> i64:
    if ptr_is_null(prepared_slots_io) != 0:
        return -1
    required: i64 = pcc_gc_object_index_plan_capacity(extra)
    if required < 0:
        return -1
    if required == 0:
        return 0
    new_slots = load_ptr(prepared_slots_io, 0)
    if ptr_is_null(new_slots) != 0 or prepared_cap < required:
        return -1

    old_slots = global_load_ptr("pcc_py_gc_object_slots")
    old_cap: i64 = load_i64(global_addr("pcc_py_gc_object_cap"), 0)
    new_used: i64 = 0
    index: i64 = 0
    while index < old_cap:
        old_offset: i64 = index * 16
        if ptr_is_null(load_ptr(old_slots, old_offset)) == 0:
            key = load_ptr(old_slots, old_offset)
            result: i64 = pcc_gc_index_py_find_slot(
                new_slots, prepared_cap, key
            )
            new_offset: i64 = (-result - 1) * 16
            store_ptr(new_slots, new_offset, key)
            store_ptr(
                new_slots,
                new_offset + 8,
                load_ptr(old_slots, old_offset + 8),
            )
            new_used = new_used + 1
        index = index + 1
    slots_cell = global_addr("pcc_py_gc_object_slots")
    _index_table_publish(
        slots_cell, global_addr("pcc_py_gc_object_cap"), new_slots, prepared_cap
    )
    store_i64(global_addr("pcc_py_gc_object_used"), 0, new_used)
    # Unlocked readers may still probe the old table: it is retired here and
    # the caller, which frees what it gets back, gets nothing.
    _index_table_release(slots_cell, old_slots)
    store_ptr(prepared_slots_io, 0, null())
    return 1


@c_abi_export("pcc_gc_object_index_insert_preallocated")
def pcc_gc_object_index_insert_preallocated(obj: c_ptr, node: c_ptr) -> i64:
    if (
        ptr_is_null(obj) != 0
        or is_tagged_int(obj) != 0
        or ptr_is_null(node) != 0
    ):
        return -1
    slots = global_load_ptr("pcc_py_gc_object_slots")
    if ptr_is_null(slots) != 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_object_cap"), 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    if result >= 0:
        return 0
    used: i64 = load_i64(global_addr("pcc_py_gc_object_used"), 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        return -1
    offset: i64 = (-result - 1) * 16
    store_ptr(slots, offset, obj)
    store_ptr(slots, offset + 8, node)
    store_i64(
        global_addr("pcc_py_gc_object_count"),
        0,
        load_i64(global_addr("pcc_py_gc_object_count"), 0) + 1,
    )
    store_i64(global_addr("pcc_py_gc_object_used"), 0, used + 1)
    return 1


@c_abi_export("pcc_gc_object_index_remove")
def pcc_gc_object_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_object_index_clear")
def pcc_gc_object_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
    )


@c_abi_export("pcc_gc_forwarding_plan_index_capacity")
def pcc_gc_forwarding_plan_index_capacity(kind: i64, extra: i64) -> i64:
    if extra <= 0:
        return -1
    cap: i64 = 0
    count: i64 = 0
    used: i64 = 0
    if kind == 0:
        cap = load_i64(global_addr("pcc_py_gc_forwarding_cap"), 0)
        count = load_i64(global_addr("pcc_py_gc_forwarding_count"), 0)
        used = load_i64(global_addr("pcc_py_gc_forwarding_used"), 0)
    elif kind == 1:
        cap = load_i64(global_addr("pcc_py_gc_forwarding_target_cap"), 0)
        count = load_i64(global_addr("pcc_py_gc_forwarding_target_count"), 0)
        used = load_i64(global_addr("pcc_py_gc_forwarding_target_used"), 0)
    elif kind == 2:
        cap = load_i64(global_addr("pcc_py_gc_identity_cap"), 0)
        count = load_i64(global_addr("pcc_py_gc_identity_count"), 0)
        used = load_i64(global_addr("pcc_py_gc_identity_used"), 0)
    else:
        return -1
    if cap > 0 and used + extra <= logical_shift_right_i64(cap, 1):
        return 0
    wanted: i64 = count + extra
    desired: i64 = wanted * 4
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        desired = wanted * 2
    if desired < 256:
        desired = 256
    capacity: i64 = pcc_gc_index_py_next_pow2(desired)
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > capacity:
            capacity = grown
    return capacity


@c_abi_export("pcc_gc_forwarding_plan_index_commit")
def pcc_gc_forwarding_plan_index_commit(
    kind: i64,
    prepared_slots_io: c_ptr,
    prepared_cap: i64,
    extra: i64,
) -> i64:
    if ptr_is_null(prepared_slots_io) != 0:
        return -1
    slots_cell = null()
    cap_cell = null()
    used_cell = null()
    if kind == 0:
        slots_cell = global_addr("pcc_py_gc_forwarding_slots")
        cap_cell = global_addr("pcc_py_gc_forwarding_cap")
        used_cell = global_addr("pcc_py_gc_forwarding_used")
    elif kind == 1:
        slots_cell = global_addr("pcc_py_gc_forwarding_target_slots")
        cap_cell = global_addr("pcc_py_gc_forwarding_target_cap")
        used_cell = global_addr("pcc_py_gc_forwarding_target_used")
    elif kind == 2:
        slots_cell = global_addr("pcc_py_gc_identity_slots")
        cap_cell = global_addr("pcc_py_gc_identity_cap")
        used_cell = global_addr("pcc_py_gc_identity_used")
    else:
        return -1
    required: i64 = pcc_gc_forwarding_plan_index_capacity(kind, extra)
    if required < 0:
        return -1
    if required == 0:
        return 0
    new_slots = load_ptr(prepared_slots_io, 0)
    if ptr_is_null(new_slots) != 0 or prepared_cap < required:
        return -1

    old_slots = load_ptr(slots_cell, 0)
    old_cap: i64 = load_i64(cap_cell, 0)
    new_used: i64 = 0
    index: i64 = 0
    while index < old_cap:
        old_offset: i64 = index * 16
        if ptr_is_null(load_ptr(old_slots, old_offset)) == 0:
            key = load_ptr(old_slots, old_offset)
            result: i64 = pcc_gc_index_py_find_slot(
                new_slots, prepared_cap, key
            )
            new_offset: i64 = (-result - 1) * 16
            store_ptr(new_slots, new_offset, key)
            store_ptr(
                new_slots,
                new_offset + 8,
                load_ptr(old_slots, old_offset + 8),
            )
            new_used = new_used + 1
        index = index + 1
    _index_table_publish(slots_cell, cap_cell, new_slots, prepared_cap)
    store_i64(used_cell, 0, new_used)
    if ptr_is_null(_index_published_cell(slots_cell)) == 0:
        # Retired for unlocked readers; the caller frees what it gets back.
        _index_table_release(slots_cell, old_slots)
        store_ptr(prepared_slots_io, 0, null())
    else:
        store_ptr(prepared_slots_io, 0, old_slots)
    return 1


@c_abi_export("pcc_gc_forwarding_plan_index_insert")
def pcc_gc_forwarding_plan_index_insert(
    kind: i64, obj: c_ptr, node: c_ptr
) -> i64:
    if (
        ptr_is_null(obj) != 0
        or is_tagged_int(obj) != 0
        or ptr_is_null(node) != 0
    ):
        return -1
    slots_cell = null()
    cap_cell = null()
    count_cell = null()
    used_cell = null()
    if kind == 0:
        slots_cell = global_addr("pcc_py_gc_forwarding_slots")
        cap_cell = global_addr("pcc_py_gc_forwarding_cap")
        count_cell = global_addr("pcc_py_gc_forwarding_count")
        used_cell = global_addr("pcc_py_gc_forwarding_used")
    elif kind == 1:
        slots_cell = global_addr("pcc_py_gc_forwarding_target_slots")
        cap_cell = global_addr("pcc_py_gc_forwarding_target_cap")
        count_cell = global_addr("pcc_py_gc_forwarding_target_count")
        used_cell = global_addr("pcc_py_gc_forwarding_target_used")
    elif kind == 2:
        slots_cell = global_addr("pcc_py_gc_identity_slots")
        cap_cell = global_addr("pcc_py_gc_identity_cap")
        count_cell = global_addr("pcc_py_gc_identity_count")
        used_cell = global_addr("pcc_py_gc_identity_used")
    else:
        return -1
    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots) != 0:
        return -1
    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    if result >= 0:
        return 0
    used: i64 = load_i64(used_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        return -1
    offset: i64 = (-result - 1) * 16
    store_ptr(slots, offset, obj)
    store_ptr(slots, offset + 8, node)
    store_i64(count_cell, 0, load_i64(count_cell, 0) + 1)
    store_i64(used_cell, 0, used + 1)
    return 1


@c_abi_export("pcc_gc_forwarding_index_find")
def pcc_gc_forwarding_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find_published(
        global_addr("pcc_py_gc_forwarding_published"), obj
    )


@c_abi_export("pcc_gc_forwarding_index_insert")
def pcc_gc_forwarding_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_forwarding_index_remove")
def pcc_gc_forwarding_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_index_clear")
def pcc_gc_forwarding_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
    )


@c_abi_export("pcc_gc_forwarding_target_index_find")
def pcc_gc_forwarding_target_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find_published(
        global_addr("pcc_py_gc_forwarding_target_published"), obj
    )


@c_abi_export("pcc_gc_forwarding_target_index_insert")
def pcc_gc_forwarding_target_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_forwarding_target_index_upsert")
def pcc_gc_forwarding_target_index_upsert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        node,
        1,
        256,
    )


@c_abi_export("pcc_gc_forwarding_target_index_remove")
def pcc_gc_forwarding_target_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_target_index_clear")
def pcc_gc_forwarding_target_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
    )


@c_abi_export("pcc_gc_identity_index_find")
def pcc_gc_identity_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_identity_index_insert")
def pcc_gc_identity_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_identity_index_remove")
def pcc_gc_identity_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_identity_index_clear")
def pcc_gc_identity_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
    )


@c_abi_export("pcc_gc_frame_index_find")
def pcc_gc_frame_index_find(slots: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        slots,
        0,
    )


@c_abi_export("pcc_gc_frame_index_plan_capacity")
def pcc_gc_frame_index_plan_capacity(extra: i64) -> i64:
    if extra <= 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_frame_cap"), 0)
    count: i64 = load_i64(global_addr("pcc_py_gc_frame_count"), 0)
    used: i64 = load_i64(global_addr("pcc_py_gc_frame_used"), 0)
    if cap > 0 and used + extra <= logical_shift_right_i64(cap, 1):
        return 0
    wanted: i64 = count + extra
    desired: i64 = wanted * 4
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        desired = wanted * 2
    if desired < 256:
        desired = 256
    capacity: i64 = pcc_gc_index_py_next_pow2(desired)
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > capacity:
            capacity = grown
    return capacity


@c_abi_export("pcc_gc_frame_index_plan_commit")
def pcc_gc_frame_index_plan_commit(
    prepared_slots_io: c_ptr,
    prepared_cap: i64,
    extra: i64,
) -> i64:
    if ptr_is_null(prepared_slots_io) != 0:
        return -1
    required: i64 = pcc_gc_frame_index_plan_capacity(extra)
    if required < 0:
        return -1
    if required == 0:
        return 0
    new_slots = load_ptr(prepared_slots_io, 0)
    if ptr_is_null(new_slots) != 0 or prepared_cap < required:
        return -1

    slots_cell = global_addr("pcc_py_gc_frame_slots")
    cap_cell = global_addr("pcc_py_gc_frame_cap")
    used_cell = global_addr("pcc_py_gc_frame_used")
    old_slots = load_ptr(slots_cell, 0)
    old_cap: i64 = load_i64(cap_cell, 0)
    new_used: i64 = 0
    index: i64 = 0
    while index < old_cap:
        old_offset: i64 = index * 16
        if ptr_is_null(load_ptr(old_slots, old_offset)) == 0:
            key = load_ptr(old_slots, old_offset)
            result: i64 = pcc_gc_index_py_find_slot(
                new_slots, prepared_cap, key
            )
            new_offset: i64 = (-result - 1) * 16
            store_ptr(new_slots, new_offset, key)
            store_ptr(
                new_slots,
                new_offset + 8,
                load_ptr(old_slots, old_offset + 8),
            )
            new_used = new_used + 1
        index = index + 1
    _index_table_publish(slots_cell, cap_cell, new_slots, prepared_cap)
    store_i64(used_cell, 0, new_used)
    store_ptr(prepared_slots_io, 0, old_slots)
    return 1


@c_abi_export("pcc_gc_frame_index_insert")
def pcc_gc_frame_index_insert(slots: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        node,
        0,
        0,
        256,
    )


@c_abi_export("pcc_gc_frame_index_replace")
def pcc_gc_frame_index_replace(slots: c_ptr, node: c_ptr) -> c_ptr:
    return pcc_gc_index_py_replace_raw(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        node,
        256,
    )


@c_abi_export("pcc_gc_frame_index_replace_preallocated")
def pcc_gc_frame_index_replace_preallocated(
    slots: c_ptr, node: c_ptr
) -> c_ptr:
    if ptr_is_null(slots) != 0 or ptr_is_null(node) != 0:
        return node
    table = global_load_ptr("pcc_py_gc_frame_slots")
    if ptr_is_null(table) != 0:
        return node
    cap: i64 = load_i64(global_addr("pcc_py_gc_frame_cap"), 0)
    result: i64 = pcc_gc_index_py_find_slot(table, cap, slots)
    if result >= 0:
        offset: i64 = result * 16
        old = load_ptr(table, offset + 8)
        store_ptr(table, offset + 8, node)
        return old
    used: i64 = load_i64(global_addr("pcc_py_gc_frame_used"), 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        return node
    count: i64 = load_i64(global_addr("pcc_py_gc_frame_count"), 0)
    offset = (-result - 1) * 16
    store_ptr(table, offset, slots)
    store_ptr(table, offset + 8, node)
    store_i64(global_addr("pcc_py_gc_frame_used"), 0, used + 1)
    store_i64(global_addr("pcc_py_gc_frame_count"), 0, count + 1)
    return null()


@c_abi_export("pcc_gc_frame_index_remove")
def pcc_gc_frame_index_remove(slots: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        0,
    )


@c_abi_export("pcc_gc_frame_index_clear")
def pcc_gc_frame_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
    )


@c_abi_export("pcc_gc_zpage_owner_index_find")
def pcc_gc_zpage_owner_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_zpage_owner_index_insert")
def pcc_gc_zpage_owner_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_owner_index_upsert")
def pcc_gc_zpage_owner_index_upsert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        node,
        1,
        256,
    )


@c_abi_export("pcc_gc_zpage_owner_index_plan_capacity")
def pcc_gc_zpage_owner_index_plan_capacity(extra: i64) -> i64:
    if extra <= 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_cap"), 0)
    count: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_count"), 0)
    used: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_used"), 0)
    if cap > 0 and used + extra <= logical_shift_right_i64(cap, 1):
        return 0
    wanted: i64 = count + extra
    desired: i64 = wanted * 4
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        desired = wanted * 2
    if desired < 256:
        desired = 256
    capacity: i64 = pcc_gc_index_py_next_pow2(desired)
    if cap > 0 and wanted > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > capacity:
            capacity = grown
    return capacity


@c_abi_export("pcc_gc_zpage_owner_index_plan_commit")
def pcc_gc_zpage_owner_index_plan_commit(
    prepared_slots_io: c_ptr, prepared_cap: i64, extra: i64
) -> i64:
    if ptr_is_null(prepared_slots_io) != 0:
        return -1
    required: i64 = pcc_gc_zpage_owner_index_plan_capacity(extra)
    if required < 0:
        return -1
    if required == 0:
        return 0
    new_slots = load_ptr(prepared_slots_io, 0)
    if ptr_is_null(new_slots) != 0 or prepared_cap < required:
        return -1
    old_slots = global_load_ptr("pcc_py_gc_zpage_owner_slots")
    old_cap: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_cap"), 0)
    new_used: i64 = 0
    index: i64 = 0
    while index < old_cap:
        old_offset: i64 = index * 16
        if ptr_is_null(load_ptr(old_slots, old_offset)) == 0:
            key = load_ptr(old_slots, old_offset)
            result: i64 = pcc_gc_index_py_find_slot(
                new_slots, prepared_cap, key
            )
            new_offset: i64 = (-result - 1) * 16
            store_ptr(new_slots, new_offset, key)
            store_ptr(
                new_slots,
                new_offset + 8,
                load_ptr(old_slots, old_offset + 8),
            )
            new_used = new_used + 1
        index = index + 1
    global_store_ptr("pcc_py_gc_zpage_owner_slots", new_slots)
    store_i64(global_addr("pcc_py_gc_zpage_owner_cap"), 0, prepared_cap)
    store_i64(global_addr("pcc_py_gc_zpage_owner_used"), 0, new_used)
    store_ptr(prepared_slots_io, 0, old_slots)
    return 1


@c_abi_export("pcc_gc_zpage_owner_index_upsert_preallocated")
def pcc_gc_zpage_owner_index_upsert_preallocated(
    obj: c_ptr, node: c_ptr
) -> i64:
    if (
        ptr_is_null(obj) != 0
        or is_tagged_int(obj) != 0
        or ptr_is_null(node) != 0
    ):
        return -1
    slots = global_load_ptr("pcc_py_gc_zpage_owner_slots")
    if ptr_is_null(slots) != 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_cap"), 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    offset: i64 = 0
    if result >= 0:
        offset = result * 16
        store_ptr(slots, offset + 8, node)
        return 0
    used: i64 = load_i64(global_addr("pcc_py_gc_zpage_owner_used"), 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        return -1
    offset = (-result - 1) * 16
    store_ptr(slots, offset, obj)
    store_ptr(slots, offset + 8, node)
    store_i64(
        global_addr("pcc_py_gc_zpage_owner_count"),
        0,
        load_i64(global_addr("pcc_py_gc_zpage_owner_count"), 0) + 1,
    )
    store_i64(global_addr("pcc_py_gc_zpage_owner_used"), 0, used + 1)
    return 1


@c_abi_export("pcc_gc_zpage_owner_index_remove")
def pcc_gc_zpage_owner_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_zpage_owner_index_clear")
def pcc_gc_zpage_owner_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
    )


@c_abi_export("pcc_gc_zpage_page_index_find")
def pcc_gc_zpage_page_index_find(page: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        page,
        0,
    )


@c_abi_export("pcc_gc_zpage_page_index_insert")
def pcc_gc_zpage_page_index_insert(page: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        node,
        0,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_page_index_upsert")
def pcc_gc_zpage_page_index_upsert(page: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        node,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_page_index_remove")
def pcc_gc_zpage_page_index_remove(page: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        0,
    )


@c_abi_export("pcc_gc_zpage_page_index_clear")
def pcc_gc_zpage_page_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
    )


@c_abi_export("pcc_gc_relocation_member_count")
def pcc_gc_relocation_member_count(obj: c_ptr) -> i64:
    """Exact linked multiplicity under the graph lock, or -1 if unknown."""
    if load_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0) != 0:
        return -1
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    slots = global_load_ptr("pcc_py_gc_relocation_member_slots")
    cap: i64 = load_i64(global_addr("pcc_py_gc_relocation_member_cap"), 0)
    count: i64 = load_i64(global_addr("pcc_py_gc_relocation_member_count"), 0)
    used: i64 = load_i64(global_addr("pcc_py_gc_relocation_member_used"), 0)
    if ptr_is_null(slots) != 0:
        if cap == 0 and count == 0 and used == 0:
            return 0
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return -1
    if (
        cap < 8
        or (cap & (cap - 1)) != 0
        or count < 0
        or used != count
        or used > logical_shift_right_i64(cap, 1)
    ):
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return -1
    if count == 0:
        return 0
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    if result < 0:
        return 0
    multiplicity: i64 = load_i64(slots, result * 16 + 8)
    if multiplicity <= 0:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return -1
    return multiplicity


@c_abi_export("pcc_gc_relocation_member_note")
def pcc_gc_relocation_member_note(obj: c_ptr) -> None:
    # Exactly one committed list insertion, including a duplicate occurrence.
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return
    multiplicity: i64 = pcc_gc_relocation_member_count(obj)
    if multiplicity < 0:
        return
    if multiplicity == 9223372036854775807:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return
    if multiplicity > 0:
        slots = global_load_ptr("pcc_py_gc_relocation_member_slots")
        cap: i64 = load_i64(global_addr("pcc_py_gc_relocation_member_cap"), 0)
        result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
        if result < 0:
            store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
            return
        store_i64(slots, result * 16 + 8, multiplicity + 1)
        return

    # The generic insertion leaves an existing value unchanged and may
    # rehash on a new key. Only a newly inserted key can start at one, and
    # its final slot must be found in the reloaded table after any rehash.
    inserted: i64 = pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_relocation_member_slots"),
        global_addr("pcc_py_gc_relocation_member_cap"),
        global_addr("pcc_py_gc_relocation_member_count"),
        global_addr("pcc_py_gc_relocation_member_used"),
        obj,
        obj,
        1,
        0,
        256,
    )
    if inserted != 1:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return
    slots = global_load_ptr("pcc_py_gc_relocation_member_slots")
    cap = load_i64(global_addr("pcc_py_gc_relocation_member_cap"), 0)
    result = pcc_gc_index_py_find_slot(slots, cap, obj)
    if result < 0:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return
    store_i64(slots, result * 16 + 8, 1)


@c_abi_export("pcc_gc_relocation_member_maybe")
def pcc_gc_relocation_member_maybe(obj: c_ptr) -> i64:
    """0 only when obj is certainly outside the relocation set."""
    return 0 if pcc_gc_relocation_member_count(obj) == 0 else 1


@c_abi_export("pcc_gc_relocation_member_forget")
def pcc_gc_relocation_member_forget(obj: c_ptr) -> None:
    # Exactly one committed unlink. This path never allocates or frees the
    # table, including the last key, so a copy commit can safely call it.
    multiplicity: i64 = pcc_gc_relocation_member_count(obj)
    if multiplicity < 0:
        return
    if multiplicity == 0:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
        return
    if multiplicity > 1:
        slots = global_load_ptr("pcc_py_gc_relocation_member_slots")
        cap: i64 = load_i64(global_addr("pcc_py_gc_relocation_member_cap"), 0)
        result: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
        if result < 0:
            store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)
            return
        store_i64(slots, result * 16 + 8, multiplicity - 1)
        return
    removed = pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_relocation_member_slots"),
        global_addr("pcc_py_gc_relocation_member_cap"),
        global_addr("pcc_py_gc_relocation_member_count"),
        global_addr("pcc_py_gc_relocation_member_used"),
        obj,
        1,
    )
    if ptr_is_null(removed) != 0:
        store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 1)


@c_abi_export("pcc_gc_relocation_member_clear")
def pcc_gc_relocation_member_clear() -> None:
    # Caller holds the graph lock and has proved the relocation list empty.
    # Preserve explicit clear's original table-release policy, including
    # when reset switches away from GC4. This may free memory: never call
    # from the preallocated copy commit, whose physical frees are deferred.
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_relocation_member_slots"),
        global_addr("pcc_py_gc_relocation_member_cap"),
        global_addr("pcc_py_gc_relocation_member_count"),
        global_addr("pcc_py_gc_relocation_member_used"),
    )
    store_i64(global_addr("pcc_py_gc_relocation_member_incomplete"), 0, 0)


@c_abi_export("pcc_gc_remembered_member_maybe")
def pcc_gc_remembered_member_maybe(obj: c_ptr) -> i64:
    """0 only when obj is certainly outside the remembered owner superset."""
    if (
        load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0
        or load_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0) != 0
    ):
        return 1
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    slots = global_load_ptr("pcc_py_gc_remembered_member_slots")
    cap: i64 = load_i64(global_addr("pcc_py_gc_remembered_member_cap"), 0)
    count: i64 = load_i64(global_addr("pcc_py_gc_remembered_member_count"), 0)
    used: i64 = load_i64(global_addr("pcc_py_gc_remembered_member_used"), 0)
    if ptr_is_null(slots) != 0:
        if cap == 0 and count == 0 and used == 0:
            return 0
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return 1
    if (
        cap < 8
        or (cap & (cap - 1)) != 0
        or count < 0
        or used != count
        or used > logical_shift_right_i64(cap, 1)
    ):
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return 1
    if count == 0:
        return 0
    return 1 if pcc_gc_index_py_find_slot(slots, cap, obj) >= 0 else 0


@c_abi_export("pcc_gc_remembered_member_note")
def pcc_gc_remembered_member_note(obj: c_ptr) -> None:
    # Call before publishing a new owner, without holding a list-node cursor
    # across this potentially allocating operation. Nested mutations cannot
    # touch the in-flight table or be mistaken for an unmodified exact epoch.
    if load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0:
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return
    present: i64 = pcc_gc_remembered_member_maybe(obj)
    if load_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0) != 0:
        return
    if present != 0:
        return
    store_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0, 1)
    # The value is the head of obj's remembered-entry chain, empty until the
    # caller links its first entry (pcc_gc_remembered_member_set_head).
    inserted: i64 = pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_remembered_member_slots"),
        global_addr("pcc_py_gc_remembered_member_cap"),
        global_addr("pcc_py_gc_remembered_member_count"),
        global_addr("pcc_py_gc_remembered_member_used"),
        obj,
        null(),
        1,
        0,
        256,
    )
    if inserted != 1:
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
    store_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0, 0)


@c_abi_export("pcc_gc_remembered_member_exact")
def pcc_gc_remembered_member_exact() -> i64:
    """1 when every remembered owner is in the index and none is being
    inserted: membership answers are exact and each owner's chain head
    reaches all of its entries."""
    if (
        load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0
        or load_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0) != 0
    ):
        return 0
    return 1


@c_abi_export("pcc_gc_remembered_member_taint")
def pcc_gc_remembered_member_taint() -> None:
    """The remembered set changed while the index could not follow it (it
    was being written, or already incomplete): membership stays a superset
    and no owner chain is trusted until the next clear."""
    store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)


@c_abi_export("pcc_gc_remembered_member_head")
def pcc_gc_remembered_member_head(obj: c_ptr) -> c_ptr:
    """The first entry of obj's remembered chain; NULL when obj holds none."""
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    slots = global_load_ptr("pcc_py_gc_remembered_member_slots")
    if ptr_is_null(slots) != 0:
        return null()
    cap: i64 = load_i64(global_addr("pcc_py_gc_remembered_member_cap"), 0)
    index: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    if index < 0:
        return null()
    return load_ptr(slots, index * 16 + 8)


@c_abi_export("pcc_gc_remembered_member_set_head")
def pcc_gc_remembered_member_set_head(obj: c_ptr, head: c_ptr) -> i64:
    """Record head as obj's chain head; -1 when obj is not in the index."""
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return -1
    slots = global_load_ptr("pcc_py_gc_remembered_member_slots")
    if ptr_is_null(slots) != 0:
        return -1
    cap: i64 = load_i64(global_addr("pcc_py_gc_remembered_member_cap"), 0)
    index: i64 = pcc_gc_index_py_find_slot(slots, cap, obj)
    if index < 0:
        return -1
    store_ptr(slots, index * 16 + 8, head)
    return 0


@c_abi_export("pcc_gc_remembered_member_require")
def pcc_gc_remembered_member_require(obj: c_ptr) -> None:
    # A callback may have removed a pre-noted target before retarget commits.
    # Never allocate while holding the cursor: a missing key makes all
    # negative membership unknown until the authoritative list is cleared.
    if (
        load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0
        or pcc_gc_remembered_member_maybe(obj) == 0
    ):
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)


@c_abi_export("pcc_gc_remembered_member_forget")
def pcc_gc_remembered_member_forget(obj: c_ptr) -> None:
    # The caller has just proved no linked slot belongs to obj. This raw
    # backward-shift deletion cannot allocate, free, or invoke a callback.
    if load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0:
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return
    if pcc_gc_remembered_member_maybe(obj) == 0:
        return
    if load_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0) != 0:
        return
    pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_remembered_member_slots"),
        global_addr("pcc_py_gc_remembered_member_cap"),
        global_addr("pcc_py_gc_remembered_member_count"),
        global_addr("pcc_py_gc_remembered_member_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_remembered_member_clear")
def pcc_gc_remembered_member_clear() -> None:
    # Call immediately after detaching the authoritative list. Publish the
    # empty index before free: a callback may build the next epoch. Do not
    # write any membership state after free, including the incomplete flag.
    if load_i64(global_addr("pcc_py_gc_remembered_member_busy"), 0) != 0:
        store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 1)
        return
    slots = global_load_ptr("pcc_py_gc_remembered_member_slots")
    global_store_ptr("pcc_py_gc_remembered_member_slots", null())
    store_i64(global_addr("pcc_py_gc_remembered_member_cap"), 0, 0)
    store_i64(global_addr("pcc_py_gc_remembered_member_count"), 0, 0)
    store_i64(global_addr("pcc_py_gc_remembered_member_used"), 0, 0)
    store_i64(global_addr("pcc_py_gc_remembered_member_incomplete"), 0, 0)
    free(slots)


@c_abi_export("pcc_gc_store_buffer_member_note")
def pcc_gc_store_buffer_member_note(owner: c_ptr) -> None:
    if load_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0) != 0:
        return
    inserted: i64 = pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_store_buffer_member_slots"),
        global_addr("pcc_py_gc_store_buffer_member_cap"),
        global_addr("pcc_py_gc_store_buffer_member_count"),
        global_addr("pcc_py_gc_store_buffer_member_used"),
        owner,
        owner,
        1,
        0,
        256,
    )
    if inserted < 0:
        store_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0, 1)


@c_abi_export("pcc_gc_store_buffer_member_taint")
def pcc_gc_store_buffer_member_taint() -> None:
    store_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0, 1)


@c_abi_export("pcc_gc_store_buffer_member_maybe")
def pcc_gc_store_buffer_member_maybe(owner: c_ptr) -> i64:
    """0 only when owner certainly has no store-buffer entry."""
    if load_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0) != 0:
        return 1
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    slots = global_load_ptr("pcc_py_gc_store_buffer_member_slots")
    if ptr_is_null(slots) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_store_buffer_member_cap"), 0)
    return 1 if pcc_gc_index_py_find_slot(slots, cap, owner) >= 0 else 0


@c_abi_export("pcc_gc_store_buffer_member_forget")
def pcc_gc_store_buffer_member_forget(owner: c_ptr) -> None:
    if load_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0) != 0:
        return
    pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_store_buffer_member_slots"),
        global_addr("pcc_py_gc_store_buffer_member_cap"),
        global_addr("pcc_py_gc_store_buffer_member_count"),
        global_addr("pcc_py_gc_store_buffer_member_used"),
        owner,
        1,
    )


@c_abi_export("pcc_gc_store_buffer_member_owner_count")
def pcc_gc_store_buffer_member_owner_count() -> i64:
    """Distinct store-buffer owners, or -1 while the epoch is incomplete."""
    if load_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0) != 0:
        return -1
    return load_i64(global_addr("pcc_py_gc_store_buffer_member_count"), 0)


@c_abi_export("pcc_gc_store_buffer_member_clear")
def pcc_gc_store_buffer_member_clear() -> None:
    # Call after both queues are empty; this also ends an incomplete epoch.
    slots = global_load_ptr("pcc_py_gc_store_buffer_member_slots")
    global_store_ptr("pcc_py_gc_store_buffer_member_slots", null())
    store_i64(global_addr("pcc_py_gc_store_buffer_member_cap"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_member_count"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_member_used"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_member_incomplete"), 0, 0)
    free(slots)


@c_abi_export("pcc_gc_store_buffer_index_taint")
def pcc_gc_store_buffer_index_taint() -> None:
    store_i64(global_addr("pcc_py_gc_store_buffer_index_incomplete"), 0, 1)


@c_abi_export("pcc_gc_store_buffer_owner_entries")
def pcc_gc_store_buffer_owner_entries(owner: c_ptr) -> i64:
    """Buffered entries recorded for owner, or -1 while the index is incomplete."""
    if load_i64(global_addr("pcc_py_gc_store_buffer_index_incomplete"), 0) != 0:
        return -1
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    slots = global_load_ptr("pcc_py_gc_store_buffer_owner_slots")
    if ptr_is_null(slots) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_store_buffer_owner_cap"), 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, owner)
    if result < 0:
        return 0
    return load_i64(slots, result * 16 + 8)


@c_abi_export("pcc_gc_store_buffer_slot_pending")
def pcc_gc_store_buffer_slot_pending(slot: c_ptr) -> i64:
    """1 when an entry records slot, 0 when none does, -1 when unknown."""
    if load_i64(global_addr("pcc_py_gc_store_buffer_index_incomplete"), 0) != 0:
        return -1
    if ptr_is_null(slot) != 0:
        return 0
    slots = global_load_ptr("pcc_py_gc_store_buffer_slot_slots")
    if ptr_is_null(slots) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_store_buffer_slot_cap"), 0)
    return 1 if pcc_gc_index_py_find_slot(slots, cap, slot) >= 0 else 0


@c_abi_export("pcc_gc_store_buffer_entry_note")
def pcc_gc_store_buffer_entry_note(owner: c_ptr, slot: c_ptr) -> None:
    """One entry for (owner, slot) joined the buffer; slot may be NULL."""
    entries: i64 = pcc_gc_store_buffer_owner_entries(owner)
    if entries < 0:
        return
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        pcc_gc_store_buffer_index_taint()
        return
    if entries == 0:
        if pcc_gc_index_py_insert(
            global_addr("pcc_py_gc_store_buffer_owner_slots"),
            global_addr("pcc_py_gc_store_buffer_owner_cap"),
            global_addr("pcc_py_gc_store_buffer_owner_count"),
            global_addr("pcc_py_gc_store_buffer_owner_used"),
            owner,
            owner,
            1,
            0,
            256,
        ) != 1:
            pcc_gc_store_buffer_index_taint()
            return
    # Insertion may rehash: find the owner's slot in the current table.
    slots = global_load_ptr("pcc_py_gc_store_buffer_owner_slots")
    cap: i64 = load_i64(global_addr("pcc_py_gc_store_buffer_owner_cap"), 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, owner)
    if result < 0:
        pcc_gc_store_buffer_index_taint()
        return
    store_i64(slots, result * 16 + 8, entries + 1)
    if ptr_is_null(slot) != 0:
        return
    # Callers skip a slot that already has an entry; a second one means the
    # queues and this index disagree.
    if pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_store_buffer_slot_slots"),
        global_addr("pcc_py_gc_store_buffer_slot_cap"),
        global_addr("pcc_py_gc_store_buffer_slot_count"),
        global_addr("pcc_py_gc_store_buffer_slot_used"),
        slot,
        slot,
        1,
        0,
        256,
    ) != 1:
        pcc_gc_store_buffer_index_taint()


@c_abi_export("pcc_gc_store_buffer_entry_forget")
def pcc_gc_store_buffer_entry_forget(owner: c_ptr, slot: c_ptr) -> None:
    """One entry for (owner, slot) left the buffer."""
    entries: i64 = pcc_gc_store_buffer_owner_entries(owner)
    if entries < 0:
        return
    if entries == 0:
        pcc_gc_store_buffer_index_taint()
        return
    if entries > 1:
        slots = global_load_ptr("pcc_py_gc_store_buffer_owner_slots")
        cap: i64 = load_i64(global_addr("pcc_py_gc_store_buffer_owner_cap"), 0)
        result: i64 = pcc_gc_index_py_find_slot(slots, cap, owner)
        if result < 0:
            pcc_gc_store_buffer_index_taint()
            return
        store_i64(slots, result * 16 + 8, entries - 1)
    else:
        pcc_gc_index_py_remove(
            global_addr("pcc_py_gc_store_buffer_owner_slots"),
            global_addr("pcc_py_gc_store_buffer_owner_cap"),
            global_addr("pcc_py_gc_store_buffer_owner_count"),
            global_addr("pcc_py_gc_store_buffer_owner_used"),
            owner,
            1,
        )
    if ptr_is_null(slot) != 0:
        return
    if ptr_is_null(
        pcc_gc_index_py_remove(
            global_addr("pcc_py_gc_store_buffer_slot_slots"),
            global_addr("pcc_py_gc_store_buffer_slot_cap"),
            global_addr("pcc_py_gc_store_buffer_slot_count"),
            global_addr("pcc_py_gc_store_buffer_slot_used"),
            slot,
            1,
        )
    ) != 0:
        pcc_gc_store_buffer_index_taint()


@c_abi_export("pcc_gc_store_buffer_slot_move")
def pcc_gc_store_buffer_slot_move(old_slot: c_ptr, new_slot: c_ptr) -> None:
    """A relocation rewrote a buffered entry's slot."""
    if load_i64(global_addr("pcc_py_gc_store_buffer_index_incomplete"), 0) != 0:
        return
    if ptr_eq(old_slot, new_slot) != 0:
        return
    if ptr_is_null(old_slot) != 0 or ptr_is_null(new_slot) != 0:
        pcc_gc_store_buffer_index_taint()
        return
    if ptr_is_null(
        pcc_gc_index_py_remove(
            global_addr("pcc_py_gc_store_buffer_slot_slots"),
            global_addr("pcc_py_gc_store_buffer_slot_cap"),
            global_addr("pcc_py_gc_store_buffer_slot_count"),
            global_addr("pcc_py_gc_store_buffer_slot_used"),
            old_slot,
            1,
        )
    ) != 0:
        pcc_gc_store_buffer_index_taint()
        return
    if pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_store_buffer_slot_slots"),
        global_addr("pcc_py_gc_store_buffer_slot_cap"),
        global_addr("pcc_py_gc_store_buffer_slot_count"),
        global_addr("pcc_py_gc_store_buffer_slot_used"),
        new_slot,
        new_slot,
        1,
        0,
        256,
    ) != 1:
        pcc_gc_store_buffer_index_taint()


@c_abi_export("pcc_gc_store_buffer_index_clear")
def pcc_gc_store_buffer_index_clear() -> None:
    # Call after both queues are empty; this also ends an incomplete epoch.
    owner_slots = global_load_ptr("pcc_py_gc_store_buffer_owner_slots")
    global_store_ptr("pcc_py_gc_store_buffer_owner_slots", null())
    store_i64(global_addr("pcc_py_gc_store_buffer_owner_cap"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_owner_count"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_owner_used"), 0, 0)
    slot_slots = global_load_ptr("pcc_py_gc_store_buffer_slot_slots")
    global_store_ptr("pcc_py_gc_store_buffer_slot_slots", null())
    store_i64(global_addr("pcc_py_gc_store_buffer_slot_cap"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_slot_count"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_slot_used"), 0, 0)
    store_i64(global_addr("pcc_py_gc_store_buffer_index_incomplete"), 0, 0)
    free(owner_slots)
    free(slot_slots)


@c_abi_export("pcc_gc_ptr_index_tls_pool_drain")
def pcc_gc_ptr_index_tls_pool_drain() -> None:
    # Open addressing owns no thread-local chained-entry pool.
    return
