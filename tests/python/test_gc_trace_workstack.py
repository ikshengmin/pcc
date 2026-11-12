"""Production mark-workstack bodies with a controlled host memory boundary.

Raw memory, atomic instructions, object registration and slot enumeration are
modeled. The persistent gray queue is explicitly disabled at its extern
boundary, so these retain the object-list fallback/stack-overflow obligations.
The workstack, gray transitions and object-list walks execute the unchanged
production bodies. These are not persistent-queue, native execution, concurrent
scheduling, reclamation or Mac/QPS evidence.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest
from tests.python.runtime_body_model import load_functions as _load_production_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WHITE, GRAY, BLACK, DYING = 8, 16, 32, 524288
CAPACITY = 4096
COMMON_FUNCTIONS = {
    "pcc_gc_trace_mark_gray_if_known", "pcc_gc_trace_stack_push",
    "pcc_gc_trace_stack_begin", "pcc_gc_trace_stack_end",
    "pcc_gc_trace_stack_drain", "pcc_gc_trace_slot", "pcc_gc_trace_referents",
    "pcc_gc_drain_all_gray_unlocked", "pcc_gc_drain_all_gray_locked_slice",
}
COLOR_FUNCTIONS = {"pcc_gc_set_mark_color", "pcc_gc_shade_gray_atomic"}


def _load_bodies(filename, names, namespace):
    # Production bodies, their literal constants, and the documented
    # cross-object defaults (runtime_body_model) a model left unbound.
    _load_production_functions(RUNTIME / filename, set(names), namespace)


class _MarkBoundary:
    def __init__(self):
        self.objects = []
        self.nodes = []
        self.known = set()
        self.gray = 0
        self.gray_changes = []
        self.gray_worklist_events = []
        self.traces = Counter()
        self.increfs = Counter()
        self.peak = 0
        self.reads = 0
        self.forwardings = {}
        self.cells = {
            "pcc_gc_trace_stack": {},
            "pcc_gc_trace_stack_count": {0: 0},
            "pcc_gc_trace_stack_owner": {0: 0},
            "pcc_gc_list_container_mode": {0: 0},
            "pcc_gc_tracing_cycle_epoch": {0: 17},
            "pcc_gc_backend_selected": {0: 2},
            "pcc_gc_trace_cext_pending_epoch": {0: 0},
            "pcc_gc_trace_cext_pending_backend": {0: -1},
        }
        self.pointers = {"pcc_gc_object_head": None,
                         "pcc_gc_trace_cext_pending_obj": None}
        self.namespace = {
            "i64": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "global_addr": self.cells.__getitem__,
            "global_load_ptr": self.pointers.__getitem__,
            "global_store_ptr": self.pointers.__setitem__,
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": self.atomic_load, "atomic_cas_i32": self.cas,
            "null": lambda: None, "ptr_is_null": lambda obj: int(obj is None),
            "ptr_eq": lambda first, second: int(first is second),
            "is_tagged_int": lambda obj: int(isinstance(obj, int)),
            "pcc_gc_gray_count_increment_acq_rel": lambda: self.change_gray(1),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.change_gray(-1),
            "pcc_gc_gray_count_load_acquire": lambda: self.gray,
            # Empty persistent queue: preserve the original fallback oracle.
            # Record the new boundary without replacing the atomic shader.
            "pcc_gc_gray_worklist_note": lambda obj: self.gray_worklist_events.append(("note", obj)),
            "pcc_gc_gray_worklist_take_locked": self.take_disabled_gray_worklist,
            "pcc_gc_gray_worklist_flush": lambda: self.gray_worklist_events.append(("flush",)),
            "pcc_gc_gray_worklist_clear_stopped": lambda: self.gray_worklist_events.append(("clear",)),
            "pcc_gc_object_is_known_no_lock": self.is_known,
            "pcc_gc_object_node_is_active": lambda node: int(node[32] == 0),
            "pcc_gc_forwarding_index_find": lambda obj: self.forwardings.get(id(obj)),
            "pcc_gc_load_ptr": lambda owner, slot: slot[0],
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == 10001),
            "pcc_gc_visit_object_slots": self.visit,
            "py_incref": lambda obj: self.increfs.update([obj["name"]]),
        }
        _load_bodies("freestanding_gc_root_operations.py", COLOR_FUNCTIONS, self.namespace)
        _load_bodies("freestanding_gc_common_mark_cycle.py", COMMON_FUNCTIONS, self.namespace)

    def take_disabled_gray_worklist(self):
        self.gray_worklist_events.append(("take",))
        return None

    def call(self, suffix, *arguments):
        return self.namespace["pcc_gc_" + suffix](*arguments)

    @property
    def count(self):
        return self.cells["pcc_gc_trace_stack_count"][0]

    @property
    def owner(self):
        return self.cells["pcc_gc_trace_stack_owner"][0]

    def object(self, name, *, cext=False, flags=WHITE):
        obj = {8: 10001 if cext else 100, 12: flags,
               "name": name, "edges": [], "borrowed": []}
        node = {0: obj, 16: None, 32: 0}
        if self.nodes:
            self.nodes[-1][16] = node
        else:
            self.pointers["pcc_gc_object_head"] = node
        self.nodes.append(node)
        self.objects.append(obj)
        self.known.add(id(obj))
        if flags & GRAY:
            self.change_gray(1)
        return obj

    def load(self, pointer, offset):
        self.reads += 1
        assert self.reads < 1_000_000, "mark traversal did not terminate"
        assert not pointer.get("retired", False), "read a retired stack entry"
        return pointer[offset]

    def store(self, pointer, offset, value):
        assert not pointer.get("retired", False), "wrote a retired object"
        if pointer is self.cells["pcc_gc_trace_stack"]:
            assert offset % 8 == 0 and 0 <= offset < CAPACITY * 8
        pointer[offset] = value
        if pointer is self.cells["pcc_gc_trace_stack_count"]:
            assert 0 <= value <= CAPACITY
            self.peak = max(self.peak, value)

    def atomic_load(self, pointer, offset, order):
        assert order == "acquire"
        return self.load(pointer, offset)

    def cas(self, pointer, offset, expected, desired, order, fail_order):
        assert (order, fail_order) == ("acq_rel", "acquire")
        observed = self.load(pointer, offset)
        if observed == expected:
            self.store(pointer, offset, desired)
        return observed

    def change_gray(self, delta):
        self.gray += delta
        self.gray_changes.append(delta)
        assert self.gray >= 0, "gray work was retired more than once"

    def is_known(self, obj):
        return int(id(obj) in self.known and not (obj[12] & DYING))

    def visit(self, obj, visitor, context):
        assert not (obj[12] & DYING)
        assert obj[8] != 10001, "C-extension trace escaped its handoff boundary"
        self.traces[obj["name"]] += 1
        for role, children in ((1, obj["edges"]), (3, obj["borrowed"])):
            for child in children:
                visitor({0: child}, role, context)
        return 1

    def assert_finished(self, expected):
        assert self.gray == self.count == self.owner == 0
        assert self.traces == Counter(obj["name"] for obj in expected)
        assert all(obj[12] & 56 == BLACK for obj in expected)


@pytest.mark.parametrize("walk", ("drain_all_gray_unlocked", "drain_all_gray_locked_slice"))
def test_wide_graph_recovers_capacity_overflow_from_the_object_list(walk):
    memory = _MarkBoundary()
    # Every child precedes the parent in list order, so the overflow child
    # cannot be rescued by simply continuing the current object-list pass.
    children = [memory.object("child-" + str(index)) for index in range(CAPACITY + 1)]
    parent = memory.object("parent")
    parent["edges"] = children
    memory.call("trace_mark_gray_if_known", parent)
    assert memory.call(walk) == CAPACITY + 2
    assert memory.peak == CAPACITY
    memory.assert_finished([parent, *children])


def test_deep_reverse_order_chain_is_iterative_not_a_capacity_test():
    memory = _MarkBoundary()
    chain = [memory.object("node-" + str(index)) for index in range(2048)]
    for child, parent in zip(chain, chain[1:]):
        parent["edges"] = [child]
    memory.call("trace_mark_gray_if_known", chain[-1])
    assert memory.call("drain_all_gray_unlocked") == len(chain)
    assert memory.peak == 1
    memory.assert_finished(chain)


def test_duplicate_gray_entries_do_not_trace_or_retire_work_twice():
    memory = _MarkBoundary()
    child = memory.object("child")
    memory.call("trace_stack_begin")
    for _ in range(32):
        memory.call("trace_mark_gray_if_known", child)
    assert memory.count == 32 and memory.gray == 1
    assert memory.call("trace_stack_drain", 1) == 1
    assert memory.count == 31 and memory.gray == 0
    assert memory.call("trace_stack_drain", 1) == 0
    assert memory.gray_changes == [1, -1]
    memory.call("trace_stack_end")
    memory.assert_finished([child])


def test_cycle_and_diamond_edges_preserve_one_trace_per_object():
    memory = _MarkBoundary()
    leaf, left, right, root = [memory.object(name) for name in ("leaf", "left", "right", "root")]
    left["edges"], right["edges"] = [leaf], [leaf, root]
    root["edges"] = [left, right, root]
    memory.call("trace_mark_gray_if_known", root)
    assert memory.call("drain_all_gray_unlocked") == 4
    memory.assert_finished([leaf, left, right, root])


@pytest.mark.parametrize("budget", (-1, 0, 1))
def test_budget_leftovers_remain_gray_after_stack_end_and_are_revisited(budget):
    memory = _MarkBoundary()
    objects = [memory.object(str(index)) for index in range(3)]
    memory.call("trace_stack_begin")
    for obj in objects:
        memory.call("trace_mark_gray_if_known", obj)
    processed = memory.call("trace_stack_drain", budget)
    assert processed == max(budget, 0)
    assert memory.count == memory.gray == 3 - processed
    memory.call("trace_stack_end")
    assert memory.owner == memory.count == 0
    assert memory.gray == 3 - processed
    assert memory.call("drain_all_gray_unlocked") == 3 - processed
    memory.assert_finished(objects)


def test_push_requires_this_drain_owner_and_a_gray_object():
    memory = _MarkBoundary()
    white = memory.object("white")
    black = memory.object("black", flags=BLACK)
    gray = memory.object("gray", flags=GRAY)
    memory.call("trace_stack_push", gray)
    assert memory.count == 0 and memory.gray == 1
    memory.call("trace_stack_begin")
    memory.call("trace_stack_push", white)
    memory.call("trace_stack_push", black)
    assert memory.count == 0
    memory.call("trace_stack_push", gray)
    assert memory.count == 1
    memory.call("trace_stack_end")
    assert memory.owner == memory.count == 0 and memory.gray == 1
    assert (white[12], black[12], gray[12]) == (WHITE, BLACK, GRAY)


def test_cext_stack_entry_is_deferred_and_locked_handoff_ends_ownership():
    memory = _MarkBoundary()
    cext = memory.object("extension", cext=True)
    parent = memory.object("parent")
    parent["edges"] = [cext]
    memory.call("trace_mark_gray_if_known", parent)
    # Parent contributes one trace; the extension contributes one scheduled
    # handoff, not a trace or gray-count retirement.
    assert memory.call("drain_all_gray_locked_slice") == 2
    assert memory.pointers["pcc_gc_trace_cext_pending_obj"] is cext
    assert memory.cells["pcc_gc_trace_cext_pending_epoch"][0] == 17
    assert memory.cells["pcc_gc_trace_cext_pending_backend"][0] == 2
    assert memory.increfs == {"extension": 1}
    assert memory.traces == {"parent": 1}
    assert parent[12] & 56 == BLACK and cext[12] & 56 == GRAY
    assert memory.gray == 1 and memory.count == memory.owner == 0


def test_dying_stack_entry_is_not_traced_or_retired_by_the_stack():
    memory = _MarkBoundary()
    dying = memory.object("dying", flags=GRAY | DYING)
    memory.call("trace_stack_begin")
    memory.call("trace_stack_push", dying)
    assert memory.call("trace_stack_drain", 1) == 0
    assert memory.gray == 1 and not memory.traces
    assert dying[12] == GRAY | DYING
    memory.call("trace_stack_end")
    assert memory.count == memory.owner == 0


def test_end_prevents_stale_entries_from_surviving_into_the_next_drain():
    memory = _MarkBoundary()
    old = [memory.object("old-" + str(index)) for index in range(2)]
    memory.call("trace_stack_begin")
    for obj in old:
        memory.call("trace_mark_gray_if_known", obj)
    memory.call("trace_stack_end")
    # Model the permitted lifetime boundary between drains, not reclamation
    # itself. Any subsequent header read through an old stack slot must fail.
    for node, obj in zip(memory.nodes, old):
        node[32] = 1
        memory.known.remove(id(obj))
        obj["retired"] = True
        memory.change_gray(-1)
    fresh = memory.object("fresh")
    memory.call("trace_stack_begin")
    memory.call("trace_mark_gray_if_known", fresh)
    assert memory.call("trace_stack_drain", 10) == 1
    memory.call("trace_stack_end")
    memory.assert_finished([fresh])


def test_borrowed_slots_unknown_values_and_black_objects_add_no_gray_work():
    memory = _MarkBoundary()
    borrowed = memory.object("borrowed")
    black = memory.object("black", flags=BLACK)
    root = memory.object("root")
    root["borrowed"] = [borrowed]
    root["edges"] = [None, 7, {8: 100, 12: WHITE}, black]
    memory.call("trace_mark_gray_if_known", root)
    assert memory.call("drain_all_gray_unlocked") == 1
    memory.assert_finished([root])
    assert borrowed[12] == WHITE and black[12] == BLACK
