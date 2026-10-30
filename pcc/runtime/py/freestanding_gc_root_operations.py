"""Known-object, root gray, root resolve, and gray-counter primitives."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.runtime.py.py_abi_constants import (
    PYOBJECTHEADER_FLAGS_OFFSET,
    PYOBJECTHEADER_REFCOUNT_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_FLAG_IMMORTAL,
    PY_FLAG_GC_DEALLOCATING,
    PY_TYPE_CONTINUATION,
    PY_TYPE_CEXT_TAG_BASE,
    PY_TYPE_USER,
    PY_FLAG_GC_PINNED,
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_COMPLEX,
    PY_TYPE_CPY_HANDLE,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
    PY_TYPE_NONE,
    PY_TYPE_STR,
)
from pcc.unsafe import (
    define_global_i64_array,
    atomic_cas_i32,
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    atomic_store_i64,
    define_thread_local_ptr_null,
    define_global_i64,
    define_global_null_ptr_array,
    frame_address,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    logical_shift_right_i64,
    null,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_forwarding_index_find = extern("pcc_gc_forwarding_index_find", (c_ptr,), c_ptr)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_granule_is_object_start = extern("pcc_gc_granule_is_object_start", (c_ptr,), c_int64)
pcc_gc_managed_pointer_index_contains = extern("pcc_gc_managed_pointer_index_contains", (c_ptr,), c_int64)
pcc_gc_managed_pointer_index_seen = extern(
    "pcc_gc_managed_pointer_index_seen", (c_ptr,), c_int64
)
pcc_gc_granule_span = extern("pcc_gc_granule_span", (c_ptr,), c_ptr)


# Exact, process-lifetime image origins, not a cache of arbitrary immortals.
# Keys only change from zero to an address. A full/colliding table declines
# admission; readers then retain the original checked lease path.
STATIC_LITERAL_CAPACITY = 16384
STATIC_LITERAL_PROBES = 16
define_global_null_ptr_array("pcc_gc_static_literal_keys", 16384)
define_global_i64("pcc_gc_static_literal_low", 0)
define_global_i64("pcc_gc_static_literal_high", 0)


@c_abi_export("pcc_gc_static_literal_probe")
def _static_literal_probe(bits: i64) -> i64:
    index: i64 = (logical_shift_right_i64(bits, 4) ^ logical_shift_right_i64(bits, 12)) & (STATIC_LITERAL_CAPACITY - 1)
    keys = global_addr("pcc_gc_static_literal_keys")
    probe: i64 = 0
    while probe < STATIC_LITERAL_PROBES:
        key: i64 = atomic_load_i64(keys, index * 8, "acquire")
        if key == bits:
            return 1
        if key == 0:
            return 0
        index = (index + 1) & (STATIC_LITERAL_CAPACITY - 1)
        probe += 1
    return 0


@c_abi_export("pcc_gc_static_literal_contains")
def pcc_gc_static_literal_contains(obj) -> i64:
    bits: i64 = ptr_to_int(obj)
    low: i64 = atomic_load_i64(global_addr("pcc_gc_static_literal_low"), 0, "acquire")
    if low == 0 or bits < low:
        return 0
    if bits > atomic_load_i64(global_addr("pcc_gc_static_literal_high"), 0, "acquire"):
        return 0
    return _static_literal_probe(bits)


@c_abi_export("pcc_gc_static_literal_record")
def pcc_gc_static_literal_record(obj) -> None:
    """Trusted compiler primitive for process-lifetime scalar image headers.

    Ordinary pointer registration must succeed first. IMMORTAL by itself is
    not this origin proof. Owned allocator storage is explicitly excluded;
    foreign callers must obey the image-storage precondition as well.
    """
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if ptr_is_null(pcc_gc_granule_span(obj)) == 0:
        return
    if (load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_IMMORTAL) == 0:
        return
    tag: i64 = load_i32(obj, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag != PY_TYPE_STR and tag != PY_TYPE_BYTES and tag != PY_TYPE_INT and tag != PY_TYPE_FLOAT:
        return
    bits: i64 = ptr_to_int(obj)
    low_slot = global_addr("pcc_gc_static_literal_low")
    low: i64 = atomic_load_i64(low_slot, 0, "acquire")
    while low == 0 or bits < low:
        seen: i64 = atomic_cas_i64(low_slot, 0, low, bits, "release", "relaxed")
        if seen == low:
            break
        low = seen
    high_slot = global_addr("pcc_gc_static_literal_high")
    high: i64 = atomic_load_i64(high_slot, 0, "acquire")
    while bits > high:
        seen = atomic_cas_i64(high_slot, 0, high, bits, "release", "relaxed")
        if seen == high:
            break
        high = seen
    index: i64 = (logical_shift_right_i64(bits, 4) ^ logical_shift_right_i64(bits, 12)) & (STATIC_LITERAL_CAPACITY - 1)
    keys = global_addr("pcc_gc_static_literal_keys")
    probe: i64 = 0
    while probe < STATIC_LITERAL_PROBES:
        key: i64 = atomic_cas_i64(keys, index * 8, 0, bits, "release", "relaxed")
        if key == 0 or key == bits:
            return
        index = (index + 1) & (STATIC_LITERAL_CAPACITY - 1)
        probe += 1

# Indexed leases and bounded terminal-tail operations reserve this central
# count. A negative value excludes new admission during a backend transition.
# Mode-only token-2 leases use the address-selected counters below instead;
# this scalar is no longer the total of every lease. Indexed node counts still
# require graph exclusion. Keep the central word isolated from shard writes.
define_global_i64_array(
    "pcc_gc_foreign_lease_active_isolation_before",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
)
define_global_i64("pcc_gc_foreign_lease_active", 0)
define_global_i64_array(
    "pcc_gc_foreign_lease_active_isolation_after",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
)


# Mode-only leases protect stable addresses, so acquire and release select
# the same counter even when a root moves or another native thread releases it.
# Each counter occupies the first i64 of its own 128-byte stride. Static image
# storage cannot expire with a worker and requires no admission-time allocation.
MODE_LEASE_SHARD_COUNT = 32
MODE_LEASE_SHARD_STRIDE = 128

define_global_i64_array(
    "pcc_gc_mode_lease_shards",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
)


@c_abi_export("pcc_gc_mode_lease_shard_index")
def _mode_lease_shard_index(value) -> i64:
    address: i64 = ptr_to_int(value)
    return ((address >> 4) ^ (address >> 9) ^ (address >> 14)) & 31


@c_abi_export("pcc_gc_mode_lease_reserve_shard")
def _mode_lease_reserve_shard(index: i64) -> i64:
    # Read only pointer bits before reservation, never an unprotected header.
    # A transition owns the central sentinel before freezing each zero shard.
    if atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") < 0:
        return -1
    shards = global_addr("pcc_gc_mode_lease_shards")
    offset: i64 = index * MODE_LEASE_SHARD_STRIDE
    active: i64 = atomic_load_i64(shards, offset, "acquire")
    while True:
        if active < 0:
            return -1
        # Overflow is checked per physical counter. The public ABI promises
        # -2 on counter overflow, not an aggregate INT64_MAX lease capacity.
        if active == 9223372036854775807:
            return -2
        observed: i64 = atomic_cas_i64(
            shards, offset, active, active + 1, "acq_rel", "acquire"
        )
        if observed == active:
            # A stale admission read can reach a shard already thawed at the
            # end of a switch. The central sentinel remains authoritative
            # until every shard has been released and publication is complete.
            if atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") < 0:
                if _mode_lease_release_shard(index) != 0:
                    return -3
                return -1
            return 0
        active = observed


@c_abi_export("pcc_gc_mode_lease_release_shard")
def _mode_lease_release_shard(index: i64) -> i64:
    # The caller supplies the exact reserved shard on acquisition rollback;
    # its source slot may already have been rewritten by a completed switch.
    shards = global_addr("pcc_gc_mode_lease_shards")
    offset: i64 = index * MODE_LEASE_SHARD_STRIDE
    active: i64 = atomic_load_i64(shards, offset, "acquire")
    while active > 0:
        observed: i64 = atomic_cas_i64(
            shards, offset, active, active - 1, "acq_rel", "acquire"
        )
        if observed == active:
            return 0
        active = observed
    return -3


@c_abi_export("pcc_gc_mode_leases_freeze")
def pcc_gc_mode_leases_freeze() -> i64:
    """Freeze all empty mode counters under graph and central exclusion.

    The caller must already own central -1. Return zero only after every
    shard is frozen; on failure restore precisely the acquired prefix before
    the caller restores the central word. Never wait for a live lease.
    """
    if atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") != -1:
        return -1
    shards = global_addr("pcc_gc_mode_lease_shards")
    index: i64 = 0
    while index < MODE_LEASE_SHARD_COUNT:
        if atomic_cas_i64(
            shards, index * MODE_LEASE_SHARD_STRIDE,
            0, -1, "acq_rel", "acquire"
        ) != 0:
            while index > 0:
                index -= 1
                atomic_store_i64(
                    shards, index * MODE_LEASE_SHARD_STRIDE, 0, "release"
                )
            return -1
        index += 1
    return 0


@c_abi_export("pcc_gc_mode_leases_thaw")
def pcc_gc_mode_leases_thaw() -> None:
    """Release a complete freeze before its caller clears central -1."""
    shards = global_addr("pcc_gc_mode_lease_shards")
    index: i64 = 0
    while index < MODE_LEASE_SHARD_COUNT:
        atomic_store_i64(shards, index * MODE_LEASE_SHARD_STRIDE, 0, "release")
        index += 1


@c_abi_export("pcc_gc_foreign_lease_count_quiescent")
def pcc_gc_foreign_lease_count_quiescent() -> i64:
    """Sum reservations only when callers have quiesced concurrent changes.

    This is a diagnostic, not a concurrent snapshot or transition admission.
    A sentinel returns -1; an unrepresentable sum returns -2 without wrapping.
    It includes the central terminal-tail reservation, as the old count did.
    """
    total: i64 = atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire")
    if total < 0:
        return -1
    shards = global_addr("pcc_gc_mode_lease_shards")
    index: i64 = 0
    while index < MODE_LEASE_SHARD_COUNT:
        active: i64 = atomic_load_i64(shards, index * MODE_LEASE_SHARD_STRIDE, "acquire")
        if active < 0:
            return -1
        if active > 9223372036854775807 - total:
            return -2
        total += active
        index += 1
    return total


@c_abi_export("pcc_gc_gray_count_load_acquire")
def pcc_gc_gray_count_load_acquire() -> i64:
    return atomic_load_i32(global_addr("pcc_gc_gray_count"), 0, "acquire")


@c_abi_export("pcc_gc_gray_count_store_release")
def pcc_gc_gray_count_store_release(value: i64) -> None:
    atomic_store_i32(global_addr("pcc_gc_gray_count"), 0, value, "release")


@c_abi_export("pcc_gc_gray_count_increment_acq_rel")
def pcc_gc_gray_count_increment_acq_rel() -> None:
    atomic_rmw_i32("add", global_addr("pcc_gc_gray_count"), 0, 1, "acq_rel")


@c_abi_export("pcc_gc_gray_count_decrement_acq_rel")
def pcc_gc_gray_count_decrement_acq_rel() -> None:
    slot = global_addr("pcc_gc_gray_count")
    old: i64 = atomic_load_i32(slot, 0, "acquire")
    while old > 0:
        desired: i64 = old - 1
        observed: i64 = atomic_cas_i32(
            slot,
            0,
            old,
            desired,
            "acq_rel",
            "acquire",
        )
        if observed == old:
            return
        old = observed


@c_abi_export("pcc_gc_set_mark_color")
def pcc_gc_set_mark_color(obj, color: i64) -> None:
    # The graph lock serializes color transitions and gray-count accounting,
    # not all header writers: terminal decref and pin/unpin publish their
    # bits atomically without that lock. Retry against the current word so
    # coloring cannot erase those updates. One CAS also avoids a transient
    # colorless word, which GC1/2 refcount operations treat as uninitialized.
    old: i64 = atomic_load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET, "acquire")
    while True:
        desired: i64 = (old & ~56) | color
        observed: i64 = atomic_cas_i32(
            obj, PYOBJECTHEADER_FLAGS_OFFSET, old, desired, "acq_rel", "acquire"
        )
        if observed == old:
            return
        old = observed


@c_abi_export("pcc_gc_object_is_known_no_lock")
def pcc_gc_object_is_known_no_lock(obj) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) != 0:
        return 0
    if load_i64(node, 32) != 0:
        return 0
    flags: i64 = load_i32(obj, 12)
    return 0 if (flags & 524288) != 0 else 1


@c_abi_export("pcc_gc_mark_root_gray_if_known")
def pcc_gc_mark_root_gray_if_known(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    forwarding = pcc_gc_forwarding_index_find(obj)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0 and ptr_eq(resolved, obj) == 0:
            obj = resolved
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        return
    flags: i64 = load_i32(obj, 12)
    if (flags & 16) == 0:
        pcc_gc_gray_count_increment_acq_rel()
    pcc_gc_set_mark_color(obj, 16)


@c_abi_export("pcc_gc_resolve_root_slot_unlocked")
def pcc_gc_resolve_root_slot_unlocked(slot_base, slot_offset: i64):
    value = load_ptr(slot_base, slot_offset)
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return value
    if pcc_gc_object_is_known_no_lock(value) == 0:
        forwarding_unknown = pcc_gc_forwarding_index_find(value)
        if ptr_is_null(forwarding_unknown) == 0:
            resolved_unknown = load_ptr(forwarding_unknown, 8)
            if (
                ptr_is_null(resolved_unknown) == 0
                and ptr_eq(resolved_unknown, value) == 0
            ):
                if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
                    store_ptr(slot_base, slot_offset, resolved_unknown)
                    return resolved_unknown
                py_incref(resolved_unknown)
                store_ptr(slot_base, slot_offset, resolved_unknown)
                py_decref(value)
                return resolved_unknown
        return value
    flags: i64 = load_i32(value, 12)
    if (flags & 2048) == 0:
        return value
    forwarding = pcc_gc_forwarding_index_find(value)
    if ptr_is_null(forwarding) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    resolved = load_ptr(forwarding, 8)
    if ptr_is_null(resolved) != 0 or ptr_eq(resolved, value) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
        store_ptr(slot_base, slot_offset, resolved)
        return resolved
    py_incref(resolved)
    store_ptr(slot_base, slot_offset, resolved)
    py_decref(value)
    return resolved


# Foreign-address leases are separate from legacy Boolean pin/unpin. The slot
# is already traced and owns its object. Lock acquisition can park, so reload
# only AFTER it succeeds. Return codes never alter Python exception state.
@c_abi_export("pcc_gc_foreign_lease_acquire")
def pcc_gc_foreign_lease_acquire(slot) -> i64:
    return _foreign_lease_acquire(slot, 0)


@c_abi_export("pcc_gc_borrowed_lease_acquire")
def pcc_gc_borrowed_lease_acquire(slot) -> i64:
    """Protect a traced borrowed slot whose external owner stays alive.

    The caller exclusively controls the slot and cannot rebind it until
    release. Relocation updates the borrowed address without transferring
    a refcount. The ordinary release API consumes the returned token: a
    counted lease prevents movement until that release finishes.
    """
    return _foreign_lease_acquire(slot, 1)


@c_abi_export("pcc_gc_slot_lease_acquire")
def _foreign_lease_acquire(slot, borrowed: i64) -> i64:
    if ptr_is_null(slot) != 0:
        return -1
    # The caller keeps its owning slot stable while leased. Collectors can
    # rewrite only object pointers: NULL/tagged words carry no address and
    # need neither relocation protection nor a backend-mode reservation.
    # Do not inspect a heap header here: heap values still take the guarded
    # path and are reloaded after any parking lock acquisition below.
    immediate = load_ptr(slot, 0)
    if ptr_is_null(immediate) != 0 or is_tagged_int(immediate) != 0:
        return 0
    # These exact objects are headers defined in static image storage by
    # py_substrate, not allocated immortals. Their addresses survive every
    # backend transition. Compare addresses only: an arbitrary heap operand
    # still must reserve a mode and reload before its header can be read.
    if (ptr_eq(immediate, global_addr("py_none_storage")) != 0
            or ptr_eq(immediate, global_addr("py_true_storage")) != 0
            or ptr_eq(immediate, global_addr("py_false_storage")) != 0
            or ptr_eq(immediate, global_addr("py_notimplemented_storage")) != 0
            or ptr_eq(immediate, global_addr("py_ellipsis_storage")) != 0):
        return 0
    if pcc_gc_static_literal_contains(immediate) != 0:
        return 0
    # Reserve a mode guard before reading any heap header. Only address bits
    # select its shard. A completed transition may rewrite the source before
    # reservation, so recheck mode and pointer-to-shard agreement afterwards.
    if load_i32(global_addr("pcc_gc_refcount_atomic"), 0) != 0:
        fast_shard: i64 = _mode_lease_shard_index(immediate)
        reserved: i64 = _mode_lease_reserve_shard(fast_shard)
        if reserved < 0:
            return reserved
        if load_i32(global_addr("pcc_gc_refcount_atomic"), 0) != 0:
            fast = load_ptr(slot, 0)
            if ptr_is_null(fast) == 0 and is_tagged_int(fast) == 0:
                # The usual stable slot needs no second address hash. Any
                # changed pointer, including a hash collision, releases the
                # exact original reservation and uses locked revalidation.
                if ptr_eq(fast, immediate) != 0:
                    if (load_i32(fast, PYOBJECTHEADER_FLAGS_OFFSET) & 524288) == 0:
                        # Both readers prove provenance without parking. The
                        # owner plus this mode reservation keep the value live
                        # and nonmoving. Unknown origins keep the locked path.
                        if pcc_gc_granule_is_object_start(fast) == 1:
                            return 2
                        if pcc_gc_managed_pointer_index_seen(fast) == 1:
                            return 2
        # Release the original shard, not the possibly rewritten slot's shard.
        # Intrinsic/special values and admission misses retain full validation.
        if _mode_lease_release_shard(fast_shard) != 0:
            return -3
    pcc_gc_config_ensure()
    pcc_py_gc_minor_graph_lock()
    if atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") < 0:
        pcc_py_gc_minor_graph_unlock()
        return -1
    obj = null()
    if borrowed != 0:
        obj = load_ptr(slot, 0)
        if ptr_is_null(obj) == 0 and is_tagged_int(obj) == 0:
            forwarding = pcc_gc_forwarding_index_find(obj)
            if ptr_is_null(forwarding) == 0:
                resolved = load_ptr(forwarding, 8)
                if ptr_is_null(resolved) == 0 and ptr_eq(resolved, obj) == 0:
                    store_ptr(slot, 0, resolved)
                    obj = resolved
    else:
        obj = pcc_gc_resolve_root_slot_unlocked(slot, 0)
    status: i64 = -1
    node = null()
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        status = 0
    else:
        # ABI precondition: a compiler-proven initialized managed object.
        # Literal/immortal objects need no allocator entry and never move.
        flags: i64 = load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET)
        node = pcc_gc_object_index_find(obj)
        if (flags & 524288) == 0 and ptr_is_null(node) == 0:
            # Tracked immortals still participate in relocation. Only
            # untracked intrinsic immortals qualify for a no-op token.
            if load_i64(node, 32) == 0:
                count: i64 = load_i64(node, 80)
                if count >= 0 and count < 9223372036854775807:
                    status = 1
                else:
                    status = -2
        elif ((flags & 1) != 0 and (flags & (2048 | 524288)) == 0
              and pcc_gc_granule_is_object_start(obj) != 1
              and pcc_gc_managed_pointer_index_contains(obj) == 0):
            # Only an unregistered static immortal is intrinsically stable.
            # Heap immortals still need a mode guard: a backend transition
            # may register/move an allocator-backed nonleaf object later.
            status = 0
        elif (flags & 524288) == 0 and (pcc_gc_granule_is_object_start(obj) == 1
                  or pcc_gc_managed_pointer_index_contains(obj) != 0):
            backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
            tag: i64 = load_i32(obj, 8)
            # GC0/1/2 never move. GC3/4 no-node malloc graph leaves are
            # intentionally refcount-only; nursery/zpage owners are not.
            # A no-node immortal (the malloc'd standard streams and object
            # class roots) is stable storage outside the object graph, so
            # GC3/4 never move it either.
            if backend >= 0 and backend <= 2:
                status = 2
            elif (backend == 3 or backend == 4) and (flags & (4096 | 65536)) == 0:
                if (
                    tag == PY_TYPE_NONE
                    or tag == PY_TYPE_BOOL
                    or tag == PY_TYPE_INT
                    or tag == PY_TYPE_FLOAT
                    or tag == PY_TYPE_STR
                    or tag == PY_TYPE_COMPLEX
                    or tag == PY_TYPE_BYTES
                    or tag == PY_TYPE_BYTEARRAY
                    or tag == PY_TYPE_CPY_HANDLE
                    or (flags & 1) != 0
                ):
                    status = 2
    if status == 2:
        # Graph exclusion fixes this resolved value through publication. Token
        # 2 keeps its public meaning; release derives the same stable address.
        mode_status: i64 = _mode_lease_reserve_shard(_mode_lease_shard_index(obj))
        if mode_status < 0:
            status = mode_status
    elif status == 1:
        counter = global_addr("pcc_gc_foreign_lease_active")
        active: i64 = atomic_load_i64(counter, 0, "acquire")
        while True:
            if active < 0 or active == 9223372036854775807:
                status = -2
                break
            observed: i64 = atomic_cas_i64(
                counter, 0, active, active + 1, "acq_rel", "acquire"
            )
            if observed == active:
                if status == 1:
                    store_i64(node, 80, load_i64(node, 80) + 1)
                break
            active = observed
    pcc_py_gc_minor_graph_unlock()
    return status


@c_abi_export("pcc_gc_foreign_lease_release")
def pcc_gc_foreign_lease_release(slot, acquired: i64) -> i64:
    # A no-op token must never decrement a counted node registered later.
    if acquired == 0:
        return 0
    if ptr_is_null(slot) != 0 or (acquired != 1 and acquired != 2):
        return -1
    # A mode-only token has no object-index entry. Its owning slot preserves
    # the leased object's stable address; root moves and cross-thread release
    # keep those pointer bits. No header or current-thread state is inspected.
    if acquired == 2:
        value = load_ptr(slot, 0)
        if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
            return -1
        return _mode_lease_release_shard(_mode_lease_shard_index(value))
    pcc_py_gc_minor_graph_lock()
    status: i64 = -3
    active: i64 = atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire")
    if active > 0:
        obj = pcc_gc_resolve_root_slot_unlocked(slot, 0)
        node = pcc_gc_object_index_find(obj)
        if ptr_is_null(node) == 0 and load_i64(node, 32) == 0:
            count: i64 = load_i64(node, 80)
            if count > 0:
                counter = global_addr("pcc_gc_foreign_lease_active")
                while active > 0:
                    observed: i64 = atomic_cas_i64(
                        counter, 0, active, active - 1, "acq_rel", "acquire"
                    )
                    if observed == active:
                        store_i64(node, 80, count - 1)
                        status = 0
                        break
                    active = observed
    pcc_py_gc_minor_graph_unlock()
    return status


@c_abi_export("pcc_gc_try_init_ptr_locked")
def pcc_gc_try_init_ptr_locked(destination, source) -> i64:
    """Retain an authoritative source into a private, unowned new field.

    Caller holds the graph lock and publishes the containing object's length
    only after success. Neither root is consumed. No callback, allocation or
    safepoint occurs; a miss leaves both slots and counts untouched. Tracing,
    moving, diagnostic, dying and contended cases keep the ordinary protocol.
    """
    if ptr_is_null(destination) != 0 or ptr_is_null(source) != 0 or ptr_eq(destination, source) != 0:
        return 0
    if (load_i32(global_addr("pcc_gc_refcount_atomic"), 0) == 0
            or load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") < 0):
        return 0
    value = load_ptr(source, 0)
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        tag: i64 = load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET)
        if not ((tag >= PY_TYPE_NONE and tag < PY_TYPE_CONTINUATION)
                or tag == PY_TYPE_CPY_HANDLE
                or (tag >= PY_TYPE_USER and tag < PY_TYPE_CEXT_TAG_BASE)):
            return 0
        flags: i64 = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET)
        if (flags & PY_FLAG_GC_DEALLOCATING) != 0:
            return 0
        if (flags & PY_FLAG_IMMORTAL) == 0:
            references: i64 = atomic_load_i64(value, PYOBJECTHEADER_REFCOUNT_OFFSET, "relaxed")
            if references <= 0 or references == 9223372036854775807:
                return 0
            if atomic_cas_i64(value, PYOBJECTHEADER_REFCOUNT_OFFSET,
                    references, references + 1, "relaxed", "relaxed") != references:
                return 0
    store_ptr(destination, 0, value)
    return 1


@c_abi_export("pcc_gc_release_nonterminal_tail")
def pcc_gc_release_nonterminal_tail(slots, count: i64) -> i64:
    """Retire at most 64 trailing owned fields without running callbacks.

    The caller is destroying a private contiguous owner array. Return the
    unconsumed prefix length; a terminal/unknown reference stays untouched
    for the caller's ordinary barrier/decref path. This is precisely the
    existing GC0 atomic, no-probe/no-log admission, not a new Dyn trust rule.
    """
    if count <= 0 or ptr_is_null(slots) != 0:
        return count
    if load_i32(global_addr("pcc_gc_refcount_atomic"), 0) == 0:
        return count
    if load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) != 0:
        return count
    # Reserve this owner array's mode-lease shard, not the central counter:
    # every thread's list and tuple deallocations come here, and the shared
    # word contended (a failed reservation sent the fields down the per-field
    # path). A backend switch freezes every shard, so the reserved one keeps
    # the raw field addresses nonmoving as the central reservation did.
    shard: i64 = _mode_lease_shard_index(slots)
    if _mode_lease_reserve_shard(shard) != 0:
        return count
    # A switch may have completed between the first gate and reservation.
    # Holding the reservation now keeps every raw field address nonmoving.
    remaining: i64 = count
    scanned: i64 = 0
    if (load_i32(global_addr("pcc_gc_refcount_atomic"), 0) != 0
            and load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) == 0):
        while remaining > 0 and scanned < 64:
            value = load_ptr(slots, (remaining - 1) * 8)
            consumed: i64 = 0
            if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
                consumed = 1
            else:
                tag: i64 = load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET)
                if ((tag >= PY_TYPE_NONE and tag < PY_TYPE_CONTINUATION)
                        or tag == PY_TYPE_CPY_HANDLE
                        or (tag >= PY_TYPE_USER and tag < PY_TYPE_CEXT_TAG_BASE)):
                    flags: i64 = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET)
                    if (flags & PY_FLAG_IMMORTAL) != 0:
                        consumed = 1
                    elif (flags & 524288) == 0:
                        references: i64 = atomic_load_i64(value, PYOBJECTHEADER_REFCOUNT_OFFSET, "relaxed")
                        if references > 1:
                            observed: i64 = atomic_cas_i64(value, PYOBJECTHEADER_REFCOUNT_OFFSET,
                                references, references - 1, "release", "relaxed")
                            if observed == references:
                                consumed = 1
            if consumed == 0:
                break
            remaining -= 1
            scanned += 1
    _mode_lease_release_shard(shard)
    return remaining


@c_abi_export("pcc_gc_object_is_address_pinned")
def pcc_gc_object_is_address_pinned(obj) -> i64:
    # Collector callers hold the graph lock (or own stopped-world traversal).
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    if pcc_gc_static_literal_contains(obj) != 0:
        return 1
    if (load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED) != 0:
        return 1
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) == 0:
        return 1 if load_i64(node, 80) != 0 else 0
    return 0


# Hot per-operation statistics: the pin balance at offset 0 and the
# allocation count at offset 8 of one 128-byte stride per shard. A thread
# updates the shard of its own stack region, so cores do not share one
# written word on every pin, unpin and allocation; readers sum the shards.
define_global_i64_array(
    "pcc_gc_metric_shards",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
)


@c_abi_export("pcc_gc_metric_shard")
def pcc_gc_metric_shard() -> i64:
    # One stride per 512 KiB stack region: thread stacks are disjoint.
    region: i64 = logical_shift_right_i64(ptr_to_int(frame_address()), 19)
    return ((region ^ logical_shift_right_i64(region, 6)) & 63) * 128


@c_abi_export("pcc_gc_metric_shard_sum")
def pcc_gc_metric_shard_sum(field: i64) -> i64:
    shards = global_addr("pcc_gc_metric_shards")
    total: i64 = 0
    index: i64 = 0
    while index < 64:
        total = total + atomic_load_i64(shards, index * 128 + field, "relaxed")
        index = index + 1
    return total


@c_abi_export("pcc_gc_metric_pin_balance")
def pcc_gc_metric_pin_balance() -> i64:
    return pcc_gc_metric_shard_sum(0)


@c_abi_export("pcc_gc_metric_allocation_count")
def pcc_gc_metric_allocation_count() -> i64:
    return pcc_gc_metric_shard_sum(8)


@c_abi_export("pcc_gc_metric_shard_reset")
def pcc_gc_metric_shard_reset(field: i64) -> None:
    shards = global_addr("pcc_gc_metric_shards")
    index: i64 = 0
    while index < 64:
        atomic_store_i64(shards, index * 128 + field, 0, "relaxed")
        index = index + 1


# Pin acquisition/release must not park before touching their raw object arg.
# Keep this existing header/metric protocol in the strict primitive owner.
@c_abi_export("pcc_gc_pin")
def pcc_gc_pin(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    # Exact image-defined singleton storage is intrinsically nonmoving.
    # Match the address-only foreign-lease admission; a heap object merely
    # carrying IMMORTAL must retain its ordinary pin/count protocol.
    if (ptr_eq(o, global_addr("py_true_storage")) != 0
            or ptr_eq(o, global_addr("py_false_storage")) != 0
            or ptr_eq(o, global_addr("py_none_storage")) != 0
            or ptr_eq(o, global_addr("py_notimplemented_storage")) != 0
            or ptr_eq(o, global_addr("py_ellipsis_storage")) != 0):
        return
    # Other threads and collector workers update the same flags word: the
    # pin bit is one atomic or, never a lost read-modify-write.
    atomic_rmw_i32("or", o, PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_GC_PINNED, "relaxed")
    # pcc_gc_note_pin(1), inline: codegen pins around most calls. The shard
    # is computed here too, so pinning calls nothing before it returns.
    region: i64 = logical_shift_right_i64(ptr_to_int(frame_address()), 19)
    shard: i64 = ((region ^ logical_shift_right_i64(region, 6)) & 63) * 128
    atomic_rmw_i64("add", global_addr("pcc_gc_metric_shards"), shard, 1, "relaxed")
    return


@c_abi_export("pcc_gc_unpin")
def pcc_gc_unpin(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    # Exact image-defined singleton storage is intrinsically nonmoving.
    # Match the address-only foreign-lease admission; a heap object merely
    # carrying IMMORTAL must retain its ordinary pin/count protocol.
    if (ptr_eq(o, global_addr("py_true_storage")) != 0
            or ptr_eq(o, global_addr("py_false_storage")) != 0
            or ptr_eq(o, global_addr("py_none_storage")) != 0
            or ptr_eq(o, global_addr("py_notimplemented_storage")) != 0
            or ptr_eq(o, global_addr("py_ellipsis_storage")) != 0):
        return
    atomic_rmw_i32("and", o, PYOBJECTHEADER_FLAGS_OFFSET, ~PY_FLAG_GC_PINNED, "relaxed")
    # pcc_gc_note_pin(-1), inline, with the same inline shard selection.
    region: i64 = logical_shift_right_i64(ptr_to_int(frame_address()), 19)
    shard: i64 = ((region ^ logical_shift_right_i64(region, 6)) & 63) * 128
    atomic_rmw_i64("sub", global_addr("pcc_gc_metric_shards"), shard, 1, "relaxed")
    return




@c_abi_export("pcc_gc_take_pinned_slot")
def pcc_gc_take_pinned_slot(slot, prior_pin: i64):
    # The caller owns one pinned reference stored in this stable slot and has
    # finished every operation that can park, including frame unregistration.
    # Transfer that reference without another callback/park or refcount change.
    if ptr_is_null(slot) != 0:
        return null()
    value = load_ptr(slot, 0)
    store_ptr(slot, 0, null())
    pcc_gc_unpin(value)
    if (prior_pin & PY_FLAG_GC_PINNED) != 0:
        if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
            atomic_rmw_i32("or", value, PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_GC_PINNED, "relaxed")
    return value


# These links refer only to live native activation records. Managed exception
# values stay in the caller's ordinary collector-visible root/frame slots.
# Freestanding entrypoints never park or acquire managed owners.
define_thread_local_ptr_null("pcc_tls_handled_exception_context")


@c_abi_export("py_handled_context_push")
def py_handled_context_push(record: c_ptr, exception_slot: c_ptr) -> None:
    store_ptr(record, 0, global_load_ptr("pcc_tls_handled_exception_context"))
    store_ptr(record, 8, exception_slot)
    global_store_ptr("pcc_tls_handled_exception_context", record)


@c_abi_export("py_handled_context_pop")
def py_handled_context_pop(record: c_ptr) -> i64:
    current = global_load_ptr("pcc_tls_handled_exception_context")
    if ptr_eq(current, record) == 0:
        # Shared exceptional cleanup may revisit an already retired scope.
        return 0
    global_store_ptr("pcc_tls_handled_exception_context", load_ptr(record, 0))
    store_ptr(record, 0, null())
    store_ptr(record, 8, null())
    return 1


@c_abi_export("py_handled_exception_slot")
def py_handled_exception_slot() -> c_ptr:
    current = global_load_ptr("pcc_tls_handled_exception_context")
    while ptr_is_null(current) == 0:
        slot = load_ptr(current, 8)
        if ptr_is_null(slot) == 0:
            if ptr_is_null(load_ptr(slot, 0)) == 0:
                return slot
        current = load_ptr(current, 0)
    return null()


@c_abi_export("py_handled_context_swap")
def py_handled_context_swap(context: c_ptr) -> c_ptr:
    previous = global_load_ptr("pcc_tls_handled_exception_context")
    global_store_ptr("pcc_tls_handled_exception_context", context)
    return previous
