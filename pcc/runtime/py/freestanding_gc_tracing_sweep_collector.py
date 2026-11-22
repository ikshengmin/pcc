"""Common PASS-0/PASS-1/PASS-2 sweep kernel for tracing GC backends."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.runtime.py.py_abi_constants import PY_FLAG_GC_TRACKED
from pcc.unsafe import (
    abi_constant,
    atomic_load_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    free,
    frame_address,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
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
pcc_py_gc_minor_graph_lock_contended = extern(
    "pcc_py_gc_minor_graph_lock_contended", (), c_int64
)
pcc_py_gc_minor_graph_lock_yield = extern(
    "pcc_py_gc_minor_graph_lock_yield", (), c_void
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_gray_registered_roots = extern("pcc_gc_gray_registered_roots", (), c_void)
pcc_gc_callback_singleton_conservative_root = extern(
    "pcc_gc_callback_singleton_conservative_root", (c_ptr, c_ptr), c_int64
)
pcc_gc_callback_root_cut_release = extern("pcc_gc_callback_root_cut_release", (c_ptr,), c_void)
pcc_gc_conservative_capture_thread_words = extern(
    "pcc_gc_conservative_capture_thread_words", (c_ptr,), c_void
)
pcc_gc_seed_roots = extern("pcc_gc_seed_roots", (), c_void)
pcc_gc_seed_roots_with_stop_cut = extern("pcc_gc_seed_roots_with_stop_cut", (c_ptr,), c_void)
pcc_gc_gray_count_load_acquire = extern("pcc_gc_gray_count_load_acquire", (), c_int64)
pcc_gc_gray_count_store_release = extern("pcc_gc_gray_count_store_release", (c_int64,), c_void)
pcc_gc_gray_worklist_clear_stopped = extern("pcc_gc_gray_worklist_clear_stopped", (), c_void)
pcc_gc_set_mark_color = extern("pcc_gc_set_mark_color", (c_ptr, c_int64), c_void)
pcc_gc_page_mark_assign = extern("pcc_gc_page_mark_assign", (c_ptr, c_int64), c_void)

pcc_gc_mark_root_gray_if_known = extern(
    "pcc_gc_mark_root_gray_if_known", (c_ptr,), c_void
)
pcc_gc_drain_all_gray_unlocked = extern(
    "pcc_gc_drain_all_gray_unlocked", (), c_int64
)
pcc_gc_tracing_clear_unpinned = extern(
    "pcc_gc_tracing_clear_unpinned", (c_ptr,), c_void
)
pcc_gc_tracing_clear_referents = extern(
    "pcc_gc_tracing_clear_referents", (c_ptr,), c_void
)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_visit_object_slots_slice = extern(
    "pcc_gc_visit_object_slots_slice", (c_ptr, c_int64, c_int64, c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_tracing_is_sweep_candidate = extern(
    "pcc_gc_tracing_is_sweep_candidate", (c_ptr,), c_int64
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
py_weakref_queue_tracing_candidates = extern(
    "py_weakref_queue_tracing_candidates", (c_ptr,), c_int64
)
py_weakref_run_queued_callbacks = extern(
    "py_weakref_run_queued_callbacks", (c_ptr, c_int64), c_void
)
py_user_del_pending = extern("py_user_del_pending", (c_ptr,), c_int64)
py_gen_finalize_pending = extern("py_gen_finalize_pending", (c_ptr,), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_thread_world_release_held = extern("pcc_thread_world_release_held", (), c_int64)
pcc_thread_world_reacquire_held = extern(
    "pcc_thread_world_reacquire_held", (c_int64,), c_void
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


# The nodes a mark's finish found white, collected by the finish's own walk
# (pcc_gc_tracing_candidates_note). The sweep of that mark visits them
# instead of walking every registered object again for each pass. Until the
# next finish the set only shrinks -- a recheck or a resurrection clears
# flags, and only a finish sets them -- so it stays a superset of the
# candidates, and every pass still checks each node's own state. Nodes stay
# valid: records are never freed, and only a sweep frees a pooled node. A
# finish committed meanwhile (a collection nested in a finalizer, which
# swept these candidates itself) retires the list. COMMIT names the finish
# that filled it; a count of -1 marks a list that could not grow, which the
# sweep does not use.
# The list's storage is freestanding_gc_state's pcc_gc_tracing_candidate_*.
# A list is mapped for the largest sweep; one this much larger than the
# candidates it held is returned after its sweep.
CANDIDATE_INITIAL_CAPACITY = 4096


@c_abi_export("pcc_gc_tracing_candidates_reset")
def pcc_gc_tracing_candidates_reset() -> None:
    """Start the candidate list of a finish (stopped world, graph lock)."""
    store_i64(global_addr("pcc_gc_tracing_candidate_count"), 0, 0)
    store_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0, -1)


@c_abi_export("pcc_gc_tracing_candidates_note")
def pcc_gc_tracing_candidates_note(node) -> None:
    count: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0)
    if count < 0:
        return
    capacity: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_capacity"), 0)
    nodes = global_load_ptr("pcc_gc_tracing_candidate_nodes")
    if count >= capacity:
        grown: i64 = capacity * 2
        if grown < CANDIDATE_INITIAL_CAPACITY:
            grown = CANDIDATE_INITIAL_CAPACITY
        bigger = malloc(grown * 8)
        if ptr_is_null(bigger) != 0:
            # An incomplete list is never used: its sweep walks.
            store_i64(global_addr("pcc_gc_tracing_candidate_count"), 0, -1)
            return
        index: i64 = 0
        while index < count:
            store_ptr(bigger, index * 8, load_ptr(nodes, index * 8))
            index = index + 1
        if ptr_is_null(nodes) == 0:
            free(nodes)
        nodes = bigger
        global_store_ptr("pcc_gc_tracing_candidate_nodes", nodes)
        store_i64(global_addr("pcc_gc_tracing_candidate_capacity"), 0, grown)
    store_ptr(nodes, count * 8, node)
    store_i64(global_addr("pcc_gc_tracing_candidate_count"), 0, count + 1)


@c_abi_export("pcc_gc_tracing_candidates_seal")
def pcc_gc_tracing_candidates_seal(commit: i64) -> None:
    """The list is complete for finish ``commit``."""
    if load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0) >= 0:
        store_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0, commit)


@c_abi_export("pcc_gc_tracing_candidates_retire")
def pcc_gc_tracing_candidates_retire() -> None:
    """The sweep consumed the list: no later sweep may use it, and a mapping
    far larger than its candidates is returned."""
    store_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0, -1)
    count: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0)
    capacity: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_capacity"), 0)
    store_i64(global_addr("pcc_gc_tracing_candidate_count"), 0, 0)
    if capacity > CANDIDATE_INITIAL_CAPACITY and count * 4 < capacity:
        free(global_load_ptr("pcc_gc_tracing_candidate_nodes"))
        global_store_ptr("pcc_gc_tracing_candidate_nodes", null())
        store_i64(global_addr("pcc_gc_tracing_candidate_capacity"), 0, 0)


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
    _recheck_reachability_after_window(null())


@c_abi_export("pcc_gc_tracing_recheck_reachability_with_stop_cut")
def _recheck_reachability_after_window(context) -> None:
    # Refresh the candidate verdict from current roots.  This is also used
    # before PASS-0; keep the exported name for the existing runtime ABI.
    # After PASS-0, PEP 442 requires the same refresh to rescue resurrection
    # before any referent is cleared.
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    cleared_candidates: i64 = 0
    if ptr_is_null(context) != 0:
        pcc_gc_seed_roots()
    else:
        pcc_gc_seed_roots_with_stop_cut(context)
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
    their slots are cleared before their teardown (by PASS1, or by the
    collector thread right before it), PASS0 ran their finalizers, and an instance
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


# A Lock's and an Event's parked virtual-thread FIFO head
# (py_threading: mutex@16, cond@24, waiters head@40, tail@48).
_THREAD_WAITERS_HEAD = 40


@c_abi_export("pcc_gc_concurrent_sweep_eligible_object")
def _concurrent_sweep_eligible_object(obj, tag: i64) -> i64:
    """_concurrent_sweep_eligible for one dead object, whose state can also
    show that its teardown frees only its own storage.

    A bytearray is freed like bytes (py_dealloc_generic). A Lock or Event
    with no parked virtual thread frees its own native mutex and condition;
    with a waiter its teardown would resume or drop that thread, so it stays
    in the stopped sweep. A generator whose close runs no user code
    (py_gen_finalize_pending) drops its frame, send value and inline slots,
    which a count-free collector does not count, and frees its own cell; one
    whose close is pending is PASS0's. Only the count-free collectors defer
    (_concurrent_sweep_admitted). The stopped world keeps the state fixed
    between PASS1 and PASS2.
    """
    if _concurrent_sweep_eligible(tag) != 0:
        return 1
    if tag == abi_constant("object.type.bytearray"):
        return 1
    if tag == abi_constant("object.type.gen"):
        if py_gen_finalize_pending(obj) == 0:
            return 1
        return 0
    if tag == abi_constant("object.type.thread_lock") or tag == abi_constant("object.type.thread_event"):
        if ptr_is_null(load_ptr(obj, _THREAD_WAITERS_HEAD)) != 0:
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
    for a stopped collection to see. A mutator that queues for the lock gets
    it after the destruction in progress (pcc_py_gc_minor_graph_lock_yield).
    A plain unlock and lock between batches did not let it in: the fast CAS
    took the word back before the queued head saw it free, and the GC3 HTTP
    gateway's carrier spent 13% of its time spinning for the lock through
    whole slices. Between batches this thread may park
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
            dead = load_ptr(objects, next_index * 8)
            # The stopped sweep may have left it uncleared (its PASS1);
            # clearing a cleared object finds nothing to clear.
            pcc_gc_tracing_clear_referents(dead)
            pcc_gc_tracing_finalize_unpinned(dead)
            freed = freed + 1
            if pcc_py_gc_minor_graph_lock_contended() != 0:
                # A queued mutator takes the lock now; this thread does not
                # barge back in before it.
                pcc_py_gc_minor_graph_lock_yield()
            elif (freed & 31) == 0:
                # Let a pending stop in between batches.
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


@c_abi_export("pcc_gc_tracing_user_code_window_open")
def _user_code_window_open(window) -> None:
    """Resume the world this sweep holds, for finalizers and weak callbacks.

    User code runs with the world running, as CPython's gc_collect_internal
    calls them after _PyEval_StartTheWorld: a finalizer that waits for a lock
    a parked mutator holds would otherwise wait forever. No other collection
    starts meanwhile -- gc.collect(), automatic collections and steps, and a
    new mark all stand aside while pcc_gc_explicit_collect_active is set --
    so no candidate is freed or re-coloured before the world stops again.
    ``window`` (16 bytes) records what _user_code_window_close restores.
    """
    store_i64(window, 8, load_i32(global_addr("pcc_gc_explicit_collect_active"), 0))
    store_i64(window, 0, 0)
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
        # GC4 runs its sweep's user code in the stopped world, as before the
        # windows existed: resuming its world mid-sweep ran some objects'
        # __del__ twice and others' never (measured on the four-thread field
        # program; GC1-GC3 stay exact). The cause is not established. A GC4
        # finalizer can therefore still wait for a lock a parked thread
        # holds; deferred finalization is the planned fix.
        return
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 1)
    store_i64(window, 0, pcc_thread_world_release_held())


@c_abi_export("pcc_gc_tracing_user_code_window_close")
def _user_code_window_close(window) -> None:
    """Stop the world again to the depth _user_code_window_open released."""
    pcc_thread_world_reacquire_held(load_i64(window, 0))
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, load_i64(window, 8))


@c_abi_export("pcc_gc_tracing_finalizer_targets_append")
def _finalizer_targets_append(targets, obj) -> None:
    """Append ``obj`` to PASS0's target array (data @0, count @8, capacity @16)."""
    count: i64 = load_i64(targets, 8)
    capacity: i64 = load_i64(targets, 16)
    data = load_ptr(targets, 0)
    if count >= capacity:
        grown: i64 = capacity * 2
        if grown < 64:
            grown = 64
        bigger = malloc(grown * 8)
        if ptr_is_null(bigger) != 0:
            pcc_platform_abort()
            return
        index: i64 = 0
        while index < count:
            store_ptr(bigger, index * 8, load_ptr(data, index * 8))
            index = index + 1
        if ptr_is_null(data) == 0:
            free(data)
        data = bigger
        store_ptr(targets, 0, data)
        store_i64(targets, 16, grown)
    store_ptr(data, count * 8, obj)
    store_i64(targets, 8, count + 1)


# Inert #9 reference primitive: the caller supplies a complete, nonmoving
# stopped cohort. This only classifies slots; it never runs callbacks.
_ACYCLIC_MAX_COHORT = 8
_ACYCLIC_WORD = 8
_ACYCLIC_EDGES = 0
# Offsets follow an 8x8 i64 multiplicity matrix and two 8-word arrays.
_ACYCLIC_INCOMING: i64 = 512
_ACYCLIC_READY: i64 = 576
_ACYCLIC_COHORT_PTR: i64 = 640
_ACYCLIC_COUNT: i64 = 648
_ACYCLIC_OWNER: i64 = 656
_ACYCLIC_UNSUPPORTED: i64 = 664
_ACYCLIC_FRONTIER: i64 = 672
_ACYCLIC_SCRATCH_BYTES: i64 = 736
_ACYCLIC_SCAN_NEXT: i64 = 0
_ACYCLIC_SCAN_EXAMINED: i64 = 8
_ACYCLIC_SCAN_BYTES: i64 = 16
_ACYCLIC_SCAN_LIMIT = 4
_ACYCLIC_ROLE_VALUE = 1
_ACYCLIC_ROLE_TYPE = 2
_ACYCLIC_ROLE_DERIVED = 3
_ACYCLIC_DROPPED = 1
_ACYCLIC_RETAINED_BY_CYCLE = 2
_ACYCLIC_HEAD = 3


@c_abi_export("pcc_gc_tracing_callback_cohort_edge")
def _callback_cohort_edge(slot, role: i64, scratch) -> None:
    if role == _ACYCLIC_ROLE_DERIVED:
        return
    if role != _ACYCLIC_ROLE_VALUE and role != _ACYCLIC_ROLE_TYPE:
        store_i64(scratch, _ACYCLIC_UNSUPPORTED, 1)
        return
    value = load_ptr(slot, 0)
    cohort = load_ptr(scratch, _ACYCLIC_COHORT_PTR)
    count: i64 = load_i64(scratch, _ACYCLIC_COUNT)
    owner: i64 = load_i64(scratch, _ACYCLIC_OWNER)
    index: i64 = 0
    while index < count:
        if ptr_eq(value, load_ptr(cohort, index * _ACYCLIC_WORD)) != 0:
            edge = _ACYCLIC_EDGES + (owner * _ACYCLIC_MAX_COHORT + index) * _ACYCLIC_WORD
            incoming = _ACYCLIC_INCOMING + index * _ACYCLIC_WORD
            store_i64(scratch, edge, load_i64(scratch, edge) + 1)
            store_i64(scratch, incoming, load_i64(scratch, incoming) + 1)
            return
        index += 1


@c_abi_export("pcc_gc_tracing_callback_cohort_valid")
def _callback_cohort_valid(cohort, count: i64, stop_epoch: i64) -> i64:
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (
        backend < 1 or backend > 3 or stop_epoch < 0
        or pcc_thread_world_stop_epoch_held() != stop_epoch
        or load_i32(global_addr("pcc_gc_forwarding_population"), 0) != 0
        or ptr_is_null(cohort) != 0 or count <= 0 or count > _ACYCLIC_MAX_COHORT
    ):
        return 0
    owner: i64 = 0
    while owner < count:
        obj = load_ptr(cohort, owner * _ACYCLIC_WORD)
        if pcc_gc_tracing_is_sweep_candidate(obj) == 0:
            return 0
        earlier: i64 = 0
        while earlier < owner:
            if ptr_eq(obj, load_ptr(cohort, earlier * _ACYCLIC_WORD)) != 0:
                return 0
            earlier += 1
        owner += 1
    return 1


@c_abi_export("pcc_gc_tracing_classify_callback_cohort")
def pcc_gc_tracing_classify_callback_cohort(
    cohort, count: i64, stop_epoch: i64, slots_left: i64, verdicts
) -> i64:
    """Classify a small supplied graph; refuse without touching verdicts."""
    if (
        slots_left < 0 or ptr_is_null(verdicts) != 0
        or _callback_cohort_valid(cohort, count, stop_epoch) == 0
    ):
        return 0
    scratch = stack_alloc(_ACYCLIC_SCRATCH_BYTES)
    memset(scratch, 0, _ACYCLIC_SCRATCH_BYTES)
    store_ptr(scratch, _ACYCLIC_COHORT_PTR, cohort)
    store_i64(scratch, _ACYCLIC_COUNT, count)
    state = stack_alloc(_ACYCLIC_SCAN_BYTES)
    owner: i64 = 0
    while owner < count:
        store_i64(scratch, _ACYCLIC_OWNER, owner)
        cursor: i64 = 0
        while cursor >= 0:
            if slots_left == 0:
                return 0
            limit: i64 = slots_left if slots_left < _ACYCLIC_SCAN_LIMIT else _ACYCLIC_SCAN_LIMIT
            handled: i64 = pcc_gc_visit_object_slots_slice(
                load_ptr(cohort, owner * _ACYCLIC_WORD), cursor, limit,
                _callback_cohort_edge, scratch, state,
            )
            examined: i64 = load_i64(state, _ACYCLIC_SCAN_EXAMINED)
            next_cursor: i64 = load_i64(state, _ACYCLIC_SCAN_NEXT)
            if (
                handled == 0 or load_i64(scratch, _ACYCLIC_UNSUPPORTED) != 0
                or examined < 0 or examined > limit
                or (next_cursor != -1 and next_cursor <= cursor)
            ):
                return 0
            slots_left -= examined
            cursor = next_cursor
        owner += 1
    queued: i64 = 0
    index: i64 = 0
    while index < count:
        if load_i64(scratch, _ACYCLIC_INCOMING + index * _ACYCLIC_WORD) == 0:
            store_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD, 1)
            store_i64(scratch, _ACYCLIC_READY + queued * _ACYCLIC_WORD, index)
            queued += 1
        index += 1
    done: i64 = 0
    while done < queued:
        owner = load_i64(scratch, _ACYCLIC_READY + done * _ACYCLIC_WORD)
        index = 0
        while index < count:
            edge = _ACYCLIC_EDGES + (owner * _ACYCLIC_MAX_COHORT + index) * _ACYCLIC_WORD
            removed: i64 = load_i64(scratch, edge)
            if removed != 0:
                inbound = _ACYCLIC_INCOMING + index * _ACYCLIC_WORD
                remaining: i64 = load_i64(scratch, inbound) - removed
                store_i64(scratch, inbound, remaining)
                if remaining == 0:
                    store_i64(scratch, _ACYCLIC_READY + queued * _ACYCLIC_WORD, index)
                    queued += 1
            index += 1
        done += 1
    if pcc_thread_world_stop_epoch_held() != stop_epoch:
        return 0
    index = 0
    while index < count:
        remaining = load_i64(scratch, _ACYCLIC_INCOMING + index * _ACYCLIC_WORD)
        verdict: i64 = _ACYCLIC_DROPPED
        if remaining != 0:
            verdict = _ACYCLIC_RETAINED_BY_CYCLE
        elif load_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD) != 0:
            verdict = _ACYCLIC_HEAD
        store_i64(verdicts, index * _ACYCLIC_WORD, verdict)
        index += 1
    return 1


_CALLBACK_REFRESH_PENDING: i64 = 1
_CALLBACK_REFRESH_SEEN: i64 = 2
_CALLBACK_REFRESH_SWEEP_FLAG = 1024


@c_abi_export("pcc_gc_tracing_callback_refresh_edge")
def _callback_refresh_edge(slot, role: i64, scratch) -> None:
    if role == _ACYCLIC_ROLE_DERIVED:
        return
    if role != _ACYCLIC_ROLE_VALUE and role != _ACYCLIC_ROLE_TYPE:
        store_i64(scratch, _ACYCLIC_UNSUPPORTED, 1)
        return
    value = load_ptr(slot, 0)
    cohort = load_ptr(scratch, _ACYCLIC_COHORT_PTR)
    count: i64 = load_i64(scratch, _ACYCLIC_COUNT)
    index: i64 = 0
    while index < count:
        if ptr_eq(value, load_ptr(cohort, index * _ACYCLIC_WORD)) != 0:
            offset: i64 = _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD
            if load_i64(scratch, offset) == 0:
                store_i64(scratch, offset, _CALLBACK_REFRESH_PENDING)
            return
        index += 1


@c_abi_export("pcc_gc_tracing_callback_refresh_owner")
def _callback_refresh_owner(owner, scratch, slots_left: i64) -> i64:
    cursor: i64 = 0
    state = stack_alloc(_ACYCLIC_SCAN_BYTES)
    while cursor >= 0:
        if slots_left <= 0:
            return -1
        limit: i64 = slots_left if slots_left < _ACYCLIC_SCAN_LIMIT else _ACYCLIC_SCAN_LIMIT
        handled: i64 = pcc_gc_visit_object_slots_slice(
            owner, cursor, limit, _callback_refresh_edge, scratch, state,
        )
        examined: i64 = load_i64(state, _ACYCLIC_SCAN_EXAMINED)
        next_cursor: i64 = load_i64(state, _ACYCLIC_SCAN_NEXT)
        if (
            handled == 0 or load_i64(scratch, _ACYCLIC_UNSUPPORTED) != 0
            or examined < 0 or examined > limit
            or (next_cursor != -1 and next_cursor <= cursor)
        ):
            return -1
        slots_left -= examined
        cursor = next_cursor
    return slots_left


@c_abi_export("pcc_gc_tracing_refresh_callback_cohort")
def pcc_gc_tracing_refresh_callback_cohort(
    cohort, count: i64, roots_and_owners, seeds: i64,
    stop_epoch: i64, slots_left: i64, coverage_complete: i64, result
) -> i64:
    """Report a bounded rescue bitmap; all raw inputs must come from this stop.

    The caller proves completeness of roots, writes and new owners before
    passing coverage_complete. This makes no changes to object flags.
    """
    if (
        coverage_complete != 1 or ptr_is_null(result) != 0
        or seeds < 0 or seeds > _ACYCLIC_MAX_COHORT or slots_left < 0
        or (seeds > 0 and ptr_is_null(roots_and_owners) != 0)
        or _callback_cohort_valid(cohort, count, stop_epoch) == 0
    ):
        return 0
    scratch = stack_alloc(_ACYCLIC_SCRATCH_BYTES)
    memset(scratch, 0, _ACYCLIC_SCRATCH_BYTES)
    store_ptr(scratch, _ACYCLIC_COHORT_PTR, cohort)
    store_i64(scratch, _ACYCLIC_COUNT, count)
    index: i64 = 0
    visited: i64 = 0
    cursor: i64 = 0
    while cursor < seeds:
        seed = load_ptr(roots_and_owners, cursor * _ACYCLIC_WORD)
        if ptr_is_null(seed) == 0:
            index = 0
            while index < count and ptr_eq(seed, load_ptr(cohort, index * _ACYCLIC_WORD)) == 0:
                index += 1
            if index < count:
                store_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD, _CALLBACK_REFRESH_PENDING)
            else:
                if (
                    is_tagged_int(seed) != 0 or pcc_gc_object_is_known_no_lock(seed) == 0
                    or (load_i32(seed, abi_constant("object.header.flags_offset"))
                        & _CALLBACK_REFRESH_SWEEP_FLAG) != 0
                ):
                    return 0
                slots_left = _callback_refresh_owner(seed, scratch, slots_left)
                if slots_left < 0:
                    return 0
                visited += 1
        cursor += 1
    while True:
        index = 0
        while index < count:
            if load_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD) == _CALLBACK_REFRESH_PENDING:
                break
            index += 1
        if index == count:
            break
        store_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD, _CALLBACK_REFRESH_SEEN)
        slots_left = _callback_refresh_owner(load_ptr(cohort, index * _ACYCLIC_WORD), scratch, slots_left)
        if slots_left < 0:
            return 0
        visited += 1
    if pcc_thread_world_stop_epoch_held() != stop_epoch:
        return 0
    bits: i64 = 0
    mask: i64 = 1
    index = 0
    while index < count:
        if load_i64(scratch, _ACYCLIC_FRONTIER + index * _ACYCLIC_WORD) == _CALLBACK_REFRESH_SEEN:
            bits = bits | mask
        mask = mask << 1
        index += 1
    store_i64(result, 0, bits)
    store_i64(result, _ACYCLIC_WORD, visited)
    return 1


@c_abi_export("pcc_gc_tracing_dispatch_callback_head")
def pcc_gc_tracing_dispatch_callback_head(target, verdict: i64, stop_epoch: i64) -> i64:
    """Inert callback arm: accept only a freshly classified, still-dead head.

    Internal caller must bind the verdict to this exact target and epoch,
    finish the cohort frame before calling, and drop its temporary target
    before refreshing roots. GC4 does not use this resumed-world arm.
    """
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (
        backend < 1 or backend > 3 or verdict != _ACYCLIC_HEAD
        or stop_epoch < 0 or pcc_thread_world_stop_epoch_held() != stop_epoch
        or pcc_gc_tracing_is_sweep_candidate(target) == 0
        or py_user_del_pending(target) == 0
    ):
        return 0
    window = stack_alloc(16)
    _user_code_window_open(window)
    if pcc_thread_world_stop_epoch_held() >= 0:
        _user_code_window_close(window)
        return 0
    # Dispatch owns and leases exactly the selected object, not the cohort.
    py_user_del_dispatch(target)
    _user_code_window_close(window)
    if pcc_thread_world_stop_epoch_held() <= stop_epoch:
        pcc_platform_abort()
    return 1


@c_abi_export("pcc_gc_tracing_dispatch_classified_head")
def pcc_gc_tracing_dispatch_classified_head(
    cohort, count: i64, index: i64, stop_epoch: i64, slots_left: i64
) -> i64:
    """Bind dispatch to a current complete cohort instead of a loose verdict.

    The stopped caller supplies unmanaged immutable cohort storage. Return
    before reseeding roots: the selected pointer must not be on the active
    dispatch stack during the full recheck. No production caller uses this arm.
    """
    if index < 0 or index >= count:
        return 0
    verdicts = stack_alloc(_ACYCLIC_MAX_COHORT * _ACYCLIC_WORD)
    if pcc_gc_tracing_classify_callback_cohort(cohort, count, stop_epoch, slots_left, verdicts) == 0:
        return 0
    verdict: i64 = load_i64(verdicts, index * _ACYCLIC_WORD)
    if verdict != _ACYCLIC_HEAD:
        return 0
    target = load_ptr(cohort, index * _ACYCLIC_WORD)
    return pcc_gc_tracing_dispatch_callback_head(target, verdict, stop_epoch)


# Review-only production admission. This is a true sweep route, not the
# injected-callback fixtures. A single acyclic object has one real ready layer.
# The layered path handles multi-object topology. GC4 and uncovered
# barriers stay on the old route; GC4 requires a separate review.
_CALLBACK_SWEEP_CANDIDATE = 1024
_CALLBACK_FRESH_ALLOCATION = 16384
_CALLBACK_MARK_WINDOW = 2
_CALLBACK_WHITE = 8
_CALLBACK_GRAY = 16
_CALLBACK_BLACK = 32
# Data pointer, count and capacity, as in the conservative root-cut owner.
_CALLBACK_ROOT_CUT_BYTES = 24


@c_abi_export("pcc_gc_review_acyclic_singleton")
def _review_acyclic_singleton(stop_epoch: i64, backend: i64):
    if (
        (backend < 1 or backend > 3)
        or stop_epoch < 0
        or stop_epoch != load_i64(global_addr("pcc_gc_tracing_finish_stop_epoch"), 0)
        or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
        or pcc_gc_gray_count_load_acquire() != 0
        or load_i32(global_addr("pcc_gc_forwarding_population"), 0) != 0
        or load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0) != 1
        or load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0) <= 0
        or load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)
           != load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    ):
        return null()
    node = load_ptr(global_load_ptr("pcc_gc_tracing_candidate_nodes"), 0)
    if pcc_gc_object_node_is_active(node) == 0:
        return null()
    obj = load_ptr(node, 0)
    if (
        (load_i32(obj, abi_constant("object.header.flags_offset")) & _CALLBACK_FRESH_ALLOCATION) != 0
        or pcc_gc_tracing_is_sweep_candidate(obj) == 0
        or load_i32(obj, abi_constant("object.header.type_tag_offset")) == abi_constant("object.type.gen")
        or py_user_del_pending(obj) == 0
    ):
        return null()
    cohort = stack_alloc(8)
    verdict = stack_alloc(8)
    store_ptr(cohort, 0, obj)
    if pcc_gc_tracing_classify_callback_cohort(cohort, 1, stop_epoch, 64, verdict) == 0:
        return null()
    return node if load_i64(verdict, 0) == _ACYCLIC_HEAD else null()


@c_abi_export("pcc_gc_review_remark_singleton_after_window")
def _review_remark_singleton_after_window(node, context) -> i64:
    """Only the singleton can be rescued. Never seed/scan the survivor heap.

    Treat an insertion shade without a current root as ambiguous: a callback
    can write to temporary storage, or it can publish into an old object.
    Until the shared coverage and drop cases are proven, use the existing
    full oracle for that unresolved case.
    """
    prior_gray: i64 = pcc_gc_gray_count_load_acquire()
    conservative: i64 = pcc_gc_callback_singleton_conservative_root(node, context)
    obj = load_ptr(node, 0)
    offset: i64 = abi_constant("object.header.flags_offset")
    explicit: i64 = pcc_gc_object_node_is_address_pinned(node, obj)
    flags: i64 = load_i32(obj, offset)
    if prior_gray > 1 or (prior_gray == 1 and (flags & _CALLBACK_GRAY) == 0):
        atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, 0, "release")
        return 0

    # A gray token from the insertion barrier does not say whether a
    # published edge still exists. Revisit the actual registered roots with
    # this one colour reset to distinguish a current owned cross-thread
    # root from a transient store. The world is stopped and there is no
    # other gray work, so this discards no peer's tracing credit.
    if prior_gray == 1:
        pcc_gc_gray_worklist_clear_stopped()
        pcc_gc_gray_count_store_release(0)
        pcc_gc_set_mark_color(obj, _CALLBACK_WHITE)
        pcc_gc_page_mark_assign(obj, 0)
    pcc_gc_gray_registered_roots()

    flags = load_i32(obj, offset)
    now_gray: i64 = pcc_gc_gray_count_load_acquire()
    declared: i64 = 1 if explicit != 0 or (flags & _CALLBACK_GRAY) != 0 else 0
    atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, 0, "release")
    if now_gray > 1 or (prior_gray != 0 and declared == 0):
        # An old-heap publication has not been ruled out. The stopped
        # fallback resets the queue and performs the full reachability scan.
        return 0
    if conservative != 0 or declared != 0:
        pcc_gc_set_mark_color(obj, _CALLBACK_BLACK)
        flags = load_i32(obj, offset)
        store_i32(obj, offset, flags & ~_CALLBACK_SWEEP_CANDIDATE)
    pcc_gc_gray_worklist_clear_stopped()
    pcc_gc_gray_count_store_release(0)
    return 1


@c_abi_export("pcc_gc_review_remark_singleton_at_stop")
def _review_remark_singleton_at_stop(node) -> None:
    # Take one root cut before the collector loads a raw candidate pointer.
    # A fail-closed full recheck must not take a second snapshot of frames
    # newly polluted by the failed fast attempt. The world has not resumed
    # between capture and either decision.
    context = stack_alloc(_CALLBACK_ROOT_CUT_BYTES)
    pcc_gc_conservative_capture_thread_words(context)
    if _review_remark_singleton_after_window(node, context) == 0:
        _recheck_reachability_after_window(context)
    pcc_gc_callback_root_cut_release(context)


@c_abi_export("pcc_gc_review_sweep_acyclic_singleton")
def _review_sweep_acyclic_singleton(node, budget: i64, backend: i64) -> i64:
    finalizers: i64 = load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0)
    listed = stack_alloc(8)
    nodes = stack_alloc(8)
    count = stack_alloc(8)
    commit = stack_alloc(8)
    # Real PASS0 routes the finalizer through the existing user-code window,
    # but its weakrefs have not been cleared yet.
    atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, _CALLBACK_MARK_WINDOW, "release")
    _sweep_finalizers(1, backend, finalizers, listed, nodes, count, commit)
    _review_remark_singleton_at_stop(node)
    atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, _CALLBACK_MARK_WINDOW, "release")
    weak_callbacks: i64 = _sweep_weak_callbacks(1)
    if weak_callbacks != 0:
        _review_remark_singleton_at_stop(node)
    else:
        atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, 0, "release")
    py_weakref_clear_tracing_candidates()
    return _sweep_clear_and_reclaim(
        budget, 1, 0, load_i64(listed, 0), load_ptr(nodes, 0),
        load_i64(count, 0), load_i64(commit, 0),
    )


pcc_gc_conservative_capture_callback_words = extern(
    "pcc_gc_conservative_capture_callback_words", (c_ptr, c_ptr, c_int64, c_int64), c_void
)
pcc_gc_callback_layers_build = extern(
    "pcc_gc_callback_layers_build", (c_ptr, c_int64, c_int64), c_ptr
)
pcc_gc_callback_layers_release = extern("pcc_gc_callback_layers_release", (c_ptr,), c_void)
pcc_gc_callback_layers_count = extern(
    "pcc_gc_callback_layers_count", (c_ptr,), c_int64
)
pcc_gc_callback_layers_range = extern(
    "pcc_gc_callback_layers_range", (c_ptr, c_int64), c_int64
)
pcc_gc_callback_layers_node = extern("pcc_gc_callback_layers_node", (c_ptr, c_int64), c_ptr)
pcc_gc_callback_layers_hull_low = extern("pcc_gc_callback_layers_hull_low", (c_ptr,), c_int64)
pcc_gc_callback_layers_hull_high = extern("pcc_gc_callback_layers_hull_high", (c_ptr,), c_int64)
pcc_gc_callback_cut_may_hit = extern(
    "pcc_gc_callback_cut_may_hit", (c_ptr, c_int64, c_int64), c_int64
)
py_weakref_layer_snapshot_create = extern(
    "py_weakref_layer_snapshot_create", (c_ptr, c_int64), c_ptr
)
py_weakref_layer_snapshot_release = extern("py_weakref_layer_snapshot_release", (c_ptr,), c_void)
py_weakref_queue_tracing_layer = extern(
    "py_weakref_queue_tracing_layer", (c_ptr, c_ptr, c_int64, c_ptr), c_int64
)
pcc_gc_gray_worklist_flush = extern("pcc_gc_gray_worklist_flush", (), c_void)
pcc_gc_gray_worklist_take_locked = extern("pcc_gc_gray_worklist_take_locked", (), c_ptr)
pcc_gc_gray_count_decrement_acq_rel = extern("pcc_gc_gray_count_decrement_acq_rel", (), c_void)
pcc_gc_trace_referents = extern("pcc_gc_trace_referents", (c_ptr,), c_void)

# Raw probe bits are indexed by the sealed candidate vector, not the
# runtime heap. The graph planner supplies the membership-size limit.
_REVIEW_SHADE_WORD_BITS = 64
_REVIEW_SHADE_WORD_BYTES = 8


@c_abi_export("pcc_gc_review_layer_finalizer")
def _review_layer_finalizer(node, window) -> i64:
    obj = load_ptr(node, 0)
    if pcc_gc_tracing_is_sweep_candidate(obj) == 0 or py_user_del_pending(obj) == 0:
        return 0
    atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, _CALLBACK_MARK_WINDOW, "release")
    _user_code_window_open(window)
    if load_i32(obj, abi_constant("object.header.type_tag_offset")) == abi_constant("object.type.gen"):
        py_gen_finalize_tracing_candidate(obj)
    else:
        py_user_del_dispatch(obj)
    _user_code_window_close(window)
    return 1


@c_abi_export("pcc_gc_review_collect_layer_roots")
def _review_collect_layer_roots(plan, nodes, count: i64, cut, suspected, shade_bytes: i64) -> i64:
    # Peel off the write-barrier evidence before looking at actual roots.
    # It can contain a transient store or publication into an old owner.
    # Keep an unmanaged bit per prior shade without a mutation journal.
    # Word indices and shift counts are independent across the sealed batch.
    prior: i64 = pcc_gc_gray_count_load_acquire()
    seen: i64 = 0
    pos: i64 = 0
    offset: i64 = abi_constant("object.header.flags_offset")
    if prior > count:
        return 0
    if prior != 0:
        memset(suspected, 0, shade_bytes)
        while pos < count:
            obj = load_ptr(load_ptr(nodes, pos * 8), 0)
            flags: i64 = load_i32(obj, offset)
            if (flags & _CALLBACK_GRAY) != 0:
                if (flags & _CALLBACK_SWEEP_CANDIDATE) == 0:
                    return 0
                word: i64 = (pos // _REVIEW_SHADE_WORD_BITS) * _REVIEW_SHADE_WORD_BYTES
                bit: i64 = 1 << (pos % _REVIEW_SHADE_WORD_BITS)
                store_i64(suspected, word, load_i64(suspected, word) | bit)
                seen += 1
            pos += 1
        if seen != prior:
            return 0
        pos = 0
        while pos < count:
            word = (pos // _REVIEW_SHADE_WORD_BITS) * _REVIEW_SHADE_WORD_BYTES
            bit = 1 << (pos % _REVIEW_SHADE_WORD_BITS)
            if (load_i64(suspected, word) & bit) != 0:
                obj = load_ptr(load_ptr(nodes, pos * 8), 0)
                pcc_gc_set_mark_color(obj, _CALLBACK_WHITE)
                pcc_gc_page_mark_assign(obj, 0)
            pos += 1
        pcc_gc_gray_worklist_clear_stopped()
        pcc_gc_gray_count_store_release(0)
    raw_possible: i64 = pcc_gc_callback_cut_may_hit(
        cut, pcc_gc_callback_layers_hull_low(plan), pcc_gc_callback_layers_hull_high(plan),
    )
    pos = 0
    while pos < count:
        node = load_ptr(nodes, pos * 8)
        obj = load_ptr(node, 0)
        # The ordinary candidate predicate excludes pins. Here a newly
        # pinned candidate is itself a root, and its children must survive.
        if (load_i32(obj, offset) & _CALLBACK_SWEEP_CANDIDATE) != 0:
            if (pcc_gc_object_node_is_address_pinned(node, obj) != 0
                    or (raw_possible != 0
                        and pcc_gc_callback_singleton_conservative_root(node, cut) != 0)):
                pcc_gc_mark_root_gray_if_known(obj)
        pos += 1
    pcc_gc_gray_registered_roots()
    while pcc_gc_gray_count_load_acquire() != 0:
        pcc_gc_gray_worklist_flush()
        obj = pcc_gc_gray_worklist_take_locked()
        if ptr_is_null(obj) != 0:
            return 0
        if (load_i32(obj, abi_constant("object.header.flags_offset")) & _CALLBACK_GRAY) != 0:
            pcc_gc_trace_referents(obj)
            pcc_gc_set_mark_color(obj, _CALLBACK_BLACK)
            # This closure came from actual current roots, not write-barrier
            # evidence. Retire the candidate bit as we drain the queue.
            flags: i64 = load_i32(obj, offset)
            store_i32(obj, offset, flags & ~_CALLBACK_SWEEP_CANDIDATE)
            pcc_gc_gray_count_decrement_acq_rel()
    if prior != 0:
        pos = 0
        while pos < count:
            word = (pos // _REVIEW_SHADE_WORD_BITS) * _REVIEW_SHADE_WORD_BYTES
            bit = 1 << (pos % _REVIEW_SHADE_WORD_BITS)
            if (load_i64(suspected, word) & bit) != 0:
                obj = load_ptr(load_ptr(nodes, pos * 8), 0)
                if (load_i32(obj, offset) & _CALLBACK_SWEEP_CANDIDATE) != 0:
                    # Some observed store is not explained by current
                    # candidate/root reachability: do not lose a live edge
                    # from an old owner, or keep a cleared edge as a root.
                    return 0
            pos += 1
    pcc_gc_gray_worklist_clear_stopped()
    return 1


@c_abi_export("pcc_gc_review_remark_layer_at_stop")
def _review_remark_layer_at_stop(plan, nodes, count: i64, caller,
                                 suspected, shade_bytes: i64) -> None:
    cut = stack_alloc(_CALLBACK_ROOT_CUT_BYTES)
    low: i64 = pcc_gc_callback_layers_hull_low(plan)
    high: i64 = pcc_gc_callback_layers_hull_high(plan)
    pcc_gc_conservative_capture_callback_words(cut, caller, low, high)
    precise: i64 = _review_collect_layer_roots(plan, nodes, count, cut, suspected, shade_bytes)
    atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, 0, "release")
    if precise == 0:
        if low > 0 and high > low:
            # A valid old owner can lie outside the candidate hull. The
            # full oracle must see the unfiltered words from this same stop.
            pcc_gc_callback_root_cut_release(cut)
            pcc_gc_conservative_capture_callback_words(cut, caller, 0, 0)
        _recheck_reachability_after_window(cut)
    pcc_gc_callback_root_cut_release(cut)


@c_abi_export("pcc_gc_review_layer_plan")
def _review_layer_plan(stop_epoch: i64, backend: i64, nodes, count: i64, validated_epoch: i64):
    if (
        (backend < 1 or backend > 3) or count <= 0
        or ptr_is_null(nodes) != 0 or stop_epoch < 0
        or stop_epoch != validated_epoch
        or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
        or pcc_gc_gray_count_load_acquire() != 0
        or load_i32(global_addr("pcc_gc_forwarding_population"), 0) != 0
        or load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0) <= 0
        or load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)
           != load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    ):
        return null()
    if (ptr_is_null(global_load_ptr("py_weakref_head")) != 0
            and ptr_is_null(global_load_ptr("py_weakref_late_head")) != 0):
        # Without weakrefs a graph cannot affect callback order unless an
        # actual candidate can finalize. In the common no-__del__ process
        # only a suspended generator needs the pending-finalizer query.
        class_finalizers: i64 = load_i32(global_addr("pcc_class_del_defined_count"), 0)
        pos: i64 = 0
        while pos < count:
            node = load_ptr(nodes, pos * 8)
            if pcc_gc_object_node_is_active(node) != 0:
                obj = load_ptr(node, 0)
                if (class_finalizers != 0
                        or load_i32(obj, abi_constant("object.header.type_tag_offset")) == abi_constant("object.type.gen")):
                    if pcc_gc_tracing_is_sweep_candidate(obj) != 0 and py_user_del_pending(obj) != 0:
                        return pcc_gc_callback_layers_build(nodes, count, stop_epoch)
            pos += 1
        return null()
    return pcc_gc_callback_layers_build(nodes, count, stop_epoch)


@c_abi_export("pcc_gc_review_full_recheck_from_caller")
def _review_full_recheck_from_caller(caller) -> None:
    cut = stack_alloc(_CALLBACK_ROOT_CUT_BYTES)
    pcc_gc_conservative_capture_callback_words(cut, caller, 0, 0)
    _recheck_reachability_after_window(cut)
    pcc_gc_callback_root_cut_release(cut)


@c_abi_export("pcc_gc_review_finish_cyclic_residue")
def _review_finish_cyclic_residue(caller) -> None:
    # A peeled candidate has either been rescued (no candidate bit), or its
    # finalizer and existing weakrefs have already been settled. What remains
    # follows the cyclic convention: early weakrefs, finalizers, late clear.
    if _sweep_weak_callbacks(1) != 0:
        _review_full_recheck_from_caller(caller)
    finalizers: i64 = load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0)
    listed = stack_alloc(8)
    nodes = stack_alloc(8)
    count = stack_alloc(8)
    commit = stack_alloc(8)
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    _sweep_finalizers(1, backend, finalizers, listed, nodes, count, commit)
    if load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) != finalizers:
        _review_full_recheck_from_caller(caller)
    py_weakref_clear_tracing_candidates()


@c_abi_export("pcc_gc_review_sweep_layers")
def _review_sweep_layers(plan, layers: i64, nodes, count: i64, budget: i64, caller,
                         suspected, shade_bytes: i64) -> i64:
    layer: i64 = 0
    window = stack_alloc(16)
    pending = stack_alloc(16)
    weak_snapshot = py_weakref_layer_snapshot_create(plan, layers)
    while layer < layers:
        pos: i64 = pcc_gc_callback_layers_range(plan, layer)
        end: i64 = pcc_gc_callback_layers_range(plan, layer + 1)
        while pos < end:
            node = pcc_gc_callback_layers_node(plan, pos)
            # A sibling may revive another sibling even through raw C roots
            # that do not shade. Settle that window before dispatching again.
            if _review_layer_finalizer(node, window) != 0:
                _review_remark_layer_at_stop(plan, nodes, count, caller, suspected, shade_bytes)
            pos += 1
        store_ptr(pending, 0, null())
        store_ptr(pending, 8, null())
        weak: i64 = py_weakref_queue_tracing_layer(weak_snapshot, plan, layer, pending)
        if weak < 0:
            pcc_platform_abort()
        if weak > 0:
            atomic_store_i32(global_addr("pcc_gc_mark_active"), 0, _CALLBACK_MARK_WINDOW, "release")
            _user_code_window_open(window)
            py_weakref_run_queued_callbacks(pending, 0)
            _user_code_window_close(window)
            _review_remark_layer_at_stop(plan, nodes, count, caller, suspected, shade_bytes)
        layer += 1
    py_weakref_layer_snapshot_release(weak_snapshot)
    peeled: i64 = pcc_gc_callback_layers_range(plan, layers)
    pcc_gc_callback_layers_release(plan)
    if peeled != count:
        _review_finish_cyclic_residue(caller)
    commit: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)
    return _sweep_clear_and_reclaim(budget, 1, 0, 1, nodes, count, commit)


@c_abi_export("pcc_gc_review_compact_refreshed_candidates")
def _review_compact_refreshed_candidates(held: i64) -> i64:
    # The caller has just run the full stopped refresh, which can only
    # remove candidate flags. A sealed list remains complete for the
    # survivors of that filtering. Compact record pointers, not roots.
    commit: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)
    count: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0)
    nodes = global_load_ptr("pcc_gc_tracing_candidate_nodes")
    if (held < 0 or ptr_is_null(nodes) != 0 or count <= 0 or commit <= 0
            or commit != load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
            or load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) == 0
            or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
            or pcc_gc_gray_count_load_acquire() != 0
            or load_i32(global_addr("pcc_gc_forwarding_population"), 0) != 0):
        return -1
    kept: i64 = 0
    index: i64 = 0
    while index < count:
        node = load_ptr(nodes, index * _ACYCLIC_WORD)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, abi_constant("object.header.flags_offset"))
            if (flags & _CALLBACK_SWEEP_CANDIDATE) != 0:
                if kept != index:
                    store_ptr(nodes, kept * _ACYCLIC_WORD, node)
                kept += 1
        index += 1
    store_i64(global_addr("pcc_gc_tracing_candidate_count"), 0, kept)
    return held


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
    validated_epoch: i64 = load_i64(global_addr("pcc_gc_tracing_finish_stop_epoch"), 0)
    backend_for_review: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    review: i64 = 1 if backend_for_review >= 1 and backend_for_review <= 3 else 0
    previous_stop: i64 = 1 if held < 0 or held != validated_epoch else 0
    if previous_stop != 0:
        pcc_gc_tracing_recheck_reachability_after_finalizers()
        if review != 0:
            validated_epoch = _review_compact_refreshed_candidates(held)
    if review != 0:
        if previous_stop == 0:
            review_node = _review_acyclic_singleton(held, backend_for_review)
            if ptr_is_null(review_node) == 0:
                return _review_sweep_acyclic_singleton(review_node, budget, backend_for_review)
        review_nodes = global_load_ptr("pcc_gc_tracing_candidate_nodes")
        review_count: i64 = load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0)
        # An earlier stop can shrink to a single node. Keep it on the
        # layered route and its caller-relative root cut; the narrower
        # singleton shortcut assumes a same-stop finished mark.
        plan = null()
        if review_count > 1 or previous_stop != 0:
            plan = _review_layer_plan(held, backend_for_review, review_nodes, review_count, validated_epoch)
        if ptr_is_null(plan) == 0:
            layers: i64 = pcc_gc_callback_layers_count(plan)
            if layers > 0:
                # A successful plan bounded review_count before this
                # arithmetic. Reserve scratch once, before running code.
                # OOM leaves the original sweep available before effects.
                shade_bytes: i64 = ((review_count + _REVIEW_SHADE_WORD_BITS - 1)
                                    // _REVIEW_SHADE_WORD_BITS) * _REVIEW_SHADE_WORD_BYTES
                suspected = malloc(shade_bytes)
                if ptr_is_null(suspected) == 0:
                    reclaimed: i64 = _review_sweep_layers(
                        plan, layers, review_nodes, review_count, budget,
                        ptr_add(frame_address(), 16), suspected, shade_bytes,
                    )
                    free(suspected)
                    return reclaimed
            pcc_gc_callback_layers_release(plan)
    # Keep phase-local object aliases out of the coordinator's live frame.
    # This is not a root filter: every phase still uses the full root scanner.
    weak_callbacks: i64 = _sweep_weak_callbacks(count_free)
    if weak_callbacks != 0:
        pcc_gc_tracing_recheck_reachability_after_finalizers()
    finalizer_runs: i64 = load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0)
    selected_backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    # Named output cells preserve the original pre-finalizer list snapshot.
    # They contain only metadata and an unmanaged candidate-array address.
    listed_slot = stack_alloc(8)
    listed_nodes_slot = stack_alloc(8)
    listed_count_slot = stack_alloc(8)
    listed_commit_slot = stack_alloc(8)
    _sweep_finalizers(count_free, selected_backend, finalizer_runs,
                      listed_slot, listed_nodes_slot, listed_count_slot,
                      listed_commit_slot)
    # Finalizers can resurrect candidates and create new weakrefs. Preserve
    # those of resurrected targets, then silently clear the remaining refs as
    # CPython 3.15 does before tp_clear. Late callbacks must not be delivered.
    # Refs without callbacks kept their targets through finalizers on the
    # early pass's late list; the late clear visits only that list.
    if load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) != finalizer_runs:
        pcc_gc_tracing_recheck_reachability_after_finalizers()
    if count_free != 0:
        py_weakref_clear_tracing_candidates()

    # No user code ran since the weak refs to candidates were settled (no
    # weak callback, finalizer or generator close): none can have gained a
    # weak ref, so clearing skips each one's walk of the weak-ref lists.
    weak_settled: i64 = 0
    if (
        count_free != 0
        and weak_callbacks == 0
        and (selected_backend == 1 or selected_backend == 2 or selected_backend == 3)
        and load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) == finalizer_runs
    ):
        weak_settled = 1

    return _sweep_clear_and_reclaim(
        budget, count_free, weak_settled, load_i64(listed_slot, 0),
        load_ptr(listed_nodes_slot, 0), load_i64(listed_count_slot, 0),
        load_i64(listed_commit_slot, 0),
    )


@c_abi_export("pcc_gc_tracing_sweep_weak_callbacks")
def _sweep_weak_callbacks(count_free: i64) -> i64:
    window = stack_alloc(16)
    weak_callbacks: i64 = 0
    if count_free != 0:
        pending_cell = stack_alloc(16)
        store_ptr(pending_cell, 0, null())
        store_ptr(pending_cell, 8, null())
        weak_callbacks = py_weakref_queue_tracing_candidates(pending_cell)
        if weak_callbacks > 0:
            # The callbacks are user code: they run with the world running.
            _user_code_window_open(window)
            py_weakref_run_queued_callbacks(pending_cell, 0)
            _user_code_window_close(window)
    return weak_callbacks


@c_abi_export("pcc_gc_tracing_sweep_finalizers")
def _sweep_finalizers(count_free: i64, selected_backend: i64, finalizer_runs: i64,
                      listed_slot, listed_nodes_slot, listed_count_slot,
                      listed_commit_slot) -> None:
    window = stack_alloc(16)
    # The nonmoving count-free collectors visit their finish's candidate
    # list (pcc_gc_tracing_candidates_note) instead of every object.
    listed: i64 = 0
    if (
        count_free != 0
        and (selected_backend == 1 or selected_backend == 2 or selected_backend == 3)
        and load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0) >= 0
        and load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0) > 0
        and load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)
        == load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    ):
        # A finish commits from 1; no finish has filled a list at commit 0.
        listed = 1
    listed_nodes = null()
    listed_count: i64 = 0
    listed_commit: i64 = 0
    if listed != 0:
        listed_nodes = global_load_ptr("pcc_gc_tracing_candidate_nodes")
        listed_count = load_i64(global_addr("pcc_gc_tracing_candidate_count"), 0)
        listed_commit = load_i64(global_addr("pcc_gc_tracing_candidate_commit"), 0)

    store_i64(listed_slot, 0, listed)
    store_ptr(listed_nodes_slot, 0, listed_nodes)
    store_i64(listed_count_slot, 0, listed_count)
    store_i64(listed_commit_slot, 0, listed_commit)

    # PASS 0: finalizers see intact fields.  C-extension tags are excluded
    # because their lifecycle is owned by the C-API bridge. The stopped
    # world selects the candidates whose finalizer runs user code (the
    # object and candidate lists change once mutators run); those finalizers
    # then run with the world running, and the world stops again for the
    # resurrection recheck below.
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    targets = stack_alloc(24)
    store_ptr(targets, 0, null())
    store_i64(targets, 8, 0)
    store_i64(targets, 16, 0)
    # A listed pass visits the list's entries; a finish committed since the
    # list was sealed ends it (that finish's own sweep owns its candidates).
    cursor: i64 = 0
    node = pcc_gc_object_walk_first() if listed == 0 else (
        load_ptr(listed_nodes, 0) if listed_count > 0 else null())
    while ptr_is_null(node) == 0 and (
        listed == 0
        or load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == listed_commit
    ):
        visited = visited + 1
        cursor = cursor + 1
        if listed == 0:
            nxt = pcc_gc_object_walk_next(node)
        elif cursor < listed_count:
            nxt = load_ptr(listed_nodes, cursor * 8)
        else:
            nxt = null()
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
                if py_user_del_pending(obj) != 0:
                    _finalizer_targets_append(targets, obj)
        node = nxt
    target_count: i64 = load_i64(targets, 8)
    if target_count > 0:
        _user_code_window_open(window)
        target_data = load_ptr(targets, 0)
        index: i64 = 0
        while index < target_count:
            target = load_ptr(target_data, index * 8)
            if count_free != 0 and load_i32(target, 8) == abi_constant("object.type.gen"):
                # Per-target generator invalidation would deliver late
                # weak callbacks before close/resurrection and before
                # the collector's silent late clear below.
                py_gen_finalize_tracing_candidate(target)
            else:
                py_user_del_dispatch(target)
            index = index + 1
        _user_code_window_close(window)
    if ptr_is_null(load_ptr(targets, 0)) == 0:
        free(load_ptr(targets, 0))
    _record_sweep_phase(1, started, pcc_platform_monotonic_us(), visited,
                        load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) - finalizer_runs)



@c_abi_export("pcc_gc_tracing_sweep_clear_and_reclaim")
def _sweep_clear_and_reclaim(budget: i64, count_free: i64, weak_settled: i64,
                             listed: i64, listed_nodes, listed_count: i64,
                             listed_commit: i64) -> i64:
    # PASS 1: clear at most budget candidates without freeing any sibling.
    # A sweep that ran no user code since its weak refs settled, has no
    # budget and may defer, leaves its eligible candidates uncleared: the
    # collector thread clears each right before destroying it
    # (pcc_gc_concurrent_sweep_slice). Clearing reads and writes only the
    # object itself, and no destruction reads another object, so the order
    # is free; this moves the clearing out of the stopped world.
    defer_uncleared: i64 = 0
    if (
        weak_settled != 0
        and budget == 9223372036854775807
        and _concurrent_sweep_admitted(count_free) != 0
    ):
        defer_uncleared = 1
    # With the finish's candidate list, PASS1 records those candidates in
    # the deferral array itself and keeps only the others in the list, the
    # only nodes PASS2 still has to visit (it used to walk every candidate
    # again to record them). The list is this sweep's: PASS1 compacts it in
    # place, writing behind its read cursor.
    early = null()
    early_count: i64 = 0
    kept: i64 = 0
    if defer_uncleared != 0 and listed != 0 and listed_count > 0:
        early = malloc(listed_count * 8)
    started: i64 = pcc_platform_monotonic_us()
    visited: i64 = 0
    cleared: i64 = 0
    cleared_nonclasses: i64 = 0
    # The phase's work: the candidates this stopped world cleared itself.
    cleared_here: i64 = 0
    cursor: i64 = 0
    node = pcc_gc_object_walk_first() if listed == 0 else (
        load_ptr(listed_nodes, 0) if listed_count > 0 else null())
    while ptr_is_null(node) == 0 and cleared < budget and (
        listed == 0
        or load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == listed_commit
    ):
        visited = visited + 1
        cursor = cursor + 1
        if listed == 0:
            nxt = pcc_gc_object_walk_next(node)
        elif cursor < listed_count:
            nxt = load_ptr(listed_nodes, cursor * 8)
        else:
            nxt = null()
        if pcc_gc_object_node_is_active(node) == 0:
            node = nxt
            continue
        obj = load_ptr(node, 0)
        flags: i64 = load_i32(obj, 12)
        recorded: i64 = 0
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
            if (
                defer_uncleared != 0
                and is_class == 0
                and _concurrent_sweep_eligible_object(obj, load_i32(obj, 8)) != 0
            ):
                # Settled for PASS2 (counted), cleared by the collector thread.
                cleared_nonclasses = cleared_nonclasses + 1
                cleared = cleared + 1
                if ptr_is_null(early) == 0:
                    # This slot commits the death after the last reachability
                    # verdict. Public snapshots must not acquire the object
                    # while it waits for the worker. Keep the tracing record
                    # active: class retirement still needs to see instances.
                    if (flags & PY_FLAG_GC_TRACKED) != 0:
                        py_gc_untrack(obj)
                    store_ptr(early, early_count * 8, obj)
                    early_count = early_count + 1
                    recorded = 1
            elif count_free == 0 or is_class == 0 or _class_needs_clear(obj) != 0:
                cleared_here = cleared_here + 1
                if weak_settled != 0:
                    pcc_gc_tracing_clear_referents(obj)
                else:
                    pcc_gc_tracing_clear_unpinned(obj)
                if count_free != 0 and is_class != 0:
                    pcc_gc_visit_object_slots(obj, _clear_class_layout_slot, null())
                else:
                    cleared_nonclasses = cleared_nonclasses + 1
                cleared = cleared + 1
        if ptr_is_null(early) == 0 and recorded == 0 and (flags & 1024) != 0:
            store_ptr(listed_nodes, kept * 8, node)
            kept = kept + 1
        node = nxt
    if ptr_is_null(early) == 0:
        listed_count = kept
    # Only a sweep that cleared every candidate may leave candidates to the
    # collector thread: an uncleared one still owns its referents.
    pass1_complete: i64 = 1 if ptr_is_null(node) != 0 else 0
    _record_sweep_phase(2, started, pcc_platform_monotonic_us(), visited, cleared_here)

    # PASS 2: finalize the same number of still-candidate objects. An
    # automatic GC1/GC2 sweep leaves plain dead objects to the collector
    # thread (pcc_gc_concurrent_sweep_slice): after PASS0/recheck and PASS1
    # nothing can reach them and their destruction touches only themselves.
    started = pcc_platform_monotonic_us()
    visited = 0
    reclaimed: i64 = 0
    defer_concurrent: i64 = 0
    deferred_objects = null()
    deferred: i64 = 0
    if ptr_is_null(early) == 0:
        # PASS1 recorded its deferrals; PASS2 visits the rest of the list.
        defer_concurrent = 1
        deferred_objects = early
        deferred = early_count
        reclaimed = early_count
    elif pass1_complete != 0 and cleared_nonclasses > 0:
        defer_concurrent = _concurrent_sweep_admitted(count_free)
        if defer_concurrent != 0:
            # PASS2 frees at most the objects PASS1 cleared; without the
            # array every object is destroyed here, as before.
            deferred_objects = malloc(cleared_nonclasses * 8)
            if ptr_is_null(deferred_objects) != 0:
                defer_concurrent = 0
    classes_kept: i64 = 0
    cursor = 0
    node = pcc_gc_object_walk_first() if listed == 0 else (
        load_ptr(listed_nodes, 0) if listed_count > 0 else null())
    while ptr_is_null(node) == 0 and (
        listed == 0
        or load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == listed_commit
    ):
        visited = visited + 1
        cursor = cursor + 1
        if listed == 0:
            nxt = pcc_gc_object_walk_next(node)
        elif cursor < listed_count:
            nxt = load_ptr(listed_nodes, cursor * 8)
        else:
            nxt = null()
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
                eligible: i64 = _concurrent_sweep_eligible_object(obj, load_i32(obj, 8))
                if defer_concurrent != 0 and eligible != 0:
                    # Same irrevocable handoff as the listed PASS1 path.
                    if (flags & PY_FLAG_GC_TRACKED) != 0:
                        py_gc_untrack(obj)
                    store_ptr(deferred_objects, deferred * 8, obj)
                    deferred = deferred + 1
                else:
                    if defer_uncleared != 0 and eligible != 0:
                        # PASS1 left it to the collector thread, which could
                        # not take it (no array): clear it before its teardown.
                        pcc_gc_tracing_clear_referents(obj)
                    deferred_scalar: i64 = 0
                    selected: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
                    if selected == 1 or selected == 2 or selected == 3:
                        deferred_scalar = pcc_gc_scalar_sweep_try_enqueue(obj, node)
                    if deferred_scalar == 0:
                        pcc_gc_tracing_finalize_unpinned(obj)
                reclaimed = reclaimed + 1
        node = nxt
    if listed != 0:
        pcc_gc_tracing_candidates_retire()
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
