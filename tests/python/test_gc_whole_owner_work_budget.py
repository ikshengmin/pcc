"""Soft concurrent work budgets over complete, locked production owner scans.

The actual scheduler, slot visitor, workstack, colors and barriers execute at
an explicit host raw-memory boundary. This proves no native scheduling,
reclamation, relocation, end-to-end latency bound or throughput improvement.
"""
from __future__ import annotations

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.test_gc_major_slot_budget_contract import TRACE_MODES, _SlotBoundary
from tests.python.test_gc_trace_workstack import BLACK, DYING, GRAY, WHITE, _load_bodies


class _WorkBoundary(_SlotBoundary):
    def __init__(self, backend=1, container_mode=0, stopped=False):
        super().__init__(backend, container_mode)
        self.completions = []
        self.cells["pcc_gc_cms_worker_current"] = {0: 0}
        self.cells["pcc_gc_tracing_finish_claim_backend"] = {0: -1}
        self.namespace["pcc_thread_owns_stopped_world"] = lambda: int(stopped)
        self.namespace["pcc_gc_complete_claimed_tracing_cycle"] = self.complete
        _load_bodies("freestanding_gc_public_collection.py", {
            "pcc_gc_is_cms_worker",
        }, self.namespace)
        _load_bodies("freestanding_gc_incremental_concurrent_scheduler.py", {
            "pcc_gc_tracing_finish_left_to_mutator",
        }, self.namespace)

    def complete(self, epoch, backend):
        # Final remark itself is outside this host scan-budget boundary.
        assert self.graph_depth == 0 and self.gray == 0
        assert self.owner == self.count == 0
        self.completions.append((epoch, backend))

    def owner_work(self, owner):
        self.lock()
        try:
            return self.namespace["pcc_gc_trace_referents_work"](owner)
        finally:
            self.unlock()


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_direct_wide_owner_completes_then_stops_without_credit_for_next(backend, container_mode):
    state = _WorkBoundary(backend, container_mode)
    first, second = state.list_owner("first"), state.list_owner("second")
    state.replace_items(first, [None] * 9)
    state.replace_items(second, [7] * 9)
    assert state.step(4) == 1
    assert len(state.slot_reads) == 9
    assert first[12] & 56 == BLACK and second[12] & 56 == GRAY
    assert state.gray == 1 and not state.completions
    assert state.pointers["pcc_gc_trace_cursor"] is state.nodes[1]
    assert state.step(4) == 1
    assert len(state.slot_reads) == 18 and state.gray == 0
    assert state.completions == [(17, backend)]


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_direct_and_stack_share_allowance_and_list_recovers_leftover(backend, container_mode):
    state = _WorkBoundary(backend, container_mode)
    left = state.list_owner("left", WHITE)
    right = state.list_owner("right", WHITE)
    root = state.list_owner("root")
    state.replace_items(left, [None] * 7)
    state.replace_items(right, [None] * 7)
    state.replace_items(root, [left, right])
    state.pointers["pcc_gc_trace_cursor"] = state.nodes[2]
    # root costs2; the LIFO right child overshoots the2 remaining. The left
    # child stays gray behind the original cursor, with no retained stack.
    assert state.step(4) == 2
    assert len(state.slot_reads) == 9
    assert root[12] & 56 == right[12] & 56 == BLACK
    assert left[12] & 56 == GRAY and state.gray == 1
    assert state.pointers["pcc_gc_trace_cursor"] is state.nodes[0]
    assert state.step(4) == 1 and state.gray == 0
    assert len(state.slot_reads) == 16
    assert state.completions == [(17, backend)]


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_stopped_world_keeps_original_object_budget(backend, container_mode):
    state = _WorkBoundary(backend, container_mode, stopped=True)
    first, second = state.list_owner("first"), state.list_owner("second")
    state.replace_items(first, [None] * 9)
    state.replace_items(second, [None] * 9)
    assert state.step(2) == 2
    assert len(state.slot_reads) == 18 and state.gray == 0
    assert state.completions == [(17, backend)]


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_zero_slot_owners_cost_one_and_keep_object_credit(backend, container_mode):
    state = _WorkBoundary(backend, container_mode)
    leaves = [state.leaf(str(i)) for i in range(5)]
    for leaf in leaves:
        state.namespace["pcc_gc_trace_mark_gray_if_known"](leaf)
    assert state.step(3) == 3
    assert state.gray == 2 and not state.slot_reads
    assert [obj[12] & 56 for obj in leaves] == [BLACK] * 3 + [GRAY] * 2
    assert state.step(3) == 2 and state.gray == 0


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_sparse_dict_holes_are_charged_without_callbacks(backend):
    state = _WorkBoundary(backend)
    owner = state.object("sparse", flags=GRAY)
    owner[8] = ABI_CONSTANTS["object.type.dict"]
    entries = {}
    for index in range(7):
        entries[index * ABI_CONSTANTS["object.dict_entry.size"] +
                ABI_CONSTANTS["object.dict_entry.key_offset"]] = None
    owner[ABI_CONSTANTS["object.dict.entries_offset"]] = entries
    owner[ABI_CONSTANTS["object.dict.entries_used_offset"]] = 7
    assert state.owner_work(owner) == 14
    assert not state.slot_reads and owner[12] & 56 == GRAY


@pytest.mark.parametrize("backend", [1, 2])
def test_work_is_captured_before_container_unlock_can_replace_layout(backend):
    state = _WorkBoundary(backend, 1)
    owner = state.list_owner("list")
    state.replace_items(owner, [None] * 9)
    old_items = owner[ABI_CONSTANTS["object.list.items_offset"]]
    original_unlock = state.namespace["pcc_container_unlock"]

    def replace_at_unlock(obj):
        original_unlock(obj)
        state.replace_items(obj, [])
        old_items["retired"] = True

    state.namespace["pcc_container_unlock"] = replace_at_unlock
    assert state.owner_work(owner) == 9
    assert len(state.slot_reads) == 9
    assert owner[ABI_CONSTANTS["object.list.length_offset"]] == 0


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_new_barrier_edge_after_complete_owner_is_not_lost(backend, container_mode):
    state = _WorkBoundary(backend, container_mode)
    owner = state.list_owner("owner")
    waiting = state.list_owner("waiting")
    child = state.leaf("new child")
    state.replace_items(owner, [None] * 9)
    assert state.step(4) == 1
    items = owner[ABI_CONSTANTS["object.list.items_offset"]]
    if container_mode:
        state.container_lock(owner)
    else:
        state.lock()
    try:
        state.namespace["pcc_gc_note_slot_write_barrier"](owner, (items, 0), child)
        items[0] = child
    finally:
        if container_mode:
            state.container_unlock(owner)
        else:
            state.unlock()
    assert child[12] & 56 == GRAY and state.gray == 2
    assert state.step(4) == 2 and state.gray == 0
    assert waiting[12] & 56 == child[12] & 56 == BLACK


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_capacity_overflow_and_budget_discard_both_recover(backend, container_mode):
    state = _WorkBoundary(backend, container_mode)
    children = [state.leaf(str(i)) for i in range(4097)]
    root = state.list_owner("root")
    state.replace_items(root, children)
    state.pointers["pcc_gc_trace_cursor"] = state.nodes[-1]
    assert state.step(4096) == 1
    assert state.peak == 4096 and state.gray == len(children)
    assert state.count == state.owner == 0
    assert state.step(5000) == len(children)
    assert state.gray == 0 and all(child[12] & 56 == BLACK for child in children)


def test_stack_work_saturates_and_never_finishes_excluded_entries():
    state = _WorkBoundary()
    wide = state.list_owner("wide")
    state.replace_items(wide, [None] * 9)
    dying = state.leaf("dying")
    dying[12] = GRAY | DYING
    state.change_gray(1)
    extension = state.object("extension", cext=True, flags=GRAY)
    state.lock()
    try:
        state.namespace["pcc_gc_trace_stack_begin"]()
        for obj in [wide, extension, dying, wide]:
            state.namespace["pcc_gc_trace_stack_push"](obj)
        remaining = {0: 1}
        assert state.namespace["pcc_gc_trace_stack_drain_work"](99, remaining) == 1
        assert remaining[0] == 0 and state.count == 3
        assert state.gray == 2 and wide[12] & 56 == BLACK
        state.namespace["pcc_gc_trace_stack_end"]()
    finally:
        state.unlock()
    assert state.owner == state.count == 0
    assert extension[12] & 56 == GRAY and dying[12] == GRAY | DYING


def test_unhandled_visitor_keeps_original_fallback():
    state = _WorkBoundary()
    owner = state.object("unhandled", flags=GRAY)
    owner[8] = 99  # Reserved, below instance/user-class dispatch.
    assert owner[8] < ABI_CONSTANTS["object.type.user_class_start"]
    calls = []
    state.namespace["pcc_gc_visit_object_slots"] = lambda obj, cb, ctx: calls.append(obj) or 0
    assert state.owner_work(owner) == 1
    assert calls == [owner] and owner[12] & 56 == GRAY


def test_positive_stack_allowance_skips_dying_extension_and_duplicate_black():
    state = _WorkBoundary()
    live = state.leaf("live")
    state.namespace["pcc_gc_trace_mark_gray_if_known"](live)
    dying = state.leaf("dying")
    dying[12] = GRAY | DYING
    state.change_gray(1)
    extension = state.object("extension", cext=True, flags=GRAY)
    state.lock()
    try:
        state.namespace["pcc_gc_trace_stack_begin"]()
        for obj in [live, live, extension, dying]:
            state.namespace["pcc_gc_trace_stack_push"](obj)
        remaining = {0: 3}
        assert state.namespace["pcc_gc_trace_stack_drain_work"](9, remaining) == 1
        assert remaining[0] == 2 and state.count == 0
        assert state.gray == 2 and live[12] & 56 == BLACK
        assert extension[12] & 56 == GRAY and dying[12] == GRAY | DYING
        assert not state.slot_reads and not state.traces
        state.namespace["pcc_gc_trace_stack_end"]()
    finally:
        state.unlock()


@pytest.mark.parametrize("symbol,parameters", [
    ("pcc_gc_trace_referents_work", "(c_ptr,)"),
    ("pcc_gc_trace_stack_drain_work", "(c_int64,c_ptr)"),
])
def test_cross_object_work_helpers_admit_only_exact_raw_signatures(symbol, parameters):
    from pcc.frontends.python.codegen.runtime_abi import (
        is_freestanding_gc_cross_object_runtime_import,
    )
    check = is_freestanding_gc_cross_object_runtime_import
    assert check(symbol, parameters, "c_int64")
    assert not check(symbol, parameters, "c_void")
    assert not check(symbol, "()", "c_int64")
    assert not check(symbol, "(c_ptr,c_int64)", "c_int64")
