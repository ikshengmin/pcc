"""MODEL: generator PASS0 must leave late weakrefs to the tracing batch.

Specification: docs/project-intent.md (weakrefs, finalizers, resurrection).
Loads the production sweep/recheck, generator finalizers and their common
helper, user-finalizer dispatcher, and weakref constructor/call/invalidation.
Memory, root discovery/marking, pin/lease/TLS ownership, class lookup, Python
user bodies, and slot destruction are explicit boundaries, not native proofs.
Inherited refcounts balance tuples; leases model count-free callback lifetime.
"""
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions
from tests.python.test_tracing_late_weakref_batch import LateWeakBatch


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class GeneratorLateWeakBatch(LateWeakBatch):
    def __init__(self, backend=4, *, count_free=True, shape="suspended"):
        super().__init__(backend)
        self.pending = 0
        self.exception_roots = set()
        self.no_park = 0
        self.lifecycle = []
        self.refs = []
        self.close_hook = lambda gen: None
        self.callback_hook = lambda ref: None
        self.marked = set()
        self.callback_leased = []
        self.store(self.global_addr("pcc_gc_backend4_no_refcounts"), 0, int(count_free))
        self.store(self.global_addr("pcc_class_del_defined_count"), 0, 1)

        # Newest object node first: A.__del__ runs before G's real GEN path.
        # A -> G -> A is intact until PASS1's slot-destruction boundary.
        self.gen = self.object()
        self.owner = self.object()
        self.store(self.gen, 8, abi.PY_TYPE_GEN)
        self.store(self.gen, 24, self.owner)
        self.store(self.gen, 32, 0 if shape == "unstarted" else 1)
        self.store(self.gen, 40, int(shape == "completed"))
        if shape == "finalized":
            self.store(self.gen, 12, self.load(self.gen, 12) | 4)
        self.store(self.owner, 8, abi.PY_TYPE_INSTANCE)
        self.store(self.owner, 48, self.gen)
        self.callbacks[0x8000] = self.callback
        self.ns.update(
            C_POINTER_SIZE=abi.C_POINTER_SIZE,
            abi_constant=lambda name: {
                "object.type.gen": abi.PY_TYPE_GEN,
                "object.type.class": abi.PY_TYPE_CLASS,
            }[name],
            atomic_rmw_i64=self.atomic,
            atomic_rmw_i32=self.atomic,
            py_current_exception=lambda: self.pending,
            py_clear_exception=lambda: self.set_error(0),
            py_raise=self.set_error,
            py_tls_exc_set=self.set_error,
            py_tls_exc_swap_slot=self.swap_error,
            pcc_gc_pin=lambda obj: self.pin(obj, True),
            pcc_gc_unpin=lambda obj: self.pin(obj, False),
            # Select checked registration explicitly; this model does not
            # exercise the native conservative-stack shortcut.
            pcc_gc_stack_slot_root_is_conservative=lambda slot: 0,
            pcc_thread_no_park_enter=lambda: self.park(1),
            pcc_thread_no_park_exit=lambda: self.park(-1),
            pcc_gc_note_frame_enter_local_checked=self.register_exception,
            pcc_gc_note_frame_leave_local=self.exception_roots.remove,
            _load_instance_cls=lambda obj: 0x9000,
            py_class_lookup=lambda cls, name: 0x9002,
            _finalizer_owner_enter=self.acquire,
            _finalizer_owner_leave=lambda slot, token, dying: self.release(slot, token),
            _call_user_unary_method_void=self.instance_body,
            py_gen_close=self.close,
        )
        load_functions(RUNTIME / "py_gen.py", {
            "py_gen_finalize", "py_gen_finalize_tracing_candidate",
        }, self.ns)
        # Replace LateWeakBatch's finalizer double; GEN and INSTANCE dispatch
        # below execute the production body, not a test-side tag switch.
        load_functions(RUNTIME / "py_dunder.py", {"py_user_del_dispatch"}, self.ns)

    def atomic(self, operation, base, offset, value, order):
        previous = self.load(base, offset)
        if operation == "add":
            result = previous + value
        elif operation == "or":
            result = previous | value
        else:
            assert operation == "and"
            result = previous & value
        self.store(base, offset, result)
        return previous

    def pin(self, obj, enabled):
        flags = self.load(obj, 12)
        self.store(obj, 12, flags | 64 if enabled else flags & ~64)

    def set_error(self, error):
        self.pending = error

    def swap_error(self, slot):
        assert slot in self.exception_roots
        previous = self.load(slot)
        self.store(slot, 0, self.pending)
        self.pending = previous

    def park(self, delta):
        self.no_park += delta
        assert self.no_park >= 0

    def register_exception(self, descriptor, slot):
        assert self.no_park == 1 and self.load(slot) == 0
        self.exception_roots.add(slot)
        return 1

    def seed_roots(self):
        self.marked.clear()
        super().seed_roots()

    def mark(self, obj):
        if obj in self.marked:
            return
        self.marked.add(obj)
        super().mark(obj)
        if obj == self.gen:
            self.mark(self.load(obj, 24))
        elif obj == self.owner:
            self.mark(self.load(obj, 48))

    def read_ref(self, ref):
        target = self.ns["py_weakref_call"](ref)
        self.decref(target)
        return target

    def instance_body(self, func, obj):
        assert obj == self.owner and self.pending == 0
        target = self.load(obj, 48)
        assert target == self.gen and self.load(target, 24) == obj
        self.lifecycle.append(("instance", obj))
        self.refs.append(self.weak(target))
        self.refs.append(self.weak(target, 0x8000))

    def close(self, gen):
        assert gen == self.gen and self.pending == 0
        assert self.load(gen, 12) & 4 and self.load(gen, 12) & 64
        assert self.load(self.owner, 48) == gen
        self.lifecycle.append(("close", gen))
        self.close_hook(gen)
        self.store(gen, 40, 1)
        return self.none

    def call(self, callback, args, kwargs):
        ref = self.tuples[args]
        self.calls.append(ref)
        self.callback_leased.append(any(self.load(slot) == ref for slot in self.leases))
        self.callbacks[callback](ref)
        return self.none

    def callback(self, ref):
        assert self.pending == 0
        self.lifecycle.append(("callback", ref))
        self.callback_hook(ref)

    def finalizer_count(self):
        return self.global_load("pcc_gc_user_finalizer_runs")


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_generator_late_siblings_clear_after_close_before_first_callback(backend):
    model = GeneratorLateWeakBatch(backend)
    observations = []

    def callback(ref):
        observations.append([model.read_ref(item) for item in model.refs])
        assert model.load(model.gen, 40) == 1
        assert model.load(model.owner, 48) == model.gen

    model.callback_hook = callback
    assert model.sweep() == 2
    assert observations == [[model.none, model.none]]
    assert model.lifecycle == [
        ("instance", model.owner), ("close", model.gen),
        ("callback", model.refs[1]),
    ]
    assert model.finalizer_count() == 2 and model.rechecks == 2
    assert model.callback_leased == [True] and not model.leases
    assert model.destroyed == [model.owner, model.gen]


def test_preexisting_refs_keep_the_initial_batch_before_generator_close():
    model = GeneratorLateWeakBatch()
    early = model.weak(model.gen, 0x8002)

    def early_callback(ref):
        assert ref == early and model.read_ref(ref) == model.none
        assert model.refs == [] and model.load(model.gen, 40) == 0
        model.lifecycle.append(("early", ref))

    model.callbacks[0x8002] = early_callback
    assert model.sweep() == 2
    assert model.lifecycle == [
        ("early", early), ("instance", model.owner), ("close", model.gen),
        ("callback", model.refs[1]),
    ]
    assert model.calls == [early, model.refs[1]]
    assert model.callback_leased == [True, True] and not model.leases
    assert model.rechecks == 3


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_generator_close_resurrection_preserves_every_late_ref(backend):
    model = GeneratorLateWeakBatch(backend)

    def resurrect(gen):
        assert [model.read_ref(ref) for ref in model.refs] == [gen, gen]
        model.roots.add(gen)

    model.close_hook = resurrect
    assert model.sweep() == 0
    assert [model.read_ref(ref) for ref in model.refs] == [model.gen, model.gen]
    assert model.calls == model.cleared == model.destroyed == []
    assert model.finalizer_count() == 2 and model.rechecks == 1
    assert not model.leases and not model.load(model.gen, 12) & 64


@pytest.mark.parametrize("shape", ("unstarted", "completed", "finalized"))
def test_no_close_guard_still_leaves_late_refs_for_the_global_batch(shape):
    model = GeneratorLateWeakBatch(shape=shape)
    model.callback_hook = lambda ref: model.lifecycle.append(
        ("siblings", [model.read_ref(item) for item in model.refs])
    )
    assert model.sweep() == 2
    assert model.finalizer_count() == 1
    assert not any(event[0] == "close" for event in model.lifecycle)
    assert model.lifecycle[-1] == ("siblings", [model.none, model.none])
    assert model.callback_leased == [True] and not model.leases


@pytest.mark.parametrize("pending", (False, True))
def test_close_and_callback_errors_preserve_pending_exception(pending):
    model = GeneratorLateWeakBatch()
    original = model.allocate_object(64, 104, 0) if pending else 0
    cleanup_error = model.allocate_object(64, 104, 0)
    model.pending = original
    model.close_hook = lambda gen: model.set_error(cleanup_error)
    model.callback_hook = lambda ref: model.set_error(cleanup_error)
    before = model.refcounts.get(original)
    assert model.sweep() == 2
    assert model.pending == original and model.refcounts.get(original) == before
    assert model.finalizer_count() == 2
    assert not model.exception_roots and model.no_park == 0
    assert not model.leases and not model.load(model.gen, 12) & 64
    if original:
        assert not model.load(original, 12) & 64


def test_late_generator_batch_keeps_queued_callback_when_chain_is_replaced():
    model = GeneratorLateWeakBatch()
    replacement_target = model.allocate_object(64, 104, 0)
    replacement = []

    def close(gen):
        model.refs.append(model.weak(gen, 0x8002))

    def mutate(ref):
        assert all(model.read_ref(item) == model.none for item in model.refs)
        replacement.append(model.weak(replacement_target))
        # Raw chain mutation is the opaque callback boundary. The fixed
        # pending batch must still deliver the other queued weakref once.
        model.head = replacement[0]

    model.close_hook = close
    model.callbacks[0x8002] = mutate
    model.callback_hook = lambda ref: model.lifecycle.append(("remaining", ref))
    assert model.sweep() == 2
    assert model.calls == [model.refs[2], model.refs[1]]
    assert model.callback_leased == [True, True] and not model.leases
    assert model.read_ref(replacement[0]) == replacement_target
    assert model.lifecycle[-1] == ("remaining", model.refs[1])


@pytest.mark.parametrize("entry", ("py_gen_finalize", "py_user_del_dispatch"))
@pytest.mark.parametrize("backend", (0, 1, 2, 3, 4))
def test_explicit_generator_finalization_still_invalidates_before_close(entry, backend):
    model = GeneratorLateWeakBatch(backend, count_free=False)
    model.refs = [model.weak(model.gen), model.weak(model.gen, 0x8000)]
    observations = []
    model.callback_hook = lambda ref: observations.append(
        (model.load(model.gen, 40), model.read_ref(model.refs[0]))
    )
    model.ns[entry](model.gen)
    model.ns[entry](model.gen)
    # GC0 batches one target. Other explicit entries intentionally retain
    # the existing one-at-a-time invalidation, independent of tracing PASS0.
    assert observations == [(0, model.none if backend == 0 else model.gen)]
    assert model.lifecycle == [("callback", model.refs[1]), ("close", model.gen)]
    assert model.finalizer_count() == 1
    assert [model.read_ref(ref) for ref in model.refs] == [model.none, model.none]


@pytest.mark.parametrize("backend", (0, 1, 2, 3, 4))
def test_counted_sweep_does_not_enter_the_tracing_candidate_finalizer(backend):
    # Backend 0 normally owns its own collector. This also checks the shared
    # sweep's counted branch without claiming a native GC0 sweep entry.
    model = GeneratorLateWeakBatch(backend, count_free=False)
    observations = []
    model.callback_hook = lambda ref: observations.append(model.load(model.gen, 40))
    assert model.sweep() == 2
    assert observations == [0]
    assert model.finalizer_count() == 2 and model.rechecks == 1
    assert model.lifecycle[:3] == [
        ("instance", model.owner), ("callback", model.refs[1]), ("close", model.gen),
    ]
