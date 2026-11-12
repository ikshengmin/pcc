"""Continuation roots transfer only after the GC4 forwarding commit.

Production public copy, payload planning/copy/commit, object-slot visitors,
forwarding prepare/install/finish, pin, root register/unregister, mount and
continuation deallocator bodies run in GC3's finite counted-memory fixture.
The shared forwarding fixture reserves at most four entries per exact index.
Allocation returns one private reserved target. Raw-span providers validate
and own at most one eight-byte span per copy; there are no native ZPages.
Checked RC tails and retain plans are bounded counted doubles, not a full RC
cascade. The terminal target tail explicitly enters the real continuation
deallocator. Successful-copy teardown below also enters that deallocator
explicitly: it does NOT prove forwarded-source/target-death lifetime ordering,
source retirement, native ABI lowering, CPU races, or a complete collection.

These tests are intended for CI. No generated model or compiler is needed.
"""
from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import PY
from tests.python.test_gc_forwarding_install_pin_interleaving import _fixture


ROOT = 'pcc_gc_continuation_root_head'
REVISION = 'pcc_gc_root_registry_revision'
CURSOR = 'pcc_gc_backend3_continuation_root_scan_cursor'
SCAN_SLOT = 'pcc_gc_backend3_frame_root_scan_slot'
COPY = PY / 'freestanding_gc_relocation_copy.py'
PAYLOAD = PY / 'freestanding_gc_relocation_payload.py'


def _continuation(*, handle=True, registered=True, mounted=False, slots=1,
                  absent_chunk=False):
    m, source, target, old_plan = _fixture(4, 'preallocated')
    m.ns['pcc_gc_forwarding_install_plan_finish'](old_plan)
    for obj in (source, target):
        m.store(obj, 8, abi.PY_TYPE_CONTINUATION)
        m.store(m.index[obj], 8, 48)
        m.raw[obj] = 48
        for offset in (16, 24, 32, 40):
            m.store(obj, offset, 0)
    m.ns['gc_backend_current'] = lambda: 4
    load_functions(PY / 'freestanding_gc_root_registry.py', {
        'pcc_gc_register_continuation_root_node',
        'pcc_gc_register_continuation_root',
        'pcc_gc_unregister_continuation_root_node',
        'pcc_gc_unregister_continuation_root',
        'pcc_gc_continuation_root_unlink_locked',
        'pcc_gc_root_registry_note_mutation_locked',
        'pcc_gc_root_slot_count_from_map', 'pcc_gc_root_map_is_borrowed',
        'pcc_gc_cycle_requested_store_release',
    }, m.ns)
    load_functions(COPY, None, m.ns)
    load_functions(PY / 'py_coroutine.py', {
        'py_continuation_mount', 'py_dealloc_continuation',
    }, m.ns)
    child, _, _ = m.value()
    chunk = 0 if absent_chunk else m.malloc(32)
    values = m.malloc(8) if chunk and slots else 0
    if chunk:
        m.zero(chunk, 0, 32)
        m.store(chunk, 0, slots)
        m.store(chunk, 8, slots)
        m.store(chunk, 16, values)
    if values:
        m.store(values, 0, child)
    m.store(source, 24, chunk)
    m.store(source, 32, int(mounted))

    # A second real node makes list identity and neighbor preservation visible.
    anchor_map, anchor_slots = m.malloc(8), m.malloc(8)
    m.store(anchor_map, 0, 1)
    m.store(anchor_slots, 0, 0)
    anchor = m.ns['pcc_gc_register_continuation_root_node'](anchor_map, anchor_slots)
    node = 0
    if registered and not mounted and chunk and slots:
        node = m.ns['pcc_gc_register_continuation_root_node'](chunk, values)
        if handle:
            m.store(chunk, 24, node)
    m.store(CURSOR, 0, node or anchor)
    m.store(SCAN_SLOT, 0, 7)
    m.store('pcc_gc_cycle_requested', 0, 0)
    relocation = m.malloc(16)
    m.zero(relocation, 0, 16)
    m.store(relocation, 0, source)
    m.store('pcc_gc_relocation_set_head', 0, relocation)
    spans, allocations, deallocated, finished = {}, [], [], []

    def allocate(size, tag, flags):
        assert not allocations and size == 48 and tag == abi.PY_TYPE_CONTINUATION
        assert m.depth == 0
        allocations.append(target)
        m.store(target, 0, 1)
        m.store(target, 12, flags)
        return target

    def preflight(obj, size):
        assert m.depth > 0 and obj == target
        return int(size in (0, 8) and obj not in spans)

    def publish(obj, head, count, size):
        assert preflight(obj, size) and count == 1 and size == 8
        assert head and m.load(head, 40) == 0
        assert m.load(head, 16) == size
        buffer = m.load(head, 8)
        assert m.raw[buffer] == size and buffer != values
        spans[obj] = (head, buffer)
        return 1

    def free_object(obj):
        # Deallocator already owns/frees chunk and slots. This provider owns
        # only the span metadata and object storage, never source retirement.
        assert obj == target and obj not in deallocated
        deallocated.append(obj)
        if obj in spans:
            span, buffer = spans.pop(obj)
            assert buffer in m.freed
            m.free(span)
        m.free(obj)

    def decref(obj):
        assert m.load(obj, 0) > 0
        m.counted_decref(obj)
        if obj == target and m.load(obj, 0) == 0:
            m.ns['py_dealloc_continuation'](obj)

    def retain(plan, value):
        assert m.depth > 0 and value == child
        m.counted_incref(value)
        return value

    def finish_detach(detached):
        assert detached == 0 and m.depth == 0
        finished.append(detached)

    m.ns.update({
        'memmove': m.memmove,
        'pcc_gc_alloc': allocate,
        'pcc_gc_backend4_relocate_copy_supported_tag':
            lambda tag: int(tag == abi.PY_TYPE_CONTINUATION),
        'pcc_gc_backend4_zpage_payload_span_preflight_locked': preflight,
        'pcc_gc_backend4_zpage_publish_relocation_payload_spans_locked': publish,
        'pcc_gc_backend4_zpage_find': lambda obj: 0,
        'pcc_gc_backend4_zpage_detach_for_relocation': lambda obj: 0,
        'pcc_gc_backend4_zpage_finish_relocation_detach': finish_detach,
        'pcc_gc_retain_plan_prepare_locked': retain,
        'pcc_gc_retain_plan_finish': lambda plan: None,
        '_py_decref_checked_tail': decref,
        'pcc_gc_free_object_memory': free_object,
        '_type_of': lambda obj: m.load(obj, 8),
    })
    m.events.clear()
    return SimpleNamespace(m=m, source=source, target=target, child=child,
                           chunk=chunk, values=values, node=node, anchor=anchor,
                           relocation=relocation, spans=spans,
                           allocations=allocations, deallocated=deallocated,
                           finished=finished)


def _registry(f):
    m = f.m
    return (m.load(ROOT), m.load(REVISION), m.load(CURSOR), m.load(SCAN_SLOT),
            tuple((node, tuple(m.load(node, offset) for offset in range(0, 56, 8)))
                  for node in (f.node, f.anchor) if node),
            m.load(f.chunk, 24) if f.chunk else 0)


@pytest.mark.parametrize('handle', [True, False])
@pytest.mark.parametrize('failure', ['pin', 'first-index-commit'])
def test_rejected_forwarding_preserves_source_registration(handle, failure):
    f = _continuation(handle=handle)
    m = f.m
    before = _registry(f)
    original_payload = m.ns['pcc_gc_relocate_copy_payload_prepared_locked']
    observed = []

    def payload(*args):
        result = original_payload(*args)
        assert result == 1 and m.depth > 0
        target_chunk = m.load(f.target, 24)
        assert target_chunk != f.chunk and m.raw[target_chunk] == 32
        assert m.load(target_chunk, 24) == 0
        assert _registry(f) == before
        assert m.load(f.child, 0) == 2
        observed.append(target_chunk)
        if failure == 'pin':
            m.ns['pcc_gc_pin'](f.source)
        return result

    commits = []

    def reject_first(kind, slots_io, capacity, extra, metadata_io):
        assert observed and m.depth > 0
        commits.append(kind)
        assert kind == 2 and capacity == 0 and extra == 2
        assert metadata_io == 0
        assert m.load(slots_io) == 0
        return -1

    m.ns['pcc_gc_relocate_copy_payload_prepared_locked'] = payload
    if failure == 'first-index-commit':
        m.ns['pcc_gc_forwarding_plan_index_commit'] = reject_first
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == 0
    assert len(observed) == 1 and f.allocations == [f.target]
    assert _registry(f) == before
    assert m.load(f.child, 0) == 1
    assert f.deallocated == [f.target] and not f.spans
    assert observed[0] in m.freed and f.chunk not in m.freed
    assert f.node not in m.freed and f.values not in m.freed
    assert m.load(f.source, 24) == f.chunk
    assert not m.forwardings and not m.reverse and not m.identities
    assert m.load('pcc_gc_relocation_set_head') == f.relocation
    assert m.load(f.source, 12) & 2048 == 0
    assert commits == ([2] if failure == 'first-index-commit' else [])
    assert m.load('pcc_gc_relocation_pin_rejects') == int(failure == 'pin')
    assert f.finished == [0] and m.depth == 0


@pytest.mark.parametrize('handle', [True, False])
@pytest.mark.parametrize('teardown', ['mount-then-dealloc', 'dealloc'])
def test_committed_copy_transfers_same_root_then_unregisters_once(handle, teardown):
    f = _continuation(handle=handle)
    m = f.m
    revision = m.load(REVISION)
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == f.target
    chunk = m.load(f.target, 24)
    values = m.load(chunk, 16)
    assert chunk != f.chunk and values != f.values and m.raw[chunk] == 32
    assert m.load(values) == f.child and m.load(f.child) == 2
    assert m.load(f.chunk, 24) == 0
    assert m.load(chunk, 24) == (f.node if handle else 0)
    assert m.load(ROOT) == f.node and m.load(f.node, 16) == f.anchor
    assert m.load(f.anchor, 48) == f.node
    assert m.load(f.node, 0) == chunk and m.load(f.node, 8) == values
    assert m.load(CURSOR) == f.node and m.load(SCAN_SLOT) == 0
    assert m.load(REVISION) == revision + 1
    assert m.load(m.forwardings[f.source], 8) == f.target
    assert m.load(f.target, 0) == 3  # Private copy + forwarding + source count.
    assert m.load(f.source, 12) & 2048 and m.load(f.target, 12) & 8192
    assert m.load('pcc_gc_relocation_set_head') == 0
    assert f.relocation in m.freed
    if teardown == 'mount-then-dealloc':
        # No slots_out: native frame-root stores are outside this fixture.
        assert m.ns['py_continuation_mount'](f.target, 0) == 0
        assert m.load(chunk, 24) == 0 and m.load(f.target, 32) == 1
        assert m.ns['py_continuation_mount'](f.target, 0) == 0
        assert m.load(REVISION) == revision + 2
    # Explicit deallocation stage, not a simulated GC4 target-death verdict.
    m.ns['py_dealloc_continuation'](f.target)
    assert m.load(ROOT) == f.anchor and m.load(f.anchor, 48) == 0
    assert m.load(CURSOR) == f.anchor and m.load(SCAN_SLOT) == 0
    assert m.load(REVISION) == revision + 2
    assert m.events.count(('free', f.node)) == 1
    assert m.load(f.child) == 1 and m.load(f.source, 24) == f.chunk
    assert f.chunk not in m.freed and f.values not in m.freed
    assert f.deallocated == [f.target] and not f.spans and m.depth == 0


@pytest.mark.parametrize('case', ['unregistered', 'mounted', 'zero-slots', 'absent-chunk'])
def test_copy_without_suspended_registration_keeps_unrelated_root(case):
    f = _continuation(registered=False, mounted=case == 'mounted',
                      slots=0 if case == 'zero-slots' else 1,
                      absent_chunk=case == 'absent-chunk')
    m = f.m
    before = _registry(f)
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == f.target
    assert _registry(f) == before
    chunk = m.load(f.target, 24)
    assert bool(chunk) == bool(f.chunk)
    if chunk:
        assert m.raw[chunk] == 32 and m.load(chunk, 24) == 0
    m.ns['py_dealloc_continuation'](f.target)
    assert _registry(f) == before
    assert m.load(f.child) == 1 and not f.spans
    assert f.anchor not in m.freed and m.depth == 0


def test_prepared_payload_has_no_registration_commit_and_public_copy_orders_commit():
    payload = ast.parse(PAYLOAD.read_text(encoding='utf-8'))
    finish = next(n for n in payload.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_relocate_copy_payload_finish')
    assert not any(isinstance(n, ast.Name) and n.id in {
        '_retarget_continuation_root_slots',
        'pcc_gc_relocation_payload_commit_continuation',
    } for n in ast.walk(finish))
    copy = ast.parse(COPY.read_text(encoding='utf-8'))
    transaction = next(n for n in copy.body if isinstance(n, ast.FunctionDef)
                       and n.name == 'pcc_gc_backend4_relocate_copy_preallocated_unlocked')
    calls = {name: [n for n in ast.walk(transaction) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == name]
             for name in ('pcc_gc_relocate_copy_payload_prepared_locked',
                          'pcc_gc_install_forwarding_preallocated_unlocked',
                          'pcc_gc_relocation_payload_commit_continuation')}
    assert all(len(nodes) == 1 for nodes in calls.values())
    assert [nodes[0].lineno for nodes in calls.values()] == sorted(
        nodes[0].lineno for nodes in calls.values())
    commit_guard = next(n for n in ast.walk(transaction) if isinstance(n, ast.If)
                        and isinstance(n.test, ast.Compare)
                        and any(isinstance(v, ast.Name) and v.id == 'PY_TYPE_CONTINUATION'
                                for v in ast.walk(n.test)))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == 'pcc_gc_relocation_payload_commit_continuation'
               for n in ast.walk(commit_guard))


@pytest.mark.parametrize('handle', [True, False])
def test_legacy_payload_wrapper_keeps_successful_retarget_contract(handle):
    f = _continuation(handle=handle)
    m = f.m
    revision = m.load(REVISION)
    # This wrapper's contract predates GC4's prepared transaction. It is
    # deliberately exercised without pretending GC3 admits continuations.
    with m.locked():
        assert m.ns['pcc_gc_relocate_copy_payload'](
            f.source, f.target, abi.PY_TYPE_CONTINUATION, 48) == 1
    chunk = m.load(f.target, 24)
    assert m.load(f.chunk, 24) == 0
    assert m.load(chunk, 24) == (f.node if handle else 0)
    assert m.load(f.node, 0) == chunk
    assert m.load(f.node, 8) == m.load(chunk, 16)
    assert m.load(ROOT) == f.node and m.load(REVISION) == revision + 1
    assert m.load(SCAN_SLOT) == 0 and not m.forwardings
    m.ns['py_dealloc_continuation'](f.target)
    assert m.events.count(('free', f.node)) == 1
    assert m.load(f.child) == 1 and not f.spans and m.depth == 0


@pytest.mark.parametrize('handle', [True, False])
def test_second_commit_transfers_cached_handle_and_same_node_again(handle):
    f = _continuation(handle=handle)
    m = f.m
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == f.target
    middle_chunk = m.load(f.target, 24)
    revision = m.load(REVISION)
    # Only the first hop exercises public copy/forwarding. For the second
    # hop, seed a prepared C payload and invoke the real commit helper.
    # This tests repeated registry ownership, not B-to-C forwarding or RC.
    third, third_chunk, third_slots = m.malloc(48), m.malloc(32), m.malloc(8)
    m.zero(third, 0, 48)
    m.zero(third_chunk, 0, 32)
    m.store(third, 24, third_chunk)
    m.store(third_chunk, 0, 1)
    m.store(third_chunk, 8, 1)
    m.store(third_chunk, 16, third_slots)
    m.store(third_slots, 0, f.child)
    m.store(SCAN_SLOT, 0, 9)
    with m.locked():
        m.ns['pcc_gc_relocation_payload_commit_continuation'](f.target, third)
    assert m.load(f.chunk, 24) == m.load(middle_chunk, 24) == 0
    assert m.load(third_chunk, 24) == (f.node if handle else 0)
    assert m.load(f.node, 0) == third_chunk
    assert m.load(f.node, 8) == third_slots
    assert m.load(ROOT) == f.node and m.load(f.node, 16) == f.anchor
    assert m.load(f.anchor, 48) == f.node
    assert m.load(CURSOR) == f.node and m.load(SCAN_SLOT) == 0
    assert m.load(REVISION) == revision + 1 and m.depth == 0
