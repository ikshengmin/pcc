"""Real next/from-list consume a child copied and remapped in the same heap.

Two success components only. Reuse the independently tested single-page GC4
fixture without changing it. The real application-gate body wraps its finite
stop/copy/remap/resume callback. Finite scheduler records, snapshot words,
page allocator boundary and settle early-return limits still apply.
No unregister mover, consumer-time relocation, logical RC counter or resolver
is added. Error cleanup, finalizers, native execution and whole-GC acceptance
remain separate obligations in the original consumer tests.
"""
import ast
from types import SimpleNamespace

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import PY
from tests.python.test_gc4_tuple_child_copy_model import _model, _run


# Named entries replace inherited doubles; private same-module dependencies
# are loaded by the shared loader. Cross-module dependencies are explicit.
CONSUMER_BODIES = (
    ('py_iter.py', {'py_obj_next'}),
    ('py_list.py', {'_native_body_py_list_get', '_native_body_py_list_len'}),
    ('py_list_native_entry.py', {'py_list_get', 'py_list_len'}),
    ('py_tuple.py', {'py_tuple_from_list', 'py_tuple_new', 'py_tuple_set_item'}),
    ('py_obj.py', {
        'pcc_gc_load_ptr', 'pcc_gc_store_ptr', '_store_root_nonmoving_tracing',
        'py_incref', 'py_decref', 'pcc_gc_publish_initialized',
        'pcc_gc_retain_plan_prepare_locked', 'pcc_gc_retain_plan_finish',
        '_py_incref_prepare', '_py_incref_finish', '_py_refcount_prepared_reset',
    }),
    ('freestanding_gc_root_operations.py', {
        'pcc_gc_pin', 'pcc_gc_unpin', 'pcc_gc_take_pinned_slot',
        'pcc_gc_try_init_ptr_locked', 'pcc_gc_metric_shard',
    }),
    ('freestanding_gc_barrier_dispatcher.py', {'pcc_gc_count_free_store_shade'}),
    ('py_substrate.py', {'py_err_occurred'}),
    ('py_gc_backend.py', {'pcc_gc_backend'}),
)


# These internal flag names are not exported by py_abi_constants. Keep their
# producing statements checked below, rather than silently inventing a clean
# published tuple. MALLOC provenance has a public ABI name and uses it.
_GC4_PENDING = 16384
_GC_WHITE = 8
_GC_YOUNG = 128
_TUPLE_ALLOCATION_FLAGS = (
    _GC4_PENDING | abi.PY_FLAG_GC_MALLOC_ALLOC | _GC_WHITE | _GC_YOUNG
)
_THREAD_RECORD_STATE = 16
_THREAD_APPLICATION_GATE = 40


def _check_allocation_provider_contract():
    """Source guard for the bounded provider, NOT execution of allocator GC."""
    checks = (
        ('py_obj.py', 'pcc_gc_alloc', (
            f'stored_flags = stored_flags | {_GC4_PENDING}',
            f'stored_flags = (stored_flags & ~65536) | {abi.PY_FLAG_GC_MALLOC_ALLOC}',
            'pcc_gc_note_object_allocated_sized(obj, size)',
        )),
        ('py_gc_backend.py', 'pcc_gc_note_object_allocated_sized', (
            f'color: int = {_GC_WHITE}',
            'new_flags: int = (flags & ~(56 | 2048 | 8192)) | color',
            f'new_flags = new_flags | {_GC_YOUNG}',
            'store_i32(o, 12, new_flags)',
            'store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)',
            'global_store_ptr("pcc_gc_last_alloc", o)',
        )),
    )
    for filename, name, statements in checks:
        module = ast.parse((PY / filename).read_text())
        body = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
        actual = {ast.dump(node) for node in ast.walk(body)}
        for statement in statements:
            assert ast.dump(ast.parse(statement).body[0]) in actual, (name, statement)


def _run_at_application_gate(f):
    """Real gate entry/exit; parking and scheduling remain finite providers."""
    m, gate = f.m, f.gate
    assert gate.tls == gate.collector and gate.stopped and m.stopped
    assert gate.words[gate.mutator, _THREAD_RECORD_STATE] == 1
    assert gate.words[gate.mutator, _THREAD_APPLICATION_GATE] == 0
    # _model supplies a stopped snapshot. Start this consumer's epoch from
    # running state instead; only the production gate may write field 40.
    gate.words[gate.mutator, _THREAD_RECORD_STATE] = 0
    gate.tls, gate.stopped = gate.mutator, False
    m.resume()
    moved = []

    def stopped_poll():
        assert gate.tls == gate.mutator and not gate.stopped and not m.stopped
        assert gate.words[gate.mutator, _THREAD_RECORD_STATE] == 0
        assert gate.words[gate.mutator, _THREAD_APPLICATION_GATE] == 1
        # Same finite park/resume boundary as ThreadGate.poll. Production
        # safepoint uses _thread_record_mark(1, frame), then (0, null()).
        gate.words[gate.mutator, _THREAD_RECORD_STATE] = 1
        gate.tls, gate.stopped = gate.collector, True
        m.stop()
        moved.append(_run(f))
        assert not gate.stopped and not m.stopped
        assert gate.words[gate.mutator, _THREAD_APPLICATION_GATE] == 1
        gate.tls = gate.mutator
        gate.words[gate.mutator, _THREAD_RECORD_STATE] = 0

    original_poll = gate.namespace['pcc_thread_safepoint']
    gate.namespace['pcc_thread_safepoint'] = stopped_poll
    try:
        gate.namespace['pcc_thread_safepoint_gate']()
    finally:
        gate.namespace['pcc_thread_safepoint'] = original_poll
    assert len(moved) == 1
    assert gate.tls == gate.mutator and not gate.stopped and not m.stopped
    assert gate.words[gate.mutator, _THREAD_RECORD_STATE] == 0, 'mutator did not resume'
    assert gate.words[gate.mutator, _THREAD_APPLICATION_GATE] == 0, 'application gate did not exit'
    return moved[0]


def _consumer(count):
    assert count in (1, 2)
    _check_allocation_provider_contract()
    f = _model(application_gate=False)
    m = f.m
    lst, _, _ = m.value(abi.PY_TYPE_LIST)
    iterator, _, _ = m.value(abi.PY_TYPE_ITER)
    items = m.malloc(count * abi.C_POINTER_SIZE)
    m.store(lst, abi.PYOBJECTHEADER_FLAGS_OFFSET, 8)
    m.store(lst, abi.PYLISTOBJECT_LENGTH_OFFSET, count)
    m.store(lst, abi.PYLISTOBJECT_CAPACITY_OFFSET, count)
    m.store(lst, abi.PYLISTOBJECT_ITEMS_OFFSET, items)
    for index in range(count):
        m.store(items, index * abi.C_POINTER_SIZE, f.child)
    m.store(iterator, abi.PYOBJECTHEADER_FLAGS_OFFSET, 8)
    m.store(iterator, 16, lst)
    m.store(iterator, 24, 0)
    # Raw input aliases must stay put. The child exists only in heap slots
    # in the finite mutator snapshot, so only that child may move.
    m.words.extend((lst, iterator))
    assert _run_at_application_gate(f) == 1
    for index in range(count):
        assert m.load(items, index * abi.C_POINTER_SIZE) == f.target, 'list slot remained stale'
    assert m.load(iterator, 16) == lst
    assert m.ns['pcc_gc_object_is_address_pinned'](lst) == 1
    assert m.ns['pcc_gc_object_is_address_pinned'](iterator) == 1
    assert m.load('pcc_gc_refcount_provenance_probe') == 2
    assert m.load('pcc_gc_list_container_mode') == 0
    assert m.load('pcc_gc_nonmoving_count_free') == 1
    for filename, names in CONSUMER_BODIES:
        load_functions(PY / filename, names, m.ns)
    m.ns['_init_config'] = m.ns['pcc_gc_config_ensure']
    assert m.ns['pcc_gc_backend']() == 4
    # Snapshot pinning lives in node metadata, not this header bit. Set an
    # explicit preexisting header pin using the production body as well.
    m.ns['pcc_gc_pin'](lst)
    source_header_pin = m.load(lst, 12) & abi.PY_FLAG_GC_PINNED
    source_node_pin = m.load(m.index[lst], 56)
    source_effective_pin = m.ns['pcc_gc_object_is_address_pinned'](lst)
    assert source_header_pin == abi.PY_FLAG_GC_PINNED
    assert source_node_pin & 1 and source_effective_pin == 1

    def unexpected(*args):
        raise AssertionError('unexpected success-probe fallback')

    # No generic success stubs: these paths are outside the two selected
    # successful operations and must fail if unexpectedly reached.
    for name in (
        'py_exc_new', 'py_raise_owned', 'py_current_exception',
        'py_clear_exception', 'py_runtime_error_if_unset', 'py_gc_track',
        'pcc_container_lock', 'pcc_container_unlock',
        'pcc_gc_native_frame_enter', 'pcc_gc_native_root_bind_range',
        'pcc_gc_native_root_unbind', 'pcc_gc_native_frame_leave',
        'pcc_gc_scheduler_root_unregister_handle',
    ):
        m.ns[name] = unexpected

    baseline = (m.load('pcc_gc_forwarding_population'),
                m.load('pcc_gc_backend4_evacuated_bytes_count'))
    baseline_counts = {obj: m.load(obj, 0) for obj in (lst, iterator, f.target)}
    parks, initialized, allocated = [], [], []
    store_shard = m.ns['pcc_gc_metric_shard']() + 16
    stores_before = m.load('pcc_gc_metric_shards', store_shard)
    no_park = [0]

    def ordinary_park(where):
        assert m.depth == 0 and no_park[0] == 0 and not f.gate.stopped
        assert f.gate.park(application_gate=False) == 0
        parks.append(where)
        assert (m.load('pcc_gc_forwarding_population'),
                m.load('pcc_gc_backend4_evacuated_bytes_count')) == baseline

    def lock():
        if m.depth == 0:
            ordinary_park('graph-lock')
        m.lock()

    def unlock():
        m.unlock()
        if m.depth == 0:
            ordinary_park('graph-unlock')

    def no_park_enter():
        no_park[0] += 1

    def no_park_exit():
        assert no_park[0] > 0
        no_park[0] -= 1

    real_init = m.ns['pcc_gc_try_init_ptr_locked']

    def observed_init(destination, source):
        assert m.depth > 0
        assert source in {items + index * abi.C_POINTER_SIZE for index in range(count)}
        assert m.load(destination) == 0 and m.load(source) == f.target
        result = real_init(destination, source)
        assert result == 1 and m.load(destination) == f.target
        assert m.load(source) == f.target
        initialized.append(source)
        return result

    def allocate_tuple(size, tag, flags):
        # Raw storage/header/index provider only. The production constructor
        # must write length/items and production stores must fill the slots.
        assert m.depth == 0 and not allocated and count == 2
        assert size == abi.PYTUPLEOBJECT_SIZE + count * abi.C_POINTER_SIZE
        assert tag == abi.PY_TYPE_TUPLE and flags == 0
        m.ns['pcc_gc_fix_backend']()
        obj = m.malloc(size)
        m.zero(obj, 0, size)
        m.store(obj, 0, 1)
        m.store(obj, 8, tag)
        assert m.load('pcc_gc_mark_active') == 0
        assert m.load('pcc_gc_backend4_no_refcounts') == 1
        m.store(obj, 12, _TUPLE_ALLOCATION_FLAGS)
        m.store('pcc_gc_cycle_requested', 0, 1)
        m.store('pcc_gc_last_alloc', 0, obj)
        node = m.ns['pcc_gc_object_node_alloc']()
        m.zero(node, 0, 88)
        m.store(node, 0, obj)
        m.store(node, 8, size)
        old = m.load('pcc_gc_object_head')
        m.store(node, 16, old)
        if old:
            m.store(old, 40, node)
        m.ns['pcc_gc_object_set_list_head'](node)
        assert m.insert_index(obj, node) == 1
        m.ns['pcc_gc_backend3_young_link_head'](node)
        m.store('pcc_gc_live_bytes', 0, m.load('pcc_gc_live_bytes') + size)
        assert m.load(obj, 12) == _TUPLE_ALLOCATION_FLAGS
        assert m.load('pcc_gc_cycle_requested') == 1
        assert m.load('pcc_gc_last_alloc') == obj
        allocated.append(obj)
        return obj

    m.ns.update({
        'pcc_py_gc_minor_graph_lock': lock,
        'pcc_py_gc_minor_graph_unlock': unlock,
        'pcc_thread_no_park_enter': no_park_enter,
        'pcc_thread_no_park_exit': no_park_exit,
        'pcc_gc_try_init_ptr_locked': observed_init,
        'pcc_gc_alloc': allocate_tuple,
    })
    return SimpleNamespace(f=f, m=m, lst=lst, iterator=iterator, items=items,
                           count=count, baseline=baseline, counts=baseline_counts,
                           parks=parks, initialized=initialized, allocated=allocated,
                           no_park=no_park, header_pin=source_header_pin,
                           node_pin=source_node_pin, effective_pin=source_effective_pin,
                           store_shard=store_shard, stores_before=stores_before)


def _assert_consumer_boundary(c):
    m, f = c.m, c.f
    assert c.parks and all(admitted == 0 for _, admitted in f.gate.observations)
    assert c.initialized == [c.items + i * abi.C_POINTER_SIZE for i in range(c.count)]
    assert (m.load('pcc_gc_forwarding_population'),
            m.load('pcc_gc_backend4_evacuated_bytes_count')) == c.baseline
    assert {obj: m.load(obj, 0) for obj in c.counts} == c.counts
    assert m.depth == c.no_park[0] == f.gate.mutex_depth == 0
    assert not m.stopped and not f.gate.stopped
    assert m.ns['py_err_occurred']() == 0
    assert m.load('py_tls_current_exc_storage') == 0
    assert m.load('py_exc_pending_threads', 192) == 0
    assert m.load(c.lst, abi.PYLISTOBJECT_LENGTH_OFFSET) == c.count
    assert m.load(c.lst, abi.PYLISTOBJECT_ITEMS_OFFSET) == c.items
    assert m.load(c.iterator, 16) == c.lst
    assert m.load(c.lst, 12) & abi.PY_FLAG_GC_PINNED == c.header_pin
    assert m.load(m.index[c.lst], 56) == c.node_pin
    assert m.ns['pcc_gc_object_is_address_pinned'](c.lst) == c.effective_pin
    assert m.load(f.target, abi.PYOBJECTHEADER_FLAGS_OFFSET) & abi.PY_FLAG_GC_PINNED == 0
    assert m.load(f.target, abi.PYFLOATOBJECT_VALUE_OFFSET) == 0x400A000000000000
    assert m.load(m.ns['pcc_gc_identity_ensure'](f.target), 8) == f.stable_id
    for index in range(c.count):
        assert m.load(c.items, index * abi.C_POINTER_SIZE) == f.target


def test_next_consumes_real_remapped_list_child():
    c = _consumer(1)
    assert c.m.ns['py_obj_next'](c.iterator) == c.f.target, 'next returned stale child'
    assert c.m.load(c.iterator, 24) == 1
    assert c.allocated == []
    assert c.m.load('pcc_gc_metric_shards', c.store_shard) == c.stores_before
    _assert_consumer_boundary(c)


def test_from_list_copies_real_remapped_aliases_into_tuple():
    c = _consumer(2)
    result = c.m.ns['py_tuple_from_list'](c.lst)
    assert c.allocated == [result] and result != 0
    assert c.m.load(result, abi.PYTUPLEOBJECT_LEN_OFFSET) == 2
    assert c.m.load(result, abi.PYOBJECTHEADER_REFCOUNT_OFFSET) == 1
    # Current setter does not publish/clear the pending bit. Preserve that
    # observable state rather than using clean flags to make this test pass.
    assert c.m.load(result, abi.PYOBJECTHEADER_FLAGS_OFFSET) == _TUPLE_ALLOCATION_FLAGS
    assert c.m.load('pcc_gc_cycle_requested') == 1
    assert c.m.load('pcc_gc_last_alloc') == result
    assert c.m.load('pcc_gc_metric_shards', c.store_shard) == c.stores_before + 2
    for index in range(2):
        assert c.m.load(result, abi.PYTUPLEOBJECT_ITEMS_OFFSET + index * abi.C_POINTER_SIZE) == c.f.target, 'tuple output slot remained stale'
    assert c.m.load(result, abi.PYOBJECTHEADER_FLAGS_OFFSET) & abi.PY_FLAG_GC_PINNED == 0
    assert c.m.load(c.iterator, 24) == 0
    _assert_consumer_boundary(c)
