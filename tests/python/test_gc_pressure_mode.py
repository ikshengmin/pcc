"""Build-selected pressure preserves Python gc control and collection guards."""

import ast
import copy
import hashlib
from pathlib import Path

import pytest

from pcc.frontends.python import owned_runtime_build as owned
from pcc.frontends.python import runtime_gc_pressure as transform
from pcc.tools import runtime_archive_provenance as provenance


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'pcc/runtime/py'


def bodies(text, names, namespace):
    nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production pressure bodies>', 'exec'), namespace)


class Pressure:
    def __init__(self, every=3):
        self.cells = {
            'pcc_gc_config_initialized': 1, 'pcc_gc_backend_frozen': 1,
            'py_gc_enabled': 1, 'py_gc_threshold0': 2000,
            'pcc_gc_tracing_finish_commits': 1,
        }
        self.safe = 1
        self.sweep = 0
        self.calls = 0
        self.complete = True
        self.during = None
        self.ns = dict(
            i64=int, _PRESSURE_EVERY=every, _PRESSURE_LIMIT=(1 << 63) - 1,
            global_addr=lambda name: name,
            load_i32=lambda name, offset: self.cells.get(name, 0),
            load_i64=lambda name, offset: self.cells.get(name, 0),
            store_i32=lambda name, offset, value: self.cells.__setitem__(name, value),
            atomic_load_i64=lambda name, offset, order: self.cells.get(name, 0),
            atomic_store_i32=lambda name, offset, value, order: self.cells.__setitem__(name, value),
            atomic_store_i64=lambda name, offset, value, order: self.cells.__setitem__(name, value),
            atomic_cas_i32=self.cas, atomic_rmw_i64=self.rmw,
            unsigned_rem_i64=lambda value, divisor: value % divisor,
            pcc_thread_gc_pressure_safe=lambda: self.safe,
            pcc_gc_sweep_owed=lambda: self.sweep,
            pcc_gc_collect=self.collect,
            pcc_platform_abort=lambda: pytest.fail('unexpected abort'),
        )
        text = (RUNTIME / 'freestanding_gc_pressure.py').read_text()
        names = {n.name for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
        bodies(text, names, self.ns)

    def cas(self, name, offset, expected, replacement, success, failure):
        old = self.cells.get(name, 0)
        if old == expected:
            self.cells[name] = replacement
        return old

    def rmw(self, operation, name, offset, value, order):
        old = self.cells.get(name, 0)
        self.cells[name] = old + value if operation == 'add' else old - value
        return old

    def collect(self, reason):
        self.calls += 1
        if self.during:
            self.during(self)
        if self.complete:
            self.ns['pcc_gc_pressure_collection_completed']()
        return 0  # a valid empty collection also returns zero

    def publish(self, number):
        for _ in range(number):
            self.ns['pcc_gc_pressure_note_publication']()

    def counter(self, key):
        return self.ns['pcc_gc_pressure_counter'](key)

    def poll(self):
        return self.ns['pcc_gc_pressure_poll']()


def test_every_n_publications_request_but_never_collect_inside_publication():
    p = Pressure(3)
    p.publish(8)
    assert (p.counter(0), p.counter(1), p.calls) == (8, 2, 0)
    assert p.poll() == 1
    assert (p.calls, p.counter(2), p.counter(3), p.counter(5)) == (1, 1, 2, 0)
    assert p.poll() == 0 and p.calls == 1


@pytest.mark.parametrize('field,value', [
    ('pcc_gc_config_initialized', 0), ('pcc_gc_backend_frozen', 0),
    ('py_gc_enabled', 0), ('py_gc_threshold0', 0),
    ('py_gc_collecting', 1), ('pcc_gc_explicit_collect_active', 1),
])
def test_policy_and_collection_guards_defer_without_consuming_requests(field, value):
    p = Pressure(1)
    p.publish(1)
    old = p.cells.get(field, 0)
    p.cells[field] = value
    assert p.poll() == 0 and p.calls == 0
    assert (p.counter(1), p.counter(3), p.counter(4), p.counter(5)) == (1, 0, 1, 1)
    p.cells[field] = old
    assert p.poll() == 1 and p.counter(5) == 0


def test_no_park_or_world_owner_defers_and_next_legal_gate_services():
    p = Pressure(1)
    p.publish(1)
    p.safe = 0
    assert p.poll() == 0 and p.counter(5) == 1
    p.safe = 1
    assert p.poll() == 1 and p.counter(5) == 0


def test_another_pressure_owner_cannot_be_reset_or_collected_recursively():
    p = Pressure(1)
    p.publish(1)
    p.cells['pcc_gc_pressure_active'] = 1
    assert p.poll() == 0 and p.calls == 0
    assert p.cells['pcc_gc_pressure_active'] == 1
    assert p.counter(5) == 1 and p.counter(4) == 1


def test_callback_allocations_remain_pending_and_reentrant_poll_is_deferred():
    p = Pressure(1)
    p.publish(1)

    def callback(model):
        model.publish(2)
        assert model.poll() == 0
        assert model.calls == 1

    p.during = callback
    assert p.poll() == 1
    assert (p.counter(1), p.counter(2), p.counter(3), p.counter(4), p.counter(5)) == (3, 1, 1, 1, 2)
    p.during = None
    assert p.poll() == 1 and p.counter(5) == 0


def test_rejected_or_failed_collect_does_not_discard_the_request():
    p = Pressure(1)
    p.publish(1)
    p.complete = False
    assert p.poll() == 0
    assert (p.counter(2), p.counter(3), p.counter(5), p.counter(6)) == (0, 0, 1, 1)
    assert p.cells['pcc_gc_pressure_active'] == p.cells['pcc_gc_pressure_owner'] == 0
    p.complete = True
    assert p.poll() == 1


def test_static_image_scope_is_not_counted_as_heap_publication():
    p = Pressure(1)
    p.ns['pcc_gc_pressure_image_enter']()
    p.ns['pcc_gc_pressure_image_enter']()
    p.publish(9)
    p.ns['pcc_gc_pressure_image_leave']()
    p.publish(1)
    p.ns['pcc_gc_pressure_image_leave']()
    assert p.counter(0) == p.counter(1) == 0
    p.publish(1)
    assert p.counter(0) == p.counter(1) == 1


def test_managed_index_counts_new_publications_not_reregistration_or_scratch():
    p = Pressure(1)
    live = set()
    globals_ = {'pcc_gc_backend_frozen': 1}

    def insert(value):
        if value in live:
            return 0
        live.add(value)
        return 1

    ns = dict(
        ptr_is_null=lambda value: value == 0, is_tagged_int=lambda value: value & 1,
        global_addr=lambda name: name, load_i32=lambda name, offset: globals_[name],
        pcc_gc_granule_object_publish=lambda value: 0,
        _object_graph_lock=lambda: None, _object_graph_unlock=lambda: None,
        pcc_gc_managed_pointer_index_insert=insert,
        pcc_gc_reclaim_retired_index_tables_if_alone=lambda: None,
        pcc_gc_pressure_note_publication=p.ns['pcc_gc_pressure_note_publication'],
        pcc_gc_pressure_image_enter=p.ns['pcc_gc_pressure_image_enter'],
        pcc_gc_pressure_image_leave=p.ns['pcc_gc_pressure_image_leave'],
        _static_literal_record=lambda value: None,
    )
    text = transform.instrument_source((RUNTIME / 'py_gc_backend.py').read_text(), 'py_gc_backend', 1)
    bodies(text, {'pcc_gc_pointer_register', 'pcc_gc_pointer_register_static_literal'}, ns)
    register = ns['pcc_gc_pointer_register']
    assert register(4096) == 1 and p.counter(0) == 1
    assert register(4096) == 0 and p.counter(0) == 1
    live.remove(4096)
    assert register(4096) == 1 and p.counter(0) == 2
    assert register(0) == register(7) == -1 and p.counter(0) == 2
    assert ns['pcc_gc_pointer_register_static_literal'](8192) == 1
    assert p.counter(0) == 2
    ns['pcc_gc_managed_pointer_index_insert'] = lambda value: -1
    assert register(12288) == -1 and p.counter(0) == 2


@pytest.mark.parametrize('state,expected_count,status', [(7, 1, 1), (8, 0, 1), (9, 0, -1)])
def test_object_granule_counts_only_successful_reserved_to_live(state, expected_count, status):
    p = Pressure(1)
    current = [state]

    def compare(slot, offset, expected, replacement, success, failure):
        old = current[0]
        if old == expected:
            current[0] = replacement
        return old

    ns = dict(i64=int, GC_STATE_RESERVED=7, GC_STATE_LIVE=8, GRANULE_STATE_OFFSET=-48,
              _granule_object_slot=lambda value: value,
              ptr_is_null=lambda value: value == 0, null=lambda: 0,
              ptr_diff=lambda left, right: left - right, global_addr=lambda name: name,
              logical_shift_right_i64=lambda value, bits: value >> bits,
              atomic_cas_i64=compare, atomic_store_i64=lambda *args: None,
              pcc_gc_pressure_note_publication=p.ns['pcc_gc_pressure_note_publication'])
    text = transform.instrument_source((RUNTIME / 'freestanding_allocator.py').read_text(), 'freestanding_allocator', 1)
    bodies(text, {'pcc_gc_granule_object_publish'}, ns)
    assert ns['pcc_gc_granule_object_publish'](4096) == status
    assert p.counter(0) == expected_count
    if status == 1:
        assert ns['pcc_gc_granule_object_publish'](4096) == 1
        assert p.counter(0) == expected_count


@pytest.mark.parametrize('active,sweep,commits,had,expected', [
    (1, 0, 2, 0, 0), (0, 1, 2, 0, 0), (0, 0, 1, 0, 0),
    (0, 0, 2, 0, 1), (0, 0, 1, 1, 1),
])
def test_only_completed_tracing_work_acknowledges_pressure(active, sweep, commits, had, expected):
    p = Pressure()
    p.cells.update(pcc_gc_pressure_owner=1, pcc_gc_mark_active=active,
                   pcc_gc_tracing_finish_commits=commits)
    p.sweep = sweep
    p.ns['pcc_gc_pressure_tracing_completed'](1, had)
    assert p.counter(2) == expected


@pytest.mark.parametrize('materialized,tracked,oom,already,expected', [
    (0, 0, False, False, 1),   # completed empty heap
    (1, 1, False, False, 1),   # completed all-live heap, return value still zero
    (-1, 0, False, False, 0),  # materialization failed
    (1, 1, True, False, 0),    # candidate-vector allocation failed
    (1, 1, False, True, 0),    # recursive collect refused
])
def test_gc0_completion_hook_distinguishes_empty_success_from_failure(materialized, tracked, oom, already, expected):
    p = Pressure()
    p.cells.update(pcc_gc_pressure_owner=1, py_gc_collecting=int(already),
                   py_gc_tracked_count=tracked, pcc_gc_backend_selected=0)
    words = {3016: 1, 3032: 0}
    load = lambda base, offset: p.cells.get(base, 0) if isinstance(base, str) else words.get(base + offset, 0)

    def store(base, offset, value):
        if isinstance(base, str):
            p.cells[base] = value
        else:
            words[base + offset] = value

    ns = dict(
        i64=int, global_addr=lambda name: name,
        global_load_ptr=lambda name: 3000 if name == 'py_gc_head' else 0,
        load_i32=load, load_i64=load, load_ptr=load,
        atomic_load_i32=lambda base, offset, order: load(base, offset),
        store_i32=store, store_i64=store, store_ptr=store,
        ptr_is_null=lambda value: value == 0,
        pcc_stop_the_world=lambda: 0, pcc_resume_world=lambda: None,
        pcc_thread_safepoint=lambda: None,
        pcc_gc_reclaim_retired_index_tables=lambda: None,
        pcc_gc_default_table_lock=lambda: None, pcc_gc_default_table_unlock=lambda: None,
        pcc_gc_tracked_materialize=lambda: materialized,
        pcc_gc_tracked_dematerialize=lambda: None,
        pcc_gc_default_drain_deferred_nodes=lambda: None,
        _finish_subset=lambda: None,
        _recompute_reachability=lambda: None,
        _maybe_finalize_unreachable=lambda vector, count: 0,
        malloc=lambda size: 0 if oom else 5000,
        free=lambda value: None,
        pcc_gc_pressure_collection_completed=p.ns['pcc_gc_pressure_collection_completed'],
    )
    text = transform.instrument_source((RUNTIME / 'freestanding_gc_backend0_collector.py').read_text(),
                                       'freestanding_gc_backend0_collector', 3)
    bodies(text, {'_collect'}, ns)
    assert ns['_collect'](0, 0) == 0
    assert p.counter(2) == expected


@pytest.mark.parametrize('bit', [1, 1 << 24, 1 << 48, 1 << 53])
def test_kernel_safe_predicate_rejects_existing_lock_and_no_park_bits(bit):
    text = transform.instrument_source((RUNTIME / 'freestanding_thread_kernel_pthread.py').read_text(),
                                       'freestanding_thread_kernel_pthread', 3)
    state = [1 << 49]
    world = [0]
    ns = dict(global_addr=lambda name: name, load_i64=lambda name, offset: state[0],
              pcc_thread_owns_stopped_world=lambda: world[0])
    bodies(text, {'pcc_thread_gc_pressure_safe'}, ns)
    safe = ns['pcc_thread_gc_pressure_safe']
    assert safe() == 1
    state[0] |= bit
    assert safe() == 0
    state[0] = 0
    assert safe() == 0
    state[0] = 1 << 49
    world[0] = 1
    assert safe() == 0


@pytest.mark.parametrize('backend', [0, 4])
@pytest.mark.parametrize('completed', [0, 1])
def test_pressure_gate_preserves_automatic_work_and_external_resource_poll(
    backend, completed,
):
    # A successful stress collection must still allow the ordinary gate to
    # progress GC4 page retirement and poll external resource completions.
    calls = []
    ns = dict(
        pcc_gc_note_safepoint=lambda: calls.append('note'),
        pcc_thread_safepoint_gate=lambda: calls.append('gate'),
        pcc_gc_pressure_poll=lambda: (calls.append('pressure'), completed)[1],
        _gc_backend_fast=lambda: backend,
        _gc4_maybe_auto_step=lambda: calls.append('gc4'),
        _gc_maybe_auto_collect=lambda: calls.append('ordinary'),
        pcc_gc_external_resource_poll=lambda: calls.append('external'),
    )
    text = transform.instrument_source(
        (RUNTIME / 'py_obj.py').read_text(), 'py_obj', 3,
    )
    bodies(text, {'pcc_gc_safepoint'}, ns)
    ns['pcc_gc_safepoint']()
    assert calls == [
        'note', 'gate', 'pressure', 'gc4' if backend == 4 else 'ordinary',
        'external',
    ]


@pytest.mark.parametrize('module', transform.INSTRUMENTED_MODULES)
def test_default_source_is_exact_and_pressure_source_parses(module):
    original = (RUNTIME / (module + '.py')).read_text()
    assert transform.instrument_source(original, module, 0) == original
    pressure = transform.instrument_source(original, module, 3)
    assert pressure != original
    ast.parse(pressure)


def test_build_selection_keeps_default_members_and_adds_separate_pressure_configuration(monkeypatch):
    monkeypatch.delenv(transform.ENV, raising=False)
    ordinary = owned.runtime_build_config()
    assert 'gc_stress_every' not in ordinary
    runtime = str(ROOT / 'pcc/runtime')
    normal_members = owned.runtime_modules(runtime, 'arm64-apple-darwin')
    assert transform.MODULE not in normal_members
    monkeypatch.setenv(transform.ENV, '3')
    pressure = owned.runtime_build_config()
    assert pressure == {**ordinary, 'gc_stress_every': 3}
    assert owned.runtime_modules(runtime, 'arm64-apple-darwin', gc_stress_every=3) == normal_members + [transform.MODULE]
    provenance._validate_runtime_build_config(pressure)


@pytest.mark.parametrize('value', ['-1', 'abc', '2147483648'])
def test_invalid_pressure_setting_fails_explicitly(monkeypatch, value):
    monkeypatch.setenv(transform.ENV, value)
    with pytest.raises(ValueError, match='PCC_RUNTIME_GC_STRESS_EVERY'):
        owned.runtime_build_config()


def test_pressure_receipt_binds_effective_source_and_rejects_missing_or_changed_transform(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, 'codegen_checksum', lambda: 'a' * 64)
    runtime = tmp_path / 'runtime'
    source = runtime / 'py/freestanding_gc_pressure.py'
    source.parent.mkdir(parents=True)
    source.write_text((RUNTIME / 'freestanding_gc_pressure.py').read_text())
    effective = transform.instrument_source(source.read_text(), source.stem, 3)
    obj = tmp_path / 'freestanding_gc_pressure.o'
    # An integrity model: this test does not claim to execute these bytes.
    payload = b'object-identity-fixture'
    obj.write_bytes(payload)
    ir = tmp_path / 'pressure.ll'
    ir.write_text('source-identity-fixture')
    record = provenance.write_pcc_python_receipt(
        object_path=obj, ir_path=ir, source_path=source, runtime_root=runtime,
        target_triple='arm64-apple-darwin', object_bytes=payload,
        runtime_build_config={'threads': True, 'refcount': 'atomic', 'gc_stress_every': 3},
        source_transform={'kind': transform.KIND, 'interval': 3,
                          'effective_source_sha256': hashlib.sha256(effective.encode()).hexdigest()},
    )
    check = lambda value: provenance._validate_member_record(
        value, member=obj.name, member_bytes=payload, runtime_root=runtime)
    assert check(record) == 'arm64-apple-darwin'
    changed = copy.deepcopy(record)
    changed['source_transform']['effective_source_sha256'] = '0' * 64
    with pytest.raises(provenance.ProvenanceError, match='source transformation'):
        check(changed)
    changed = copy.deepcopy(record)
    del changed['source_transform']
    with pytest.raises(provenance.ProvenanceError, match='source transformation'):
        check(changed)
    changed = copy.deepcopy(record)
    changed['runtime_build_config']['gc_stress_every'] = 7
    with pytest.raises(provenance.ProvenanceError, match='source transformation'):
        check(changed)
    changed = copy.deepcopy(record)
    del changed['runtime_build_config']['gc_stress_every']
    with pytest.raises(provenance.ProvenanceError, match='unexpected runtime source transformation'):
        check(changed)
