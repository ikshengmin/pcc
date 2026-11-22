"""Shared write-barrier and five-backend step dispatch policy."""

from pcc import i64
from pcc.runtime.py.py_abi_constants import PY_TYPE_LIST, PY_TYPE_TUPLE
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_rmw_i64,
    atomic_store_i64,
    define_global_i64,
    function_addr,
    global_addr,
    global_load_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_diff,
    ptr_is_null,
    ptr_to_int,
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
pcc_gc_granule_is_object_start = extern("pcc_gc_granule_is_object_start", (c_ptr,), c_int64)
pcc_gc_visit_object_slots = extern("pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64)


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
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_gray_count_increment_acq_rel = extern(
    "pcc_gc_gray_count_increment_acq_rel", (), c_void
)
pcc_gc_pin_conservative_thread_roots = extern(
    "pcc_gc_pin_conservative_thread_roots", (), c_void
)
pcc_gc_object_is_white = extern("pcc_gc_object_is_white", (c_ptr, c_int64), c_int64)


@c_abi_export("pcc_gc_dispatch_selected_backend")
def _selected_backend() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_config_ensure()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


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
                # Selection ranks the object slabs by the live cells the
                # finished mark left.
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
                            or ptr_is_null(
                                global_load_ptr("pcc_gc_backend4_slab_evacuation_head")
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
    elif pcc_gc_object_is_white(value, load_i32(value, 12)) != 0:
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
            elif pcc_gc_object_is_white(value, flags) != 0:
                pcc_gc_shade_gray_atomic(value, 0)
    pcc_thread_no_park_exit()


# Review-only stopped observer. Its caller owns this scratch layout and
# installs/removes it between stops. No heap walker consumes this buffer.
define_global_i64("pcc_raw_gc3_owner_cards", 0)
_GC3_CARD_CAPACITY = 0
_GC3_CARD_USED = 8
_GC3_CARD_OVERFLOW = 16
_GC3_CARD_OWNERS = 24
_GC3_CARD_OBJECT_FLAGS = 12
_GC3_CARD_OLD = 256
_GC3_CARD_YOUNG = 128


@c_abi_export("pcc_gc3_review_owner_cards_set_stopped")
def pcc_gc3_review_owner_cards_set_stopped(buffer) -> i64:
    if _selected_backend() != 3 or pcc_thread_owns_stopped_world() == 0:
        return 0
    atomic_store_i64(global_addr("pcc_raw_gc3_owner_cards"), 0,
                     ptr_to_int(buffer), "release")
    return 1


@c_abi_export("pcc_gc3_review_note_owner_card")
def pcc_gc3_review_note_owner_card(owner, value) -> None:
    bits: i64 = atomic_load_i64(global_addr("pcc_raw_gc3_owner_cards"), 0, "acquire")
    if bits == 0:
        return
    if (atomic_load_i32(owner, _GC3_CARD_OBJECT_FLAGS, "acquire") & _GC3_CARD_OLD) == 0:
        return
    if (atomic_load_i32(value, _GC3_CARD_OBJECT_FLAGS, "acquire") & _GC3_CARD_YOUNG) == 0:
        return
    buffer = int_to_ptr(bits)
    if atomic_load_i64(buffer, _GC3_CARD_OVERFLOW, "relaxed") != 0:
        return
    index: i64 = atomic_rmw_i64("add", buffer, _GC3_CARD_USED, 1, "relaxed")
    if index >= load_i64(buffer, _GC3_CARD_CAPACITY):
        atomic_store_i64(buffer, _GC3_CARD_OVERFLOW, 1, "release")
        return
    atomic_store_i64(buffer, _GC3_CARD_OWNERS + index * 8,
                     ptr_to_int(owner), "release")


_GC3_FRONTIER_CAPACITY = 0
_GC3_FRONTIER_COUNT = 8
_GC3_FRONTIER_OLD_VISITS = 16
_GC3_FRONTIER_STRONG_SLOTS = 24
_GC3_FRONTIER_FAILED = 32
_GC3_FRONTIER_ITEMS = 40
_GC3_CARD_TYPE_TAG = 8
_GC3_CARD_STRONG_ROLE = 1


@c_abi_export("pcc_gc3_review_append_young_slot")
def pcc_gc3_review_append_young_slot(slot, role: i64, frontier) -> None:
    if role != _GC3_CARD_STRONG_ROLE:
        return
    store_i64(frontier, _GC3_FRONTIER_STRONG_SLOTS,
              load_i64(frontier, _GC3_FRONTIER_STRONG_SLOTS) + 1)
    child = load_ptr(slot, 0)
    if ptr_is_null(child) != 0 or is_tagged_int(child) != 0:
        return
    if pcc_gc_granule_is_object_start(child) != 1:
        return
    if (load_i32(child, _GC3_CARD_OBJECT_FLAGS) & _GC3_CARD_YOUNG) == 0:
        return
    count: i64 = load_i64(frontier, _GC3_FRONTIER_COUNT)
    if count >= load_i64(frontier, _GC3_FRONTIER_CAPACITY):
        store_i64(frontier, _GC3_FRONTIER_FAILED, 1)
        return
    store_ptr(frontier, _GC3_FRONTIER_ITEMS + count * 8, child)
    store_i64(frontier, _GC3_FRONTIER_COUNT, count + 1)


@c_abi_export("pcc_gc3_review_scan_owner_cards_stopped")
def pcc_gc3_review_scan_owner_cards_stopped(cards, frontier) -> i64:
    """Collect young frontier roots from dirty old list/tuple owners only.

    A live card remains in the buffer across minor cuts until it has no
    young referents. The buffer is caller-owned; an incomplete cut returns
    -1 and latches overflow, so its partial frontier is never a verdict.
    This review entry does not mark or free any object.
    """
    if (_selected_backend() != 3 or pcc_thread_owns_stopped_world() == 0
            or ptr_is_null(cards) != 0 or ptr_is_null(frontier) != 0
            or ptr_diff(cards, frontier) == 0):
        return -1
    if (atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") != 0
            or atomic_load_i32(global_addr("pcc_gc_cms_sweep_pending"), 0, "acquire") != 0
            or atomic_load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, "acquire") != 0):
        return -1
    used: i64 = load_i64(cards, _GC3_CARD_USED)
    if (used < 0 or used > load_i64(cards, _GC3_CARD_CAPACITY)
            or load_i64(cards, _GC3_CARD_OVERFLOW) != 0
            or load_i64(frontier, _GC3_FRONTIER_CAPACITY) < 0):
        return -1
    store_i64(frontier, _GC3_FRONTIER_COUNT, 0)
    store_i64(frontier, _GC3_FRONTIER_OLD_VISITS, 0)
    store_i64(frontier, _GC3_FRONTIER_STRONG_SLOTS, 0)
    store_i64(frontier, _GC3_FRONTIER_FAILED, 0)
    visit = function_addr("pcc_gc3_review_append_young_slot")
    read: i64 = 0
    retain: i64 = 0
    while read < used:
        owner = load_ptr(cards, _GC3_CARD_OWNERS + read * 8)
        read = read + 1
        if ptr_is_null(owner) != 0:
            store_i64(cards, _GC3_CARD_OVERFLOW, 1)
            return -1
        # Retired owners are not roots, and their old header may be gone.
        if pcc_gc_object_index_seen_stable(owner) == 0:
            continue
        if (load_i32(owner, _GC3_CARD_OBJECT_FLAGS) & _GC3_CARD_OLD) == 0:
            continue
        tag: i64 = load_i32(owner, _GC3_CARD_TYPE_TAG)
        if tag != PY_TYPE_LIST and tag != PY_TYPE_TUPLE:
            store_i64(cards, _GC3_CARD_OVERFLOW, 1)
            return -1
        previous: i64 = load_i64(frontier, _GC3_FRONTIER_COUNT)
        store_i64(frontier, _GC3_FRONTIER_OLD_VISITS,
                  load_i64(frontier, _GC3_FRONTIER_OLD_VISITS) + 1)
        if (pcc_gc_visit_object_slots(owner, visit, frontier) == 0
                or load_i64(frontier, _GC3_FRONTIER_FAILED) != 0):
            store_i64(cards, _GC3_CARD_OVERFLOW, 1)
            return -1
        if load_i64(frontier, _GC3_FRONTIER_COUNT) != previous:
            store_ptr(cards, _GC3_CARD_OWNERS + retain * 8, owner)
            retain = retain + 1
    store_i64(cards, _GC3_CARD_USED, retain)
    return load_i64(frontier, _GC3_FRONTIER_COUNT)


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    """The store barrier of every tracing collector; GC0 owes none.

    GC1-GC4 share one count-free mutator protocol: no object a running
    thread names moves, so a store owes only the marking work of an active
    cycle. A cycle's mark_active changes only in a stopped world, so a 0
    read here is the answer a locked read would give. Since GC4 joined that
    protocol (pcc_gc_fix_backend sets pcc_gc_nonmoving_count_free for every
    tracing collector) it records no old-to-young edge here either.
    """
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return
    backend: i64 = _selected_backend()
    if backend == 0:
        return
    if ptr_is_null(owner) == 0:
        if is_tagged_int(owner) != 0:
            return
        if backend == 3:
            # Direct writes hold no-park across the store; planned writes
            # already hold the destination container or graph lock.
            pcc_gc3_review_note_owner_card(owner, value)
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
            elif pcc_gc_object_is_white(value, load_i32(value, 12)) != 0:
                pcc_gc_shade_gray_atomic(value, 0)
            return
    if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") == 0:
        return
    pcc_gc_count_free_barrier_shade(value)


@c_abi_export("pcc_gc_note_root_slot_barrier")
def pcc_gc_note_root_slot_barrier(slot) -> None:
    """The store barrier for the value a producer just published in ``slot``,
    a root of the calling thread (a slot frame's result: the _adopt helpers).

    Outside container mode (the provenance probe) the slot is reread and
    barriered under the graph lock, as those helpers did. In container mode
    every collector scans roots with the world stopped, an object a running
    thread names never moves, and the count-free barrier is the unlocked one
    (pcc_gc_note_slot_write_barrier), so no lock is taken: every special
    method call, sequence repeat and slot-frame call of every thread queued
    on the graph lock here.
    """
    if load_i32(global_addr("pcc_gc_list_container_mode"), 0) != 0:
        pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
        return
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_note_write_barrier")
def pcc_gc_note_write_barrier(owner, value) -> None:
    pcc_gc_note_slot_write_barrier(owner, null(), value)
