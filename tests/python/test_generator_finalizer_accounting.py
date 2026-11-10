"""Count actual generator finalization, not no-op sweep dispatches.

Host models execute production bodies with controlled memory/runtime boundaries.
The integration program below must additionally execute against a real archive.
"""
import ast
import os
from pathlib import Path
import subprocess

import pytest

from tests.python.runtime_body_model import load_functions

ROOT = Path(os.environ.get('PCC_FINALIZER_MODEL_SOURCE_ROOT',
                           str(Path(__file__).resolve().parents[2])))
COUNTER = 'pcc_gc_user_finalizer_runs'


def _bodies(name, functions, namespace):
    load_functions(ROOT / 'pcc/runtime/py' / name, functions, namespace)


class Finalization:
    def __init__(self, *, done=0, state=1, flags=0, count_free=False):
        self.memory = {(1000, 0): 1, (1000, 8): 15, (1000, 12): flags,
                       (1000, 16): 2000, (1000, 32): state, (1000, 40): done,
                       (9000, 0): 1, (9000, 12): 0,
                       (COUNTER, 0): 0,
                       ('pcc_class_del_defined_count', 0): 1,
                       ('pcc_gc_backend_selected', 0): 4,
                       ('pcc_gc_backend4_no_refcounts', 0): int(count_free),
                       ('pcc_gc_tracing_finish_stop_epoch', 0): 7,
                       (3000, 0): 1000, (3000, 16): 0}
        self.events = []
        self.pin_events = []
        self.phase_observations = []
        self.pending = 0
        self.count_free = count_free
        self.weak_hook = None
        self.close_hook = None
        self.close_count = 0
        self.rescued = []
        self.active = True
        self.next_slot = 10000
        constants = ast.parse((ROOT / 'pcc/runtime/py/py_abi_constants.py').read_text())
        self.ns = {}
        for node in constants.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name): self.ns[target.id] = node.value.value
        self.memory[1000, 8] = self.ns['PY_TYPE_GEN']
        self.ns.update(
            ptr_is_null=lambda value: value == 0,
            is_tagged_int=lambda value: value & 1,
            global_addr=lambda name: name,
            global_load_ptr=lambda name: 3000,
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i32=self.store, store_i64=self.store, store_ptr=self.store,
            atomic_rmw_i64=self.add, atomic_rmw_i32=self.bits,
            cstr=str, null=lambda: 0, stack_alloc=self.stack,
            py_incref=lambda value: self.ref(value, 1),
            py_decref=lambda value: self.ref(value, -1),
            pcc_gc_pin=lambda value: self.pin(value, True),
            pcc_gc_unpin=lambda value: self.pin(value, False),
            py_weakref_invalidate=self.weak,
            py_current_exception=lambda: self.pending,
            py_clear_exception=self.clear_error,
            py_raise=self.raise_error, py_tls_exc_set=self.set_error,
            py_gen_close=self.close,
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            _load_instance_cls=lambda value: 2000,
            py_class_lookup=lambda cls, name: 7000,
            _finalizer_owner_enter=lambda slot: 0,
            _finalizer_owner_leave=lambda *args: None,
            _call_user_unary_method_void=lambda func, value: self.events.append(('instance', self.count())),
            pcc_diagnostics_runtime_log_event_code=lambda *args: None,
            pcc_thread_world_stop_epoch_held=lambda: 7,
            pcc_gc_object_node_is_active=lambda node: int(self.active),
            pcc_gc_object_is_address_pinned=lambda value: int(bool(self.load(value, 12) & 64)),
            pcc_gc_object_node_is_address_pinned=self.node_is_address_pinned,
            pcc_gc_forwarding_index_find=lambda value: 0,
            pcc_gc_tracing_recheck_reachability_after_finalizers=self.recheck,
            py_weakref_invalidate_tracing_candidates=lambda: 0,
            pcc_gc_tracing_clear_unpinned=lambda value: self.events.append(('clear',)),
            pcc_gc_tracing_finalize_unreachable=self.free,
            pcc_gc_tracing_finalize_unpinned=self.free_unpinned,
            pcc_platform_monotonic_us=lambda: 100,
            _record_sweep_phase=lambda *args: self.phase_observations.append(args),
            abi_constant=lambda name: {
                'object.type.class': self.ns['PY_TYPE_CLASS'],
                'object.type.gen': self.ns['PY_TYPE_GEN'],
            }[name],
        )
        _bodies('py_gen.py', {
            'py_gen_finalize', 'py_gen_finalize_tracing_candidate',
        }, self.ns)
        _bodies('py_dunder.py', {'py_user_del_dispatch'}, self.ns)
        _bodies('freestanding_gc_tracing_sweep_collector.py',
                {'pcc_gc_tracing_sweep_unreachable'}, self.ns)

    def load(self, base, offset):
        return self.memory.get((base, offset), 0)

    def store(self, base, offset, value):
        self.memory[base, offset] = value

    def add(self, op, base, offset, amount, order):
        assert op == 'add'
        previous = self.load(base, offset)
        self.store(base, offset, previous + amount)
        self.events.append(('count', self.count()))
        return previous

    def bits(self, op, base, offset, bits, order):
        previous = self.load(base, offset)
        self.store(base, offset, previous | bits if op == 'or' else previous & bits)
        return previous

    def stack(self, size):
        self.next_slot += size
        return self.next_slot

    def ref(self, value, delta):
        if value and not self.count_free:
            self.store(value, 0, self.load(value, 0) + delta)

    def pin(self, value, enabled):
        if value:
            self.pin_events.append((value, enabled))
            flags = self.load(value, 12)
            self.store(value, 12, flags | 64 if enabled else flags & ~64)

    def count(self):
        return self.load(COUNTER, 0)

    def set_error(self, value):
        # Raw TLS setter transfers ownership; it does not retain or release.
        self.pending = value

    def clear_error(self):
        previous = self.pending
        self.pending = 0
        self.ref(previous, -1)

    def raise_error(self, value):
        # This accounting-only double normalizes a borrowed exception into
        # an owned TLS reference. Context semantics are exercised below with
        # the production raise and implicit-context bodies.
        self.ref(value, 1)
        self.clear_error()
        self.set_error(value)

    def weak(self, value):
        self.events.append(('weak', self.count()))
        if self.weak_hook:
            saved = self.pending
            self.weak_hook()
            self.pending = saved

    def close(self, value):
        self.events.append(('close', self.count()))
        self.close_count += 1
        if self.close_hook: self.close_hook()
        self.store(value, 40, 1)
        return 0

    def recheck(self):
        self.events.append(('recheck',))
        if self.rescued:
            self.store(1000, 12, self.load(1000, 12) & ~1024)

    def free(self, value):
        self.events.append(('free',))
        self.active = False

    def node_is_address_pinned(self, node, value):
        assert node == 3000 and self.load(node, 0) == value and self.active
        return int(bool(self.load(value, 12) & 64))

    def free_unpinned(self, value):
        assert self.active and not self.load(value, 12) & 64
        self.free(value)


@pytest.mark.parametrize('entry', (
    'py_user_del_dispatch', 'py_gen_finalize', 'py_gen_finalize_tracing_candidate',
))
@pytest.mark.parametrize('shape', ('completed', 'unstarted', 'finalized'))
@pytest.mark.parametrize('count_free', (False, True))
def test_noop_generators_do_not_count_user_finalizers(entry, shape, count_free):
    model = Finalization(done=int(shape == 'completed'), state=0 if shape == 'unstarted' else 1,
                         flags=4 if shape == 'finalized' else 0, count_free=count_free)
    before = dict(model.memory)
    model.ns[entry](1000)
    assert model.count() == 0
    assert model.memory == before
    assert model.events == [] and model.close_count == 0


@pytest.mark.parametrize('entry', ('py_user_del_dispatch', 'py_gen_finalize'))
@pytest.mark.parametrize('scenario', ('normal', 'weak-reentry', 'close-reentry', 'close-error', 'resurrect'))
@pytest.mark.parametrize('count_free', (False, True))
def test_started_generator_counts_before_callbacks_and_finalizes_once(entry, scenario, count_free):
    model = Finalization(count_free=count_free)
    model.pending = 9000
    if scenario == 'weak-reentry':
        model.weak_hook = lambda: model.ns['py_user_del_dispatch'](1000)
    if scenario == 'close-reentry':
        model.close_hook = lambda: model.ns['py_gen_finalize'](1000)
    if scenario == 'close-error':
        model.close_hook = lambda: model.raise_error(9100)
    if scenario == 'resurrect':
        model.close_hook = lambda: model.rescued.append(1000)
    model.ns[entry](1000)
    model.ns[entry](1000)
    assert model.count() == model.close_count == 1
    assert model.events[:3] == [('count', 1), ('weak', 1), ('close', 1)]
    assert model.pending == 9000
    assert model.load(1000, 0) == model.load(9000, 0) == 1
    assert model.load(1000, 12) & 4 and not model.load(1000, 12) & 64
    assert model.rescued == ([1000] if scenario == 'resurrect' else [])


@pytest.mark.parametrize('scenario', ('normal', 'close-reentry', 'close-error', 'resurrect'))
def test_tracing_candidate_close_preserves_accounting_and_pending_error(scenario):
    model = Finalization(count_free=True)
    model.pending = 9000
    if scenario == 'close-reentry':
        model.close_hook = lambda: model.ns['py_user_del_dispatch'](1000)
    if scenario == 'close-error':
        model.close_hook = lambda: model.raise_error(9100)
    if scenario == 'resurrect':
        model.close_hook = lambda: model.rescued.append(1000)
    model.ns['py_gen_finalize_tracing_candidate'](1000)
    model.ns['py_gen_finalize'](1000)
    assert model.count() == model.close_count == 1
    assert model.events == [('count', 1), ('close', 1)]
    assert model.pending == 9000
    assert model.load(1000, 0) == model.load(9000, 0) == 1
    assert model.load(1000, 12) & 4 and not model.load(1000, 12) & 64
    assert model.rescued == ([1000] if scenario == 'resurrect' else [])


@pytest.mark.parametrize('entry', (
    'py_user_del_dispatch', 'py_gen_finalize', 'py_gen_finalize_tracing_candidate',
))
@pytest.mark.parametrize('gen_pinned', (False, True))
@pytest.mark.parametrize('pending_pinned', (False, True))
@pytest.mark.parametrize('has_pending', (False, True))
@pytest.mark.parametrize('scenario', ('normal', 'close-error', 'close-reentry'))
@pytest.mark.parametrize('count_free', (False, True))
def test_generator_finalizer_preserves_prior_pins(
    entry, gen_pinned, pending_pinned, has_pending, scenario, count_free,
):
    model = Finalization(count_free=count_free)
    pin = model.ns['PY_FLAG_GC_PINNED']
    offset = model.ns['PYOBJECTHEADER_FLAGS_OFFSET']
    # Keep unrelated incoming flags and a flag added during close. Restoring
    # the entire old header would preserve the pin but lose the latter.
    gen_flags = 2 | (pin if gen_pinned else 0)
    exc_flags = 32 | (pin if pending_pinned else 0)
    model.store(1000, offset, gen_flags)
    model.store(9000, offset, exc_flags)
    model.pending = 9000 if has_pending else 0

    def close():
        assert model.load(1000, offset) & pin
        if has_pending:
            assert model.load(9000, offset) & pin
        model.bits('or', 1000, offset, 128, 'relaxed')
        model.bits('or', 9000, offset, 256, 'relaxed')
        if scenario == 'close-error':
            model.raise_error(9100)
        if scenario == 'close-reentry':
            model.ns[entry](1000)

    model.close_hook = close
    model.ns[entry](1000)
    assert model.count() == model.close_count == 1
    assert model.pending == (9000 if has_pending else 0)
    assert model.load(1000, offset) == gen_flags | 4 | 128
    assert model.load(9000, offset) == exc_flags | 256
    assert model.load(1000, 0) == model.load(9000, 0) == 1
    expected_pins = [(1000, True)]
    if has_pending:
        expected_pins += [(9000, True), (9000, False)]
    expected_pins += [(1000, False)]
    assert model.pin_events == expected_pins
    before = dict(model.memory)
    model.ns[entry](1000)
    assert model.memory == before
    assert model.pin_events == expected_pins
    assert model.count() == model.close_count == 1


@pytest.mark.parametrize('entry', (
    'py_user_del_dispatch', 'py_gen_finalize', 'py_gen_finalize_tracing_candidate',
))
@pytest.mark.parametrize('gen_pinned', (False, True))
@pytest.mark.parametrize('pending_pinned', (False, True))
def test_already_finalized_generator_does_not_touch_prior_pins(
    entry, gen_pinned, pending_pinned,
):
    model = Finalization(flags=4 | 2 | (64 if gen_pinned else 0))
    model.pending = 9000
    model.store(9000, 12, 32 | (64 if pending_pinned else 0))
    before = dict(model.memory)
    model.ns[entry](1000)
    assert model.memory == before
    assert model.pending == 9000
    assert model.pin_events == model.events == []
    assert model.count() == model.close_count == 0


@pytest.mark.parametrize('shape,expected', (('completed', 0), ('unstarted', 0), ('finalized', 0), ('suspended', 1)))
@pytest.mark.parametrize('count_free', (False, True))
def test_production_sweep_rechecks_only_for_real_generator_finalization(shape, expected, count_free):
    model = Finalization(done=int(shape == 'completed'), state=0 if shape == 'unstarted' else 1,
                         flags=1024 | 8 | (4 if shape == 'finalized' else 0), count_free=count_free)
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 1
    assert model.count() == expected
    assert model.events.count(('recheck',)) == expected
    assert model.close_count == expected
    assert model.events.count(('weak', 1)) == (expected if not count_free else 0)


def test_generator_resurrection_still_triggers_recheck_before_clear():
    model = Finalization(flags=1024 | 8, count_free=True)
    model.close_hook = lambda: model.rescued.append(1000)
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 0
    assert model.count() == 1 and model.events.count(('recheck',)) == 1
    assert ('clear',) not in model.events and ('free',) not in model.events


def test_completed_generator_weak_callback_keeps_its_independent_recheck():
    model = Finalization(done=1, flags=1024 | 8, count_free=True)

    def invalidate_candidates():
        model.rescued.append(1000)
        return 1

    model.ns['py_weakref_invalidate_tracing_candidates'] = invalidate_candidates
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 0
    assert model.count() == model.close_count == 0
    assert model.events.count(('recheck',)) == 1
    assert ('clear',) not in model.events and ('free',) not in model.events


def test_instance_finalizer_accounting_is_unchanged():
    model = Finalization()
    model.store(1000, 8, model.ns['PY_TYPE_INSTANCE'])
    model.ns['py_user_del_dispatch'](1000)
    model.ns['py_user_del_dispatch'](1000)
    assert model.count() == 1
    assert model.events.count(('instance', 1)) == 1


class ContextFinalization(Finalization):
    """Real finalizer/raise/context bodies; explicit ownership and TLS doubles.

    This models a pending C with context B while caller A remains handled.
    The ordinary native regression separately proves that error-unwind shape.
    Collector scheduling, pointer movement and native frames are not modeled.
    """

    def __init__(self, *, count_free=False, pending=True, suppress=False):
        super().__init__(count_free=count_free)
        self.pending = 9000 if pending else 0
        self.store(9000, 0, int(pending))
        self.store(9000, 32, 9300)
        self.store(9000, 40, 9200)
        self.store(9000, 12, self.ns['PY_FLAG_EXC_SUPPRESS_CONTEXT'] if suppress else 0)
        self.store(9200, 0, 1)
        self.store(9300, 0, 1)
        self.store(9400, 0, 1)
        self.store(9500, 0, 9400)  # The caller's active handled-exception slot.
        self.frames = []
        self.plans = {}
        self.graph_locked = False
        self.on_drop = None
        self.dropped = []
        self.raw_sets = []
        self.close_result = 9600
        self.ns.update(
            c_ptr=int,
            ptr_eq=lambda left, right: left == right,
            ptr_add=lambda base, offset: (base, offset),
            _type_of=lambda obj: self.ns['PY_TYPE_EXC'],
            _resolve_current_exception=lambda: self.pending,
            py_handled_exception_slot=lambda: 9500,
            pcc_gc_note_relocation_read=lambda value: value,
            pcc_gc_load_ptr=lambda owner, slot: self.slot_read(slot),
            pcc_gc_store_root=self.root_store,
            pcc_gc_frame_enter=self.frame_enter,
            pcc_gc_frame_leave=self.frame_leave,
            pcc_gc_take_pinned_slot=self.take_pinned,
            py_tls_exc_swap_slot=self.swap,
            pcc_gc_backend=lambda: 4 if self.count_free else 0,
            pcc_py_gc_minor_graph_lock=self.graph_lock,
            pcc_py_gc_minor_graph_unlock=self.graph_unlock,
            pcc_gc_store_ptr_plan_init=self.plan_init,
            pcc_gc_store_ptr_plan_commit_locked=self.plan_commit,
            pcc_gc_store_ptr_plan_finish=self.plan_finish,
        )
        for value in (9000, 9100, 9200, 9300, 9400):
            self.store(value, 8, self.ns['PY_TYPE_EXC'])
        _bodies('py_exc_objects.py', {'py_exc_set_implicit_context_slots'}, self.ns)
        # Do not stub py_raise as a setter: the old finalizer's py_raise(saved)
        # must run the real implicit re-chaining logic and fail these checks.
        _bodies('py_exc_tls.py', {'py_raise'}, self.ns)

    def set_error(self, value):
        self.raw_sets.append((self.pending, value))
        super().set_error(value)

    def ref(self, value, delta):
        super().ref(value, delta)
        if value and not self.count_free:
            assert self.load(value, 0) >= 0, 'released an unowned value'
            if delta < 0 and self.load(value, 0) == 0:
                assert not self.graph_locked, 'destruction under the graph lock'
                self.dropped.append(value)
                if self.on_drop:
                    self.on_drop(value)

    def slot_read(self, slot):
        return self.load(*slot) if isinstance(slot, tuple) else self.load(slot, 0)

    def slot_write(self, slot, value):
        base, offset = slot if isinstance(slot, tuple) else (slot, 0)
        self.store(base, offset, value)

    def root_store(self, slot, value):
        old = self.slot_read(slot)
        self.ref(value, 1)
        self.slot_write(slot, value)
        self.ref(old, -1)

    def frame_enter(self, frame_map, slot):
        assert slot not in self.frames
        self.frames.append(slot)

    def frame_leave(self, slot):
        assert self.frames.pop() == slot

    def take_pinned(self, slot, prior):
        value = self.slot_read(slot)
        self.slot_write(slot, 0)
        self.pin(value, False)
        if prior:
            self.bits('or', value, 12, prior, 'relaxed')
        return value

    def swap(self, slot):
        assert slot in self.frames
        previous = self.pending
        self.pending = self.slot_read(slot)
        self.slot_write(slot, previous)

    def graph_lock(self):
        assert not self.graph_locked
        self.graph_locked = True

    def graph_unlock(self):
        assert self.graph_locked
        self.graph_locked = False

    def plan_init(self, plan, owner, backend):
        self.plans[plan] = 0

    def plan_commit(self, plan, owner, slot, value):
        assert self.graph_locked
        old = self.slot_read(slot)
        self.ref(value, 1)
        self.slot_write(slot, value)
        self.plans[plan] = old

    def plan_finish(self, plan):
        assert not self.graph_locked
        self.ref(self.plans.pop(plan), -1)

    def close(self, value):
        super().close(value)
        self.ref(self.close_result, 1)
        return self.close_result


@pytest.mark.parametrize('entry', (
    'py_user_del_dispatch', 'py_gen_finalize', 'py_gen_finalize_tracing_candidate',
))
@pytest.mark.parametrize('count_free', (False, True))
@pytest.mark.parametrize('pending', (False, True))
@pytest.mark.parametrize('suppress', (False, True))
@pytest.mark.parametrize('scenario', ('normal', 'close-error', 'close-reentry'))
def test_generator_restore_transfers_owner_without_rechaining(
    entry, count_free, pending, suppress, scenario,
):
    model = ContextFinalization(count_free=count_free, pending=pending, suppress=suppress)
    original_fields = tuple(model.load(9000, offset) for offset in (12, 32, 40))
    if scenario == 'close-error':
        model.close_result = 0
        model.close_hook = lambda: model.raise_error(9100)
    elif scenario == 'close-reentry':
        model.close_hook = lambda: model.ns[entry](1000)
    model.ns[entry](1000)
    assert model.pending == (9000 if pending else 0)
    assert tuple(model.load(9000, offset) for offset in (12, 32, 40)) == original_fields
    assert model.load(9500, 0) == 9400
    assert model.count() == model.close_count == 1
    assert not model.frames and not model.plans and not model.graph_locked
    if pending:
        assert model.raw_sets[-1] == (0, 9000)
    if not count_free:
        assert model.load(9000, 0) == int(pending)
        assert model.load(1000, 0) == 1
        assert all(model.load(value, 0) == 1 for value in (9200, 9300, 9400))
        assert model.load(9100, 0) == model.load(9600, 0) == 0


def test_generator_restore_keeps_saved_owner_during_reentrant_error_disposal():
    model = ContextFinalization()
    model.close_result = 0
    model.close_hook = lambda: model.raise_error(9100)
    observations = []

    def destroy(value):
        if value == 9100:
            observations.append((model.pending, model.load(9000, 0)))
            assert model.load(9000, 12) & model.ns['PY_FLAG_GC_PINNED']
            model.ns['py_gen_finalize'](1000)

    model.on_drop = destroy
    model.ns['py_gen_finalize'](1000)
    assert observations == [(0, 1)]
    assert model.pending == 9000 and model.load(9000, 0) == 1
    assert model.load(9000, 40) == 9200
    assert model.count() == model.close_count == 1
    assert model.dropped == [9100]


NATIVE_PROGRAM = '''import gc
import weakref
from pcc.extern import c_int64, c_obj, c_void, extern
count = extern("generator_finalizer_runs", (), c_int64)
dispatch = extern("generator_dispatch", (c_obj,), c_void)
finalize = extern("generator_finalize_direct", (c_obj,), c_void)
preserve = extern("generator_finalize_preserves_pending", (c_obj,), c_int64)
events = []
rescued = []
def values(label):
    try:
        yield 1
    finally:
        events.append(label)
def failing():
    try:
        yield 1
    finally:
        events.append("error")
        raise ValueError("cleanup error")
def callback(ref):
    events.append("weak")
def cyclic(box):
    try:
        yield 1
    finally:
        events.append("resurrect")
        rescued.append(box[0])
        gc.collect()
def make_cycle():
    box = []
    gen = cyclic(box)
    box.append(gen)
    next(gen)
    return weakref.ref(gen, callback)
class Finalizable:
    def __del__(self):
        events.append("instance")
def main():
    gc.disable()
    unstarted = values("unstarted")
    before = count()
    dispatch(unstarted)
    finalize(unstarted)
    assert count() == before
    assert events == []
    completed = values("completed")
    assert next(completed) == 1
    try:
        next(completed)
    except StopIteration:
        pass
    else:
        raise AssertionError("generator did not complete")
    before = count()
    dispatch(completed)
    finalize(completed)
    assert count() == before
    assert events == ["completed"]
    events.clear()
    suspended = values("suspended")
    next(suspended)
    ref = weakref.ref(suspended, callback)
    before = count()
    dispatch(suspended)
    assert count() - before == 1
    assert events == ["weak", "suspended"]
    assert ref() is None
    dispatch(suspended)
    finalize(suspended)
    assert count() - before == 1
    events.clear()
    pinned = values("pinned")
    next(pinned)
    before = count()
    assert preserve(pinned) == 1
    assert count() - before == 1
    assert events == ["pinned"]
    dispatch(pinned)
    finalize(pinned)
    assert count() - before == 1
    events.clear()
    bad = failing()
    next(bad)
    before = count()
    assert preserve(bad) == 1
    assert count() - before == 1
    assert events == ["error"]
    events.clear()
    before = count()
    resurrected_ref = make_cycle()
    for index in range(4):
        gc.collect()
    assert count() - before == 1
    assert resurrected_ref() is None
    assert events == ["weak", "resurrect"]
    assert len(rescued) == 1
    try:
        next(rescued[0])
    except StopIteration:
        pass
    else:
        raise AssertionError("resurrected generator was not closed")
    rescued.clear()
    for index in range(4):
        gc.collect()
    assert count() - before == 1
    instance = Finalizable()
    before = count()
    dispatch(instance)
    dispatch(instance)
    assert count() - before == 1
    assert events == ["weak", "resurrect", "instance"]
    print("GENERATOR_FINALIZER_ACCOUNTING_OK")
main()
'''


@pytest.fixture(scope='module')
def finalizer_accounting_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir
    root = tmp_path_factory.mktemp('generator_finalizer_accounting')
    fixture = Path(__file__).with_name('fixtures') / 'gc_generator_finalizer_accounting.c'
    helper = root / 'counter.o'
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = root / 'accounting.py'
    source.write_text(NATIVE_PROGRAM)
    binary = root / 'accounting'
    compile_python(str(source), str(binary), backend='self',
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('generator_finalizer_accounting')
@pytest.mark.parametrize('backend,tracing', ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)))
def test_generator_finalizer_accounting_native(finalizer_accounting_binary, backend, tracing):
    result = subprocess.run(
        [str(finalizer_accounting_binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, 'GENERATOR_FINALIZER_ACCOUNTING_OK\n', '',
    ), (backend, tracing, result)
