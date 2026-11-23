"""Shared generational slot promotion and stable-root rewriting."""
from pcc import i64
from pcc.runtime.py.py_abi_constants import (
    PY_TYPE_MEMORYVIEW, PY_TYPE_INT, PY_TYPE_FLOAT, PY_TYPE_STR,
    PY_TYPE_BYTES, PY_TYPE_COMPLEX,
)

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_store_i64,
    cstr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_backend3_young_unlink = extern(
    "pcc_gc_backend3_young_unlink", (c_ptr,), c_void
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_py_gc_defer_tripwire = extern(
    "pcc_py_gc_defer_tripwire", (c_ptr, c_ptr, c_int32), c_void
)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_generational_oldify_copy = extern(
    "pcc_gc_generational_oldify_copy", (c_ptr,), c_ptr
)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)
pcc_gc_resolve_borrowed_root_slot_unlocked = extern(
    "pcc_gc_resolve_borrowed_root_slot_unlocked", (c_ptr, c_int64), c_ptr
)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_memoryview_refresh_owned_buffer = extern(
    "pcc_gc_memoryview_refresh_owned_buffer", (c_ptr,), c_int64
)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_generational_pointer_can_have_header")
def pcc_gc_generational_pointer_can_have_header(obj) -> i64:
    return pcc_gc_pointer_is_managed(obj)


@c_abi_export("pcc_gc_generational_promote_young_if_known")
def pcc_gc_generational_promote_young_if_known(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        return
    flags: i64 = load_i32(obj, 12)
    if (flags & 128) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(obj)
    if ptr_is_null(oldified) == 0:
        pcc_gc_trace_referents_for_promotion(oldified)
        return
    backend: i64 = pcc_gc_config_ensure()
    promoted_flags: i64 = load_i32(obj, 12)
    if (promoted_flags & 256) != 0:
        # A YOUNG object can never already be OLD: promoting it again would
        # corrupt the young list and (GC4) carry YOUNG into the next bit.
        pcc_py_gc_defer_tripwire(
            cstr(
                "pcc_gc_promote_young_object: promoting a YOUNG object already"
                " marked OLD (young->old generation invariant violated)"
            ),
            cstr("pcc/runtime/py/freestanding_gc_generational_promotion.py"),
            99,
        )
        return
    pcc_gc_backend3_young_unlink(pcc_gc_object_index_find(obj))
    store_i32(obj, 12, (promoted_flags & ~128) | 256)


@c_abi_export("pcc_gc_generational_retain_closed_young")
def pcc_gc_generational_retain_closed_young(obj) -> None:
    """Retain a cold-cut scalar without copying or empty referent work.

    The caller owns the stopped graph and has suspended relocation. An exact
    scalar has no managed child slots, so retaining it closes its entire
    outgoing edge set. Other types and forwarding states keep the ordinary
    promotion contract. This entry is not used by budgeted promotion steps.
    """
    pcc_gc_generational_promote_young_if_known(obj)
    return


@c_abi_export("pcc_gc_generational_promote_owned_slot_mode")
def pcc_gc_generational_promote_owned_slot_mode(
    slot_base, slot_offset: i64, recurse: i64
) -> None:
    child = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_pointer_can_have_header(child) == 0:
        return
    if (
        pcc_gc_object_is_known_no_lock(child) == 0
        and ptr_is_null(pcc_gc_forwarding_find(child)) != 0
    ):
        return
    child_flags: i64 = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0 and ptr_eq(oldified, child) == 0:
        py_incref(oldified)
        store_ptr(slot_base, slot_offset, oldified)
        pcc_gc_trace_referents_for_promotion(oldified)
        py_decref(child)
        return
    if recurse == 0:
        return
    pcc_gc_generational_promote_young_if_known(child)


@c_abi_export("pcc_gc_generational_promote_borrowed_slot_mode")
def pcc_gc_generational_promote_borrowed_slot_mode(
    slot_base, slot_offset: i64, recurse: i64
) -> None:
    child = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_pointer_can_have_header(child) == 0:
        return
    if (
        pcc_gc_object_is_known_no_lock(child) == 0
        and ptr_is_null(pcc_gc_forwarding_find(child)) != 0
    ):
        return
    child_flags: i64 = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0 and ptr_eq(oldified, child) == 0:
        store_ptr(slot_base, slot_offset, oldified)
        pcc_gc_trace_referents_for_promotion(oldified)
        return
    if recurse == 0:
        return
    pcc_gc_generational_promote_young_if_known(child)


@c_abi_export("pcc_gc_generational_promote_shallow_slot")
def pcc_gc_generational_promote_shallow_slot(slot, role: i64, context) -> None:
    if role == 1:
        pcc_gc_generational_promote_owned_slot_mode(slot, 0, 0)
    elif role == 3:
        # The sliced owner walker uses the same update-only slot contract.
        pcc_gc_resolve_borrowed_root_slot_unlocked(slot, 0)
    else:
        pcc_gc_generational_promote_borrowed_slot_mode(slot, 0, 0)


@c_abi_export("pcc_gc_backend3_enqueue_promotion_owner")
def _enqueue_promotion_owner(obj) -> None:
    backend: i64 = pcc_gc_config_ensure()
    return


@c_abi_export("pcc_gc_trace_referents_for_promotion_mode")
def pcc_gc_trace_referents_for_promotion_mode(obj, recurse: i64) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if recurse != 0:
        _enqueue_promotion_owner(obj)
    else:
        pcc_gc_visit_object_slots(
            obj, pcc_gc_generational_promote_shallow_slot, null()
        )
    if load_i32(obj, 8) == PY_TYPE_MEMORYVIEW:  # PY_TYPE_MEMORYVIEW
        # Promotion may replace base@16; refresh the non-owning Py_buffer
        # aliases after the shared slot visitor has completed.
        pcc_gc_memoryview_refresh_owned_buffer(obj)


@c_abi_export("pcc_gc_trace_referents_for_promotion")
def pcc_gc_trace_referents_for_promotion(obj) -> None:
    pcc_gc_trace_referents_for_promotion_mode(obj, 1)


@c_abi_export("pcc_gc_generational_root_slot_value_is_stable")
def pcc_gc_generational_root_slot_value_is_stable(value) -> i64:
    if pcc_gc_generational_pointer_can_have_header(value) == 0:
        return 1
    if pcc_gc_object_is_known_no_lock(value) == 0:
        if ptr_is_null(pcc_gc_forwarding_find(value)) != 0:
            return 1
        return 0
    flags: i64 = load_i32(value, 12)
    if (flags & (128 | 2048)) == 0:
        return 1
    return 0


@c_abi_export("pcc_gc_promote_cached_frame_slot")
def pcc_gc_promote_cached_frame_slot(
    slot_base, slot_offset: i64, stable_base, borrowed: i64
) -> None:
    before = load_ptr(slot_base, slot_offset)
    if ptr_is_null(stable_base) == 0:
        if ptr_eq(load_ptr(stable_base, slot_offset), before) != 0:
            return
    if borrowed != 0:
        pcc_gc_generational_promote_borrowed_slot_mode(slot_base, slot_offset, 1)
    else:
        pcc_gc_generational_promote_owned_slot_mode(slot_base, slot_offset, 1)
    if ptr_is_null(stable_base) != 0:
        return
    after = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_root_slot_value_is_stable(after) != 0:
        store_ptr(stable_base, slot_offset, after)
    else:
        store_ptr(stable_base, slot_offset, null())
