"""An abandoned persistent-root publication permanently closes the young cut.

Contract: docs/project-intent.md shared roots and ordinary object lifetime.
Execute production unchecked wrappers, root/node/index owners, and domain
revocation/admission. Allocator failures are finite model faults, not native
OOM evidence. Heap-backed slots model persistent module/global roots; they
cannot be rescued by a conservative scan of a caller's machine stack.
"""
from __future__ import annotations

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import (
    declare_module_globals,
    load_functions,
)
from tests.python.test_gc3_closed_young_registration import (
    ClosedRegistration,
    ROOT,
)


DOMAIN = 'pcc_gc_backend3_closed_young_domain'
FRAME = 'pcc_gc_frame_enter'
LIFO = 'pcc_gc_frame_enter_lifo'
SCHEDULER = 'pcc_gc_scheduler_root_register'
CONTINUATION = 'pcc_gc_register_continuation_root'
ROOT_HEADS = (
    'pcc_gc_frame_head',
    'pcc_gc_scheduler_root_head',
    'pcc_gc_continuation_root_head',
)
FAILURES = (
    (FRAME, 'node'),
    (FRAME, 'plan'),
    (FRAME, 'table'),
    (FRAME, 'publication'),
    (LIFO, 'node'),
    (SCHEDULER, 'node'),
    (CONTINUATION, 'node'),
)


class ClosedRootRegistration(ClosedRegistration):
    def __init__(self, *, admitted):
        self.events = []
        self.no_park = 0
        self.failure = None
        self.entry = None
        self.faults = []
        self.backend_queries = 0
        super().__init__(admitted=False)
        # Object allocation/registration is real and succeeds before the root
        # metadata fault. The root slot will hold an otherwise reclaimable float.
        self.store(self.object, 8, abi.PY_TYPE_FLOAT)
        self.register()
        self.scalar_node = self.call('pcc_gc_object_index_find', self.object)
        assert self.scalar_node and self.load(self.object, 12) & 128
        self.frame_map = self.calloc(1, 4)
        self.root_slots = self.calloc(1, 8)
        self.store(self.frame_map, 0, 1)
        self.store(self.root_slots, 0, self.object)
        self.ns.update({
            'gc_backend_current': self.current_backend,
            'pcc_py_gc_minor_graph_lock': self.root_lock,
            'pcc_py_gc_minor_graph_unlock': self.root_unlock,
            'pcc_thread_no_park_enter': self.no_park_enter,
            'pcc_thread_no_park_exit': self.no_park_exit,
            'pcc_thread_no_park_depth': lambda: self.no_park,
            # Explicit machine-stack boundary: these are persistent heap slots,
            # never a conservative stack handle. No stack scan is modeled.
            'pcc_gc_stack_slot_root_is_conservative': self.heap_slot_not_stack,
            'define_thread_local_i32': self.define_word,
            'define_thread_local_ptr_null': self.define_null,
            'define_global_i32': self.define_word,
            'define_global_ptr_null': self.define_null,
        })
        registry = ROOT / 'freestanding_gc_frame_registry.py'
        roots = ROOT / 'freestanding_gc_root_registry.py'
        declare_module_globals(registry, self.ns)
        declare_module_globals(roots, self.ns)
        load_functions(roots, {
            'pcc_gc_root_slot_count_from_map',
            'pcc_gc_root_map_is_borrowed',
            'pcc_gc_root_registry_note_mutation_locked',
            'pcc_gc_cycle_requested_store_release',
            SCHEDULER, CONTINUATION,
        }, self.ns)
        load_functions(registry, {
            'pcc_gc_note_frame_enter', 'pcc_gc_note_frame_enter_lifo',
        }, self.ns)
        load_functions(ROOT / 'py_obj.py', {FRAME, LIFO}, self.ns)
        self.real_frame_plan = self.ns['pcc_gc_frame_index_plan_capacity']
        self.real_frame_replace = self.ns['pcc_gc_frame_index_replace_preallocated']
        self.ns['pcc_gc_frame_index_plan_capacity'] = self.frame_plan
        self.ns['pcc_gc_frame_index_replace_preallocated'] = self.frame_replace
        if admitted:
            self.call('pcc_gc_backend3_enable_closed_young_domain')
        self.events.clear()
        self.reads.clear()
        self.prepared.clear()

    def define_word(self, name, initial):
        self.words.setdefault(name, initial)

    def define_null(self, name):
        self.define_word(name, 0)

    def store(self, base, offset, value, *order):
        super().store(base, offset, value, *order)
        if base == DOMAIN:
            self.events.append(('domain-store', value, self.no_park, order))

    def current_backend(self):
        self.backend_queries += 1
        return 3

    def heap_slot_not_stack(self, slot):
        assert slot == self.root_slots
        return 0

    def root_lock(self):
        assert not self.locked
        if self.entry in (FRAME, LIFO):
            assert self.no_park > 0, 'an unpublished frame may not park'
        self.locked = True
        self.events.append(('lock',))

    def root_unlock(self):
        assert self.locked
        self.locked = False
        self.events.append(('unlock',))

    def no_park_enter(self):
        self.no_park += 1
        self.events.append(('no-park-enter',))

    def no_park_exit(self):
        assert not self.locked and self.no_park > 0
        if self.failure is not None:
            assert self.words[DOMAIN] == 2, 'revocation must precede a possible park'
        self.events.append(('no-park-exit',))
        self.no_park -= 1

    def prepare(self, size):
        if size == self.fail_malloc:
            self.faults.append(('malloc', size))
            self.events.append(('fault', 'malloc', size))
        return super().prepare(size)

    def frame_plan(self, extra):
        assert self.locked and self.no_park > 0 and extra == 1
        required = self.real_frame_plan(extra)
        if self.failure == 'plan':
            # Explicit planner-error boundary. Do not fake a node allocator,
            # root publication, or revocation result to reach this branch.
            assert required >= 0
            self.faults.append(('plan',))
            self.events.append(('fault', 'plan'))
            return -1
        return required

    def frame_replace(self, slots, node):
        assert self.locked and self.no_park > 0
        if self.failure != 'publication':
            return self.real_frame_replace(slots, node)
        # Withdraw the planned spare capacity at the publication boundary.
        # The production index, rather than a returning-node mock, produces
        # the rejection sentinel. This is a finite fault, not a legal writer
        # interleaving under the graph lock. Restore the synthetic occupancy.
        cap = self.load('pcc_py_gc_frame_cap')
        used = self.load('pcc_py_gc_frame_used')
        assert cap > 0 and used == 0
        self.store('pcc_py_gc_frame_used', 0, cap // 2)
        self.faults.append(('publication',))
        self.events.append(('fault', 'publication'))
        rejected = self.real_frame_replace(slots, node)
        self.store('pcc_py_gc_frame_used', 0, used)
        assert rejected == node
        return rejected

    def enter(self, entry, failure=None, *, frame_map=None, slots=None):
        self.entry, self.failure = entry, failure
        if failure == 'node':
            self.fail_malloc = {
                FRAME: 72, LIFO: 72, SCHEDULER: 24, CONTINUATION: 64,
            }[entry]
        elif failure == 'table':
            self.fail_malloc = 256 * 16
        frame_map = self.frame_map if frame_map is None else frame_map
        slots = self.root_slots if slots is None else slots
        if entry == SCHEDULER:
            return self.call(entry, slots)
        return self.call(entry, frame_map, slots)

    def assert_retained_after_refused_cut(self):
        assert not self.locked and self.no_park == 0
        assert self.call('pcc_gc_backend3_collect_closed_young') == -1
        assert self.call('pcc_gc_object_index_find', self.object) == self.scalar_node
        assert self.load('pcc_gc_backend3_young_head') == self.scalar_node
        assert self.load(self.object, 12) & 128
        assert self.load(self.root_slots, 0) == self.object
        assert self.object not in self.freed and self.scalar_node not in self.freed
        assert self.load('pcc_gc_backend3_young_cut_freed') == 0
        assert self.load('pcc_gc_backend3_young_cut_commits') == 0


@pytest.mark.parametrize('admitted', (False, True))
@pytest.mark.parametrize(('entry', 'failure'), FAILURES)
def test_missing_persistent_root_revokes_before_or_after_generated_entry(
    entry, failure, admitted,
):
    model = ClosedRootRegistration(admitted=admitted)
    assert model.load(DOMAIN) == int(admitted)
    assert model.enter(entry, failure) is None  # Actual unchecked/void ABI.
    assert len(model.faults) == 1
    assert all(model.load(head) == 0 for head in ROOT_HEADS)
    assert model.call('pcc_gc_frame_index_find', model.root_slots) == 0
    assert model.load(DOMAIN) == 2
    revocations = [event for event in model.events if event[0] == 'domain-store']
    assert revocations == [
        ('domain-store', 2, 1 if entry in (FRAME, LIFO) else 0, ('release',)),
    ]
    if entry in (FRAME, LIFO):
        assert model.events.index(revocations[0]) < model.events.index(('no-park-exit',))
    model.assert_retained_after_refused_cut()
    # A later generated main cannot repair the missing persistent module root.
    model.call('pcc_gc_backend3_enable_closed_young_domain')
    assert model.load(DOMAIN) == 2
    model.assert_retained_after_refused_cut()


@pytest.mark.parametrize('entry', (FRAME, LIFO, SCHEDULER, CONTINUATION))
def test_successful_root_registration_has_no_young_domain_hot_path_query(entry):
    model = ClosedRootRegistration(admitted=True)
    assert model.enter(entry) is None
    assert not any(base == DOMAIN for base, _, _ in model.reads)
    assert not any(event[0] == 'domain-store' for event in model.events)
    assert model.backend_queries == 1 and model.faults == []
    assert not model.locked and model.no_park == 0
    assert model.load(DOMAIN) == 1
    if entry == SCHEDULER:
        node = model.load('pcc_gc_scheduler_root_head')
        assert node and model.load(node, 0) == model.root_slots
    else:
        head = (
            'pcc_gc_continuation_root_head'
            if entry == CONTINUATION else 'pcc_gc_frame_head'
        )
        node = model.load(head)
        assert node and model.load(node, 0) == model.frame_map
        assert model.load(node, 8) == model.root_slots
        if entry == FRAME:
            assert model.call('pcc_gc_frame_index_find', model.root_slots) == node
    assert model.call('_young_cut_admitted') == 1


@pytest.mark.parametrize('entry', (FRAME, LIFO, CONTINUATION))
@pytest.mark.parametrize('empty', ('null-map', 'null-slots', 'zero-map'))
def test_intentional_empty_root_registration_does_not_revoke(entry, empty):
    model = ClosedRootRegistration(admitted=True)
    arguments = {}
    if empty == 'null-map':
        arguments['frame_map'] = 0
    elif empty == 'null-slots':
        arguments['slots'] = 0
    else:
        model.store(model.frame_map, 0, 0)
    assert model.enter(entry, **arguments) is None
    assert model.events == [] and model.prepared == []
    assert not any(base == DOMAIN for base, _, _ in model.reads)
    assert all(model.load(head) == 0 for head in ROOT_HEADS)
    assert model.load(DOMAIN) == 1
    assert model.call('_young_cut_admitted') == 1


def test_null_scheduler_slot_is_not_a_failed_root_publication():
    model = ClosedRootRegistration(admitted=True)
    assert model.enter(SCHEDULER, slots=0) is None
    assert model.events == [] and model.prepared == []
    assert not any(base == DOMAIN for base, _, _ in model.reads)
    assert all(model.load(head) == 0 for head in ROOT_HEADS)
    assert model.load(DOMAIN) == 1
    assert model.call('_young_cut_admitted') == 1
