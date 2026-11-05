"""Common PASS-0/PASS-1/PASS-2 sweep kernel for tracing GC backends."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_dealloc_typed = extern("pcc_gc_dealloc_typed", (c_ptr, c_int64), c_void)


pcc_gc_object_is_address_pinned = extern("pcc_gc_object_is_address_pinned", (c_ptr,), c_int64)
pcc_gc_object_node_is_address_pinned = extern(
    "pcc_gc_object_node_is_address_pinned", (c_ptr, c_ptr), c_int64
)


pcc_py_gc_minor_graph_lock = extern(
    "pcc_py_gc_minor_graph_lock", (), c_void
)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_seed_roots = extern("pcc_gc_seed_roots", (), c_void)
pcc_gc_mark_root_gray_if_known = extern(
    "pcc_gc_mark_root_gray_if_known", (c_ptr,), c_void
)
pcc_gc_drain_all_gray_unlocked = extern(
    "pcc_gc_drain_all_gray_unlocked", (), c_int64
)
pcc_gc_tracing_clear_unreachable = extern(
    "pcc_gc_tracing_clear_unreachable", (c_ptr,), c_void
)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_note_object_freeing = extern(
    "pcc_gc_note_object_freeing", (c_ptr,), c_void
)
pcc_gc_free_watch_begin = extern(
    "pcc_gc_free_watch_begin", (c_ptr, c_ptr), c_void
)
pcc_gc_free_watch_end = extern("pcc_gc_free_watch_end", (c_ptr,), c_int64)
# pcc_gc_free_watch_begin's record (layout owned by py_gc_backend.py).
GC_FREE_WATCH_BYTES = 24
pcc_gc_forwarding_index_find = extern(
    "pcc_gc_forwarding_index_find", (c_ptr,), c_ptr
)
pcc_refcount_forget = extern("pcc_refcount_forget", (c_ptr,), c_void)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_weakref_invalidate_tracing_candidates = extern(
    "py_weakref_invalidate_tracing_candidates", (), c_int64
)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)

pcc_thread_world_stop_epoch_held = extern(
    "pcc_thread_world_stop_epoch_held", (), c_int64
)


@c_abi_export("pcc_gc_tracing_has_sweep_candidate")
def pcc_gc_tracing_has_sweep_candidate() -> i64:
    # Only a finished mark sets SWEEP_CANDIDATE (1024), and it commits after
    # flagging (pcc_gc_tracing_finish_commits). A walk that finds none stays
    # true until the next commit; schedulers ask once per step, so walking
    # every tracked object per question made a GC3 collection quadratic.
    pcc_py_gc_minor_graph_lock()
    commits: i64 = load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    if load_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0) == commits:
        pcc_py_gc_minor_graph_unlock()
        return 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_object_node_is_active(node) == 0:
            node = load_ptr(node, 16)
            continue
        obj = load_ptr(node, 0)
        flags: i64 = load_i32(obj, 12)
        if (flags & 1024) != 0:
            pcc_py_gc_minor_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    # A finish that committed during the walk may have flagged nodes behind it.
    if load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == commits:
        store_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0, commits)
    pcc_py_gc_minor_graph_unlock()
    return 0


@c_abi_export("pcc_gc_tracing_finalize_unreachable")
def pcc_gc_tracing_finalize_unreachable(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if pcc_gc_object_is_address_pinned(obj) != 0:
        return
    pcc_gc_tracing_finalize_unpinned(obj)


@c_abi_export("pcc_gc_tracing_finalize_unpinned")
def pcc_gc_tracing_finalize_unpinned(obj) -> None:
    """Finalize a candidate whose caller found it unpinned (its walk read
    the object's own node)."""
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    flags: i64 = load_i32(obj, 12)
    tag: i64 = load_i32(obj, 8)
    if (
        load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0
        and pcc_capi_is_cext_type_tag(tag) == 0
    ):
        # PASS 0 has already run ordinary finalizers. Instance destruction
        # must not look up __del__ again in namespaces cleared by PASS 1,
        # including when the first lookup found no finalizer.
        flags = flags | 4
    delay_zpage_freeing_note: i64 = 0
    if backend == 4 and (flags & 65536) != 0:
        delay_zpage_freeing_note: i64 = 1

    # Publish logical death before a type-specific deallocator can safepoint.
    store_i32(obj, 12, flags | 524288)
    if delay_zpage_freeing_note == 0:
        pcc_gc_note_object_freeing(obj)
    pcc_refcount_forget(obj)
    py_gc_untrack(obj)
    store_i64(obj, 0, 0)
    watch = stack_alloc(GC_FREE_WATCH_BYTES)
    if delay_zpage_freeing_note != 0:
        pcc_gc_free_watch_begin(watch, obj)

    pcc_gc_dealloc_typed(obj, tag)
    if delay_zpage_freeing_note != 0:
        # The deallocator's free normally noted obj, and that note may have
        # handed its zpage bytes to another thread: note only when it did not.
        if pcc_gc_free_watch_end(watch) == 0:
            pcc_gc_note_object_freeing(obj)


@c_abi_export("pcc_gc_tracing_recheck_reachability_after_finalizers")
def pcc_gc_tracing_recheck_reachability_after_finalizers() -> None:
    # Refresh the candidate verdict from current roots.  This is also used
    # before PASS-0; keep the exported name for the existing runtime ABI.
    # After PASS-0, PEP 442 requires the same refresh to rescue resurrection
    # before any referent is cleared.
    pcc_gc_seed_roots()
    if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
        # This sweep owns a fixed disposal set. A finalizer can allocate an
        # object outside it, then leave that object temporarily unreachable.
        # It will survive this sweep, so its referents and class layout must
        # survive too. Ordinary RC used to hide this physical-lifetime edge.
        node = global_load_ptr("pcc_gc_object_head")
        while ptr_is_null(node) == 0:
            nxt = load_ptr(node, 16)
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (load_i32(obj, 12) & 1024) == 0:
                    pcc_gc_mark_root_gray_if_known(obj)
            node = nxt
    pcc_gc_drain_all_gray_unlocked()
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            # A GC4 forwarding source is a physical shell, not another
            # finalizable object. Its payload and forwarding reference belong
            # to remap retirement. Test the edge, not the candidate flag: a
            # selected object that has not moved still needs ordinary sweeping.
            if (flags & 1024) != 0 and (
                (flags & 8) == 0
                or (
                    backend == 4
                    and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
                )
            ):
                store_i32(obj, 12, flags & ~1024)
        node = nxt


_CLASS_CLEAR_HAS_REFS: i64 = 0
_CLASS_CLEAR_STATE_BYTES: i64 = 8


@c_abi_export("pcc_gc_tracing_class_note_owned_slot")
def _class_note_owned_slot(slot, role: i64, context) -> None:
    if role == 1 or role == 2:
        if ptr_is_null(load_ptr(slot, 0)) == 0:
            store_i64(context, _CLASS_CLEAR_HAS_REFS, 1)


@c_abi_export("pcc_gc_tracing_class_needs_clear")
def _class_needs_clear(obj) -> i64:
    state = stack_alloc(_CLASS_CLEAR_STATE_BYTES)
    store_i64(state, _CLASS_CLEAR_HAS_REFS, 0)
    pcc_gc_visit_object_slots(obj, _class_note_owned_slot, state)
    return load_i64(state, _CLASS_CLEAR_HAS_REFS)


@c_abi_export("pcc_gc_tracing_clear_class_layout_slot")
def _clear_class_layout_slot(slot, role: i64, context) -> None:
    # Class bases are role-2 owning layout edges. Their counted path keeps
    # them until class deallocation; tracing must detach them before any
    # sibling class is freed. No child header or reference count is read.
    if role == 2:
        store_ptr(slot, 0, null())


@c_abi_export("pcc_gc_tracing_sweep_unreachable")
def pcc_gc_tracing_sweep_unreachable(budget: i64) -> i64:
    if budget <= 0:
        return 0
    count_free: i64 = load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0)

    # A completed concurrent/incremental mark may leave a pending candidate
    # that is reachable at this sweep boundary.  Revalidate before invoking
    # user finalizers: rescuing it only after __del__ is already too late.
    # A mark finished in this same stopped world saw no mutator since, so
    # its verdicts stand. Keep the separate post-finalizer refresh for PEP
    # 442 resurrection.
    held: i64 = pcc_thread_world_stop_epoch_held()
    if held < 0 or held != load_i64(global_addr("pcc_gc_tracing_finish_stop_epoch"), 0):
        pcc_gc_tracing_recheck_reachability_after_finalizers()
    if count_free != 0:
        if py_weakref_invalidate_tracing_candidates() != 0:
            # Weak callbacks can resurrect other candidates. Their new roots
            # must be visible before any candidate's finalizer can run.
            pcc_gc_tracing_recheck_reachability_after_finalizers()
    finalizer_runs: i64 = load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0)

    # PASS 0: finalizers see intact fields.  C-extension tags are excluded
    # because their lifecycle is owned by the C-API bridge.
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            # Shell retirement owns this physical object, even when the
            # same-world shortcut omitted the reachability refresh.
            if (
                (flags & 1024) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
                and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
            ):
                flags = flags & ~1024
                store_i32(obj, 12, flags)
            if (flags & 1024) != 0 and (flags & 16384) == 0 and pcc_gc_object_node_is_address_pinned(node, obj) == 0:
                tag: i64 = load_i32(obj, 8)
                if pcc_capi_is_cext_type_tag(tag) == 0:
                    py_user_del_dispatch(obj)
        node = nxt

    # Only user code -- a finalizer -- can resurrect a candidate or change
    # the graph during this stopped-world sweep.
    if load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) != finalizer_runs:
        pcc_gc_tracing_recheck_reachability_after_finalizers()

    # PASS 1: clear at most budget candidates without freeing any sibling.
    cleared: i64 = 0
    cleared_nonclasses: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0 and cleared < budget:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) == 0:
            node = nxt
            continue
        obj = load_ptr(node, 0)
        flags = load_i32(obj, 12)
        # Re-evaluate eligibility after callback-capable finalizers.
        if (
            (flags & 1024) != 0
            and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
            and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
        ):
            flags = flags & ~1024
            store_i32(obj, 12, flags)
        if (flags & 1024) != 0 and (flags & 16384) == 0 and pcc_gc_object_node_is_address_pinned(node, obj) == 0:
            is_class: i64 = 1 if load_i32(obj, 8) == abi_constant("object.type.class") else 0
            # A class can remain until a later budget slice frees its
            # instances. Empty owning slots prove clearing already finished;
            # do not spend each slice clearing the same class again.
            if count_free == 0 or is_class == 0 or _class_needs_clear(obj) != 0:
                pcc_gc_tracing_clear_unreachable(obj)
                if count_free != 0 and is_class != 0:
                    pcc_gc_visit_object_slots(obj, _clear_class_layout_slot, null())
                else:
                    cleared_nonclasses = cleared_nonclasses + 1
                cleared = cleared + 1
        node = nxt

    # PASS 2: finalize the same number of still-candidate objects.
    reclaimed: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) == 0:
            node = nxt
            continue
        obj = load_ptr(node, 0)
        flags = load_i32(obj, 12)
        if (flags & 1024) != 0:
            # Slot/weakref cleanup may call user code; check again before
            # generic destruction, without charging a shell to the budget.
            if (
                load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
                and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
            ):
                store_i32(obj, 12, flags & ~1024)
            elif (flags & 16384) != 0 or pcc_gc_object_node_is_address_pinned(node, obj) != 0:
                store_i32(obj, 12, flags & ~1024)
            elif load_i32(obj, 8) == abi_constant("object.type.class"):
                if count_free == 0:
                    # Legacy counted collectors still release metadata with
                    # the last owning instance/subclass reference.
                    store_i32(obj, 12, flags & ~1024)
            elif reclaimed < (cleared_nonclasses if count_free != 0 else cleared):
                pcc_gc_tracing_finalize_unpinned(obj)
                reclaimed = reclaimed + 1
        node = nxt

    if count_free != 0 and reclaimed < budget:
        # Keep layout metadata until EVERY pending ordinary object has been
        # destroyed, and every candidate class has detached its owning edges.
        # This also covers bounded slices and inheritance/metaclass cycles.
        node = global_load_ptr("pcc_gc_object_head")
        while ptr_is_null(node) == 0:
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (load_i32(obj, 12) & 1024) != 0:
                    if load_i32(obj, 8) != abi_constant("object.type.class"):
                        return reclaimed
                    if _class_needs_clear(obj) != 0:
                        return reclaimed
            node = load_ptr(node, 16)
        node = global_load_ptr("pcc_gc_object_head")
        while ptr_is_null(node) == 0 and reclaimed < budget:
            nxt = load_ptr(node, 16)
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (
                    load_i32(obj, 8) == abi_constant("object.type.class")
                    and (load_i32(obj, 12) & 1024) != 0
                    and pcc_gc_object_node_is_address_pinned(node, obj) == 0
                ):
                    pcc_gc_tracing_finalize_unpinned(obj)
                    reclaimed = reclaimed + 1
            node = nxt
    return reclaimed
