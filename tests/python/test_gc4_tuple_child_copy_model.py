"""GC4 step/copy/remap with a tuple read in the SAME finite address space.

This is a component boundary, not iterator-next/tuple-conversion integration,
real thread scheduling, native stack capture, ZPage allocation or full GC.
The existing finite storage/index fixture supplies one reserved float target,
one selected page and one relocation member. Production bodies own admission,
conservative pins, drain, copy/payload, forwarding, slot remap and tuple read.
No address resolver repairs a stale consumer slot. GC3.copy is never called.
"""
from types import SimpleNamespace

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions
from tests.python.runtime_collector_contract_model import CollectorConfiguration, ThreadGate
from tests.python.test_gc3_norc_source_retirement import PY
from tests.python.test_gc_forwarding_install_pin_interleaving import _fixture


def _model(*, snapshot_alias=False, application_gate=True, omit_remap=False):
    config = CollectorConfiguration(4, requested_count_free=False)
    m, child, target, initial_plan = _fixture(4, 'preallocated')
    m.ns['pcc_gc_forwarding_install_plan_finish'](initial_plan)
    # Copy configuration only: never replace the live heap/list/index globals.
    for name in (
        'pcc_gc_backend_selected', 'pcc_gc_config_initialized',
        'pcc_gc_backend_frozen', 'pcc_gc_backend4_no_refcounts',
        'pcc_gc_nonmoving_count_free', 'pcc_gc_refcount_provenance_probe',
        'pcc_gc_list_container_mode', 'pcc_gc_read_barrier_enabled',
        'pcc_gc_refcount_atomic',
    ):
        m.store(name, 0, config.cells.get(name, 0))
    load_functions(PY / 'freestanding_gc_public_collection.py',
                   {'pcc_gc_config_ensure'}, m.ns)
    load_functions(PY / 'py_gc_backend.py', {'pcc_gc_fix_backend'}, m.ns)
    m.ns['gc_backend_current'] = m.ns['pcc_gc_config_ensure']
    for obj in (child, target):
        # Complete float allocation: header, f64 payload and zero padding.
        # Payload bits represent 3.25, rather than a fake STR with no bytes.
        m.zero(obj, 0, 32)
        m.store(obj, 0, 1)
        m.store(obj, 8, abi.PY_TYPE_FLOAT)
        m.store(obj, 12, 8)
        m.store(obj, abi.PYFLOATOBJECT_VALUE_OFFSET, 0x400A000000000000)
    owner, _, _ = m.value(abi.PY_TYPE_TUPLE)
    m.store(owner, 12, 8)
    m.store(owner, abi.PYTUPLEOBJECT_LEN_OFFSET, 1)
    m.store(owner, abi.PYTUPLEOBJECT_ITEMS_OFFSET, child)
    root = m.allocate(8)
    m.store(root, 0, owner)
    m.root_cells.append(root)
    # Snapshot words are finite inputs, not modeled CPU register capture.
    # Owner raw alias always stays at the same address; child is heap-only
    # in the move case and has a raw interior alias in the pin case.
    m.words = [owner] + ([child + abi.PYFLOATOBJECT_VALUE_OFFSET] if snapshot_alias else [])
    identity = m.ns['pcc_gc_identity_ensure'](child)
    stable_id = m.load(identity, 8)

    page, znode, page_entry, member = [m.allocate(n) for n in (120, 56, 16, 16)]
    for address, size in ((page, 120), (znode, 56), (page_entry, 16), (member, 16)):
        m.zero(address, 0, size)
    m.store(znode, 0, child)
    m.store(znode, 8, page)
    m.store(page, 112, znode)
    m.store(page, 108, 1)
    m.store(page_entry, 0, page)
    m.store(member, 0, child)
    m.store('pcc_gc_backend4_evacuation_page_head', 0, page_entry)
    m.store('pcc_gc_relocation_set_head', 0, member)
    members = {child: 1}
    znodes = {child: znode}
    allocations, detached = [], []

    def allocate(size, tag, flags):
        assert m.depth == 0 and not allocations
        assert size == 32 and tag == abi.PY_TYPE_FLOAT
        m.ns['pcc_gc_fix_backend']()
        m.store(target, 12, flags)
        allocations.append(target)
        return target

    def forget(obj):
        assert m.depth > 0 and members.pop(obj) == 1

    def detach(obj):
        assert m.depth > 0 and obj == child
        assert znodes.pop(obj) == znode
        detached.append(obj)
        return 0  # No native page allocator or release queue in this fixture.

    def empty_work(budget):
        assert budget > 0
        assert m.load('pcc_gc_backend4_store_buffer_entries_count') == 0
        return 0

    gate = ThreadGate()
    gate.tls, gate.stopped = gate.collector, True
    gate.words[gate.mutator, 16] = 1
    gate.words[gate.mutator, 40] = int(application_gate)
    m.stopped = True
    m.ns.update({
        'memmove': m.memmove,
        'pcc_gc_alloc': allocate,
        'pcc_gc_relocation_member_count': lambda obj: members.get(obj, 0),
        'pcc_gc_relocation_member_forget': forget,
        'pcc_gc_backend4_zpage_find': lambda obj: znodes.get(obj, 0),
        'pcc_gc_backend4_zpage_detach_for_relocation': detach,
        'pcc_gc_backend4_zpage_finish_relocation_detach': lambda value: None if value == 0 else m.abort(),
        'pcc_thread_live_count': lambda: 2,
        'pcc_thread_all_stopped_at_gates': gate.namespace['pcc_thread_all_stopped_at_gates'],
        'pcc_thread_owns_stopped_world': lambda: int(gate.stopped),
        'pcc_gc_backend4_step_remembered_roots': empty_work,
        'pcc_gc_backend4_step_generation_aging': empty_work,
        'pcc_gc_precise_stack_diff_if_requested': lambda: None,
        'pcc_platform_monotonic_us': lambda: 0,
        'pcc_gc_tracing_record_pause': lambda start, end: None,
    })
    # Explicitly load these entries over inherited fixtures, which include
    # historical doubles for some of the same symbol names.
    for filename, names in (
        ('freestanding_gc_relocation_copy.py', None),
        ('freestanding_gc_relocation_drain.py', None),
        ('freestanding_gc_relocation_remap.py', None),
        ('freestanding_gc_barrier_dispatcher.py', {'pcc_gc_step', '_selected_backend', '_tracing_work_pending'}),
        ('py_obj.py', {'pcc_gc_load_ptr', 'py_incref', 'py_decref'}),
        ('py_tuple.py', {'py_tuple_get'}),
    ):
        load_functions(PY / filename, names, m.ns)
    if omit_remap:
        # Fault control: remove only publication, not forwarding or the read.
        m.ns['pcc_gc_backend4_remap_heal_slot'] = lambda base, offset: None
    return SimpleNamespace(m=m, child=child, target=target, owner=owner,
                           root=root, gate=gate, stable_id=stable_id,
                           allocations=allocations, detached=detached)


def _run(f):
    m = f.m
    assert m.ns['pcc_gc_config_ensure']() == 4
    assert m.load('pcc_gc_backend4_no_refcounts') == 1
    assert m.load('pcc_gc_nonmoving_count_free') == 1
    assert m.load('pcc_gc_read_barrier_enabled') == 0
    assert m.ns['py_tuple_get'](f.owner, 0) == f.child
    before_words = tuple(m.words)
    moved = m.ns['pcc_gc_step'](1)
    m.ns['pcc_gc_relocation_settle_stopped']()
    assert m.load('pcc_gc_backend4_remap_owed') == 0
    assert tuple(m.words) == before_words
    assert m.load(f.root) == f.owner
    assert m.ns['pcc_gc_object_is_address_pinned'](f.owner) == int(bool(moved) or len(m.words) > 1)
    assert m.depth == 0 and f.gate.mutex_depth == 0
    # Finite scheduler transition, only after the production settle returns.
    f.gate.stopped = False
    m.resume()
    return moved


def _assert_moved_value(f):
    m = f.m
    assert f.allocations == [f.target] and f.detached == [f.child]
    assert f.target != f.child
    assert m.load(f.owner, abi.PYTUPLEOBJECT_ITEMS_OFFSET) == f.target, 'tuple slot remained stale'
    assert m.ns['py_tuple_get'](f.owner, 0) == f.target
    assert m.load(f.target, abi.PYFLOATOBJECT_VALUE_OFFSET) == 0x400A000000000000
    assert m.load(m.ns['pcc_gc_identity_ensure'](f.target), 8) == f.stable_id
    assert m.load('pcc_gc_backend4_evacuated_bytes_count') == 32


def test_gc4_step_moves_tuple_child_and_rewrites_real_slot():
    f = _model()
    assert _run(f) == 1
    _assert_moved_value(f)


@pytest.mark.parametrize('restriction', ['ordinary-park', 'snapshot-alias'])
def test_gc4_tuple_child_is_not_moved_without_legal_admission(restriction):
    f = _model(snapshot_alias=restriction == 'snapshot-alias',
               application_gate=restriction != 'ordinary-park')
    assert _run(f) == 0
    assert f.allocations == [] and f.detached == []
    assert f.child not in f.m.forwardings
    assert f.m.load(f.owner, abi.PYTUPLEOBJECT_ITEMS_OFFSET) == f.child
    assert f.m.ns['py_tuple_get'](f.owner, 0) == f.child
    if restriction == 'snapshot-alias':
        assert f.m.ns['pcc_gc_object_is_address_pinned'](f.child) == 1


def test_gc4_tuple_slot_oracle_detects_missing_real_remap():
    f = _model(omit_remap=True)
    assert _run(f) == 1
    with pytest.raises(AssertionError, match='tuple slot remained stale'):
        _assert_moved_value(f)
