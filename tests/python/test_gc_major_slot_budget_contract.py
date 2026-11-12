"""Current major-mark object budget and current-layout slot cursor contracts.

This is a test-only prerequisite for a future edge-budget implementation.
The unchanged major marker intentionally scans each selected owner completely.
The interleaving examples below are unsafe *hypothetical callers* of the slot
API, not reports that today's whole-owner marker loses reachable objects.
No native concurrency, collector qualification or performance is established.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.test_gc_trace_workstack import (
    BLACK, GRAY, WHITE, _MarkBoundary, _load_bodies,
)


class _SlotBoundary(_MarkBoundary):
    """Keep published mark/atomic bodies; replace slot seam with real bodies.

    Memory, registration, forwarding lookup and lock ownership remain doubles.
    No collector callback, slot traversal, write barrier or color transition is
    replaced by a convenient always-shading implementation.
    """
    def __init__(self, backend=1, container_mode=0):
        super().__init__()
        self.graph_depth = 0
        self.container_owner = None
        self.slot_reads = []
        self.cells.update({
            "pcc_gc_backend_selected": {0: backend},
            "pcc_gc_list_container_mode": {0: container_mode},
            # The marker keeps its graph/container budget contracts here;
            # a store shades through the production count-free helper.
            "pcc_gc_nonmoving_count_free": {0: 0},
            "pcc_gc_config_initialized": {0: 1},
            "pcc_gc_mark_active": {0: 1},
            "pcc_gc_cycle_requested": {0: 0},
            "pcc_gc_trace_extension_roots_pending": {0: 0},
            "pcc_gc_tracing_finish_claim_epoch": {0: 0},
            "pcc_gc_cms_wb_flushes": {0: 0},
        })
        self.pointers["pcc_gc_trace_cursor"] = None
        self.namespace.update({
            "abi_constant": ABI_CONSTANTS.__getitem__,
            "ptr_add": lambda base, offset: (base, offset),
            "stack_alloc": lambda size: {i: 0 for i in range(0, size, 8)},
            "call_void_ptr_i64_ptr": lambda visitor, slot, role, ctx: visitor(slot, role, ctx),
            "pcc_gc_load_ptr": self.read_slot,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_lock_for": self.lock_for,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_container_lock": self.container_lock,
            "pcc_container_unlock": self.container_unlock,
            "pcc_container_lock_held": lambda: int(self.container_owner is not None),
            "pcc_thread_no_park_enter": lambda: None,
            "pcc_thread_no_park_exit": lambda: None,
            "atomic_rmw_i32": self.atomic_add,
            "pcc_gc_object_index_seen_stable": self.is_known,
            "pcc_gc_pointer_is_managed": self.is_known,
            "pcc_thread_owns_stopped_world": lambda: 0,
            "pcc_gc_is_cms_worker": lambda: 0,
            "pcc_threads_enabled": lambda: 1,
        })
        _load_bodies("freestanding_gc_object_slots.py", {
            "_visit_slot", "_has_no_pointer_slots",
            "pcc_gc_visit_object_slots_slice", "pcc_gc_visit_object_slots",
        }, self.namespace)
        _load_bodies("freestanding_gc_common_mark_cycle.py", {
            "pcc_gc_trace_referents_work", "pcc_gc_trace_stack_drain_work",
        }, self.namespace)
        _load_bodies("freestanding_gc_incremental_concurrent_scheduler.py", {
            "pcc_gc_tracing_step_cycle",
        }, self.namespace)
        _load_bodies("freestanding_gc_barrier_dispatcher.py", {
            "_selected_backend", "pcc_gc_note_slot_write_barrier",
            "pcc_gc_count_free_barrier_shade",
        }, self.namespace)

    def atomic_add(self, op, cell, offset, delta, order):
        assert op == "add"
        cell[offset] += delta

    def lock_for(self, site):
        assert site == 1
        self.namespace["pcc_py_gc_minor_graph_lock"]()

    def lock(self):
        self.graph_depth += 1

    def unlock(self):
        assert self.graph_depth > 0
        self.graph_depth -= 1

    def container_lock(self, owner):
        assert self.container_owner is None
        self.container_owner = owner

    def container_unlock(self, owner):
        assert self.container_owner is owner
        self.container_owner = None

    def read_slot(self, owner, slot):
        base, offset = slot
        assert not base.get("retired", False), "read from retired backing storage"
        if self.cells["pcc_gc_list_container_mode"][0]:
            assert self.container_owner is base["layout_owner"], "missing owner container lock"
        else:
            assert self.graph_depth > 0, "missing graph lock"
        assert offset in base, "slot cursor read beyond current layout"
        self.slot_reads.append((id(base), offset))
        return base[offset]

    def leaf(self, name):
        obj = self.object(name)
        obj[8] = ABI_CONSTANTS["object.type.float"]
        return obj

    def list_owner(self, name, flags=GRAY):
        obj = self.object(name, flags=flags)
        obj[8] = ABI_CONSTANTS["object.type.list"]
        self.replace_items(obj, [])
        return obj

    @staticmethod
    def replace_items(owner, values):
        owner[ABI_CONSTANTS["object.list.length_offset"]] = len(values)
        backing = {
            index * 8: value for index, value in enumerate(values)
        }
        backing["layout_owner"] = owner
        owner[ABI_CONSTANTS["object.list.items_offset"]] = backing

    def slice(self, owner, cursor, limit):
        state = {0: 999, 8: 999}
        self.lock()
        if self.cells["pcc_gc_list_container_mode"][0]:
            self.container_lock(owner)
        try:
            handled = self.namespace["pcc_gc_visit_object_slots_slice"](
                owner, cursor, limit, self.namespace["pcc_gc_trace_slot"], None, state,
            )
        finally:
            if self.container_owner is not None:
                self.container_unlock(owner)
            self.unlock()
        return handled, state[0], state[8]

    def step(self, budget):
        result = self.namespace["pcc_gc_tracing_step_cycle"](budget)
        assert self.graph_depth == 0 and self.container_owner is None
        assert self.count == self.owner == 0
        return result


# GC0 does not use the major tracing scheduler. Its common visitor is tested
# separately below; routing it through step_cycle would invent a fifth mode.
TRACE_MODES = [(1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (4, 0)]


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
@pytest.mark.parametrize("length", [11, 1025])
def test_one_major_object_budget_still_scans_every_list_slot(backend, container_mode, length):
    state = _SlotBoundary(backend, container_mode)
    parent = state.list_owner("parent")
    children = [state.leaf(str(i)) for i in range(length)]
    state.replace_items(parent, children)
    assert state.step(1) == 1
    assert len(state.slot_reads) == length
    assert parent[12] & 56 == BLACK
    assert all(child[12] & 56 == GRAY for child in children)
    assert state.gray == length  # Only the completed parent earned credit.


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_major_workstack_child_uses_the_same_whole_owner_contract(backend, container_mode):
    state = _SlotBoundary(backend, container_mode)
    root = state.list_owner("root")
    wide = state.list_owner("wide", flags=WHITE)
    children = [state.leaf(str(i)) for i in range(35)]
    state.replace_items(root, [wide])
    state.replace_items(wide, children)
    assert state.step(2) == 2
    assert len(state.slot_reads) == 36
    assert root[12] & 56 == wide[12] & 56 == BLACK
    assert all(child[12] & 56 == GRAY for child in children)
    assert state.gray == len(children)


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("budget", [1, 4, 16])
def test_current_layout_visitor_bounds_positions_without_completing_owner(backend, budget):
    state = _SlotBoundary(backend)
    parent = state.list_owner("parent")
    child = state.leaf("child")
    length = budget * 2 + 3
    state.replace_items(parent, [None, 7, child] + [None] * (length - 3))
    cursor, total = 0, 0
    while cursor >= 0:
        before = len(state.slot_reads)
        handled, cursor, examined = state.slice(parent, cursor, budget)
        assert handled == 1 and 0 < examined <= budget
        assert len(state.slot_reads) - before == examined
        total += examined
    assert total == length  # Null/tagged positions consume budget as well.
    assert parent[12] & 56 == GRAY and child[12] & 56 == GRAY
    assert state.gray == 2  # Slot enumeration never completes the owner.


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_current_layout_cursor_cannot_resume_after_front_shift(backend, container_mode):
    state = _SlotBoundary(backend, container_mode)
    parent = state.list_owner("parent")
    children = [state.leaf(str(i)) for i in range(11)]
    state.replace_items(parent, children)
    assert state.slice(parent, 0, 4) == (1, 4, 4)
    # Deliberately simulate an UNSAFE caller releasing the owner between
    # slices. list.pop(0)'s memmove shifts old index 4 to index 3. This raw
    # boundary models that layout effect, not execution of Python's pop.
    items = parent[ABI_CONSTANTS["object.list.items_offset"]]
    for index in range(10):
        items[index * 8] = items[(index + 1) * 8]
    del items[80]
    parent[ABI_CONSTANTS["object.list.length_offset"]] = 10
    assert state.slice(parent, 4, 100) == (1, -1, 6)
    assert children[4][12] & 56 == WHITE  # New prefix edge was not visited.
    assert items[24] is children[4]
    assert state.slice(parent, 0, 100) == (1, -1, 10)
    assert children[4][12] & 56 == GRAY  # A complete current scan finds it.


@pytest.mark.parametrize("backend,container_mode", TRACE_MODES)
def test_gray_owner_prefix_write_needs_more_than_a_resume_cursor(backend, container_mode):
    state = _SlotBoundary(backend, container_mode)
    parent = state.list_owner("parent")
    original = [state.leaf(str(i)) for i in range(11)]
    fresh = state.leaf("fresh")
    state.replace_items(parent, original)
    assert state.slice(parent, 0, 4) == (1, 4, 4)
    items = parent[ABI_CONSTANTS["object.list.items_offset"]]
    if container_mode:
        state.container_lock(parent)
    else:
        state.lock()
    try:
        state.namespace["pcc_gc_note_slot_write_barrier"](parent, (items, 0), fresh)
        items[0] = fresh
    finally:
        if container_mode:
            state.container_unlock(parent)
        else:
            state.unlock()
    assert state.slice(parent, 4, 100) == (1, -1, 7)
    # Every tracing collector's store shades a white value during a cycle,
    # whatever the owner's color (pcc_gc_count_free_barrier_shade).
    assert fresh[12] & 56 == GRAY
    # Graph-mode GC1/3/4 are correct for today's atomic whole-owner scan;
    # they do not promise the extra partial-gray state this caller invented.
    assert parent[12] & 56 == GRAY


def test_slice_reloads_current_backing_array():
    state = _SlotBoundary()
    parent = state.list_owner("parent")
    children = [state.leaf(str(i)) for i in range(11)]
    state.replace_items(parent, children)
    assert state.slice(parent, 0, 4) == (1, 4, 4)
    old = parent[ABI_CONSTANTS["object.list.items_offset"]]
    state.replace_items(parent, children)
    old["retired"] = True
    state.slot_reads.clear()
    assert state.slice(parent, 4, 100) == (1, -1, 7)
    assert all(base != id(old) for base, _ in state.slot_reads)
    assert state.increfs == {}  # Not a root/forwarding/lifetime guarantee.


def test_original_native_slot_contract_identity_and_layout():
    original = Path(__file__).with_name("fixtures") / "gc_major_slot_budget_contract.c"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == (
        "c31bb289601d70420bfe2082e9409570d4eb21ab511d6a5afac75c19e4a418cd"
    )
    assert ABI_CONSTANTS["object.type.list"] == 5
    assert ABI_CONSTANTS["object.header.type_tag_offset"] == 8
    assert ABI_CONSTANTS["object.list.length_offset"] == 16
    assert ABI_CONSTANTS["object.list.items_offset"] == 32
    assert ABI_CONSTANTS["object.list.size"] == 40
