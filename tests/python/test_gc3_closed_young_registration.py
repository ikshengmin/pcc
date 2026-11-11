"""A missing ObjectNode permanently invalidates the young-domain proof.

Contract: docs/project-intent.md shared roots and object lifetime. Execute the
complete production allocation registration, node/index owners, admission and
revocation bodies. Allocation failures are finite allocator faults, not native
OOM evidence. This model deliberately does not claim machine-stack coverage.
"""

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import gc_state_initial_values, load_functions
from tests.python.test_gc_allocation_index_noop_commit import Registration, ROOT


class ClosedRegistration(Registration):
    def __init__(self, *, admitted=True):
        super().__init__(3, pooled_node=False)
        for name, value in gc_state_initial_values().items():
            self.words.setdefault(name, value)
        self.store('pcc_gc_backend4_no_refcounts', 0, 1)
        self.ns.update(vars(abi))
        self.ns.update({
            'atomic_load_i32': self.load,
            'atomic_store_i32': self.store,
            'atomic_cas_i32': self.cas,
            'cstr': lambda text: text,
            'pcc_platform_getenv': lambda name: 0,
            'pcc_thread_no_park_depth': lambda: 0,
            # A refused cut must not reach any stopped-world operation. The
            # real admission body, not this tripwire, decides whether to stop.
            'pcc_thread_owns_stopped_world': self.unexpected_stop,
        })
        load_functions(ROOT / 'freestanding_gc_generational_scheduler.py', {
            'pcc_gc_backend3_enable_closed_young_domain',
            'pcc_gc_backend3_revoke_closed_young_domain',
            '_young_cut_admitted', 'pcc_gc_backend3_collect_closed_young',
        }, self.ns)
        if admitted:
            self.call('pcc_gc_backend3_enable_closed_young_domain')

    def cas(self, base, offset, expected, replacement, *order):
        old = self.load(base, offset)
        if old == expected:
            self.store(base, offset, replacement)
        return old

    @staticmethod
    def unexpected_stop():
        pytest.fail('an invalid allocation closure reached the world-stop boundary')

    def fail_registration(self, failure):
        if failure in ('table', 'commit'):
            self.fill_index()
        if failure == 'node':
            self.fail_malloc = 88
        elif failure == 'table':
            self.fail_malloc = 16384 * 16
        elif failure == 'commit':
            self.short_commit = True
        else:
            raise AssertionError(failure)
        self.register()


@pytest.mark.parametrize('failure', ('node', 'table', 'commit'))
@pytest.mark.parametrize('admitted', (False, True))
def test_failed_registration_revokes_before_or_after_generated_entry(failure, admitted):
    model = ClosedRegistration(admitted=admitted)
    model.fail_registration(failure)
    assert not model.locked
    assert model.call('pcc_gc_object_index_find', model.object) == 0
    assert model.load('pcc_gc_object_head') == 0
    assert model.load('pcc_gc_backend3_closed_young_domain') == 2
    model.call('pcc_gc_backend3_enable_closed_young_domain')
    assert model.load('pcc_gc_backend3_closed_young_domain') == 2
    assert model.call('pcc_gc_backend3_collect_closed_young') == -1


@pytest.mark.parametrize('failure', ('node', 'table', 'commit'))
def test_unindexed_live_owner_cannot_lose_later_indexed_scalar_child(failure):
    model = ClosedRegistration()
    owner = model.object
    # This is the earlier provenance publication performed by pcc_gc_alloc,
    # before pcc_gc_note_object_allocated_sized attempts metadata allocation.
    assert model.call('pcc_gc_managed_pointer_index_insert', owner) >= 0
    model.fail_registration(failure)
    assert model.call('pcc_gc_object_index_find', owner) == 0
    assert model.load(owner, 12) & (128 | 256) == 0

    # The allocator fault clears, so a later scalar receives a genuine index
    # entry and young-list link through the same production registration.
    model.short_commit = False
    model.object = model.calloc(1, 40)
    child = model.object
    model.store(child, 8, abi.PY_TYPE_FLOAT)
    model.register()
    child_node = model.call('pcc_gc_object_index_find', child)
    assert child_node and model.load(child, 12) & 128
    assert model.load('pcc_gc_backend3_young_head') == child_node

    items = model.calloc(1, 8)
    model.store(items, 0, child)
    model.store(owner, 16, 1)
    model.store(owner, 24, 1)
    model.store(owner, 32, items)
    # A real scheduler root holds only the unindexed owner, not its child.
    slot = model.calloc(1, 8)
    root = model.calloc(1, 24)
    model.store(slot, 0, owner)
    model.store(root, 0, slot)
    model.store('pcc_gc_scheduler_root_head', 0, root)
    assert model.call('pcc_gc_backend3_collect_closed_young') == -1
    assert model.call('pcc_gc_object_index_find', child) == child_node
    assert model.load(items, 0) == child
    assert model.load('pcc_gc_backend3_young_cut_freed') == 0


def test_successful_registration_preserves_admission_without_extra_queries():
    model = ClosedRegistration()
    model.register()
    model.assert_published()
    assert model.load('pcc_gc_backend3_closed_young_domain') == 1
    assert model.call('_young_cut_admitted') == 1
