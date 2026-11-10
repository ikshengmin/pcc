"""Source-body witnesses for pending exception ownership and reentrant disposal.

Run the actual callback and clear bodies against explicit logical ownership,
checked-registration, conservative-root, TLS-swap and allocation doubles. These
are not executions of the collector, frame registry, native stack discovery or
count-free reference counting. Relocation is injected only outside no-park
registration, at guard exit or TLS retirement. Native qualification is separate.
"""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
OUTER, CALLBACK_ERROR, MIDDLE, INNER_ERROR = 1000, 2000, 3000, 4000
WEAKREF, NESTED_WEAKREF, RESULT, MOVED = 5000, 6000, 7000, 10000
TERMINAL = 524288


class CallbackBoundaryModel:
    def __init__(self, backend, *, pending=True, conservative=False):
        self.backend = backend  # Scenario label, not collector execution.
        self.conservative = conservative
        self.pending = OUTER if pending else 0
        self.expected_outer = OUTER
        self.pending_threads = int(pending)
        self.refcounts = {OUTER: int(pending)}
        self.memory = {}
        self.exception_objects = {OUTER, CALLBACK_ERROR, MIDDLE, INNER_ERROR, MOVED}
        self.scratch = 100000
        self.stack_slots, self.frames, self.registrations, self.swaps = [], [], [], []
        self.no_park = 0
        self.registration_status = 1
        self.after_guard = None
        self.after_swap = None
        self.on_tuple = None
        self.tuples = {}
        self.calls, self.released = [], []
        self.on_callback = lambda ref: 0
        self.on_drop = None
        self.fail_tuple = False
        self.aborted = 0
        self.store(WEAKREF, 24, 8000)
        self.store(NESTED_WEAKREF, 24, 9000)
        self.ns = dict(
            stack_alloc=self.stack_alloc,
            ptr_add=lambda base, offset: base + offset,
            ptr_is_null=lambda value: int(value == 0), null=lambda: 0,
            load_ptr=self.load, store_ptr=self.store,
            global_addr=lambda name: name,
            pcc_gc_load_ptr=lambda owner, slot: self.load(slot),
            py_current_exception=lambda: self.pending,
            _resolve_current_exception=lambda: self.pending,
            _type_of=lambda obj: 104,
            py_tls_exc_swap_slot=self.swap_exception_slot,
            pcc_gc_stack_slot_root_is_conservative=self.is_conservative,
            pcc_thread_no_park_enter=self.no_park_enter,
            pcc_thread_no_park_exit=self.no_park_exit,
            pcc_gc_note_frame_enter_local_checked=self.frame_enter,
            pcc_gc_note_frame_leave_local=self.frame_leave,
            pcc_gc_store_root=self.store_root,
            py_decref=self.decref,
            py_tuple_new=self.tuple_new, py_tuple_set_item=self.tuple_set,
            py_obj_call=self.call, _py_none=lambda: 0,
            pcc_diagnostics_runtime_log_event_code=lambda *args: None,
            pcc_platform_abort=self.abort,
        )
        load_functions(RUNTIME / "py_weakref.py", {"_weakref_run_callback"}, self.ns)
        load_functions(RUNTIME / "py_exc_tls.py", {"py_clear_exception"}, self.ns)

    def load(self, base, offset=0):
        return self.memory.get(base + offset, 0)

    def store(self, base, offset, value):
        self.memory[base + offset] = value

    def stack_alloc(self, size):
        slot = self.scratch
        self.scratch += size + 16
        self.stack_slots.append(slot)
        return slot

    def is_conservative(self, slot):
        assert slot in self.stack_slots and self.load(slot) == 0
        return int(self.conservative)

    def protected(self, slot):
        return slot in self.frames or (self.conservative and slot in self.stack_slots)

    def rooted(self, obj):
        return self.pending == obj or any(
            self.protected(slot) and self.load(slot) == obj for slot in self.stack_slots
        )

    def no_park_enter(self):
        self.no_park += 1

    def no_park_exit(self):
        assert self.no_park == 1
        self.no_park -= 1
        if self.after_guard:
            hook, self.after_guard = self.after_guard, None
            hook()

    def frame_enter(self, frame_map, slot):
        assert frame_map in ("pcc_raise_owned_frame_map", "pcc_weakref_callback_exception_map")
        assert self.no_park == 1 and self.load(slot) == 0
        assert slot not in self.frames and not self.conservative
        self.registrations.append(slot)
        if self.registration_status == 1:
            self.frames.append(slot)
        return self.registration_status

    def set_pending(self, value):
        self.pending_threads += int(bool(value)) - int(bool(self.pending))
        self.pending = value

    def swap_exception_slot(self, slot):
        assert self.no_park == 0 and self.protected(slot)
        incoming = self.load(slot)
        self.store(slot, 0, self.pending)
        self.set_pending(incoming)
        self.swaps.append(slot)
        if self.after_swap:
            hook, self.after_swap = self.after_swap, None
            hook(slot)

    def ref(self, obj, delta):
        if obj:
            if delta > 0:
                assert not self.load(obj, 12) & TERMINAL, "retained terminal exception"
            self.refcounts[obj] = self.refcounts.get(obj, 0) + delta
            assert self.refcounts[obj] >= 0

    def raise_owned(self, obj):
        self.ns["py_clear_exception"]()
        self.ref(obj, 1)
        self.set_pending(obj)

    def decref(self, obj):
        assert self.no_park == 0
        if not obj:
            return
        self.released.append(obj)
        self.ref(obj, -1)
        if self.refcounts[obj] == 0:
            if obj in self.exception_objects:
                self.store(obj, 12, self.load(obj, 12) | TERMINAL)
            self.tuples.pop(obj, None)
            if self.on_drop:
                self.on_drop(obj)

    def store_root(self, slot, value):
        assert self.no_park == 0 and self.protected(slot) and value == 0
        assert self.pending == 0 and self.pending_threads == 0
        old = self.load(slot)
        self.store(slot, 0, 0)
        self.decref(old)

    def frame_leave(self, slot):
        assert self.no_park == 0 and self.frames[-1] == slot
        assert self.load(slot) == 0
        self.frames.pop()

    def relocate(self, old, new):
        assert self.no_park == 0 and not self.conservative and self.rooted(old)
        self.refcounts[new] = self.refcounts.pop(old)
        self.store(new, 12, self.load(old, 12))
        if self.pending == old:
            self.set_pending(new)
        for slot in self.frames:
            if self.load(slot) == old:
                self.store(slot, 0, new)
        if self.expected_outer == old:
            self.expected_outer = new

    def tuple_new(self, size):
        assert self.no_park == 0 and size == 1 and self.pending == 0
        if self.on_tuple:
            self.on_tuple()
        if self.fail_tuple:
            self.raise_owned(CALLBACK_ERROR)
            return 0
        obj = self.stack_alloc(32)
        self.ref(obj, 1)
        self.tuples[obj] = 0
        return obj

    def tuple_set(self, args, index, ref):
        assert index == 0
        self.tuples[args] = ref

    def call(self, callback, args, kwargs):
        ref = self.tuples[args]
        assert callback == self.load(ref, 24)
        assert self.no_park == 0 and self.pending == self.pending_threads == 0
        self.calls.append(ref)
        return self.on_callback(ref)

    def abort(self):
        assert self.no_park == 0
        self.aborted += 1  # Returning double tests explicit production returns.

    def run(self, ref=WEAKREF):
        self.ns["_weakref_run_callback"](ref)

    def assert_finished(self, pending=True):
        assert self.pending == (self.expected_outer if pending else 0)
        assert self.pending_threads == int(pending)
        assert self.refcounts.get(self.expected_outer, 0) == int(pending)
        assert not self.tuples and not self.frames and self.no_park == 0
        assert self.aborted == 0
        if self.conservative:
            assert not self.registrations


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("conservative", (False, True))
@pytest.mark.parametrize("raises", (False, True))
@pytest.mark.parametrize("pending", (False, True))
def test_callback_success_or_error_preserves_original_exception(backend, conservative, raises, pending):
    model = CallbackBoundaryModel(backend, pending=pending, conservative=conservative)

    def callback(ref):
        if pending:
            assert model.rooted(OUTER) and model.refcounts[OUTER] == 1
        if raises:
            model.raise_owned(CALLBACK_ERROR)
        model.ref(RESULT, 1)
        return RESULT

    model.on_callback = callback
    model.run()
    model.assert_finished(pending)
    assert model.calls == [WEAKREF] and model.refcounts[RESULT] == 0
    assert model.refcounts.get(CALLBACK_ERROR, 0) == 0


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("conservative", (False, True))
def test_tuple_allocation_failure_restores_outer_error(backend, conservative):
    model = CallbackBoundaryModel(backend, conservative=conservative)
    model.fail_tuple = True
    model.run()
    model.assert_finished()
    assert model.calls == [] and model.refcounts[CALLBACK_ERROR] == 0


@pytest.mark.parametrize("backend", range(5))
def test_missing_callback_does_not_touch_pending_error(backend):
    model = CallbackBoundaryModel(backend)
    model.store(WEAKREF, 24, 0)
    model.run()
    model.assert_finished()
    assert not model.stack_slots and not model.released and not model.calls


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("conservative", (False, True))
@pytest.mark.parametrize("reentry", ("callback", "result-drop", "tuple-drop"))
def test_nested_callbacks_restore_each_enclosing_exception(backend, conservative, reentry):
    model = CallbackBoundaryModel(backend, conservative=conservative)

    def nested():
        model.raise_owned(MIDDLE)
        model.run(NESTED_WEAKREF)
        assert model.pending == MIDDLE and model.refcounts[MIDDLE] == 1

    def callback(ref):
        if ref == NESTED_WEAKREF:
            assert model.rooted(OUTER) and model.rooted(MIDDLE)
            model.raise_owned(INNER_ERROR)
            return 0
        if reentry == "callback":
            nested()
        model.ref(RESULT, 1)
        return RESULT

    def dropped(obj):
        if reentry == "result-drop" and obj == RESULT:
            nested()
        if reentry == "tuple-drop" and obj not in model.exception_objects | {RESULT} and model.calls == [WEAKREF]:
            nested()

    model.on_callback, model.on_drop = callback, dropped
    model.run()
    model.assert_finished()
    assert model.calls == [WEAKREF, NESTED_WEAKREF]
    assert model.refcounts[MIDDLE] == model.refcounts[INNER_ERROR] == 0


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("conservative", (False, True))
def test_callback_error_destruction_reenters_with_empty_tls(backend, conservative):
    model = CallbackBoundaryModel(backend, conservative=conservative)

    def callback(ref):
        if ref == WEAKREF:
            model.raise_owned(CALLBACK_ERROR)
        else:
            assert model.load(CALLBACK_ERROR, 12) & TERMINAL
            assert not model.rooted(CALLBACK_ERROR)
            model.raise_owned(INNER_ERROR)
        return 0

    def dropped(obj):
        if obj == CALLBACK_ERROR:
            assert model.pending == model.pending_threads == 0
            assert not model.rooted(obj)
            model.run(NESTED_WEAKREF)

    model.on_callback, model.on_drop = callback, dropped
    model.run()
    model.assert_finished()
    assert model.calls == [WEAKREF, NESTED_WEAKREF]
    assert model.refcounts[CALLBACK_ERROR] == model.refcounts[INNER_ERROR] == 0


@pytest.mark.parametrize("operation", ("callback", "clear"))
@pytest.mark.parametrize("status", (0, -1))
def test_checked_registration_failure_keeps_tls_owner(operation, status):
    model = CallbackBoundaryModel(4)
    model.registration_status = status
    if operation == "callback":
        model.run()
    else:
        model.ns["py_clear_exception"]()
    assert model.aborted == 1 and model.no_park == 0
    assert model.pending == OUTER and model.pending_threads == 1
    assert model.refcounts == {OUTER: 1}
    assert not model.frames and not model.swaps and not model.calls and not model.released
    assert len(model.registrations) == 1


@pytest.mark.parametrize("operation", ("callback", "clear"))
@pytest.mark.parametrize("boundary", ("guard-exit", "tls-retirement"))
def test_relocation_follows_authoritative_owner(operation, boundary):
    model = CallbackBoundaryModel(4)
    if boundary == "guard-exit":
        def move_after_guard():
            assert model.pending == OUTER and model.frames
            assert all(model.load(slot) == 0 for slot in model.frames)
            model.relocate(OUTER, MOVED)
        model.after_guard = move_after_guard
    else:
        def move_after_swap(slot):
            assert model.pending == 0 and model.load(slot) == OUTER
            model.relocate(OUTER, MOVED)
        model.after_swap = move_after_swap
    if operation == "callback":
        def callback(ref):
            assert model.rooted(MOVED)
            return 0
        model.on_callback = callback
        model.run()
        model.assert_finished()
        assert model.pending == MOVED and model.refcounts[MOVED] == 1
    else:
        model.ns["py_clear_exception"]()
        assert model.pending == model.pending_threads == 0
        assert model.refcounts[MOVED] == 0 and not model.frames and model.no_park == 0
    assert OUTER not in model.released


def test_callback_is_reloaded_after_tuple_allocation():
    model = CallbackBoundaryModel(4)
    model.on_tuple = lambda: model.store(WEAKREF, 24, 8800)
    model.run()
    model.assert_finished()
    assert model.calls == [WEAKREF]
