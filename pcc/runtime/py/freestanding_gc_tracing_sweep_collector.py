"""Common PASS-0/PASS-1/PASS-2 sweep kernel for tracing GC backends."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    atomic_load_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_scalar_sweep_try_enqueue = extern(
    "pcc_gc_scalar_sweep_try_enqueue", (c_ptr, c_ptr), c_int64
)

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
pcc_gc_tracing_clear_unpinned = extern(
    "pcc_gc_tracing_clear_unpinned", (c_ptr,), c_void
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
py_gen_finalize_tracing_candidate = extern(
    "py_gen_finalize_tracing_candidate", (c_ptr,), c_void
)
py_weakref_invalidate_tracing_candidates = extern(
    "py_weakref_invalidate_tracing_candidates", (), c_int64
)
py_weakref_clear_tracing_candidates = extern(
    "py_weakref_clear_tracing_candidates", (), c_int64
)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)

pcc_thread_world_stop_epoch_held = extern(
    "pcc_thread_world_stop_epoch_held", (), c_int64
)
pcc_platform_monotonic_us = extern("pcc_platform_monotonic_us", (), c_int64)
pcc_gc_object_walk_first = extern("pcc_gc_object_walk_first", (), c_ptr)
pcc_gc_object_walk_next = extern("pcc_gc_object_walk_next", (c_ptr,), c_ptr)
pcc_gc_object_is_white = extern("pcc_gc_object_is_white", (c_ptr, c_int64), c_int64)


@c_abi_export("pcc_gc_record_sweep_phase")
def _record_sweep_phase(phase: i64, start_us: i64, end_us: i64, nodes: i64, work: i64) -> None:
    # Aggregate once per completed phase, never atomically on every object.
    # The slots are individually atomic; interval read/reset requires a
    # quiescent collector, not an assumed multi-counter snapshot.
    if nodes < 0 or work < 0:
        return
    if phase == 0:
        calls_slot = global_addr("pcc_gc_metric_sweep_candidate_calls")
        nodes_slot = global_addr("pcc_gc_metric_sweep_candidate_nodes")
        work_slot = global_addr("pcc_gc_metric_sweep_candidate_work")
        time_slot = global_addr("pcc_gc_metric_sweep_candidate_sum_us")
    elif phase == 1:
        calls_slot = global_addr("pcc_gc_metric_sweep_pass0_calls")
        nodes_slot = global_addr("pcc_gc_metric_sweep_pass0_nodes")
        work_slot = global_addr("pcc_gc_metric_sweep_pass0_work")
        time_slot = global_addr("pcc_gc_metric_sweep_pass0_sum_us")
    elif phase == 2:
        calls_slot = global_addr("pcc_gc_metric_sweep_pass1_calls")
        nodes_slot = global_addr("pcc_gc_metric_sweep_pass1_nodes")
        work_slot = global_addr("pcc_gc_metric_sweep_pass1_work")
        time_slot = global_addr("pcc_gc_metric_sweep_pass1_sum_us")
    elif phase == 3:
        calls_slot = global_addr("pcc_gc_metric_sweep_pass2_calls")
        nodes_slot = global_addr("pcc_gc_metric_sweep_pass2_nodes")
        work_slot = global_addr("pcc_gc_metric_sweep_pass2_work")
        time_slot = global_addr("pcc_gc_metric_sweep_pass2_sum_us")
    elif phase == 4:
        calls_slot = global_addr("pcc_gc_metric_sweep_recheck_calls")
        nodes_slot = global_addr("pcc_gc_metric_sweep_recheck_nodes")
        work_slot = global_addr("pcc_gc_metric_sweep_recheck_work")
        time_slot = global_addr("pcc_gc_metric_sweep_recheck_sum_us")
    else:
        return
    atomic_rmw_i64("add", calls_slot, 0, 1, "relaxed")
    atomic_rmw_i64("add", nodes_slot, 0, nodes, "relaxed")
    atomic_rmw_i64("add", work_slot, 0, work, "relaxed")
    if start_us > 0 and end_us >= start_us:
        elapsed: i64 = end_us - start_us
        if elapsed == 0:
            elapsed = 1
        atomic_rmw_i64("add", time_slot, 0, elapsed, "relaxed")


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
    # Leave the cached no-candidate fast path untouched. Measure the actual
    # walk after graph-lock acquisition; lock wait has a different owner.
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    node = pcc_gc_object_walk_first()
    while ptr_is_null(node) == 0:
        visited = visited + 1
        if pcc_gc_object_node_is_active(node) == 0:
            node = pcc_gc_object_walk_next(node)
            continue
        obj = load_ptr(node, 0)
        flags: i64 = load_i32(obj, 12)
        if (flags & 1024) != 0:
            _record_sweep_phase(0, started, pcc_platform_monotonic_us(), visited, 1)
            pcc_py_gc_minor_graph_unlock()
            return 1
        node = pcc_gc_object_walk_next(node)
    # A finish that committed during the walk may have flagged nodes behind it.
    if load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == commits:
        store_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0, commits)
    _record_sweep_phase(0, started, pcc_platform_monotonic_us(), visited, 0)
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
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    cleared_candidates: i64 = 0
    pcc_gc_seed_roots()
    if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
        # This sweep owns a fixed disposal set. A finalizer can allocate an
        # object outside it, then leave that object temporarily unreachable.
        # It will survive this sweep, so its referents and class layout must
        # survive too. Ordinary RC used to hide this physical-lifetime edge.
        node = pcc_gc_object_walk_first()
        while ptr_is_null(node) == 0:
            visited = visited + 1
            nxt = pcc_gc_object_walk_next(node)
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (load_i32(obj, 12) & 1024) == 0:
                    pcc_gc_mark_root_gray_if_known(obj)
            node = nxt
    pcc_gc_drain_all_gray_unlocked()
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    node = pcc_gc_object_walk_first()
    while ptr_is_null(node) == 0:
        visited = visited + 1
        nxt = pcc_gc_object_walk_next(node)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            # A GC4 forwarding source is a physical shell, not another
            # finalizable object. Its payload and forwarding reference belong
            # to remap retirement. Test the edge, not the candidate flag: a
            # selected object that has not moved still needs ordinary sweeping.
            if (flags & 1024) != 0 and (
                pcc_gc_object_is_white(obj, flags) == 0
                or (
                    backend == 4
                    and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
                )
            ):
                store_i32(obj, 12, flags & ~1024)
                cleared_candidates = cleared_candidates + 1
        node = nxt
    _record_sweep_phase(4, started, pcc_platform_monotonic_us(), visited, cleared_candidates)


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


@c_abi_export("pcc_gc_concurrent_sweep_eligible")
def _concurrent_sweep_eligible(tag: i64) -> i64:
    """1 for a kind whose destruction frees only its own storage.

    Scalars, plain containers, functions, iterators and instances qualify:
    PASS1 cleared their slots, PASS0 ran their finalizers, and an instance
    reads only its class layout (classes retire last, in a stopped world).
    Classes, weakrefs, generators and coroutines, tasks and threads, files,
    exceptions, C-API handles and C-extension objects keep the stopped
    sweep: their teardown touches shared structures or foreign code.
    """
    if (
        tag == abi_constant("object.type.int")
        or tag == abi_constant("object.type.float")
        or tag == abi_constant("object.type.str")
        or tag == abi_constant("object.type.bytes")
        or tag == abi_constant("object.type.list")
        or tag == abi_constant("object.type.tuple")
        or tag == abi_constant("object.type.dict")
        or tag == abi_constant("object.type.set")
        or tag == abi_constant("object.type.func")
        or tag == abi_constant("object.type.iter")
        or tag == abi_constant("object.type.instance")
        or tag == abi_constant("object.type.valuebox")
    ):
        return 1
    if tag >= abi_constant("object.type.user_class_start"):
        if pcc_capi_is_cext_type_tag(tag) == 0:
            return 1
    return 0


@c_abi_export("pcc_gc_concurrent_sweep_admitted")
def _concurrent_sweep_admitted(count_free: i64) -> i64:
    """1 when this sweep may leave eligible candidates to the collector
    thread: a count-free nonmoving collector (GC1/GC2), an automatic
    collection (pcc_gc_concurrent_sweep_allowed) and a running thread."""
    if count_free == 0:
        return 0
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 3:
        return 0
    if load_i32(global_addr("pcc_gc_concurrent_sweep_allowed"), 0) == 0:
        return 0
    if atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") != 1:
        return 0
    return 1


@c_abi_export("pcc_gc_concurrent_sweep_slice")
def pcc_gc_concurrent_sweep_slice(budget: i64) -> i64:
    """Free up to budget deferred objects; returns the number freed.

    Runs on the collector thread, concurrently with mutators. The stopped
    sweep recorded each deferred object in an array: no walk over the live
    heap, and no unreachable object can gain a reference, a pin or a mark
    meanwhile (no mark begins before this sweep is done). Destruction runs
    under the graph lock, in batches: the object's own unlink, index and
    allocator transactions then nest in one acquisition instead of taking it
    again from mutators for each object, and nothing parks half-destroyed
    for a stopped collection to see. Between batches this thread may park
    and a stopped collection may finish the sweep
    (pcc_gc_concurrent_sweep_finish_stopped): each entry is claimed and
    loaded under the lock, and the loop stops once the sweep is over.
    """
    if atomic_load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, "acquire") == 0:
        return 0
    freed: i64 = 0
    pcc_py_gc_minor_graph_lock()
    while freed < budget and load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0) != 0:
        next_index: i64 = load_i64(global_addr("pcc_gc_concurrent_sweep_next"), 0)
        if next_index >= load_i64(global_addr("pcc_gc_concurrent_sweep_count"), 0):
            _concurrent_sweep_finish_locked()
        else:
            store_i64(global_addr("pcc_gc_concurrent_sweep_next"), 0, next_index + 1)
            objects = global_load_ptr("pcc_gc_concurrent_sweep_objects")
            pcc_gc_tracing_finalize_unpinned(load_ptr(objects, next_index * 8))
            freed = freed + 1
            if (freed & 31) == 0:
                # Let waiting mutators allocate between batches.
                pcc_py_gc_minor_graph_unlock()
                pcc_py_gc_minor_graph_lock()
    pcc_py_gc_minor_graph_unlock()
    atomic_rmw_i64("add", global_addr("pcc_gc_concurrent_sweep_freed"), 0, freed, "relaxed")
    return freed


@c_abi_export("pcc_gc_concurrent_sweep_finish_locked")
def _concurrent_sweep_finish_locked() -> None:
    """Every deferred object is freed: retire the array and, when the sweep
    kept classes (they retire after all their instances), request the
    stopped sweep that retires them. The caller holds the graph lock."""
    objects = global_load_ptr("pcc_gc_concurrent_sweep_objects")
    global_store_ptr("pcc_gc_concurrent_sweep_objects", null())
    store_i64(global_addr("pcc_gc_concurrent_sweep_count"), 0, 0)
    store_i64(global_addr("pcc_gc_concurrent_sweep_next"), 0, 0)
    atomic_store_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, 0, "release")
    if load_i64(global_addr("pcc_gc_concurrent_sweep_remaining"), 0) != 0:
        atomic_store_i32(global_addr("pcc_gc_cms_sweep_pending"), 0, 1, "release")
        atomic_store_i32(global_addr("pcc_gc_backend4_auto_step_pending"), 0, 1, "release")
    else:
        # The sweep left no other candidate: none is left now.
        store_i64(
            global_addr("pcc_gc_tracing_no_candidate_commit"),
            0,
            load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0),
        )
    free(objects)


@c_abi_export("pcc_gc_concurrent_sweep_finish_stopped")
def pcc_gc_concurrent_sweep_finish_stopped() -> i64:
    """Free every remaining deferred candidate now. A stopped collection
    calls this before its own mark or sweep reads candidate flags."""
    freed: i64 = 0
    while atomic_load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, "acquire") != 0:
        freed = freed + pcc_gc_concurrent_sweep_slice(4096)
    return freed


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
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    node = pcc_gc_object_walk_first()
    while ptr_is_null(node) == 0:
        visited = visited + 1
        nxt = pcc_gc_object_walk_next(node)
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
                    if count_free != 0 and tag == abi_constant("object.type.gen"):
                        # Per-target generator invalidation would deliver late
                        # weak callbacks before close/resurrection and before
                        # the collector's silent late clear below.
                        py_gen_finalize_tracing_candidate(obj)
                    else:
                        py_user_del_dispatch(obj)
        node = nxt
    _record_sweep_phase(1, started, pcc_platform_monotonic_us(), visited,
                        load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) - finalizer_runs)

    # Finalizers can resurrect candidates and create new weakrefs. Preserve
    # those of resurrected targets, then silently clear the remaining refs as
    # CPython 3.15 does before tp_clear. Late callbacks must not be delivered.
    if load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) != finalizer_runs:
        pcc_gc_tracing_recheck_reachability_after_finalizers()
        if count_free != 0:
            py_weakref_clear_tracing_candidates()

    # PASS 1: clear at most budget candidates without freeing any sibling.
    started = pcc_platform_monotonic_us()
    visited = 0
    cleared: i64 = 0
    cleared_nonclasses: i64 = 0
    node = pcc_gc_object_walk_first()
    while ptr_is_null(node) == 0 and cleared < budget:
        visited = visited + 1
        nxt = pcc_gc_object_walk_next(node)
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
                pcc_gc_tracing_clear_unpinned(obj)
                if count_free != 0 and is_class != 0:
                    pcc_gc_visit_object_slots(obj, _clear_class_layout_slot, null())
                else:
                    cleared_nonclasses = cleared_nonclasses + 1
                cleared = cleared + 1
        node = nxt
    # Only a sweep that cleared every candidate may leave candidates to the
    # collector thread: an uncleared one still owns its referents.
    pass1_complete: i64 = 1 if ptr_is_null(node) != 0 else 0
    _record_sweep_phase(2, started, pcc_platform_monotonic_us(), visited, cleared)

    # PASS 2: finalize the same number of still-candidate objects. An
    # automatic GC1/GC2 sweep leaves plain dead objects to the collector
    # thread (pcc_gc_concurrent_sweep_slice): after PASS0/recheck and PASS1
    # nothing can reach them and their destruction touches only themselves.
    started = pcc_platform_monotonic_us()
    visited = 0
    reclaimed: i64 = 0
    defer_concurrent: i64 = 0
    deferred_objects = null()
    if pass1_complete != 0 and cleared_nonclasses > 0:
        defer_concurrent = _concurrent_sweep_admitted(count_free)
        if defer_concurrent != 0:
            # PASS2 frees at most the objects PASS1 cleared; without the
            # array every object is destroyed here, as before.
            deferred_objects = malloc(cleared_nonclasses * 8)
            if ptr_is_null(deferred_objects) != 0:
                defer_concurrent = 0
    deferred: i64 = 0
    classes_kept: i64 = 0
    node = pcc_gc_object_walk_first()
    while ptr_is_null(node) == 0:
        visited = visited + 1
        nxt = pcc_gc_object_walk_next(node)
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
                else:
                    classes_kept = classes_kept + 1
            elif reclaimed < (cleared_nonclasses if count_free != 0 else cleared):
                if defer_concurrent != 0 and _concurrent_sweep_eligible(load_i32(obj, 8)) != 0:
                    store_ptr(deferred_objects, deferred * 8, obj)
                    deferred = deferred + 1
                else:
                    deferred_scalar: i64 = 0
                    selected: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
                    if selected == 1 or selected == 2 or selected == 3:
                        deferred_scalar = pcc_gc_scalar_sweep_try_enqueue(obj, node)
                    if deferred_scalar == 0:
                        pcc_gc_tracing_finalize_unpinned(obj)
                reclaimed = reclaimed + 1
        node = nxt
    if deferred != 0:
        # Publish after the walk. The kept classes stay candidates until the
        # collector thread has freed every deferred instance.
        pcc_py_gc_minor_graph_lock()
        global_store_ptr("pcc_gc_concurrent_sweep_objects", deferred_objects)
        store_i64(global_addr("pcc_gc_concurrent_sweep_count"), 0, deferred)
        store_i64(global_addr("pcc_gc_concurrent_sweep_next"), 0, 0)
        store_i64(global_addr("pcc_gc_concurrent_sweep_remaining"), 0, classes_kept)
        atomic_store_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, 1, "release")
        pcc_py_gc_minor_graph_unlock()
    elif ptr_is_null(deferred_objects) == 0:
        free(deferred_objects)

    if count_free != 0 and reclaimed < budget:
        # Keep layout metadata until EVERY pending ordinary object has been
        # destroyed, and every candidate class has detached its owning edges.
        # This also covers bounded slices and inheritance/metaclass cycles.
        node = pcc_gc_object_walk_first()
        while ptr_is_null(node) == 0:
            visited = visited + 1
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (load_i32(obj, 12) & 1024) != 0:
                    if load_i32(obj, 8) != abi_constant("object.type.class"):
                        _record_sweep_phase(3, started, pcc_platform_monotonic_us(), visited, reclaimed)
                        return reclaimed
                    if _class_needs_clear(obj) != 0:
                        _record_sweep_phase(3, started, pcc_platform_monotonic_us(), visited, reclaimed)
                        return reclaimed
            node = pcc_gc_object_walk_next(node)
        node = pcc_gc_object_walk_first()
        while ptr_is_null(node) == 0 and reclaimed < budget:
            visited = visited + 1
            nxt = pcc_gc_object_walk_next(node)
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
    _record_sweep_phase(3, started, pcc_platform_monotonic_us(), visited, reclaimed)
    return reclaimed
