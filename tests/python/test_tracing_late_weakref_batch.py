"""MODEL: PEP 442 late weakrefs must be batched before slot destruction.

Runs the production sweep, reachability recheck, weakref constructor/call and
batch bodies. Raw memory, root discovery, mark draining, user dispatch and
slot destruction are doubles; this does not prove native root discovery.
The shared WeakBatch's refcounts only balance argument-tuple bookkeeping;
count-free callback lifetime is checked through explicit lease-slot doubles,
not through the inherited GC0 accounting or actual stack/root registration.
"""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc0_weakref_batch import WeakBatch


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class LateWeakBatch(WeakBatch):
    def __init__(self, backend):
        super().__init__(backend)
        self.globals = {}
        self.nodes = {}
        self.roots = set()
        self.leases = {}
        self.cleared = []
        self.destroyed = []
        self.rechecks = 0
        self.finalizer = lambda obj: None
        self.finalized = set()
        self.ns.update(
            i64=int,
            global_addr=self.global_addr,
            global_load_ptr=self.global_load,
            pcc_gc_object_node_is_active=lambda node: int(
                self.load(node) not in self.destroyed),
            pcc_gc_object_node_is_address_pinned=lambda node, obj: 0,
            pcc_gc_forwarding_index_find=lambda obj: 0,
            pcc_gc_tracing_is_sweep_candidate=lambda obj: int(
                bool(self.load(obj, 12) & 1024)),
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            pcc_gc_foreign_lease_acquire=self.acquire,
            pcc_gc_foreign_lease_release=self.release,
            pcc_thread_world_stop_epoch_held=lambda: 7,
            pcc_platform_monotonic_us=lambda: 0,
            _record_sweep_phase=lambda *args: None,
            pcc_gc_seed_roots=self.seed_roots,
            pcc_gc_mark_root_gray_if_known=self.mark,
            pcc_gc_drain_all_gray_unlocked=lambda: 0,
            py_user_del_dispatch=self.finalize,
            pcc_gc_tracing_clear_unpinned=self.clear,
            pcc_gc_tracing_finalize_unpinned=self.destroyed.append,
            pcc_gc_scalar_sweep_try_enqueue=lambda obj, node: 0,
            abi_constant=lambda name: -1,  # No class objects in this model.
        )
        self.store(self.global_addr("pcc_gc_backend_selected"), 0, backend)
        self.store(self.global_addr("pcc_gc_backend4_no_refcounts"), 0, 1)
        self.store(self.global_addr("pcc_gc_tracing_finish_stop_epoch"), 0, 7)
        load_functions(RUNTIME / "py_weakref.py", {
            "py_weakref_invalidate_tracing_candidates",
        }, self.ns)
        load_functions(RUNTIME / "freestanding_gc_tracing_sweep_collector.py", {
            "pcc_gc_tracing_sweep_unreachable",
            "pcc_gc_tracing_recheck_reachability_after_finalizers",
        }, self.ns)

    def global_addr(self, name):
        if name not in self.globals:
            self.globals[name] = self.malloc(8)
        return self.globals[name]

    def global_load(self, name):
        if name == "py_None":
            return self.none
        if name == "py_weakref_head":
            return self.head
        return self.load(self.global_addr(name))

    def object(self):
        obj = self.allocate_object(64, 104, 1024 | 8)
        node = self.malloc(24)
        self.store(node, 0, obj)
        self.store(node, 16, self.global_load("pcc_gc_object_head"))
        self.store(self.global_addr("pcc_gc_object_head"), 0, node)
        self.nodes[obj] = node
        self.store(obj, 48, 777)
        return obj

    def acquire(self, slot):
        token = slot + 1
        self.leases[slot] = token
        return token

    def release(self, slot, token):
        assert self.leases.pop(slot) == token
        return 0

    def call(self, callback, args, kwargs):
        wr = self.tuples[args]
        assert any(self.load(slot) == wr for slot in self.leases)
        self.calls.append(wr)
        self.callbacks[callback](wr)
        return self.none

    def mark(self, obj):
        self.store(obj, 12, self.load(obj, 12) & ~8)

    def seed_roots(self):
        self.rechecks += 1
        for obj in self.roots:
            self.mark(obj)

    def finalize(self, obj):
        if obj in self.finalized:
            return
        self.finalized.add(obj)
        slot = self.global_addr("pcc_gc_user_finalizer_runs")
        self.store(slot, 0, self.load(slot) + 1)
        self.finalizer(obj)

    def clear(self, obj):
        # Real PASS1's generic invalidation: this would expose the old
        # sibling order if the collector omitted its second global batch.
        self.ns["py_weakref_invalidate"](obj)
        self.cleared.append(obj)
        self.store(obj, 48, 0)

    def weak(self, obj, callback=0):
        return self.ns["py_weakref_new"](obj, callback)

    def sweep(self, budget=100):
        return self.ns["pcc_gc_tracing_sweep_unreachable"](budget)


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_late_siblings_clear_before_callback_and_lease_survives_chain_mutation(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    live_target = model.allocate_object(64, 104, 0)
    refs = []
    replacement = []

    def finalize(obj):
        refs.append(model.weak(obj))
        refs.append(model.weak(obj, 0x8000))

    def callback(wr):
        assert all(model.ns["py_weakref_call"](ref) == model.none for ref in refs)
        assert model.load(target, 48) == 777
        replacement.append(model.weak(live_target))
        model.head = replacement[0]

    model.finalizer = finalize
    model.callbacks[0x8000] = callback
    assert model.sweep() == 1
    assert model.calls == [refs[1]] and not model.leases
    assert model.rechecks == 2 and model.destroyed == [target]
    assert model.load(replacement[0], 16) == live_target


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_finalizer_resurrection_keeps_new_refs_live_without_callbacks(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    refs = []

    def finalize(obj):
        refs.append(model.weak(obj, 0x8000))
        model.roots.add(obj)

    model.finalizer = finalize
    assert model.sweep() == 0
    assert model.ns["py_weakref_call"](refs[0]) == target
    assert model.calls == model.cleared == model.destroyed == []
    assert model.rechecks == 1 and not model.leases


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_late_batch_clears_other_candidate_refs_and_rechecks_callback_roots(backend):
    model = LateWeakBatch(backend)
    first, other = model.object(), model.object()
    refs = []

    def finalize(obj):
        refs.append(model.weak(obj, 0x8000 if obj == first else 0))

    def callback(wr):
        assert all(model.ns["py_weakref_call"](ref) == model.none for ref in refs)
        assert model.load(other, 48) == 777
        # Root publication is a primitive double, not a claim that a Python
        # callback's already-traced closure could hide a strong reference.
        model.roots.add(other)

    model.finalizer = finalize
    model.callbacks[0x8000] = callback
    assert model.sweep(budget=1) == 1
    assert model.destroyed == [first] and model.cleared == [first]
    assert model.load(other, 48) == 777 and model.rechecks == 2
    assert not model.leases


@pytest.mark.parametrize("flags", (1024, 524288))
def test_late_candidate_or_deallocating_weakref_is_cleared_silently(flags):
    model = LateWeakBatch(4)
    target = model.object()
    refs = []

    def finalize(obj):
        ref = model.weak(obj, 0x8000)
        model.store(ref, 12, flags)
        refs.append(ref)

    model.finalizer = finalize
    assert model.sweep() == 1
    assert model.load(refs[0], 16) == 0 and model.calls == []
    assert model.rechecks == 1 and not model.leases


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_no_finalizer_activity_does_not_repeat_batch_or_recheck(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    model.finalized.add(target)
    batches = []
    batch = model.ns["py_weakref_invalidate_tracing_candidates"]

    def record_batch():
        batches.append(True)
        return batch()

    model.ns["py_weakref_invalidate_tracing_candidates"] = record_batch
    assert model.sweep() == 1
    assert batches == [True] and model.rechecks == 0
