"""Cleared LIST ownership across the producer/raw-payload/worker boundary.

Specification: docs/project-intent.md, five-GC lifetime equality. These execute
the production queue and tracking bodies over raw cells; the native companion
executes real public collection and bounded PASS0/PASS1/PASS2 on populated lists.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_cleared_gen_sweep import GeneratorRuntime, TRACKED
from tests.python.test_gc_deferred_free import RUNTIME, _load
from tests.python.test_gc_scalar_sweep import ScalarRuntime

LIST = 5


class ListRuntime(GeneratorRuntime):
    def __init__(self, backend=1, *, tracking="flags"):
        super().__init__(backend, merged=tracking == "merged")
        self.tracking = tracking
        self.items_owners = {}
        self.define_global("py_gc_tracked_count", 0)
        self.ns["_flag_only_tracking"] = lambda: int(tracking == "flags")
        self.ns["atomic_rmw_i32"] = self.tracking_bits

    def list(self, capacity=4, *, tracked=True):
        obj, node = self.generator(tracked=tracked)
        if self.tracking == "flags":
            self.tracked_nodes.pop(obj, None)
        self.write(obj, 8, LIST)
        self.write(obj, 16, 0)
        self.write(obj, 24, capacity)
        items = self.alloc(capacity * 8)
        self.managed.remove(items)
        self.items_owners[items] = obj
        self.write(obj, 32, items)
        self.write(self.global_address("py_gc_tracked_count"), 0, int(tracked))
        return obj, node, items

    def tracking_bits(self, operation, obj, offset, value, order):
        if operation == "sub":
            assert obj == self.global_address("py_gc_tracked_count")
            return self.rmw(operation, obj, offset, value, order)
        return super().tracking_bits(operation, obj, offset, value, order)

    def free(self, value):
        if value in self.items_owners:
            obj = self.items_owners[value]
            assert self.graph == self.tracked_lock == 0
            assert self.world == 1 and self.thread == 1 and self.sc(0) == 1
            assert self.read(obj, 16) == self.read(obj, 24) == self.read(obj, 32) == 0
            assert not self.read(obj, 12) & TRACKED and obj not in self.tracked_nodes
            assert self.read(self.nodes[obj], 32) == -self.sc(11)
            assert obj in self.granules and obj in self.noted
            assert self.sc(4) == self.sc(5) == 0
        return super().free(value)


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("tracking", ("flags", "nodes", "merged"))
@pytest.mark.parametrize("tracked", (False, True))
@pytest.mark.parametrize("capacity", (4, 17, 1024))
def test_list_claim_releases_items_before_seal_and_only_worker_retires_shell(
    backend, tracking, tracked, capacity,
):
    rt = ListRuntime(backend, tracking=tracking)
    obj, node, items = rt.list(capacity, tracked=tracked)
    gc_node = rt.tracked_nodes.get(obj)
    rt.begin()
    # GC3 joined GC1/GC2's mutator protocol (03cbd1849): its cleared lists
    # are claimed by the same scalar sweep; only GC4 relocates.
    admitted = int(backend in (1, 2, 3))
    assert rt.enqueue((obj, node)) == admitted
    if admitted:
        assert rt.freed == {items} and rt.read(obj, 0) == 731
        assert rt.live_bytes == 0 and rt.live_usable == 64
        assert rt.read(node, 32) == -rt.sc(11) and rt.noted == [obj]
        assert obj in rt.granules and rt.nodes[obj] == node
        assert rt.sc(1) == 1 and rt.sc(2) == 64
        if tracking == "flags":
            assert rt.scalar("py_gc_tracked_count") == 0
        if tracked and tracking == "merged":
            assert rt.scalar("pcc_gc_deferred_node_free_head") == gc_node
    else:
        assert not rt.freed and not rt.noted and not rt.tracking_events
        assert rt.read(obj, 24) == capacity and rt.read(obj, 32) == items
        assert bool(rt.read(obj, 12) & TRACKED) == tracked
    rt.seal()
    assert rt.drain() == admitted
    assert rt.sc(9) == rt.sc(1) == rt.sc(2) == 0
    assert (obj in rt.freed) == bool(admitted)


@pytest.mark.parametrize("reason", (
    "positive-length", "negative-length", "zero-capacity", "small-capacity",
    "negative-capacity", "large-capacity", "null-items", "terminal-shape",
    "other-tag", "pin", "cycle-pin", "lease", "static", "gray", "black",
    "fresh", "minor", "zpage", "immortal", "deallocating", "identity",
    "provenance", "raw", "full-count", "full-bytes", "wrong-node", "owned",
))
def test_rejected_list_keeps_payload_tracking_and_fallback_owner_untouched(reason):
    rt = ListRuntime()
    obj, node, items = rt.list()
    if reason.endswith("length"):
        rt.write(obj, 16, 1 if reason == "positive-length" else -1)
    capacities = {"zero-capacity": 0, "small-capacity": 3,
                  "negative-capacity": -1, "large-capacity": 134217729}
    if reason in capacities:
        rt.write(obj, 24, capacities[reason])
    if reason in ("null-items", "terminal-shape"):
        rt.write(obj, 32, 0)
    if reason == "terminal-shape": rt.write(obj, 24, 0)
    if reason == "other-tag": rt.write(obj, 8, 10001)
    bits = {"pin": 64, "gray": 16, "black": 32, "fresh": 16384,
            "minor": 4096, "zpage": 65536, "immortal": 1, "deallocating": 524288}
    if reason in bits: rt.write(obj, 12, rt.read(obj, 12) | bits[reason])
    if reason == "cycle-pin": rt.write(node, 56, 1)
    if reason == "lease": rt.write(node, 80, 1)
    if reason == "static": rt.static.add(obj)
    if reason == "identity": rt.identities.add(obj)
    if reason == "provenance": rt.provenance.add(obj)
    if reason == "raw": rt.granules.remove(obj)
    if reason == "owned": rt.write(node, 32, 1)
    rt.begin()
    if reason == "full-count": rt.write(rt.global_address("pcc_gc_scalar_sweep_count"), 0, 65536)
    if reason == "full-bytes": rt.write(rt.global_address("pcc_gc_scalar_sweep_bytes"), 0, 4194304)
    before = dict(rt.memory[obj]), dict(rt.memory[node]), rt.live_bytes, rt.live_usable
    assert rt.enqueue((obj, node if reason != "wrong-node" else 123456)) == 0
    assert before == (dict(rt.memory[obj]), dict(rt.memory[node]), rt.live_bytes, rt.live_usable)
    assert not rt.freed and not rt.noted and not rt.tracking_events


def test_list_detaches_only_after_locked_recheck_and_final_capacity_admission():
    for change in ("length", "capacity", "items", "byte-limit"):
        rt = ListRuntime()
        obj, node, items = rt.list()
        rt.begin()
        before = {}
        def lock():
            rt.lock()
            if change == "length": rt.write(obj, 16, 1)
            if change == "capacity": rt.write(obj, 24, 0)
            if change == "items": rt.write(obj, 32, 0)
            if change == "byte-limit":
                rt.write(rt.global_address("pcc_gc_scalar_sweep_bytes"), 0, 4194304 - 32)
            before.update(rt.memory[obj])
        rt.ns["pcc_py_gc_minor_graph_lock"] = lock
        assert rt.enqueue((obj, node)) == 0
        assert rt.memory[obj] == before and rt.read(node, 32) == 0
        assert not rt.freed and not rt.noted and rt.live_bytes == 64


def test_building_blocks_worker_between_detach_untrack_and_items_free():
    rt = ListRuntime(tracking="nodes")
    obj, node, items = rt.list()
    rt.begin()
    observed = []
    def try_worker(stage):
        assert rt.sc(0) == 1 and rt.read(obj, 32) == 0
        assert items not in rt.freed and obj not in rt.freed
        observed.append(stage)
        rt.thread = 2
        assert rt.scalar_call("drain", 1, 0, 0) == 0
        rt.thread = 1
    def unlocked():
        if rt.sc(3) == 1 and rt.read(obj, 12) & TRACKED:
            try_worker("detached")
    rt.unlock_hook = unlocked
    rt.free_hook = lambda value: try_worker("untracked") if value == items else None
    assert rt.enqueue((obj, node)) == 1
    assert observed == ["detached", "untracked"]
    rt.unlock_hook = rt.free_hook = None
    rt.seal()
    assert rt.drain() == 1 and rt.freed == {obj, items}


@pytest.mark.parametrize("change", ("length", "capacity", "items", "tracked", "tag", "granule"))
def test_consumer_rechecks_zero_shell_and_quarantines_without_releasing_again(change):
    rt = ListRuntime()
    obj, node, items = rt.list()
    rt.begin()
    assert rt.enqueue((obj, node)) == 1
    rt.seal()
    if change in ("length", "capacity", "items"):
        rt.write(obj, {"length": 16, "capacity": 24, "items": 32}[change], 1234)
    if change == "tracked": rt.write(obj, 12, rt.read(obj, 12) | TRACKED)
    if change == "granule": rt.retirement_failures.add(obj)
    if change == "tag":
        rt.write(obj, 8, 10001)
        def checked_load(base, offset):
            assert base != obj or offset not in (16, 24, 32), "retagged LIST payload read"
            return rt.read(base, offset)
        rt.ns["load_ptr"] = rt.ns["load_i64"] = checked_load
    assert rt.drain() == 0
    assert rt.sc(0) == 4 and rt.sc(1) == rt.sc(9) == 1 and rt.sc(2) == 64
    assert rt.nodes[obj] == node and rt.read(node, 32) == -rt.sc(11)
    assert rt.read(obj, 0) == 731 and rt.freed == {items}


def test_real_list_pass1_clears_owned_slots_but_only_successful_claim_frees_items():
    rt = ListRuntime()
    obj, node, items = rt.list()
    rt.write(obj, 16, 2)
    rt.write(items, 0, obj)
    rt.write(items, 8, 67890)
    rt.ns.update({
        "PY_TYPE_LIST": LIST, "PY_TYPE_TUPLE": 7, "PY_TYPE_DICT": 6,
        "PY_TYPE_SET": 8, "PY_TYPE_VTHREAD_CHANNEL": 57,
        "py_decref": lambda child: None, "py_weakref_invalidate": lambda value: None,
        "pcc_gc_object_is_address_pinned": lambda value: 0,
        "pcc_gc_tracing_is_sweep_candidate": lambda child: int(child == obj),
    })
    def visit(value, visitor, context):
        for offset in range(0, rt.read(value, 16) * 8, 8):
            # The raw model addresses each slot independently for production's
            # load/store-at-zero callback, without changing its role contract.
            slot = rt.next_address
            rt.next_address += 64
            rt.memory[slot] = {0: rt.read(items, offset)}
            visitor(slot, 1, context)
            rt.write(items, offset, rt.read(slot, 0))
        return 1
    rt.ns["pcc_gc_visit_object_slots"] = visit
    _load(RUNTIME / "freestanding_gc_sweep_slots.py", rt.ns, {
        "pcc_gc_tracing_clear_slot", "pcc_gc_clear_container_metadata",
        "pcc_gc_tracing_clear_referents", "pcc_gc_tracing_clear_unreachable",
        "pcc_gc_tracing_clear_unpinned",
    })
    rt.begin()
    assert rt.enqueue((obj, node)) == 0 and not rt.freed
    rt.ns["pcc_gc_tracing_clear_unreachable"](obj)
    assert rt.read(obj, 16) == rt.read(items, 0) == rt.read(items, 8) == 0
    assert rt.read(obj, 24) == 4 and rt.read(obj, 32) == items and not rt.freed
    assert rt.enqueue((obj, node)) == 1 and rt.freed == {items}
    rt.seal()
    assert rt.drain() == 1


@pytest.mark.parametrize("capacity", (4, 17, 1024))
def test_claim_payload_tail_matches_real_empty_list_deallocator(capacity):
    ordinary = ListRuntime()
    obj, node, items = ordinary.list(capacity, tracked=False)
    terminal_calls = []
    def unexpected_child_release(*args):
        raise AssertionError("a post-PASS1 list must not release children again")
    ordinary.ns.update({
        "PYLISTOBJECT_LENGTH_OFFSET": 16, "PYLISTOBJECT_CAPACITY_OFFSET": 24,
        "PYLISTOBJECT_ITEMS_OFFSET": 32, "pcc_gc_backend": lambda: 1,
        "free": lambda value: ScalarRuntime.free(ordinary, value),
        "pcc_gc_free_object_memory": lambda value: terminal_calls.append(value),
        "pcc_gc_release_nonterminal_tail": unexpected_child_release,
        "py_decref": unexpected_child_release,
    })
    _load(RUNTIME / "py_obj_dealloc.py", ordinary.ns, {"py_dealloc_list"})
    ordinary.ns["py_dealloc_list"](obj)
    assert terminal_calls == [obj] and ordinary.freed == {items}
    expected = tuple(ordinary.read(obj, offset) for offset in (16, 24, 32))
    assert expected == (0, 0, 0)
    queued = ListRuntime()
    target, target_node, target_items = queued.list(capacity, tracked=False)
    queued.begin()
    assert queued.enqueue((target, target_node)) == 1
    assert tuple(queued.read(target, offset) for offset in (16, 24, 32)) == expected
    assert queued.freed == {target_items} and queued.read(target, 0) == 731
    assert queued.nodes[target] == target_node and target in queued.granules
