"""Single-collector finalizer ownership: temporary count, TLS and resurrection."""
from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime

RUNTIME = Path(__file__).resolve().parents[2] / 'pcc/runtime/py'
TERMINAL = 524288


class FinalizerModel:
    """Run the live finalizer and lease bodies with a deterministic heap.

    The default stable-address lease is inert. The optional refcount
    provenance probe still exercises the checked counted-token path, not an
    alternate collector. The temporary reference is the finalizer owner in
    both cases.
    """

    def __init__(self, *, terminal=True, resurrect=False, pending=False,
                 provenance_probe=0):
        self.memory = {}
        self.nodes = {1000: 11000}
        self.depth = 0
        self.on_unlock = None
        self.provenance_probe = provenance_probe
        self.self_slot = 21000
        self.store(self.self_slot, 0, 1000)
        self.store(1000, 8, 104)
        self.store(1000, 12, TERMINAL if terminal else 0)
        self.store(1000, 0, 0 if terminal else 2)
        self.store(1000, 16, 2000)
        self.store('pcc_gc_config_initialized', 0, 1)
        self.store('pcc_gc_refcount_provenance_probe', 0, provenance_probe)
        self.store('pcc_class_del_defined_count', 0, 1)
        self.store('pcc_gc_user_finalizer_runs', 0, 0)
        self.store(4000, 0, int(pending))
        self.store(4000, 12, 64)
        self.pending = 4000 if pending else 0
        self.saved_pending = self.pending
        self.resurrect = resurrect
        self.calls = 0
        self.next_slot = 50000
        self.ns = dict(
            i64=int, c_ptr=int, C_POINTER_SIZE=8,
            PYOBJECTHEADER_FLAGS_OFFSET=12, PY_FLAG_GC_PINNED=64,
            PY_TYPE_GEN=15, PY_TYPE_INSTANCE=11, PY_TYPE_USER_CLASS_START=104,
            ptr_to_int=lambda value: value, ptr_is_null=lambda value: int(value == 0),
            is_tagged_int=lambda value: int(isinstance(value, int) and value & 1),
            ptr_eq=lambda a,b: int(a == b), null=lambda: 0,
            global_addr=lambda name: name, global_load_ptr=lambda name: self.load(name),
            ptr_add=lambda base, offset: base + offset, cstr=lambda value: value,
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i32=self.store, store_i64=self.store, store_ptr=self.store,
            atomic_load_i32=lambda base, offset, order: self.load(base, offset),
            atomic_load_i64=lambda base, offset, order: self.load(base, offset),
            atomic_store_i32=lambda base, offset, value, order: self.store(base, offset, value),
            atomic_cas_i64=self.compare_exchange,
            atomic_rmw_i64=self.atomic_add,
            pcc_gc_config_ensure=lambda: 0,
            pcc_gc_object_index_find=lambda obj: self.nodes.get(obj, 0),
            pcc_gc_resolve_root_slot_unlocked=lambda slot, offset: self.load(slot, offset),
            pcc_gc_resolve_borrowed_root_slot_unlocked=lambda slot, offset: self.load(slot, offset),
            pcc_gc_static_literal_contains=lambda obj: 0,
            pcc_gc_granule_is_object_start=lambda obj: int(obj in self.nodes),
            pcc_gc_managed_pointer_index_contains=lambda obj: 0,
            pcc_gc_managed_pointer_index_seen=lambda obj: 0,
            pcc_py_gc_minor_graph_lock=self.lock,
            pcc_py_gc_minor_graph_unlock=self.unlock,
            stack_alloc=self.alloc,
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            _load_instance_cls=lambda value: self.load(value, 16),
            py_class_lookup=lambda cls, name: 3000,
            pcc_refcount_incref=lambda value: self.ref(value, 1),
            pcc_refcount_decref=lambda value: self.ref(value, -1),
            py_incref=lambda value: self.ref(value, 1),
            py_decref=lambda value: self.ref(value, -1),
            py_current_exception=lambda: self.pending,
            py_tls_exc_set=self.set_exception,
            py_clear_exception=lambda: self.set_exception(0),
            _call_user_unary_method_void=self.callback,
            pcc_diagnostics_runtime_log_event_code=lambda *args: None,
            pcc_platform_abort=lambda: pytest.fail('finalizer owner protocol abort'),
        )
        load_functions(RUNTIME / 'freestanding_gc_root_operations.py', {
            'pcc_gc_foreign_lease_acquire', 'pcc_gc_foreign_lease_release',
            'pcc_gc_foreign_lease_count_quiescent',
            'pcc_gc_object_is_address_pinned',
        }, self.ns)
        load_functions(RUNTIME / 'py_dunder.py', {
            '_finalizer_owner_enter', '_finalizer_owner_leave', 'py_user_del_dispatch',
        }, self.ns)
        self.on_unlock = self.boundary

    def load(self, base, offset=0):
        key = base + offset if isinstance(base, int) else (base, offset)
        return self.memory.get(key, 0)

    def store(self, base, offset, value):
        key = base + offset if isinstance(base, int) else (base, offset)
        self.memory[key] = value

    def alloc(self, size):
        self.next_slot += size
        return self.next_slot

    def compare_exchange(self, base, offset, expected, desired, success, failure):
        observed = self.load(base, offset)
        if observed == expected:
            self.store(base, offset, desired)
        return observed

    def lock(self):
        self.depth += 1

    def unlock(self):
        self.depth -= 1
        assert self.depth >= 0
        if self.depth == 0 and self.on_unlock:
            hook, self.on_unlock = self.on_unlock, None
            hook()

    def atomic_add(self, op, base, offset, delta, order):
        assert op == "add"
        previous = self.load(base, offset)
        self.store(base, offset, previous + delta)
        return previous

    def ref(self, value, change):
        if not value:
            return 0
        count = self.load(value, 0) + change
        assert count >= 0
        self.store(value, 0, count)
        return count

    def set_exception(self, value):
        # The actual setter transfers a counted owner into TLS.
        previous = self.pending
        self.ref(value, 1)
        self.pending = value
        self.ref(previous, -1)

    def acquire(self, slot):
        return self.ns['pcc_gc_foreign_lease_acquire'](slot)

    def release(self, slot, token):
        return self.ns['pcc_gc_foreign_lease_release'](slot, token)

    def count(self):
        return self.load(self.nodes[1000], 80)

    def active(self):
        return self.ns['pcc_gc_foreign_lease_count_quiescent']()

    def boundary(self):
        expected_token = int(self.provenance_probe != 0)
        assert self.count() == self.active() == expected_token
        assert self.ns['pcc_gc_object_is_address_pinned'](1000) == expected_token
        assert self.load(1000, 0) > 0
        assert not self.load(1000, 12) & TERMINAL

    def callback(self, func, value):
        assert self.depth == 0 and self.pending == 0
        if self.saved_pending:
            assert self.load(self.saved_pending, 0) == 1
        self.calls += 1
        self.boundary()
        token = self.acquire(self.self_slot)
        expected_token = int(self.provenance_probe != 0)
        assert token == expected_token and self.count() == 2 * expected_token
        assert self.release(self.self_slot, token) == 0
        self.boundary()
        # Preserve unrelated flags, and model a real resurrection owner.
        self.store(value, 12, self.load(value, 12) | 32)
        if self.resurrect:
            self.ref(value, 1)
        self.set_exception(6000)


@pytest.mark.parametrize('provenance_probe', (0, 2))
@pytest.mark.parametrize('scenario', ('dealloc', 'resurrect', 'cycle', 'pending-error'))
def test_actual_finalizer_body_preserves_owner_flags_and_exception(provenance_probe, scenario):
    m = FinalizerModel(terminal=scenario != 'cycle',
                       resurrect=scenario == 'resurrect', pending=scenario == 'pending-error',
                       provenance_probe=provenance_probe)
    before = m.load(1000, 0)
    m.ns['py_user_del_dispatch'](1000)
    assert m.calls == 1 and m.depth == 0
    assert m.count() == 0 and m.active() == 0
    assert m.load(1000, 0) == before + (scenario == 'resurrect')
    flags = m.load(1000, 12)
    assert flags & 4 and flags & 32
    assert bool(flags & TERMINAL) == (scenario in ('dealloc', 'pending-error'))
    assert m.pending == (4000 if scenario == 'pending-error' else 0)
    assert m.load(6000, 0) == 0
    if scenario == 'pending-error':
        assert m.load(4000, 0) == 1 and m.load(4000, 12) & 64
    m.ns['py_user_del_dispatch'](1000)
    assert m.calls == 1


@pytest.mark.parametrize('provenance_probe, expected', [(0, 0), (2, -1)])
def test_terminal_address_probe_has_the_single_collector_contract(provenance_probe, expected):
    m = FinalizerModel(provenance_probe=provenance_probe)
    m.on_unlock = None
    assert m.acquire(m.self_slot) == expected
    assert m.active() == 0 and m.load(1000, 0) == 0


@pytest.mark.parametrize('provenance_probe', (0, 2))
def test_temporary_finalizer_reference_owns_terminal_instance(provenance_probe):
    m = FinalizerModel(provenance_probe=provenance_probe)
    token = m.ns['_finalizer_owner_enter'](m.self_slot)
    m.boundary()
    assert m.load(1000, 0) == 1
    m.ns['_finalizer_owner_leave'](m.self_slot, token, TERMINAL)
    assert m.load(1000, 0) == 0 and m.load(1000, 12) & TERMINAL
    assert m.count() == 0 and m.active() == 0


PROGRAM = textwrap.dedent('''\
    import gc
    import weakref
    events = []
    references = []
    rescued = []
    class Token:
        def __del__(self):
            events.append('token')
    class Value:
        def __init__(self, name, truth=False):
            self.name = name
            self.truth = truth
        def __bool__(self):
            events.append(self.name)
            gc.collect()
            return self.truth
        def __del__(self):
            events.append('dispose:' + self.name)
            gc.collect()
    class Rescue:
        def __init__(self, name):
            self.name = name
        def __del__(self):
            events.append(self.name)
            rescued.append(self)
            gc.collect()
    def drop(*, value, **extras):
        references.append(weakref.ref(value))
    def take(*, value, **extras):
        gc.collect()
        return value
    def rhs(value):
        events.append('rhs')
        gc.collect()
        return value
    def main():
        token = Token()
        ref = weakref.ref(token)
        del token
        gc.collect()
        assert ref() is None and events == ['token']
        events.clear()
        value = Value('direct')
        ref = weakref.ref(value)
        del value
        gc.collect()
        assert ref() is None and events == ['dispose:direct']
        events.clear()
        drop(value=Value('argument'))
        gc.collect()
        assert references[0]() is None and events == ['dispose:argument']
        events.clear()
        selected = {'identity': 42}
        assert take(value=Value('temporary') or rhs(selected)) is selected
        assert events == ['temporary', 'dispose:temporary', 'rhs']
        events.clear()
        rescued_value = Rescue('resurrected')
        del rescued_value
        gc.collect()
        assert events == ['resurrected']
        assert rescued[0].name == 'resurrected'
        rescued.clear()
        gc.collect()
        assert events == ['resurrected']
        print('FINALIZER_CALLBACK_LEASE_OK')
    main()
''')


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
@pytest.mark.parametrize('provenance_probe', ('0', '2'))
def test_finalizer_callback_owner_native_single_collector(python_program_compiler, provenance_probe, request,
                                                explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'FINALIZER_CALLBACK_LEASE_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime,
                         capfd, provenance_probe=provenance_probe)
    assert (tmp_path / 'compiler-wrapper.stderr').read_text() == ''
