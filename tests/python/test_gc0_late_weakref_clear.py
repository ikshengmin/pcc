"""GC0 silently clears finalizer-created weakrefs before clearing any fields.

Reference: CPython v3.15.0 Python/gc.c clear_weakrefs and the second weakref
clear after handle_resurrected_objects, before delete_garbage (PEP 442).
Production collector, recompute, subset, finalizer and weakref bodies run via
runtime_body_model. Graph slot operations and allocation/deallocation are
primitives here; this model does not replace native lifecycle comparisons.
"""
from __future__ import annotations

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import (
    gc_state_initial_values,
    load_functions,
)
from tests.python.test_gc0_weakref_batch import SOURCE, WeakBatch


COLLECTOR = SOURCE.with_name("freestanding_gc_backend0_collector.py")
REACHABLE = 1
SUBSET = 4
FINALIZED_ZERO = 8
WEAK_CANDIDATE = 16


class LateWeakrefCollection(WeakBatch):
    def __init__(self):
        super().__init__()
        self.stopped = False
        self.global_slots = {}
        self.initial_globals = gc_state_initial_values()
        self.index = {}
        self.nodes = {}
        self.edges = {}
        self.empty_nodes = set()
        self.finalizers = {}
        self.cleared = []
        self.disposed = []
        self.late_dead = []
        self.recomputes = 0
        self.silent_passes = []
        self.global_store("pcc_gc_backend_selected", 0)
        # The graph supplies ordinary tracked nodes, not flag-mode discovery.
        self.global_store("pcc_gc_tracking_flag_mode", 0)
        self.ns.update(
            i64=int,
            global_addr=self.global_addr,
            global_load_ptr=self.global_load,
            global_store_ptr=self.global_store,
            atomic_load_i32=lambda base, offset, order: self.load(base, offset),
            abi_constant=lambda name: getattr(
                abi, "PY_TYPE_" + name.removeprefix("object.type.").upper()
            ),
            pcc_stop_the_world=self.stop,
            pcc_resume_world=self.resume,
            pcc_thread_safepoint=lambda: pytest.fail("unexpected STW retry"),
            pcc_platform_monotonic_us=lambda: 1,
            pcc_gc_record_collection_stw_pause=lambda start, end: None,
            pcc_gc_reclaim_retired_index_tables=lambda: 0,
            pcc_gc_default_table_lock=lambda: None,
            pcc_gc_default_table_unlock=lambda: None,
            pcc_gc_tracked_materialize=lambda: 0,
            pcc_gc_tracked_dematerialize=lambda owned: None,
            py_gc_index_find=lambda obj: self.index.get(obj, 0),
            py_gc_index_remove=lambda obj: self.index.pop(obj),
            pcc_gc_default_unlink_tracked_node=self.unlink_node,
            pcc_gc_backend0_visit_subtract=self.subtract,
            pcc_gc_backend0_mark_reachable=self.mark,
            pcc_gc_backend0_clear_referents=self.clear_referents,
            pcc_gc_visit_native_root_frames=lambda kind, flags: 0,
            pcc_gc_visit_walked_stack_roots=lambda kind, flags: 0,
            pcc_gc_trace_continuation_roots=lambda: 0,
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            py_user_del_dispatch=self.finalize,
            pcc_gc_note_object_freeing=lambda obj: None,
            _dealloc_unreachable=self.disposed.append,
        )
        load_functions(
            COLLECTOR,
            {"_collect", "_recompute_reachability",
             "pcc_gc_backend0_is_weak_candidate"},
            self.ns,
        )
        actual_recompute = self.ns["_recompute_reachability"]
        actual_clear = self.ns["py_weakref_clear_gc0_candidates"]

        def recompute():
            self.recomputes += 1
            self.events.append(("recompute", self.recomputes))
            actual_recompute()

        def silent_clear():
            assert self.stopped
            candidates = {
                obj for obj in self.index
                if self.ns["pcc_gc_backend0_is_weak_candidate"](obj)
            }
            before = (self.next, self.malloc_calls, len(self.calls), len(self.events))
            result = actual_clear()
            assert (self.next, self.malloc_calls, len(self.calls), len(self.events)) == before
            self.silent_passes.append((self.recomputes, candidates, result))
            self.events.append(("silent_clear", self.recomputes))
            return result

        self.ns["_recompute_reachability"] = recompute
        self.ns["py_weakref_clear_gc0_candidates"] = silent_clear

    def load(self, base, offset=0):
        assert base not in getattr(self, "empty_nodes", ()) or offset == 0, (
            "dereferenced an invalidated snapshot node", base, offset,
        )
        return super().load(base, offset)

    def global_addr(self, name):
        if name not in self.global_slots:
            self.global_slots[name] = self.stack_alloc(8)
            self.store(self.global_slots[name], 0, self.initial_globals.get(name, 0))
        return self.global_slots[name]

    def global_load(self, name):
        if name == "py_None":
            return self.none
        if name == "py_weakref_head":
            return self.head
        return self.load(self.global_addr(name))

    def global_store(self, name, value):
        if name == "py_weakref_head":
            super().global_store(name, value)
        else:
            self.store(self.global_addr(name), 0, value)

    def stop(self):
        assert not self.stopped
        self.stopped = True
        return 0

    def resume(self):
        assert self.stopped
        assert self.global_load("py_gc_collecting") == 0
        self.stopped = False
        return 0

    def add_node(self, obj, edges):
        assert obj not in self.index
        # Production tracking obtains a 40-byte heap node from its pool or
        # malloc, and the collector frees it after unlinking the dead object.
        node = self.malloc(40)
        self.index[obj] = self.nodes[obj] = node
        self.edges[obj] = list(edges)
        self.store(node, 0, obj)
        previous = 0
        cursor = self.global_load("py_gc_head")
        while cursor:
            previous = cursor
            cursor = self.load(cursor, 32)
        self.store(node, 24, previous)
        if previous:
            self.store(previous, 32, node)
        else:
            self.global_store("py_gc_head", node)
        self.global_store("py_gc_tracked_count", len(self.index))
        return node

    def target(self, *, finalizable=False, rooted=False):
        tag = abi.PY_TYPE_INSTANCE if finalizable else abi.PY_TYPE_LIST
        obj = self.allocate_object(48, tag, 2)
        self.refcounts[obj] = 2 if rooted else 1
        self.add_node(obj, [obj])
        return obj

    def track(self, wr):
        super().track(wr)
        if not self.fail_tracking:
            # The callback is not a tracked target in this graph. The weak
            # target slot never contributes a strong edge to reachability.
            self.add_node(wr, [])

    def in_scope(self, node):
        return not self.global_load("pcc_gc_backend0_subset_active") or (
            self.load(node, 16) & SUBSET
        )

    def subtract(self, obj):
        for child in self.edges[obj]:
            node = self.index.get(child, 0)
            if node and self.in_scope(node):
                self.store(node, 8, self.load(node, 8) - 1)

    def mark(self, obj):
        node = self.index.get(obj, 0)
        if not node or not self.in_scope(node) or self.load(node, 16) & REACHABLE:
            return
        self.store(node, 16, self.load(node, 16) | REACHABLE)
        for child in self.edges[obj]:
            self.mark(child)

    def finalize(self, obj):
        if obj in self.finalizers and not self.load(obj, 12) & 4:
            self.store(obj, 12, self.load(obj, 12) | 4)
            self.events.append(("finalizer", obj))
            self.finalizers[obj]()

    def unlink_node(self, node):
        previous, following = self.load(node, 24), self.load(node, 32)
        if previous:
            self.store(previous, 32, following)
        else:
            self.global_store("py_gc_head", following)
        if following:
            self.store(following, 24, previous)

    def remove_target_during_finalizer(self, obj):
        node = self.index.pop(obj)
        self.unlink_node(node)
        self.store(node, 0, 0)
        self.empty_nodes.add(node)
        self.refcounts[obj] = 0
        self.edges[obj] = []

    def clear_referents(self, obj):
        assert self.stopped
        assert all(self.load(wr, 16) == 0 for wr in self.late_dead), (
            "a late weakref survived until field clearing", obj,
        )
        assert self.load(self.nodes[obj], 16) & (FINALIZED_ZERO | WEAK_CANDIDATE) == 0
        self.events.append(("clear", obj))
        self.cleared.append(obj)
        for child in self.edges[obj]:
            self.refcounts[child] -= 1
            assert self.refcounts[child] >= 0
        self.edges[obj] = []

    def late_ref(self, target):
        callback = 0x8000
        self.callbacks[callback] = lambda wr: pytest.fail("late weakref callback ran")
        wr = self.ns["py_weakref_new"](target, callback)
        assert wr and self.head == wr
        return wr

    def collect(self, scope, candidates):
        if scope == "full":
            result = self.ns["_collect"](0, 0)
        else:
            pairs = self.stack_alloc(len(candidates) * 16)
            for index, obj in enumerate(candidates):
                self.store(pairs, index * 16, self.index[obj])
                self.store(pairs, index * 16 + 8, obj)
            result = self.ns["_collect"](pairs, len(candidates))
        assert not self.stopped
        assert self.global_load("py_gc_collecting") == 0
        assert self.global_load("pcc_gc_backend0_subset_active") == 0
        assert self.global_load("pcc_gc_backend0_subset_pairs") == 0
        for node in self.index.values():
            assert self.load(node, 16) & (SUBSET | FINALIZED_ZERO | WEAK_CANDIDATE) == 0
        return result


@pytest.mark.parametrize("scope", ("full", "subset"))
@pytest.mark.parametrize("creator", ("finalizer", "weak_callback"))
def test_late_links_clear_as_one_silent_cut_before_any_field_clear(scope, creator):
    model = LateWeakrefCollection()
    first = model.target(finalizable=creator == "finalizer")
    second = model.target()
    resurrected = model.target()
    outside = model.target(rooted=scope == "full")
    original = [first, second, resurrected]
    early = [model.ref(first, 0x8100), model.ref(second, 0x8200)]
    preserved = []
    new_targets = []

    def create_late_refs():
        assert all(model.load(wr, 16) == 0 for wr in early)
        assert model.cleared == [] and model.recomputes == 1
        model.refcounts[resurrected] += 1
        new = model.target()  # Unreachable, but absent from the disposal snapshot.
        new_targets.append(new)
        model.late_dead.extend(model.late_ref(obj) for obj in (first, second))
        preserved.extend((model.late_ref(obj), obj)
                         for obj in (resurrected, outside, new))

    def early_callback(wr):
        assert all(model.load(ref, 16) == 0 for ref in early)
        assert model.cleared == []
        model.events.append(("early", wr))
        if creator == "weak_callback" and wr == early[0]:
            create_late_refs()

    model.callbacks[0x8100] = model.callbacks[0x8200] = early_callback
    if creator == "finalizer":
        model.finalizers[first] = create_late_refs

    assert model.collect(scope, original) == 2
    assert model.calls == list(reversed(early))
    assert model.recomputes == 2
    assert model.silent_passes == [(1, set(original), 2), (2, {first, second}, 2)]
    assert model.cleared == model.disposed == [first, second]
    assert all(model.load(wr, 16) == target for wr, target in preserved)
    assert all(model.load(wr, 16) == 0 for wr in early + model.late_dead)
    assert new_targets[0] in model.index and resurrected in model.index
    late_clear = model.events.index(("silent_clear", 2))
    assert all(late_clear < model.events.index(("clear", obj)) for obj in (first, second))
    if creator == "finalizer":
        assert all(model.events.index(("early", wr)) < model.events.index(("finalizer", first))
                   for wr in early)


@pytest.mark.parametrize("scope", ("full", "subset"))
def test_finalizer_only_zero_count_remains_in_silent_cut_and_flags_are_cleared(scope):
    model = LateWeakrefCollection()
    target = model.target(finalizable=True)

    def finalize_to_zero():
        model.late_dead.append(model.late_ref(target))
        model.edges[target] = []
        model.refcounts[target] = 0

    model.finalizers[target] = finalize_to_zero
    assert model.collect(scope, [target]) == 1
    assert model.calls == []
    assert model.recomputes == 2
    assert model.silent_passes == [(1, {target}, 0), (2, {target}, 1)]
    assert model.cleared == model.disposed == [target]


@pytest.mark.parametrize("scope", ("full", "subset"))
def test_finalizer_invalidated_original_node_is_not_rearmed_or_dereferenced(scope):
    model = LateWeakrefCollection()
    first = model.target(finalizable=True)
    removed = model.target()

    def invalidate_original():
        model.remove_target_during_finalizer(removed)
        model.late_dead.append(model.late_ref(first))

    model.finalizers[first] = invalidate_original
    assert model.collect(scope, [first, removed]) == 1
    assert model.calls == []
    assert model.silent_passes == [(1, {first, removed}, 0), (2, {first}, 1)]
    assert model.cleared == model.disposed == [first]
    assert model.load(model.nodes[removed], 0) == 0


@pytest.mark.parametrize("scope", ("full", "subset"))
def test_no_user_callback_or_finalizer_keeps_one_recompute_and_one_weakref_pass(scope):
    model = LateWeakrefCollection()
    target = model.target()
    silent = model.ref(target)
    assert model.collect(scope, [target]) == 1
    assert model.calls == []
    assert model.recomputes == 1
    assert model.silent_passes == [(1, {target}, 1)]
    assert model.load(silent, 16) == 0
    assert model.cleared == model.disposed == [target]
