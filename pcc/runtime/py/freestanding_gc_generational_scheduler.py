"""Backend 3/4 root promotion and budgeted young-list scheduling."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.runtime.py.py_abi_constants import (
    PY_FLAG_GC_PINNED, PY_FLAG_IMMORTAL, PY_TYPE_INT, PY_TYPE_FLOAT,
    PY_TYPE_STR, PY_TYPE_BYTES, PY_TYPE_COMPLEX,
)
from pcc.unsafe import (
    cstr,
    atomic_cas_i32,
    atomic_load_i32,
    atomic_store_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    free,
    function_addr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_capi_visit_extension_module_state_roots = extern(
    "pcc_capi_visit_extension_module_state_roots", (c_ptr, c_ptr), c_void
)
pcc_gc_backend3_drain_promotion_worklist = extern("pcc_gc_backend3_drain_promotion_worklist", (c_int64,), c_int64)
pcc_gc_backend3_young_link_head = extern(
    "pcc_gc_backend3_young_link_head", (c_ptr,), c_void
)
pcc_gc_backend3_young_list_head = extern(
    "pcc_gc_backend3_young_list_head", (), c_ptr
)
pcc_gc_backend3_young_unlink = extern(
    "pcc_gc_backend3_young_unlink", (c_ptr,), c_void
)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_generational_oldify_copy = extern(
    "pcc_gc_generational_oldify_copy", (c_ptr,), c_ptr
)
pcc_gc_generational_promote_young_if_known = extern(
    "pcc_gc_generational_promote_young_if_known", (c_ptr,), c_void
)
pcc_gc_generational_retain_closed_young = extern(
    "pcc_gc_generational_retain_closed_young", (c_ptr,), c_void
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_store_buffer_member_forget = extern(
    "pcc_gc_store_buffer_member_forget", (c_ptr,), c_void
)
pcc_gc_store_buffer_entry_forget = extern(
    "pcc_gc_store_buffer_entry_forget", (c_ptr, c_ptr), c_void
)
pcc_gc_store_buffer_owner_entries = extern(
    "pcc_gc_store_buffer_owner_entries", (c_ptr,), c_int64
)
pcc_gc_generational_promote_owned_slot_mode = extern(
    "pcc_gc_generational_promote_owned_slot_mode",
    (c_ptr, c_int64, c_int64),
    c_void
)
pcc_gc_trace_referents_for_promotion = extern(
    "pcc_gc_trace_referents_for_promotion", (c_ptr,), c_void
)
pcc_gc_visit_mapped_root_slot = extern(
    "pcc_gc_visit_mapped_root_slot",
    (c_ptr, c_int64, c_ptr, c_int64, c_int64, c_int64),
    c_int64
)
pcc_gc_visit_walked_stack_roots = extern("pcc_gc_visit_walked_stack_roots", (c_int64, c_int64), c_int64)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_thread_all_stopped_at_gates = extern("pcc_thread_all_stopped_at_gates", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_gc_pin_conservative_thread_roots = extern("pcc_gc_pin_conservative_thread_roots", (), c_void)
pcc_gc_conservative_step_pin_begin = extern("pcc_gc_conservative_step_pin_begin", (c_ptr,), c_void)
pcc_gc_conservative_step_pin_end = extern("pcc_gc_conservative_step_pin_end", (c_ptr,), c_void)

# Sorted words: data/count/capacity; previous context for nested STW steps.
_STEP_PIN_CONTEXT_BYTES: i64 = 32
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_tls_exc_get = extern("py_tls_exc_get", (), c_ptr)
py_tls_exc_set = extern("py_tls_exc_set", (c_ptr,), c_void)
py_subs_exc_cache_slot = extern(
    "py_subs_exc_cache_slot", (c_int64,), c_ptr
)


@c_abi_export("pcc_gc_backend4_drain_remembered_batch")
def _backend4_drain_remembered_batch(remaining_budget: i64) -> i64:
    """Drain one bounded GC4 store-buffer batch under the graph lock, then
    run its callback-capable cleanup unlocked."""
    batch_limit: i64 = remaining_budget
    if batch_limit > 8:
        batch_limit = 8
    cleanup = null()
    local_drained: i64 = 0

    pcc_py_gc_minor_graph_lock()
    medium = load_ptr(
        global_addr("pcc_gc_backend4_store_buffer_medium_head"), 0
    )
    medium_count: i64 = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_count"), 0
    )
    if ptr_is_null(medium) == 0:
        store_ptr(
            global_addr("pcc_gc_backend4_store_buffer_medium_head"),
            0,
            null(),
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_medium_count"), 0, 0
        )
        # Medium nodes record their enqueuing thread at +32.
        me: i64 = pcc_current_thread_id()
        foreign: i64 = 0
        tail = medium
        if load_i64(tail, 32) != me:
            foreign = foreign + 1
        tail_next = load_ptr(tail, 24)
        while ptr_is_null(tail_next) == 0:
            tail = tail_next
            if load_i64(tail, 32) != me:
                foreign = foreign + 1
            tail_next = load_ptr(tail, 24)
        if foreign > 0:
            cross_flushes: i64 = load_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count"
                ),
                0,
            )
            store_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count"
                ),
                0,
                cross_flushes + 1,
            )
            cross_entries: i64 = load_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count"
                ),
                0,
            )
            store_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count"
                ),
                0,
                cross_entries + foreign,
            )
        store_ptr(
            tail,
            24,
            load_ptr(global_addr("pcc_gc_backend4_store_buffer_head"), 0),
        )
        store_ptr(
            global_addr("pcc_gc_backend4_store_buffer_head"), 0, medium
        )
        flushes: i64 = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"),
            0,
            flushes + 1,
        )
        flushed: i64 = load_i32(
            global_addr(
                "pcc_gc_backend4_store_buffer_medium_flushed_entries_count"
            ),
            0,
        )
        store_i32(
            global_addr(
                "pcc_gc_backend4_store_buffer_medium_flushed_entries_count"
            ),
            0,
            flushed + medium_count,
        )
        if medium_count >= 32:
            full_flushes: i64 = load_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_medium_full_flushes_count"
                ),
                0,
            )
            store_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_medium_full_flushes_count"
                ),
                0,
                full_flushes + 1,
            )

    node = load_ptr(global_addr("pcc_gc_backend4_store_buffer_head"), 0)
    while ptr_is_null(node) == 0 and local_drained < batch_limit:
        nxt = load_ptr(node, 24)
        store_ptr(global_addr("pcc_gc_backend4_store_buffer_head"), 0, nxt)
        owner = load_ptr(node, 0)
        slot = load_ptr(node, 8)
        value = load_ptr(node, 16)
        store_ptr(node, 24, cleanup)
        cleanup = node
        pending: i64 = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0
        )
        if pending > 0:
            store_i32(
                global_addr("pcc_gc_backend4_store_buffer_entries_count"),
                0,
                pending - 1,
            )
        local_drained = local_drained + 1
        node = nxt
        owner_known: i64 = pcc_gc_object_is_known_no_lock(owner)
        flags: i64 = 0
        if owner_known != 0:
            flags = load_i32(owner, 12)
            if (flags & 512) != 0:
                pcc_gc_generational_promote_young_if_known(value)
                if ptr_is_null(slot) == 0:
                    pcc_gc_generational_promote_owned_slot_mode(slot, 0, 1)
                else:
                    pcc_gc_trace_referents_for_promotion(owner)

        # Membership follows both queues even when promotion is inapplicable.
        # Forget is raw deletion only; cleanup can reenqueue after unlocking.
        # The entry index answers whether the owner keeps another entry; an
        # incomplete index falls back to walking the queues.
        pcc_gc_store_buffer_entry_forget(owner, slot)
        owner_pending: i64 = pcc_gc_store_buffer_owner_entries(owner)
        if owner_pending > 0:
            owner_pending = 1
        elif owner_pending < 0:
            owner_pending = 0
            pending_node = load_ptr(
                global_addr("pcc_gc_backend4_store_buffer_medium_head"), 0
            )
            while ptr_is_null(pending_node) == 0:
                if ptr_eq(load_ptr(pending_node, 0), owner) != 0:
                    owner_pending = 1
                    break
                pending_node = load_ptr(pending_node, 24)
            if owner_pending == 0:
                pending_node = load_ptr(
                    global_addr("pcc_gc_backend4_store_buffer_head"), 0
                )
                while ptr_is_null(pending_node) == 0:
                    if ptr_eq(load_ptr(pending_node, 0), owner) != 0:
                        owner_pending = 1
                        break
                    pending_node = load_ptr(pending_node, 24)
        if owner_pending == 0:
            pcc_gc_store_buffer_member_forget(owner)
            if owner_known != 0 and (flags & 512) != 0:
                atomic_rmw_i32("and", owner, 12, -513, "acq_rel")

    if local_drained > 0:
        batches: i64 = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"),
            0,
            batches + 1,
        )
        drained_entries: i64 = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"),
            0,
            drained_entries + local_drained,
        )
        max_batch: i64 = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"), 0
        )
        if local_drained > max_batch:
            store_i32(
                global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"),
                0,
                local_drained,
            )
        if local_drained >= 8:
            full_batches: i64 = load_i32(
                global_addr("pcc_gc_backend4_store_buffer_full_batches_count"), 0
            )
            store_i32(
                global_addr("pcc_gc_backend4_store_buffer_full_batches_count"),
                0,
                full_batches + 1,
            )
        if (
            load_i32(
                global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0
            )
            > 0
        ):
            incomplete: i64 = load_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_incomplete_drains_count"
                ),
                0,
            )
            store_i32(
                global_addr(
                    "pcc_gc_backend4_store_buffer_incomplete_drains_count"
                ),
                0,
                incomplete + 1,
            )
    pcc_py_gc_minor_graph_unlock()

    while ptr_is_null(cleanup) == 0:
        nxt = load_ptr(cleanup, 24)
        value = load_ptr(cleanup, 16)
        free(cleanup)
        py_decref(value)
        cleanup = nxt
    return local_drained


@c_abi_export("pcc_gc_backend4_step_remembered_roots")
def pcc_gc_backend4_step_remembered_roots(remaining_budget: i64) -> i64:
    """Drain GC4 store-buffer batches until the budget or the buffer runs out.

    Each batch holds the graph lock for at most eight entries. One batch per
    step fell behind any mutator storing young values into old objects, and
    while entries remain a GC4 step does no other work, so the buffer grew
    without bound.
    """
    if remaining_budget <= 0:
        return 0
    local_drained: i64 = 0
    batch_drained: i64 = 1
    while local_drained < remaining_budget and batch_drained > 0:
        batch_drained = _backend4_drain_remembered_batch(
            remaining_budget - local_drained
        )
        local_drained = local_drained + batch_drained
    promotion_examined: i64 = 0
    if local_drained < remaining_budget:
        promotion_examined = pcc_gc_backend3_drain_promotion_worklist(
            remaining_budget - local_drained
        )
    if local_drained > 0:
        pcc_thread_safepoint()
    return local_drained + promotion_examined


@c_abi_export("pcc_gc_backend4_step_generation_aging")
def pcc_gc_backend4_step_generation_aging(remaining_budget: i64) -> i64:
    """Age pending GC4 young nodes in poll-free graph-lock tenures."""
    if remaining_budget <= 0:
        return 0
    total_examined: i64 = 0
    while total_examined < remaining_budget:
        batch_limit: i64 = remaining_budget - total_examined
        if batch_limit > 16:
            batch_limit = 16
        batch_examined: i64 = 0
        more_work: i64 = 0
        pcc_py_gc_minor_graph_lock()
        while (
            ptr_is_null(pcc_gc_backend3_young_list_head()) == 0
            and batch_examined < batch_limit
        ):
            node = pcc_gc_backend3_young_list_head()
            pcc_gc_backend3_young_unlink(node)
            batch_examined = batch_examined + 1
            total_examined = total_examined + 1
            if pcc_gc_object_node_is_active(node) == 0:
                continue
            obj = load_ptr(node, 0)
            flags: i64 = atomic_load_i32(obj, 12, "acquire")
            if (flags & 128) == 0 or (flags & 256) != 0:
                continue
            # YOUNG (0x80) and OLD (0x100) are adjacent.  Adding 0x80
            # atomically clears YOUNG, carries into OLD, and preserves all
            # unrelated header bits.
            atomic_rmw_i32("add", obj, 12, 128, "acq_rel")
            zpage_node = load_ptr(node, 48)
            if ptr_is_null(zpage_node) == 0:
                page = load_ptr(zpage_node, 8)
                if ptr_is_null(page) == 0:
                    store_i32(page, 28, 2)
            atomic_rmw_i32(
                "add",
                global_addr("pcc_gc_backend4_young_promotions"),
                0,
                1,
                "acq_rel",
            )
        if ptr_is_null(pcc_gc_backend3_young_list_head()) == 0:
            more_work = 1
        pcc_py_gc_minor_graph_unlock()
        if batch_examined > 0:
            pcc_thread_safepoint()
        if batch_examined == 0 or more_work == 0:
            break
    return total_examined


# Admission belongs to the linked application proof, not an environment flag.
# 0 is unproved (including native C hosts); 1 is a proved managed domain;
# 2 is permanently revoked after any untracked foreign exposure.
pcc_platform_getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)

pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_gc_concurrent_sweep_finish_stopped = extern(
    "pcc_gc_concurrent_sweep_finish_stopped", (), c_int64
)
pcc_gc_visit_registered_root_slots = extern(
    "pcc_gc_visit_registered_root_slots", (c_int64, c_int64), c_int64
)
pcc_gc_generational_object_is_address_pinned = extern(
    "pcc_gc_generational_object_is_address_pinned", (c_ptr,), c_int64
)
pcc_gc_object_node_young_next = extern(
    "pcc_gc_object_node_young_next", (c_ptr,), c_ptr
)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_tracing_finalize_unpinned = extern(
    "pcc_gc_tracing_finalize_unpinned", (c_ptr,), c_void
)


@c_abi_export("pcc_gc_backend3_enable_closed_young_domain")
def pcc_gc_backend3_enable_closed_young_domain() -> None:
    # A generated entry cannot reopen a domain revoked by an earlier native
    # constructor or dynamic load. A native host never calls this entry.
    if (ptr_is_null(pcc_platform_getenv(cstr("LD_PRELOAD"))) == 0
            or ptr_is_null(pcc_platform_getenv(cstr("LD_LIBRARY_PATH"))) == 0
            or ptr_is_null(pcc_platform_getenv(cstr("DYLD_INSERT_LIBRARIES"))) == 0
            or ptr_is_null(pcc_platform_getenv(cstr("DYLD_LIBRARY_PATH"))) == 0):
        pcc_gc_backend3_revoke_closed_young_domain()
        return
    atomic_cas_i32(global_addr("pcc_gc_backend3_closed_young_domain"),
                   0, 0, 1, "acq_rel", "acquire")


@c_abi_export("pcc_gc_backend3_revoke_closed_young_domain")
def pcc_gc_backend3_revoke_closed_young_domain() -> None:
    atomic_store_i32(global_addr("pcc_gc_backend3_closed_young_domain"),
                     0, 2, "release")
