"""Shared write-barrier and five-backend step dispatch policy."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32,
    atomic_rmw_i32,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    null,
    ptr_diff,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

pcc_gc_set_mark_color = extern("pcc_gc_set_mark_color", (c_ptr, c_int64), c_void)
pcc_gc_shade_gray_atomic = extern(
    "pcc_gc_shade_gray_atomic", (c_ptr, c_int64), c_void
)
pcc_container_lock_held = extern("pcc_container_lock_held", (), c_int64)
pcc_gc_object_index_seen_stable = extern("pcc_gc_object_index_seen_stable", (c_ptr,), c_int64)


pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_thread_live_count = extern("pcc_thread_live_count", (), c_int64)
pcc_thread_all_stopped_at_gates = extern("pcc_thread_all_stopped_at_gates", (), c_int64)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_platform_monotonic_us = extern(
    "pcc_platform_monotonic_us", (), c_int64
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_precise_stack_diff_if_requested = extern(
    "pcc_gc_precise_stack_diff_if_requested", (), c_void
)
pcc_gc_incremental_concurrent_step = extern(
    "pcc_gc_incremental_concurrent_step", (c_int64,), c_int64
)
pcc_gc_backend4_step_remembered_roots = extern(
    "pcc_gc_backend4_step_remembered_roots", (c_int64,), c_int64
)
pcc_gc_backend3_drain_promotion_worklist = extern("pcc_gc_backend3_drain_promotion_worklist", (c_int64,), c_int64)
pcc_gc_backend4_step_generation_aging = extern(
    "pcc_gc_backend4_step_generation_aging", (c_int64,), c_int64
)
pcc_gc_backend4_evacuation_page_drain = extern(
    "pcc_gc_backend4_evacuation_page_drain", (c_int64,), c_int64
)
pcc_gc_backend4_select_relocation_pages = extern(
    "pcc_gc_backend4_select_relocation_pages", (c_int64,), c_int64
)
pcc_gc_tracing_has_sweep_candidate = extern(
    "pcc_gc_tracing_has_sweep_candidate", (), c_int64
)
pcc_gc_tracing_step_cycle = extern(
    "pcc_gc_tracing_step_cycle", (c_int64,), c_int64
)
pcc_gc_tracing_record_pause = extern(
    "pcc_gc_tracing_record_pause", (c_int64, c_int64), c_void
)
pcc_gc_backend4_remap_and_retire_stopped_world = extern(
    "pcc_gc_backend4_remap_and_retire_stopped_world", (), c_int64
)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_py_gc_minor_graph_lock = extern(
    "pcc_py_gc_minor_graph_lock", (), c_void
)
pcc_py_gc_minor_graph_lock_for = extern(
    "pcc_py_gc_minor_graph_lock_for", (c_int64,), c_void
)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_gray_count_increment_acq_rel = extern(
    "pcc_gc_gray_count_increment_acq_rel", (), c_void
)
pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)
pcc_gc_pin_conservative_thread_roots = extern(
    "pcc_gc_pin_conservative_thread_roots", (), c_void
)
pcc_gc_backend3_remember_owner = extern(
    "pcc_gc_backend3_remember_owner", (c_ptr, c_int64), c_void
)
pcc_gc_backend4_store_buffer_enqueue = extern(
    "pcc_gc_backend4_store_buffer_enqueue", (c_ptr, c_ptr, c_ptr), c_int64
)


@c_abi_export("pcc_gc_dispatch_selected_backend")
def _selected_backend() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_config_ensure()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


@c_abi_export("pcc_gc_dispatch_ptr_can_have_header")
def _ptr_can_have_header(obj) -> i64:
    return pcc_gc_pointer_is_managed(obj)


@c_abi_export("pcc_gc_dispatch_tracing_work_pending")
def _tracing_work_pending() -> i64:
    if load_i32(global_addr("pcc_gc_cycle_requested"), 0) != 0:
        return 1
    if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
        return 1
    return pcc_gc_tracing_has_sweep_candidate()


@c_abi_export("pcc_gc_sweep_owed")
def pcc_gc_sweep_owed() -> i64:
    # Mirror of the C predicate: a mark cycle completed and its sweep is still
    # owed.  pcc_gc_finish_tracing_cycle is the only writer of the candidate
    # flag and it clears mark_active in the same commit, so checking both is a
    # mark-complete test.  Gating a sweep on the candidate flag alone is
    # unsound - leftovers from a previous unfinished sweep read true mid-mark.
    if _selected_backend() == 0:
        return 0
    if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
        return 0
    return pcc_gc_tracing_has_sweep_candidate()


@c_abi_export("pcc_gc_step")
def pcc_gc_step(budget: i64) -> i64:
    backend: i64 = _selected_backend()
    if budget <= 0:
        return 0
    if backend == 4 and load_i32(global_addr("pcc_gc_backend4_remap_active"), 0) != 0:
        return 0
    if backend == 1 or backend == 2 or backend == 3:
        # GC3 copies and remaps nothing: its step is the shared mark step.
        return pcc_gc_incremental_concurrent_step(budget)
    # GC4 steps copy, remap and retire objects only when every other
    # mutator is parked at an application gate, which
    # pcc_thread_all_stopped_at_gates answers for a stopped world. Explicit
    # collects and the automatic GC4 step stop the world before stepping; a
    # direct call that had not read the collector thread's gate state at a
    # random instant, so relocation and retirement ran or were skipped by
    # timing. Stop the world for such a call, as
    # pcc_gc_backend4_remap_and_retire_stopped_world does.
    acquired_world: i64 = 0
    if (backend == 4) and pcc_thread_owns_stopped_world() == 0:
        if pcc_stop_the_world() != 0:
            return 0
        acquired_world = 1
    if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
        # Stack-map step 3: automatic steps run where an allocation triggered
        # them; compare that stack's mapped roots with the registered ones
        # (explicit collections did so once after stop-the-world).
        pcc_gc_precise_stack_diff_if_requested()

    store_i64(
        global_addr("pcc_gc_metric_step"),
        0,
        load_i64(global_addr("pcc_gc_metric_step"), 0) + 1,
    )
    start_us: i64 = pcc_platform_monotonic_us()
    processed: i64 = 0

    # GC1-GC3 returned through the shared mark step above.
    if backend == 4:
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
            # Store-buffer entries own a reference to their recorded value. An
            # explicit collect drains them as background steps do (promoting
            # the recorded value, then releasing the hold), or a value whose
            # slot was overwritten outlives every collect and never finalizes.
            if (
                load_i32(global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0)
                > 0
            ):
                processed = processed + pcc_gc_backend4_step_remembered_roots(
                    budget - processed
                )
            if processed < budget:
                processed = processed + pcc_gc_backend3_drain_promotion_worklist(
                    budget - processed
                )
            if processed < budget:
                processed = processed + pcc_gc_tracing_step_cycle(
                    budget - processed
                )
            # Forwarding edges own a reference to each replacement. An idle
            # explicit collect must advance the same remap/retirement epochs
            # as background steps, or that hold outlives the last real owner.
            # Do not retire across an unfinished mark or pending sweep. The
            # existing helper preserves root healing, source quarantine and
            # the one release for each detached forwarding edge.
            if (
                processed == 0
                and _tracing_work_pending() == 0
                and (
                    load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0
                    or ptr_is_null(global_load_ptr("pcc_gc_backend4_parked_head")) == 0
                    or ptr_is_null(global_load_ptr("pcc_gc_backend4_retained_page_head")) == 0
                )
                and (
                    pcc_thread_live_count() <= 1
                    or pcc_thread_all_stopped_at_gates() != 0
                )
            ):
                # Retirement frees relocation sources: as for evacuation,
                # every other mutator must be parked at an application gate.
                if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0:
                    pcc_gc_pin_conservative_thread_roots()
                retired: i64 = pcc_gc_backend4_remap_and_retire_stopped_world()
                processed = processed + retired
                if retired > 0:
                    # Each edge's hold kept its replacement reachable in the
                    # mark this collect swept. Mark again: garbage held only
                    # by an edge would otherwise outlive gc.collect().
                    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
        else:
            processed = processed + pcc_gc_backend4_step_remembered_roots(
                budget - processed
            )
            if (
                load_i32(
                    global_addr("pcc_gc_backend4_store_buffer_entries_count"),
                    0,
                )
                == 0
            ):
                if processed < budget:
                    processed = processed + pcc_gc_backend4_step_generation_aging(
                        budget - processed
                    )
                # Relocation (evacuation and its remap/retirement) needs every
                # other mutator parked at an application gate: one parked or
                # blocked inside runtime code may hold raw pointers no root
                # describes. Otherwise this step does only non-moving work.
                may_relocate: i64 = 1
                if pcc_thread_live_count() > 1 and pcc_thread_all_stopped_at_gates() == 0:
                    may_relocate = 0
                # Selection scores every zpage owner. Page occupancy changes
                # its answer only when a sweep frees bytes, so select once
                # per completed mark (as ZGC does per cycle); selected pages
                # keep draining at every step.
                marks_finished: i64 = load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
                selection_due: i64 = 0
                if marks_finished != load_i64(global_addr("pcc_gc_backend4_selection_commit"), 0):
                    selection_due = 1
                if processed < budget and may_relocate != 0:
                    # Refresh before selection/copy, which may run before a
                    # mark seeds roots, and before a drain's remap of
                    # remaining edges. A step with neither needs no scan.
                    if (
                        load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0
                        and (
                            selection_due != 0
                            or ptr_is_null(
                                global_load_ptr("pcc_gc_backend4_evacuation_page_head")
                            ) == 0
                            or load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0
                        )
                    ):
                        pcc_gc_pin_conservative_thread_roots()
                    processed = processed + pcc_gc_backend4_evacuation_page_drain(
                        budget - processed
                    )
                if processed < budget and may_relocate != 0 and selection_due != 0:
                    store_i64(global_addr("pcc_gc_backend4_selection_commit"), 0, marks_finished)
                    selected: i64 = pcc_gc_backend4_select_relocation_pages(
                        budget - processed
                    )
                    if selected > 0:
                        moved: i64 = pcc_gc_backend4_evacuation_page_drain(
                            budget - processed
                        )
                        if moved > 0:
                            processed = processed + moved
                        else:
                            processed = processed + selected
                # The gate's automatic step (pcc_gc_in_auto_step) leaves the
                # mark to its gate, which runs or finishes it and sweeps it
                # in one stopped world (_gc4_maybe_auto_step): a mark
                # finished in this stop was swept at a later one, after a
                # whole-heap re-mark. Other callers keep marking here.
                if (
                    processed < budget
                    and load_i32(global_addr("pcc_gc_in_auto_step"), 0) == 0
                    and _tracing_work_pending() != 0
                ):
                    stw: i64 = pcc_stop_the_world()
                    # A call ends at each phase boundary (root seeding,
                    # extension roots, one pass over the object list).
                    # Continue within this step's budget until the mark
                    # completes: a mark spread over several debt intervals
                    # kept whatever died meanwhile, and the inflated marked
                    # heap raised the next threshold.
                    commits: i64 = load_i64(
                        global_addr("pcc_gc_tracing_finish_commits"), 0
                    )
                    calls: i64 = 0
                    while (
                        processed < budget
                        and calls < 8
                        and (
                            calls == 0
                            or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
                        )
                        and load_i64(
                            global_addr("pcc_gc_tracing_finish_commits"), 0
                        ) == commits
                    ):
                        processed = processed + pcc_gc_tracing_step_cycle(
                            budget - processed
                        )
                        calls = calls + 1
                    if stw == 0:
                        pcc_resume_world()
                if (
                    processed == 0
                    and may_relocate != 0
                    and (
                        load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0
                        or ptr_is_null(global_load_ptr("pcc_gc_backend4_parked_head")) == 0
                        or ptr_is_null(global_load_ptr("pcc_gc_backend4_retained_page_head")) == 0
                    )
                ):
                    processed = processed + (
                        pcc_gc_backend4_remap_and_retire_stopped_world()
                    )

    pcc_gc_tracing_record_pause(start_us, pcc_platform_monotonic_us())
    if acquired_world != 0:
        pcc_resume_world()
    return processed


@c_abi_export("pcc_gc_count_free_store_shade")
def pcc_gc_count_free_store_shade(value) -> None:
    """The nonmoving count-free GC1/GC2 barrier for a locked first store.

    The caller holds the destination owner's container lock or the graph
    lock, so its mark_active read is ordered with the marker's scan of that
    owner, exactly as the container branch of pcc_gc_note_slot_write_barrier
    decides. Outside a cycle nothing is owed; during one, GC2 shades the
    value and GC1 shades a white value, both with one atomic transition.
    """
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return
    if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") == 0:
        return
    if pcc_gc_object_index_seen_stable(value) == 0:
        return
    if (load_i32(value, 12) & 524288) != 0:
        return
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 2:
        pcc_gc_shade_gray_atomic(value, 1)
    elif (load_i32(value, 12) & 8) != 0:
        pcc_gc_shade_gray_atomic(value, 0)


@c_abi_export("pcc_gc_count_free_barrier_shade")
def pcc_gc_count_free_barrier_shade(value) -> None:
    """The nonmoving count-free GC1/GC2 barrier for an unlocked store.

    A cycle's mark_active changes only with the world stopped, and this
    no-park scope keeps the world running until the shade is done, so the
    phase read here holds for the whole transition. The marker reads each
    slot word whole: it sees either the stored value, which it traces, or
    the old one, and then this shade gives the stored value its marking
    work. Outside a cycle the caller's store owes nothing. GC2 shades the
    value and counts the shade as its locked barrier did; GC1 shades a white
    value whatever the owner's color, as the container branch does.
    """
    pcc_thread_no_park_enter()
    if (
        atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") != 0
        and pcc_gc_object_index_seen_stable(value) != 0
    ):
        flags: i64 = atomic_load_i32(value, 12, "acquire")
        if (flags & 524288) == 0:
            if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 2:
                if (flags & 16) == 0:
                    atomic_rmw_i32(
                        "add", global_addr("pcc_gc_cms_wb_flushes"), 0, 1, "relaxed"
                    )
                pcc_gc_shade_gray_atomic(value, 1)
            elif (flags & 8) != 0:
                pcc_gc_shade_gray_atomic(value, 0)
    pcc_thread_no_park_exit()


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return
    backend: i64 = _selected_backend()

    if ptr_is_null(owner) != 0:
        if backend != 1 and backend != 2 and backend != 3 and backend != 4:
            return
        # A root store only shades during an active cycle. The cycle's
        # mark_active becomes 1 under the graph lock, but the caller's store
        # is not inside this barrier's lock section either way: reading 0
        # here is the answer the locked read would give, so skip the lock and
        # both index probes outside a cycle.
        if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") == 0:
            return
        if load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0:
            pcc_gc_count_free_barrier_shade(value)
            return
        pcc_py_gc_minor_graph_lock_for(1)
        if (
            pcc_gc_object_is_known_no_lock(value) != 0
            and load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
        ):
            value_flags: i64 = load_i32(value, 12)
            should_gray: i64 = 0
            if (value_flags & 8) != 0:
                should_gray: i64 = 1
            if backend == 2 and (value_flags & 16) == 0:
                should_gray: i64 = 1
            if should_gray != 0:
                # A root introduces marking work for every tracing backend.
                # Count only the actual non-GRAY -> GRAY transition; a
                # container barrier may shade the same object without this
                # lock.
                pcc_gc_shade_gray_atomic(value, 1)
                if backend == 2:
                    store_i32(
                        global_addr("pcc_gc_cms_wb_flushes"),
                        0,
                        load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0) + 1,
                    )
        pcc_py_gc_minor_graph_unlock()
        return

    if is_tagged_int(owner) != 0:
        return
    if backend == 1 or backend == 2 or backend == 3 or (
        backend == 4
        and load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
    ):
        if pcc_container_lock_held() != 0:
            # Container mode: the store holds the owner's container lock, and
            # the marker scans that owner under the same lock
            # (pcc_gc_trace_referents). Either the store precedes the scan,
            # which then reads it, or it follows the scan, and the cycle's
            # mark_active (published before any scan) is visible here. No
            # graph lock is taken. The marker blackens an owner after its
            # unlocked scan, so GC1 shades a white value whenever a cycle is
            # active instead of only below a black owner.
            if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") == 0:
                return
            if pcc_gc_object_index_seen_stable(value) == 0:
                return
            if (load_i32(value, 12) & 524288) != 0:
                return
            if backend == 2:
                pcc_gc_shade_gray_atomic(value, 1)
            elif (load_i32(value, 12) & 8) != 0:
                pcc_gc_shade_gray_atomic(value, 0)
            return
        # As for roots: GC1/GC2 heap barriers shade only during a cycle, and
        # a 0 read here is what the locked read would answer for a store
        # that lands after this barrier returns. A caller already inside a
        # graph transaction keeps that ordering through its own lock.
        if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") == 0:
            return
        # Count-free GC1/GC2 never move or count, so the graph lock orders
        # nothing a phase-stable shade does not: the marker blackens an owner
        # after reading its slots, and the shade covers a store it missed.
        if load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0:
            pcc_gc_count_free_barrier_shade(value)
            return
        pcc_py_gc_minor_graph_lock_for(1)
        if (
            pcc_gc_object_is_known_no_lock(owner) != 0
            and pcc_gc_object_is_known_no_lock(value) != 0
        ):
            owner_flags: i64 = load_i32(owner, 12)
            value_flags = load_i32(value, 12)
            should_shade: i64 = 0
            # Backend 1 shades a black owner's new white referent to keep the
            # tricolor invariant DURING a marking cycle.  Outside a cycle there
            # is no invariant to keep, and shading there used to also store
            # mark_active=1 below - fabricating a cycle with no epoch, no
            # whitening pass and no seeded roots.  The next explicit collect
            # then saw an active cycle, skipped pcc_gc_begin_mark_cycle, traced
            # nothing, finished that phantom cycle and computed an empty
            # candidate set, so it reclaimed nothing at all.  Require an active
            # cycle, exactly as the backend-2 arm below already does.
            if (
                (backend == 1 or backend == 3)
                and load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
                and (owner_flags & 32) != 0
            ):
                should_shade: i64 = 1
            if (
                backend == 2
                and load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
            ):
                should_shade: i64 = 1
            should_gray_value: i64 = 0
            if (value_flags & 8) != 0:
                should_gray_value: i64 = 1
            if backend == 2 and (value_flags & 16) == 0:
                should_gray_value: i64 = 1
            if should_shade != 0 and should_gray_value != 0:
                pcc_gc_shade_gray_atomic(value, 1)
                if backend == 2:
                    store_i32(
                        global_addr("pcc_gc_cms_wb_flushes"),
                        0,
                        load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0) + 1,
                    )
        pcc_py_gc_minor_graph_unlock()
        return

    if backend != 4:
        return
    if _ptr_can_have_header(owner) == 0 or _ptr_can_have_header(value) == 0:
        return
    owner_flags = load_i32(owner, 12)
    value_flags = load_i32(value, 12)
    if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
        if (owner_flags & 256) == 0 or (value_flags & 128) == 0:
            return
    pcc_py_gc_minor_graph_lock_for(1)
    if (
        pcc_gc_object_is_known_no_lock(owner) != 0
        and pcc_gc_object_is_known_no_lock(value) != 0
    ):
        owner_flags = load_i32(owner, 12)
        value_flags = load_i32(value, 12)
        # Major marking and generation recording are independent obligations.
        # A black young owner can acquire a white old child as well.
        if (
            load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
            and (owner_flags & 32) != 0
            and (value_flags & 8) != 0
        ):
            pcc_gc_shade_gray_atomic(value, 0)
        if (owner_flags & 256) != 0 and (value_flags & 128) != 0:
            if backend == 4:
                if pcc_gc_backend4_store_buffer_enqueue(owner, slot, value) != 0:
                    store_i32(
                        global_addr("pcc_gc_backend4_genzgc_store_barriers"),
                        0,
                        load_i32(
                            global_addr("pcc_gc_backend4_genzgc_store_barriers"),
                            0,
                        )
                        + 1,
                    )
            else:
                pcc_gc_backend3_remember_owner(owner, owner_flags)
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_note_write_barrier")
def pcc_gc_note_write_barrier(owner, value) -> None:
    pcc_gc_note_slot_write_barrier(owner, null(), value)
