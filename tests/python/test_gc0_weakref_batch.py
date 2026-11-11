"""GC0 weakref callbacks use the fixed candidate batch and counted holds.

Actual weakref constructor, unlink, callback and GC0 batch bodies execute over
raw-memory/allocator primitives. The caller supplies its candidate predicate;
native lifecycle comparison separately verifies the complete collector.
Specification: docs/project-intent.md, weakrefs/finalizers/resurrection.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_weakref.py"


class WeakBatch:
    def __init__(self, backend=0):
        self.backend = backend
        self.words = {}
        self.head = 0
        self.none = 0xF000
        self.next = 0x10000
        self.candidates = set()
        self.callbacks = {}
        self.refcounts = {}
        self.tuples = {}
        self.events = []
        self.allocated = []  # Raw malloc allocations, not managed objects.
        self.raw_freed = []
        self.managed_allocated = []
        self.managed_freed = []
        self.tuple_allocated = []
        self.freed = []  # Both domains, also used by the stale-read poison.
        self.poison_freed = False
        self.malloc_calls = 0
        self.fail_at = 0
        self.fail_tracking = False
        self.tracked = []
        self.errors = []
        self.calls = []
        self.stopped = True
        ns = {name: getattr(abi, name) for name in dir(abi) if name.startswith("PY")}
        ns.update(
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i32=self.store, store_i64=self.store, store_ptr=self.store,
            global_load_ptr=lambda name: self.none if name == "py_None" else self.head,
            global_store_ptr=self.global_store,
            ptr_is_null=lambda value: int(value == 0), is_tagged_int=lambda value: value & 1,
            ptr_eq=lambda left, right: int(left == right), ptr_add=lambda base, offset: base + offset,
            null=lambda: 0, cstr=lambda text: text,
            malloc=self.malloc, free=self.free,
            stack_alloc=self.stack_alloc,
            pcc_gc_backend=lambda: self.backend,
            pcc_thread_owns_stopped_world=lambda: int(self.stopped),
            pcc_gc_backend0_is_weak_candidate=lambda obj: int(obj in self.candidates),
            pcc_platform_abort=lambda: (_ for _ in ()).throw(RuntimeError("abort")),
            pcc_gc_note_relocation_read=lambda obj: obj,
            pcc_gc_load_ptr=lambda owner, slot: self.load(slot),
            pcc_gc_store_ptr=self.store_owned,
            pcc_gc_alloc=self.allocate_object,
            pcc_gc_publish_initialized=lambda obj: self.events.append(("publish", obj)),
            pcc_gc_free_object_memory=self.free_object,
            pcc_diagnostics_runtime_log_event_code=lambda *args: None,
            py_incref=self.incref, py_decref=self.decref,
            py_gc_track=self.track,
            py_tuple_new=self.tuple_new, py_tuple_set_item=self.tuple_set,
            py_obj_call=self.call,
            py_clear_exception=lambda: self.events.append(("clear_error",)),
            # This batch model has no pending caller exception. The dedicated
            # callback-boundary model exercises preservation and nested errors.
            py_current_exception=lambda: 0,
            py_exc_new=lambda code, message: (code, message),
            py_raise_owned=self.errors.append,
        )
        names = {"_py_none", "_unlink", "py_weakref_new", "py_dealloc_weakref",
                 "_weakref_run_callback", "py_weakref_invalidate_gc0_candidates",
                 "py_weakref_clear_gc0_candidates",
                 "py_weakref_invalidate", "py_weakref_call"}
        ns["_weakref_target_is_base_object"] = lambda target: 0
        load_functions(SOURCE, names, ns)
        self.ns = ns

    def load(self, base, offset=0):
        assert not self.poison_freed or not any(
            address <= base + offset < address + 48
            for address in self.freed if address in self.refcounts
        ), (
            "read from freed weakref", base, offset,
        )
        if offset == 0 and base in self.refcounts:
            return self.refcounts[base]
        return self.words.get(base + offset, 0)

    def store(self, base, offset, value):
        self.words[base + offset] = value
        if offset == 0 and base in self.refcounts:
            self.refcounts[base] = value

    def global_store(self, name, value):
        assert name == "py_weakref_head"
        self.head = value
        self.events.append(("link", value))

    def malloc(self, size):
        self.malloc_calls += 1
        if self.malloc_calls == self.fail_at:
            return 0
        address = self.next
        self.next += size + 16
        self.allocated.append(address)
        return address

    def stack_alloc(self, size):
        address = self.next
        self.next += size + 16
        return address

    def free(self, address):
        assert address in self.allocated
        assert address not in self.freed
        self.raw_freed.append(address)
        self.freed.append(address)

    def allocate_object(self, size, tag, flags):
        address = self.next
        self.next += size + 16
        self.managed_allocated.append(address)
        self.refcounts[address] = 1
        self.words[address + 8] = tag
        self.words[address + 12] = flags
        return address

    def free_object(self, address):
        assert address in self.managed_allocated
        assert address not in self.freed
        self.managed_freed.append(address)
        self.freed.append(address)

    def incref(self, obj):
        if obj == 0 or obj == self.none or obj & 1:
            return
        self.refcounts[obj] = self.refcounts.get(obj, 1) + 1
        self.events.append(("retain", obj))

    def decref(self, obj):
        if obj == 0 or obj == self.none or obj & 1:
            return
        self.refcounts[obj] = self.refcounts.get(obj, 1) - 1
        self.events.append(("release", obj))
        assert self.refcounts[obj] >= 0
        if self.refcounts[obj] == 0:
            if obj in self.tuples:
                self.decref(self.tuples.pop(obj))
                self.free_object(obj)
            elif self.load(obj, 8) == abi.PY_TYPE_WEAKREF:
                self.ns["py_dealloc_weakref"](obj)

    def store_owned(self, owner, slot, value):
        self.incref(value)
        self.store(slot, 0, value)

    def tuple_new(self, length):
        assert length == 1
        address = self.allocate_object(32, abi.PY_TYPE_TUPLE, 0)
        self.tuple_allocated.append(address)
        self.tuples[address] = 0
        return address

    def tuple_set(self, args, index, value):
        assert index == 0
        self.incref(value)
        self.tuples[args] = value

    def call(self, callback, args, kwargs):
        wr = self.tuples[args]
        assert self.refcounts[wr] >= 2  # pending hold plus argument tuple
        assert wr not in self.freed
        self.calls.append(wr)
        self.callbacks.get(callback, lambda value: None)(wr)
        return self.none

    def track(self, wr):
        assert self.head != wr and self.load(wr, 32) == self.load(wr, 40) == 0
        self.tracked.append(wr)
        self.events.append(("track", wr))
        if not self.fail_tracking:
            self.store(wr, 12, self.load(wr, 12) | 2)

    def ref(self, target, callback=0, *, candidate=False, count=1, dying=False):
        wr = self.allocate_object(48, abi.PY_TYPE_WEAKREF, 524288 if dying else 0)
        self.refcounts[wr] = count
        self.store(wr, 16, target)
        self.store(wr, 24, callback)
        self.store(wr, 32, 0)
        self.store(wr, 40, self.head)
        if self.head:
            self.store(self.head, 32, wr)
        self.head = wr
        if candidate:
            self.candidates.add(wr)
        return wr

    def batch(self):
        return self.ns["py_weakref_invalidate_gc0_candidates"]()


def test_all_candidate_targets_clear_before_callback_and_dead_weakref_is_silent():
    model = WeakBatch()
    targets = (0x2000, 0x3000)
    model.candidates.update(targets)
    dead = model.ref(targets[0], 0x8000, candidate=True)
    live_a = model.ref(targets[0], 0x8100)
    live_b = model.ref(targets[1], 0x8200)
    refs = (dead, live_a, live_b)
    def observe(wr):
        assert all(model.load(item, 16) == 0 for item in refs)
    model.callbacks[0x8100] = model.callbacks[0x8200] = observe
    assert model.batch() == 2
    assert model.calls == [live_b, live_a]
    assert model.refcounts[dead] == model.refcounts[live_a] == model.refcounts[live_b] == 1
    assert not any(event == ("retain", dead) for event in model.events)
    assert model.batch() == 0 and model.calls == [live_b, live_a]


def test_counted_queue_holds_survive_callbacks_dropping_refs_and_mutating_chain():
    model = WeakBatch()
    target = 0x2000
    model.candidates.add(target)
    later = model.ref(target, 0x8000)
    first = model.ref(target, 0x8100)
    def drop_owners(wr):
        model.decref(later)
        model.decref(first)
        assert later not in model.freed and first not in model.freed
        model.head = 0
    model.callbacks[0x8100] = drop_owners
    assert model.batch() == 2
    assert model.calls == [first, later]
    assert model.refcounts[first] == model.refcounts[later] == 0
    assert first in model.freed and later in model.freed


@pytest.mark.parametrize('failure', [1, 2])
def test_queue_oom_rolls_back_every_hold_before_any_target_clear(failure):
    model = WeakBatch()
    target = 0x2000
    model.candidates.add(target)
    refs = [model.ref(target, 0x8000), model.ref(target, 0x8100)]
    model.fail_at = failure
    with pytest.raises(RuntimeError, match='abort'):
        model.batch()
    assert all(model.load(wr, 16) == target and model.refcounts[wr] == 1 for wr in refs)
    assert model.calls == []
    assert model.freed == model.allocated


def test_dead_and_callback_free_refs_clear_without_callback_holds():
    model = WeakBatch()
    target = 0x2000
    model.candidates.add(target)
    refs = [model.ref(target), model.ref(target, 0x8000, count=0),
            model.ref(target, 0x8100, dying=True)]
    assert model.batch() == 0
    assert all(model.load(wr, 16) == 0 for wr in refs)
    assert model.allocated == [] and model.calls == []


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_gc0_batch_leaves_other_collectors_untouched(backend):
    model = WeakBatch(backend)
    model.candidates.add(0x2000)
    wr = model.ref(0x2000, 0x8000)
    assert model.batch() == 0
    assert model.load(wr, 16) == 0x2000 and model.allocated == []


def test_gc0_batch_requires_stopped_world_before_touching_refs():
    model = WeakBatch()
    model.stopped = False
    with pytest.raises(RuntimeError, match='abort'):
        model.batch()
    assert model.allocated == []


def test_constructor_tracks_gc0_callback_owner_before_weak_chain_publication():
    model = WeakBatch()
    model.refcounts[0x8000] = 1
    result = model.ns['py_weakref_new'](0x2000, 0x8000)
    assert result != 0 and model.head == result and model.tracked == [result]
    assert model.events.index(('track', result)) < model.events.index(('link', result))
    assert model.load(result, 24) == 0x8000 and model.refcounts[0x8000] == 2


def test_constructor_tracking_failure_frees_private_ref_and_callback_hold():
    model = WeakBatch()
    previous = model.ref(0x3000)
    model.refcounts[0x8000] = 1
    model.fail_tracking = True
    assert model.ns['py_weakref_new'](0x2000, 0x8000) == 0
    failed = model.tracked[0]
    assert model.head == previous and failed in model.freed
    assert model.refcounts[0x8000] == 1
    assert model.errors == [(19, 'weak reference tracking failed')]
    assert ('link', failed) not in model.events


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_constructor_preserves_tracing_tracking_route(backend):
    model = WeakBatch(backend)
    result = model.ns['py_weakref_new'](0x2000, 0)
    assert result != 0 and model.head == result and model.tracked == []


def test_terminal_target_clears_all_refs_before_callback_without_a_cycle():
    model = WeakBatch()
    model.stopped = False  # Immediate deallocation does not require a GC stop.
    target = 0x2000
    unrelated_target = 0x3000
    older = model.ref(target, 0x8000)
    silent = model.ref(target)
    unrelated = model.ref(unrelated_target, 0x8200)
    newer = model.ref(target, 0x8100)

    def observe(wr):
        assert all(model.ns["py_weakref_call"](ref) == model.none
                   for ref in (older, silent, newer))
        assert model.load(unrelated, 16) == unrelated_target

    model.callbacks[0x8000] = model.callbacks[0x8100] = observe
    model.ns["py_weakref_invalidate"](target)
    assert model.calls == [newer, older]
    assert not model.candidates  # Immediate terminal death, not GC0's batch.
    refs = (older, silent, unrelated, newer)
    assert all(model.refcounts[ref] == 1 and ref not in model.freed for ref in refs)
    assert model.raw_freed == model.allocated
    assert len(model.allocated) == len(model.tuple_allocated) == 2
    assert model.managed_allocated == [*refs, *model.tuple_allocated]
    assert model.managed_freed == model.tuple_allocated
    assert not model.tuples
    assert model.freed == [
        model.tuple_allocated[0], model.allocated[0],
        model.tuple_allocated[1], model.allocated[1],
    ]
    events_before = list(model.events)
    freed_before = list(model.freed)
    model.ns["py_weakref_invalidate"](target)
    assert model.calls == [newer, older]
    assert model.events == events_before and model.freed == freed_before


def test_terminal_callback_can_drop_the_next_ref_and_replace_the_chain():
    model = WeakBatch()
    model.poison_freed = True
    target = 0x2000
    older = model.ref(target, 0x8000)
    newer = model.ref(target, 0x8100)
    replacement = []

    def drop_refs(wr):
        model.decref(older)
        model.decref(newer)
        assert older not in model.freed and newer not in model.freed
        replacement.append(model.ref(0x3000, 0x8200))
        # Reentrancy cannot deliver either callback again.
        model.ns["py_weakref_invalidate"](target)

    model.callbacks[0x8100] = drop_refs
    model.ns["py_weakref_invalidate"](target)
    assert model.calls == [newer, older]
    assert model.refcounts[older] == model.refcounts[newer] == 0
    assert older in model.freed and newer in model.freed
    assert model.head == replacement[0]
    assert model.load(replacement[0], 16) == 0x3000


@pytest.mark.parametrize("callback", (0, 0x8200))
def test_terminal_callback_can_free_the_next_unrelated_ref(callback):
    model = WeakBatch()
    model.poison_freed = True
    target = 0x2000
    older = model.ref(target, 0x8000)
    unrelated = model.ref(0x3000, callback)
    newer = model.ref(target, 0x8100)

    def drop_unrelated(wr):
        model.decref(unrelated)
        assert unrelated in model.freed

    model.callbacks[0x8100] = drop_unrelated
    model.ns["py_weakref_invalidate"](target)
    assert model.calls == [newer, older]
    assert model.load(newer, 40) == older
    assert model.refcounts[older] == model.refcounts[newer] == 1


@pytest.mark.parametrize("failure", (1, 2))
def test_terminal_queue_oom_preserves_targets_and_rolls_back_holds(failure):
    model = WeakBatch()
    target = 0x2000
    refs = [model.ref(target, 0x8000), model.ref(target, 0x8100)]
    model.fail_at = failure
    with pytest.raises(RuntimeError, match="abort"):
        model.ns["py_weakref_invalidate"](target)
    assert all(model.load(ref, 16) == target and model.refcounts[ref] == 1
               for ref in refs)
    assert model.calls == [] and model.freed == model.allocated


def test_terminal_target_clears_silent_and_dying_refs_without_holding_them():
    model = WeakBatch()
    target = 0x2000
    refs = [model.ref(target), model.ref(target, 0x8000, count=0),
            model.ref(target, 0x8100, dying=True)]
    model.ns["py_weakref_invalidate"](target)
    assert all(model.load(ref, 16) == 0 for ref in refs)
    assert model.calls == [] and model.allocated == []


@pytest.mark.parametrize("target", (0, 1))
def test_terminal_null_and_tagged_targets_leave_the_chain_untouched(target):
    model = WeakBatch()
    ref = model.ref(0x2000, 0x8000)
    model.ns["py_weakref_invalidate"](target)
    assert model.load(ref, 16) == 0x2000
    assert model.calls == [] and model.allocated == []


def test_gc0_silent_clear_never_queues_or_retains_callback_owners():
    model = WeakBatch()
    target = 0x2000
    model.candidates.add(target)
    refs = [model.ref(target, 0x8000), model.ref(target),
            model.ref(target, 0x8100, candidate=True),
            model.ref(target, 0x8200, count=0),
            model.ref(target, 0x8300, dying=True)]
    untouched = [model.ref(0x3000, 0x8400), model.ref(0), model.ref(1)]
    words_before = dict(model.words)
    counts_before = dict(model.refcounts)
    head_before = model.head
    next_before = model.next
    # A callback queue allocation would fail. This phase must not attempt one.
    model.fail_at = 1
    assert model.ns["py_weakref_clear_gc0_candidates"]() == len(refs)
    expected = dict(words_before)
    expected.update({wr + 16: 0 for wr in refs})
    assert model.words == expected
    assert model.head == head_before and model.refcounts == counts_before
    assert [model.load(wr, 16) for wr in untouched] == [0x3000, 0, 1]
    assert model.calls == model.events == model.allocated == model.freed == []
    assert model.malloc_calls == 0
    assert model.next == next_before
    assert model.ns["py_weakref_clear_gc0_candidates"]() == 0


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_gc0_silent_clear_does_not_change_tracing_weakrefs(backend):
    model = WeakBatch(backend)
    model.stopped = False
    model.candidates.add(0x2000)
    wr = model.ref(0x2000, 0x8000)
    before = dict(model.words)
    assert model.ns["py_weakref_clear_gc0_candidates"]() == 0
    assert model.words == before and model.load(wr, 16) == 0x2000
    assert model.calls == model.events == model.allocated == []


def test_gc0_silent_clear_requires_stopped_world_before_any_mutation():
    model = WeakBatch()
    model.stopped = False
    model.candidates.add(0x2000)
    model.ref(0x2000, 0x8000)
    before = dict(model.words)
    with pytest.raises(RuntimeError, match="abort"):
        model.ns["py_weakref_clear_gc0_candidates"]()
    assert model.words == before
    assert model.calls == model.events == model.allocated == []
