"""GC4 copy's committed source updates preserve completed pin operations.

Reuse the finite counted-memory continuation fixture: production public copy,
prepared payload, forwarding plan/install/finish and pin/unpin bodies execute,
with one reserved target, bounded exact indexes, one child and one raw span.
The deterministic hook completes a pin operation immediately before either
source-header update. It also intercepts a legacy plain store after its stale
value has been evaluated, so reverting either update exposes lost metadata.

This is a word-update regression, not proof of CPU scheduling, native atomic
lowering, source retirement, a full collector cycle or check-pin-to-publication
exclusion. Normal collector stopped-world requirements are unchanged. In
particular, the unpin schedule seeds its pin only AFTER forwarding succeeds;
it does not claim that an initially pinned source can be admitted for copying.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc4_copy_continuation_roots import _continuation


IMMORTAL, PINNED, SWEEP_CANDIDATE, FORWARDED = 1, 64, 1024, 2048
UNRELATED = 0x1000000
UPDATES = {
    'clear-sweep': ('and', ~SWEEP_CANDIDATE),
    'set-immortal': ('or', IMMORTAL),
}


@pytest.mark.parametrize('site', ['clear-sweep', 'set-immortal'])
@pytest.mark.parametrize('operation', ['pin', 'unpin'])
def test_copy_source_updates_preserve_completed_pin_operation(site, operation):
    f = _continuation()
    m = f.m
    initial = m.load(f.source, 12) | SWEEP_CANDIDATE | UNRELATED
    assert initial & (PINNED | IMMORTAL | FORWARDED) == 0
    m.store(f.source, 12, initial)
    install = m.ns['pcc_gc_install_forwarding_preallocated_unlocked']
    installed, injected, updates = [], [], []
    armed = False

    def install_then_arm(source, target, plan):
        nonlocal armed
        assert source == f.source and target == f.target and m.depth > 0
        assert m.ns['pcc_gc_object_is_address_pinned'](source) == 0
        result = install(source, target, plan)
        assert result == 0
        assert m.load(m.forwardings[source], 8) == target
        assert m.load(source, 12) == initial | FORWARDED
        installed.append(result)
        # A pin before admission would reject the transaction, never reach
        # either update. Seed this one only after the real installer commits.
        if operation == 'unpin':
            m.ns['pcc_gc_pin'](source)
        armed = True
        return result

    def before_update(boundary):
        nonlocal armed
        assert installed == [0] and m.depth > 0
        updates.append(boundary)
        if not armed or boundary != site:
            return
        armed = False  # Pin/unpin's own bit RMW must never recurse here.
        before = m.load(f.source, 12)
        expected_before = initial | FORWARDED
        if boundary == 'set-immortal':
            expected_before &= ~SWEEP_CANDIDATE
        if operation == 'unpin':
            expected_before |= PINNED
        assert before == expected_before
        assert m.ns['pcc_gc_object_is_address_pinned'](f.source) == int(
            operation == 'unpin')
        depth = m.depth
        m.ns['pcc_gc_' + operation](f.source)
        assert m.depth == depth
        expected_after = (before | PINNED if operation == 'pin'
                          else before & ~PINNED)
        assert m.load(f.source, 12) == expected_after
        assert m.ns['pcc_gc_object_is_address_pinned'](f.source) == int(
            operation == 'pin')
        injected.append(boundary)

    def atomic(opcode, obj, offset, operand, order):
        if installed and obj == f.source and offset == 12:
            for boundary, update in UPDATES.items():
                if (opcode, operand) == update:
                    assert order == 'relaxed'
                    before_update(boundary)
        # Read the word only after the injected operation has completed.
        return m.rmw(opcode, obj, offset, operand, order)

    def store(obj, offset, value):
        if installed and obj == f.source and offset == 12:
            before = m.load(obj, offset)
            # A reverted store already computed `value` from a header load.
            # Inject now, then actually write that stale value: final header
            # equality must catch the lost pin or resurrected unpin.
            if before & SWEEP_CANDIDATE and not value & SWEEP_CANDIDATE:
                before_update('clear-sweep')
            elif not before & IMMORTAL and value & IMMORTAL:
                before_update('set-immortal')
        m.store(obj, offset, value)

    m.ns.update(
        pcc_gc_install_forwarding_preallocated_unlocked=install_then_arm,
        atomic_rmw_i32=atomic,
        store_i32=store,
    )
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == f.target
    expected = (initial | FORWARDED | IMMORTAL) & ~SWEEP_CANDIDATE
    if operation == 'pin':
        expected |= PINNED
    assert m.load(f.source, 12) == expected
    assert m.ns['pcc_gc_object_is_address_pinned'](f.source) == int(
        operation == 'pin')
    assert installed == [0] and injected == [site] and not armed
    assert updates == ['clear-sweep', 'set-immortal']
    assert m.load(m.forwardings[f.source], 8) == f.target
    assert m.load(f.source, 0) == 1
    assert m.load(f.target, 0) == 3  # Private copy + forwarding + source count.
    assert m.load(f.child, 0) == 2
    assert m.load('pcc_gc_relocation_set_head') == 0
    assert f.relocation in m.freed and f.allocations == [f.target]
    assert not f.deallocated and f.finished == [0] and m.depth == 0


def test_copy_source_pinned_before_admission_still_rejects():
    f = _continuation()
    m = f.m
    m.ns['atomic_rmw_i32'] = m.rmw
    m.store(f.source, 12, m.load(f.source, 12) | SWEEP_CANDIDATE)
    m.ns['pcc_gc_pin'](f.source)
    before = m.load(f.source, 12)
    assert m.ns['pcc_gc_object_is_address_pinned'](f.source) == 1
    assert m.ns['pcc_gc_relocate_copy'](f.source, 48) == 0
    assert m.load(f.source, 12) == before
    assert m.load(f.source, 0) == m.load(f.child, 0) == 1
    assert not f.allocations and not f.deallocated and not f.finished
    assert not m.forwardings and not m.reverse and not m.identities
    assert m.load('pcc_gc_relocation_set_head') == f.relocation
    assert f.relocation not in m.freed and m.depth == 0
