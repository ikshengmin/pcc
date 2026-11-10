"""MODEL: late weakrefs to final-unreachable targets clear without callbacks.

Runs the production sweep, reachability recheck, weakref constructor/call and
early-batch/late-clear bodies. CPython v3.15.0 Python/gc.c:1568-1574 is the
late-clear reference; docs/project-intent.md requires weakref/resurrection
semantics. Raw memory, root discovery, mark draining, user dispatch and
slot destruction are doubles; this does not prove native root discovery.
The shared WeakBatch's refcounts only balance argument-tuple bookkeeping;
early count-free callback lifetime is checked through explicit lease-slot
doubles, not inherited GC0 accounting or actual stack/root registration.
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
        self.lease_events = []
        self.silent_clears = []
        self.cleared = []
        self.destroyed = []
        self.rechecks = 0
        self.clear_hook = lambda obj: None
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
            "py_weakref_clear_tracing_candidates",
        }, self.ns)
        self.silent_clear_body = self.ns["py_weakref_clear_tracing_candidates"]
        self.ns["py_weakref_clear_tracing_candidates"] = self.clear_late_refs
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

    def object(self, flags=1024 | 8):
        obj = self.allocate_object(64, 104, flags)
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
        self.lease_events.append(("acquire", slot))
        return token

    def release(self, slot, token):
        assert self.leases.pop(slot) == token
        self.lease_events.append(("release", slot))
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
        self.clear_hook(obj)
        # Real PASS1's generic invalidation must find the late targets already
        # detached, or it would incorrectly deliver their callbacks here.
        self.ns["py_weakref_invalidate"](obj)
        self.cleared.append(obj)
        self.store(obj, 48, 0)

    def clear_late_refs(self):
        # Instrument the real helper, not a copied clear algorithm. Even a
        # transient lease/heap allocation or callback violates this boundary.
        before = (self.next, self.malloc_calls, list(self.lease_events),
                  list(self.calls), dict(self.refcounts), dict(self.leases))
        count = self.silent_clear_body()
        assert before == (self.next, self.malloc_calls, self.lease_events,
                          self.calls, self.refcounts, self.leases)
        self.silent_clears.append(count)
        return count

    def weak(self, obj, callback=0):
        return self.ns["py_weakref_new"](obj, callback)

    def sweep(self, budget=100):
        return self.ns["pcc_gc_tracing_sweep_unreachable"](budget)


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_late_siblings_clear_silently_before_slot_destruction(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    refs = []

    def finalize(obj):
        refs.append(model.weak(obj))
        refs.append(model.weak(obj, 0x8000))

    def before_clear(obj):
        assert obj == target
        assert all(model.ns["py_weakref_call"](ref) == model.none for ref in refs)
        assert model.load(target, 48) == 777

    model.finalizer = finalize
    model.clear_hook = before_clear
    assert model.sweep() == 1
    assert model.calls == model.lease_events == [] and not model.leases
    assert model.silent_clears == [2]
    assert model.rechecks == 1 and model.destroyed == [target]
    assert model.load(refs[1], 24) == 0x8000


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_early_batch_keeps_callback_queue_and_leases_across_chain_mutation(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    model.finalized.add(target)
    live_target = model.allocate_object(64, 104, 0)
    refs = [model.weak(target), model.weak(target, 0x8000),
            model.weak(target, 0x8002)]
    replacement = []

    def first_callback(wr):
        assert wr == refs[2]
        assert all(model.ns["py_weakref_call"](ref) == model.none for ref in refs)
        assert model.load(target, 48) == 777
        replacement.append(model.weak(live_target))
        model.head = replacement[0]

    model.callbacks[0x8002] = first_callback
    model.callbacks[0x8000] = lambda wr: None
    assert model.sweep() == 1
    assert model.calls == [refs[2], refs[1]] and not model.leases
    assert [event[0] for event in model.lease_events] == [
        "acquire", "acquire", "release", "release",
    ]
    assert model.rechecks == 1 and model.silent_clears == []
    assert model.destroyed == [target]
    assert model.load(replacement[0], 16) == live_target


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_resurrection_preserves_late_ref_until_a_later_genuine_death(backend):
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
    assert model.rechecks == 1 and model.silent_clears == [0]
    assert not model.leases

    def callback(wr):
        assert wr == refs[0] and model.ns["py_weakref_call"](wr) == model.none
        assert model.load(target, 48) == 777

    model.callbacks[0x8000] = callback
    model.roots.remove(target)
    # A later completed mark, not the old finalizer pass, owns this death.
    model.store(target, 12, model.load(target, 12) | 1024 | 8)
    assert model.sweep() == 1
    assert model.calls == [refs[0]] and model.destroyed == [target]
    assert model.rechecks == 2 and model.silent_clears == [0]
    assert len(refs) == 1 and not model.leases


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_late_clear_covers_all_candidates_before_bounded_slices(backend):
    model = LateWeakBatch(backend)
    first, other = model.object(), model.object()
    refs = []

    def finalize(obj):
        refs.append(model.weak(obj, 0x8000))

    def before_clear(obj):
        assert all(model.ns["py_weakref_call"](ref) == model.none for ref in refs)
        assert model.load(obj, 48) == 777

    model.finalizer = finalize
    model.clear_hook = before_clear
    assert model.sweep(budget=1) == 1
    assert model.destroyed == model.cleared == [other]
    assert model.load(first, 48) == 777 and model.rechecks == 1
    assert model.silent_clears == [2] and model.calls == []
    assert model.sweep(budget=1) == 1
    assert model.sweep(budget=1) == 0
    assert model.destroyed == model.cleared == [other, first]
    assert model.rechecks == 1 and model.silent_clears == [2]
    assert model.calls == model.lease_events == [] and not model.leases


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("flags", (0, 1024, 524288))
def test_late_live_candidate_or_deallocating_weakref_is_silent(backend, flags):
    model = LateWeakBatch(backend)
    target = model.object()
    refs = []

    def finalize(obj):
        ref = model.weak(obj, 0x8000)
        model.store(ref, 12, flags)
        refs.append(ref)

    model.finalizer = finalize
    assert model.sweep() == 1
    assert model.load(refs[0], 16) == 0 and model.calls == []
    assert model.rechecks == 1 and model.silent_clears == [1]
    assert model.lease_events == [] and not model.leases


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_late_clear_preserves_outside_and_finalizer_created_targets(backend):
    model = LateWeakBatch(backend)
    target = model.object()
    outside = model.object(flags=0)
    created = []
    refs = []

    def finalize(obj):
        assert obj == target
        created.append(model.object(flags=0))
        refs.extend(model.weak(item, 0x8000) for item in (obj, outside, created[0]))

    model.finalizer = finalize
    assert model.sweep() == 1
    assert [model.load(ref, 16) for ref in refs] == [0, outside, created[0]]
    assert model.load(outside, 48) == model.load(created[0], 48) == 777
    assert model.destroyed == model.cleared == [target]
    assert model.rechecks == 1 and model.silent_clears == [1]
    assert model.calls == model.lease_events == []


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
    assert batches == [True] and model.silent_clears == []
    assert model.rechecks == 0
