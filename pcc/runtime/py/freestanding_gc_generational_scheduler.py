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
pcc_gc_backend3_drain_remembered_owners = extern(
    "pcc_gc_backend3_drain_remembered_owners",
    (c_int64, c_ptr),
    c_int64
)
pcc_capi_visit_extension_module_state_roots = extern(
    "pcc_capi_visit_extension_module_state_roots", (c_ptr, c_ptr), c_void
)
pcc_gc_backend3_finish_detached_remembered_owners = extern(
    "pcc_gc_backend3_finish_detached_remembered_owners", (c_ptr,), c_void
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


@c_abi_export("pcc_gc_backend3_frame_root_scan_reset_locked")
def _reset_frame_root_scan() -> None:
    store_i32(global_addr("pcc_gc_backend3_frame_root_scan_phase"), 0, 0)
    store_i64(global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0, -1)
    global_store_ptr("pcc_gc_backend3_frame_root_scan_cursor", null())
    global_store_ptr(
        "pcc_gc_backend3_continuation_root_scan_cursor", null()
    )


@c_abi_export("pcc_gc_backend3_scheduler_root_scan_reset_locked")
def _reset_scheduler_root_scan() -> None:
    store_i32(
        global_addr("pcc_gc_backend3_scheduler_root_scan_phase"), 0, 0
    )
    store_i64(
        global_addr("pcc_gc_backend3_scheduler_root_scan_slot"), 0, -1
    )
    global_store_ptr("pcc_gc_backend3_scheduler_root_scan_cursor", null())


@c_abi_export("pcc_gc_generational_promote_frame_roots")
def pcc_gc_generational_promote_frame_roots(remaining_budget: i64) -> None:
    if remaining_budget <= 0:
        return
    walk_pending: i64 = 0
    pcc_py_gc_minor_graph_lock()
    revision: i64 = 0
    examined: i64 = 0
    while examined < remaining_budget:
        phase: i64 = load_i32(
            global_addr("pcc_gc_backend3_frame_root_scan_phase"), 0
        )
        slot_index: i64 = load_i64(
            global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0
        )
        if phase == 0:
            if slot_index < 0:
                global_store_ptr(
                    "pcc_gc_backend3_frame_root_scan_cursor",
                    global_load_ptr("pcc_gc_frame_head"),
                )
                store_i64(
                    global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                    0,
                    0,
                )
                slot_index = 0
            frame = global_load_ptr("pcc_gc_backend3_frame_root_scan_cursor")
            if ptr_is_null(frame) != 0:
                store_i32(
                    global_addr("pcc_gc_backend3_frame_root_scan_phase"),
                    0,
                    1,
                )
                store_i64(
                    global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                    0,
                    -1,
                )
                continue
            root_count: i64 = load_i64(frame, 40)
            if slot_index >= root_count:
                global_store_ptr(
                    "pcc_gc_backend3_frame_root_scan_cursor",
                    load_ptr(frame, 16),
                )
                store_i64(
                    global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                    0,
                    0,
                )
                continue
            revision_before: i64 = atomic_load_i64(
                global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
            )
            pcc_gc_visit_mapped_root_slot(
                load_ptr(frame, 8),
                slot_index * 8,
                load_ptr(frame, 56),
                load_i32(frame, 48) & 1,
                2,
                0,
            )
            examined = examined + 1
            revision = atomic_load_i64(
                global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
            )
            if revision != revision_before:
                continue
            store_i64(
                global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                0,
                slot_index + 1,
            )
            if slot_index + 1 >= root_count:
                global_store_ptr(
                    "pcc_gc_backend3_frame_root_scan_cursor",
                    load_ptr(frame, 16),
                )
                store_i64(
                    global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                    0,
                    0,
                )
            continue

        if slot_index < 0:
            global_store_ptr(
                "pcc_gc_backend3_continuation_root_scan_cursor",
                global_load_ptr("pcc_gc_continuation_root_head"),
            )
            store_i64(
                global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0, 0
            )
            slot_index = 0
        continuation = global_load_ptr(
            "pcc_gc_backend3_continuation_root_scan_cursor"
        )
        if ptr_is_null(continuation) != 0:
            # The pass ends with the mapped stack frames of every mutator,
            # promoted together under a stopped world (they do not register
            # while the precise walk is active).
            _reset_frame_root_scan()
            walk_pending = 1
            break
        root_count = load_i64(continuation, 24)
        if slot_index >= root_count:
            global_store_ptr(
                "pcc_gc_backend3_continuation_root_scan_cursor",
                load_ptr(continuation, 16),
            )
            store_i64(
                global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0, 0
            )
            continue
        revision_before = atomic_load_i64(
            global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
        )
        pcc_gc_visit_mapped_root_slot(
            load_ptr(continuation, 8),
            slot_index * 8,
            load_ptr(continuation, 40),
            load_i32(continuation, 32),
            2,
            0,
        )
        examined = examined + 1
        revision = atomic_load_i64(
            global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
        )
        if revision != revision_before:
            continue
        store_i64(
            global_addr("pcc_gc_backend3_frame_root_scan_slot"),
            0,
            slot_index + 1,
        )
        if slot_index + 1 >= root_count:
            global_store_ptr(
                "pcc_gc_backend3_continuation_root_scan_cursor",
                load_ptr(continuation, 16),
            )
            store_i64(
                global_addr("pcc_gc_backend3_frame_root_scan_slot"), 0, 0
            )
    pcc_py_gc_minor_graph_unlock()
    if walk_pending != 0:
        # The walker may wait for another stopped-world owner or mutator.
        # Finish/reset the registry cursor under the graph lock, then drop
        # it before that wait; the walk owns STW while it visits stack slots.
        # No registry node or slot address is carried across this unlock.
        pcc_gc_visit_walked_stack_roots(2, 0)
    if examined < remaining_budget:
        pcc_gc_backend3_drain_promotion_worklist(
            remaining_budget - examined
        )


@c_abi_export("pcc_gc_generational_promote_scheduler_roots")
def pcc_gc_generational_promote_scheduler_roots(
    remaining_budget: i64,
) -> None:
    if remaining_budget <= 0:
        return
    pcc_py_gc_minor_graph_lock()
    revision: i64 = 0
    examined: i64 = 0
    while examined < remaining_budget:
        phase: i64 = load_i32(
            global_addr("pcc_gc_backend3_scheduler_root_scan_phase"), 0
        )
        slot_index: i64 = load_i64(
            global_addr("pcc_gc_backend3_scheduler_root_scan_slot"), 0
        )
        if phase == 0:
            if slot_index < 0:
                global_store_ptr(
                    "pcc_gc_backend3_scheduler_root_scan_cursor",
                    global_load_ptr("pcc_gc_scheduler_root_head"),
                )
                store_i64(
                    global_addr("pcc_gc_backend3_scheduler_root_scan_slot"),
                    0,
                    0,
                )
            node = global_load_ptr(
                "pcc_gc_backend3_scheduler_root_scan_cursor"
            )
            if ptr_is_null(node) != 0:
                store_i32(
                    global_addr("pcc_gc_backend3_scheduler_root_scan_phase"),
                    0,
                    1,
                )
                store_i64(
                    global_addr("pcc_gc_backend3_scheduler_root_scan_slot"),
                    0,
                    0,
                )
                continue
            next_node = load_ptr(node, 8)
            revision_before: i64 = atomic_load_i64(
                global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
            )
            slot = load_ptr(node, 0)
            if ptr_is_null(slot) == 0:
                pcc_gc_visit_mapped_root_slot(slot, 0, null(), 0, 2, 0)
            examined = examined + 1
            revision = atomic_load_i64(
                global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
            )
            if revision != revision_before:
                continue
            global_store_ptr(
                "pcc_gc_backend3_scheduler_root_scan_cursor", next_node
            )
            continue

        slot = null()
        if slot_index < 65:  # PY_EXC_N_BUILTIN
            slot = py_subs_exc_cache_slot(slot_index)
        else:
            slot = load_ptr(global_addr("pcc_builtin_type_root_slots"), (slot_index - 65) * 8)
        if ptr_is_null(slot) != 0:
            _reset_scheduler_root_scan()
            break
        revision_before = atomic_load_i64(
            global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
        )
        if ptr_is_null(slot) == 0:
            pcc_gc_visit_mapped_root_slot(slot, 0, null(), 0, 2, 0)
        examined = examined + 1
        revision = atomic_load_i64(
            global_addr("pcc_gc_root_registry_revision"), 0, "acquire"
        )
        if revision != revision_before:
            continue
        store_i64(
            global_addr("pcc_gc_backend3_scheduler_root_scan_slot"),
            0,
            slot_index + 1,
        )
    pcc_py_gc_minor_graph_unlock()
    if examined < remaining_budget:
        pcc_gc_backend3_drain_promotion_worklist(
            remaining_budget - examined
        )


@c_abi_export("pcc_gc_generational_promote_tls_exception_root")
def pcc_gc_generational_promote_tls_exception_root(cleanup_out: c_ptr) -> None:
    if ptr_is_null(cleanup_out) != 0:
        return
    if ptr_is_null(load_ptr(cleanup_out, 0)) == 0:
        return
    current = py_tls_exc_get()
    if ptr_is_null(current) != 0 or is_tagged_int(current) != 0:
        return
    oldified = pcc_gc_generational_oldify_copy(current)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, current) == 0:
            py_incref(oldified)
            py_tls_exc_set(oldified)
            pcc_gc_trace_referents_for_promotion(oldified)
            store_ptr(cleanup_out, 0, current)
            return
    pcc_gc_generational_promote_young_if_known(current)


@c_abi_export("pcc_gc_generational_promote_extension_module_state_root")
def pcc_gc_generational_promote_extension_module_state_root(
    root: c_ptr, ctx: c_ptr
) -> None:
    if ptr_is_null(root) != 0 or is_tagged_int(root) != 0:
        return
    pcc_py_gc_minor_graph_lock()
    pcc_gc_generational_promote_young_if_known(root)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_backend3_drain_promotion_worklist(16)


@c_abi_export("pcc_gc_generational_step")
def pcc_gc_generational_step(
    remaining_budget: i64, promote_all_young: i64
) -> i64:
    if remaining_budget <= 0:
        return 0
    # One admission covers every moving source: legacy/native/stackmap
    # frames, scheduler/TLS roots, remembered owners, worklist and extensions.
    # Nursery refill enters here directly, outside pcc_gc_step's dispatcher.
    stopped_here: i64 = 0
    if pcc_thread_owns_stopped_world() == 0:
        if pcc_stop_the_world() != 0:
            return 0
        stopped_here = 1
    oldify_suspended: i64 = load_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0)
    if pcc_thread_all_stopped_at_gates() == 0:
        store_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0, 1)
    pin_snapshot = null()
    if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
        if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 3:
            pin_snapshot = stack_alloc(_STEP_PIN_CONTEXT_BYTES)
            pcc_gc_conservative_step_pin_begin(pin_snapshot)
        else:
            pcc_gc_pin_conservative_thread_roots()
    batch_budget: i64 = remaining_budget
    if batch_budget > 16:
        batch_budget = 16
    detached_remembered = stack_alloc(8)
    store_ptr(detached_remembered, 0, null())
    tls_cleanup = stack_alloc(8)
    store_ptr(tls_cleanup, 0, null())
    pcc_gc_generational_promote_frame_roots(batch_budget)
    pcc_gc_generational_promote_scheduler_roots(batch_budget)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_generational_promote_tls_exception_root(tls_cleanup)
    local_processed: i64 = 0
    local_processed = local_processed + pcc_gc_backend3_drain_remembered_owners(
        batch_budget - local_processed, detached_remembered
    )
    if promote_all_young != 0:
        while (
            ptr_is_null(pcc_gc_backend3_young_list_head()) == 0
            and local_processed < batch_budget
        ):
            node = pcc_gc_backend3_young_list_head()
            pcc_gc_backend3_young_unlink(node)
            if pcc_gc_object_node_is_active(node) == 0:
                continue
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            if (flags & 128) == 0:
                continue
            if ptr_is_null(pcc_gc_forwarding_find(obj)) == 0:
                continue
            pcc_gc_generational_promote_young_if_known(obj)
            after_flags: i64 = load_i32(obj, 12)
            if (
                (after_flags & 128) == 0
                or ptr_is_null(pcc_gc_forwarding_find(obj)) == 0
            ):
                local_processed = local_processed + 1
            else:
                pcc_gc_backend3_young_link_head(node)
                break
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_backend3_finish_detached_remembered_owners(
        load_ptr(detached_remembered, 0)
    )
    tls_cleanup_value = load_ptr(tls_cleanup, 0)
    if ptr_is_null(tls_cleanup_value) == 0:
        py_decref(tls_cleanup_value)
    if local_processed < remaining_budget:
        local_processed = local_processed + (
            pcc_gc_backend3_drain_promotion_worklist(
                remaining_budget - local_processed
            )
        )
    pcc_capi_visit_extension_module_state_roots(
        function_addr("pcc_gc_generational_promote_extension_module_state_root"),
        null(),
    )
    store_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0, oldify_suspended)
    if ptr_is_null(pin_snapshot) == 0:
        pcc_gc_conservative_step_pin_end(pin_snapshot)
    if stopped_here != 0:
        pcc_resume_world()
    if local_processed > 0:
        pcc_thread_safepoint()
    return local_processed


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


@c_abi_export("pcc_gc_backend3_young_cut_admitted")
def _young_cut_admitted() -> i64:
    if (load_i32(global_addr("pcc_gc_backend_selected"), 0) != 3
            or load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0
            or atomic_load_i32(global_addr("pcc_gc_backend3_closed_young_domain"),
                               0, "acquire") != 1):
        return 0
    # This is a separate young liveness cut, never a consumer of major colors
    # or partially traced major state. Every admission query is constant-size.
    if (load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
            or load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend4_remap_active"), 0) != 0
            or load_i32(global_addr("pcc_gc_cms_sweep_pending"), 0) != 0
            or load_i32(global_addr("pcc_gc_cms_finish_pending"), 0) != 0
            or load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0) != 0
            or load_i64(global_addr("pcc_gc_tracing_finish_claim_epoch"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend3_remembered_overflow"), 0) != 0):
        return 0
    commits: i64 = load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    if (commits != 0
            and load_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0) != commits):
        return 0
    # The first implementation never invalidates weakrefs or invokes callbacks.
    # Even a weakref to a noncandidate can resurrect a candidate via a callback,
    # so keep this conservative process-wide gate until a weak batch owner exists.
    if ptr_is_null(global_load_ptr("py_weakref_head")) == 0:
        return 0
    return 1


@c_abi_export("pcc_gc_backend3_young_scalar_tag")
def _young_scalar_tag(tag: i64) -> i64:
    # Exact builtin tags only: no subclasses, foreign handles, user finalizers,
    # or mutable out-of-line buffers are destructed by this first young cut.
    if (tag == PY_TYPE_INT or tag == PY_TYPE_FLOAT or tag == PY_TYPE_STR
            or tag == PY_TYPE_BYTES or tag == PY_TYPE_COMPLEX):
        return 1
    return 0


@c_abi_export("pcc_gc_backend3_collect_closed_young")
def pcc_gc_backend3_collect_closed_young() -> i64:
    """Cold complete scalar-young collection, not a budgeted promotion step.

    A stopped cut visits complete roots, young owners and remembered edges,
    never the old-object list. Promoting survivors in place provides a separate
    liveness bit without changing major colors or installing forwarding edges.
    Non-scalar young owners and their referents are retained for the major GC.
    Returns -1 when not admitted; otherwise the number actually reclaimed.
    """
    if (_young_cut_admitted() == 0
            or load_i32(global_addr("pcc_gc_backend3_young_cut_active"), 0) != 0
            or pcc_thread_no_park_depth() > 0):
        return -1
    stopped_here: i64 = 0
    if pcc_thread_owns_stopped_world() == 0:
        if pcc_stop_the_world() != 0:
            return -1
        stopped_here = 1
    # Every collection entry finishes any outstanding detached destruction
    # before interpreting index/node lifetime. GC3 normally has no such work.
    pcc_gc_concurrent_sweep_finish_stopped()
    pcc_py_gc_minor_graph_lock()
    if (_young_cut_admitted() == 0
            or load_i32(global_addr("pcc_gc_backend3_young_cut_active"), 0) != 0):
        pcc_py_gc_minor_graph_unlock()
        if stopped_here != 0:
            pcc_resume_world()
        return -1
    store_i32(global_addr("pcc_gc_backend3_young_cut_active"), 0, 1)
    suspended: i64 = load_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0)
    store_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0, 1)
    root_revision: i64 = atomic_load_i64(global_addr("pcc_gc_root_registry_revision"), 0, "acquire")
    object_revision: i64 = load_i64(global_addr("pcc_gc_object_list_revision"), 0)
    pin_snapshot = stack_alloc(_STEP_PIN_CONTEXT_BYTES)
    pcc_gc_conservative_step_pin_begin(pin_snapshot)

    # Mode 7 always inspects the current value, even when the promotion cache
    # remembers the same address from an older object lifetime.
    pcc_gc_visit_registered_root_slots(7, 0)
    node = pcc_gc_backend3_young_list_head()
    while ptr_is_null(node) == 0:
        nxt = pcc_gc_object_node_young_next(node)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            if (_young_scalar_tag(load_i32(obj, 8)) == 0
                    or (flags & (PY_FLAG_GC_PINNED | PY_FLAG_IMMORTAL | 1024 | 2048 | 8192 | 524288)) != 0
                    or pcc_gc_generational_object_is_address_pinned(obj) != 0):
                pcc_gc_generational_retain_closed_young(obj)
        node = nxt
    detached = stack_alloc(8)
    store_ptr(detached, 0, null())
    pcc_gc_backend3_drain_remembered_owners(9223372036854775807, detached)
    pcc_gc_backend3_finish_detached_remembered_owners(load_ptr(detached, 0))
    pcc_gc_backend3_drain_promotion_worklist(9223372036854775807)

    freed: i64 = -1
    # A callback-capable visitor can invalidate the cut. No user object is
    # freed unless both queues and the complete root/heap publication cut hold.
    if (_young_cut_admitted() != 0
            and load_i64(pin_snapshot, 16) >= 0
            and root_revision == atomic_load_i64(global_addr("pcc_gc_root_registry_revision"), 0, "acquire")
            and object_revision == load_i64(global_addr("pcc_gc_object_list_revision"), 0)
            and ptr_is_null(global_load_ptr("pcc_gc_backend3_remembered_owner_head")) != 0
            and ptr_is_null(global_load_ptr("pcc_gc_backend3_promotion_head")) != 0):
        freed = 0
        node = pcc_gc_backend3_young_list_head()
        while ptr_is_null(node) == 0:
            nxt = pcc_gc_object_node_young_next(node)
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                flags = load_i32(obj, 12)
                if ((flags & 128) != 0
                        and (flags & (PY_FLAG_GC_PINNED | PY_FLAG_IMMORTAL | 1024 | 2048 | 8192 | 524288)) == 0
                        and _young_scalar_tag(load_i32(obj, 8)) != 0):
                    pcc_gc_tracing_finalize_unpinned(obj)
                    if ptr_is_null(pcc_gc_object_index_find(obj)) != 0:
                        freed = freed + 1
            node = nxt
        store_i64(global_addr("pcc_gc_backend3_young_cut_commits"), 0,
                  load_i64(global_addr("pcc_gc_backend3_young_cut_commits"), 0) + 1)
        store_i64(global_addr("pcc_gc_backend3_young_cut_freed"), 0,
                  load_i64(global_addr("pcc_gc_backend3_young_cut_freed"), 0) + freed)
    pcc_gc_conservative_step_pin_end(pin_snapshot)
    store_i32(global_addr("pcc_gc_backend3_oldify_suspended"), 0, suspended)
    store_i32(global_addr("pcc_gc_backend3_young_cut_active"), 0, 0)
    pcc_py_gc_minor_graph_unlock()
    if stopped_here != 0:
        pcc_resume_world()
    return freed


@c_abi_export("pcc_gc_backend3_young_cut_count")
def pcc_gc_backend3_young_cut_count() -> i64:
    return load_i64(global_addr("pcc_gc_backend3_young_cut_commits"), 0)


@c_abi_export("pcc_gc_backend3_young_cut_reclaimed")
def pcc_gc_backend3_young_cut_reclaimed() -> i64:
    return load_i64(global_addr("pcc_gc_backend3_young_cut_freed"), 0)
