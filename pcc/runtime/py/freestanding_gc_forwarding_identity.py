"""Shared freestanding forwarding and stable-identity substrate.

Backend 3 copy-oldification and Backend 4 colored relocation share this
pointer-only state machine.  Page selection, object copying, remap, and page
retirement deliberately live in their backend policy modules.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_store_i32,
    atomic_store_i64,
    abi_constant,
    calloc,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True






pcc_gc_forwarding_index_find = extern(
    "pcc_gc_forwarding_index_find", (c_ptr,), c_ptr
)
pcc_gc_forwarding_target_index_find = extern(
    "pcc_gc_forwarding_target_index_find", (c_ptr,), c_ptr
)
pcc_gc_identity_index_remove = extern(
    "pcc_gc_identity_index_remove", (c_ptr,), c_ptr
)


@c_abi_export("pcc_gc_forwarding_target_find")
def _forwarding_target_find(target: c_ptr) -> c_ptr:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return null()
    return pcc_gc_forwarding_target_index_find(target)


@c_abi_export("pcc_gc_identity_set_head")
def _set_identity_head(head: c_ptr) -> None:
    # Publish the authoritative identity list for terminal-free emptiness
    # checks. New identity creation requires a live owner; a last-owner free
    # cannot race creation of an identity for that same object.
    head_bits: i64 = ptr_diff(head, null())
    atomic_store_i64(global_addr("pcc_gc_identity_head"), 0, head_bits, "release")


@c_abi_export("pcc_gc_forwarding_find")
def pcc_gc_forwarding_find(from_obj: c_ptr) -> c_ptr:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return null()
    return pcc_gc_forwarding_index_find(from_obj)


@c_abi_export("pcc_gc_forwarding_target_exists")
def pcc_gc_forwarding_target_exists(target: c_ptr) -> i64:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return 0
    if ptr_is_null(_forwarding_target_find(target)) == 0:
        return 1
    return 0


@c_abi_export("pcc_gc_identity_list_head")
def pcc_gc_identity_head() -> c_ptr:
    head_bits: i64 = atomic_load_i64(global_addr("pcc_gc_identity_head"), 0, "acquire")
    return int_to_ptr(head_bits)


@c_abi_export("pcc_gc_identity_detach")
def pcc_gc_identity_detach(obj: c_ptr) -> c_ptr:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    # Every indexed identity node is linked from the head, so an empty list
    # means an empty index: skip the probe every object free used to pay.
    if ptr_is_null(global_load_ptr("pcc_gc_identity_head")) != 0:
        return null()
    node = pcc_gc_identity_index_remove(obj)
    if ptr_is_null(node) != 0:
        return null()
    prev = load_ptr(node, 24)
    nxt = load_ptr(node, 16)
    if ptr_is_null(prev) != 0:
        _set_identity_head(nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 24, prev)
    store_ptr(node, 16, null())
    store_ptr(node, 24, null())
    return node


@c_abi_export("pcc_gc_identity_finish_detached")
def pcc_gc_identity_finish_detached(nodes: c_ptr) -> None:
    while ptr_is_null(nodes) == 0:
        node = nodes
        nodes = load_ptr(node, 16)
        store_ptr(node, 16, null())
        free(node)


@c_abi_export("pcc_gc_identity_remove")
def pcc_gc_identity_remove(obj: c_ptr) -> None:
    pcc_gc_identity_finish_detached(pcc_gc_identity_detach(obj))


# Forwarding install plan: the original node/table cells keep their offsets.
# The six trailing cells own separate allocations, not inline reader/retire
# records: publication and stopped-world reclamation can outlive this plan.
_FORWARDING_PLAN_SOURCE_IDENTITY: i64 = 0
_FORWARDING_PLAN_TARGET_IDENTITY: i64 = 8
_FORWARDING_PLAN_EDGE: i64 = 16
_FORWARDING_PLAN_IDENTITY_SLOTS: i64 = 24
_FORWARDING_PLAN_IDENTITY_CAP: i64 = 32
_FORWARDING_PLAN_SOURCE_SLOTS: i64 = 40
_FORWARDING_PLAN_SOURCE_CAP: i64 = 48
_FORWARDING_PLAN_TARGET_SLOTS: i64 = 56
_FORWARDING_PLAN_TARGET_CAP: i64 = 64
_FORWARDING_PLAN_SOURCE_RECORD: i64 = 72
_FORWARDING_PLAN_SOURCE_RETIRE_RECORD: i64 = 80
_FORWARDING_PLAN_SOURCE_RETIRE_SLOTS: i64 = 88
_FORWARDING_PLAN_TARGET_RECORD: i64 = 96
_FORWARDING_PLAN_TARGET_RETIRE_RECORD: i64 = 104
_FORWARDING_PLAN_TARGET_RETIRE_SLOTS: i64 = 112
_FORWARDING_PLAN_BYTES: i64 = 120


@c_abi_export("pcc_gc_note_relocation_read")
def pcc_gc_note_relocation_read(obj: c_ptr) -> c_ptr:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return obj
    # Non-moving backends cannot have anything to resolve, so the whole chain
    # below is the identity for them.  Two existing invariants make that
    # exact, not optimistic: pcc_gc_install_forwarding_unlocked refuses
    # outright unless the selected backend is 3 or 4, and pcc_gc_set_backend
    # refuses to leave 3/4 while a forwarding node or a nonzero forwarding
    # population still exists.  So under 0/1/2 pcc_gc_forwarding_find cannot
    # match and the old outcome was always `obj`.  The only work skipped is
    # clearing a stale RELOCATION_CANDIDATE hint, and no backend outside 3/4
    # reads that flag.  Without this gate the barrier paid a full provenance
    # lookup on every pointer read -- plus the graph lock whenever the pointer
    # was not a known object -- to answer a question the backend had already
    # answered.  pcc_gc_load_ptr has gated on the same global for the same
    # reason; this is the missing half of that pair.
    # A pre-config read of 0 is also safe: an install must itself observe 3/4
    # in this same global, so nothing can be forwarded before config runs.
    return obj
    # Under the nonmoving protocol a forwarded address is reachable only
    # inside the stop that moved it, until its references are rewritten.
    # Outside that window this is the identity: asking the index instead
    # took the graph lock for every object not yet in a registration batch.
