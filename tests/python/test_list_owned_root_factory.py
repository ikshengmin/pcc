"""Owning-root construction preserves references and publishes complete lists."""

from collections import Counter

import pytest

from tests.python.test_collection_factory_allocation_errors import ListFactoryModel, RUNTIME
from tests.python.test_foreign_address_leases import _functions


class OwnedListFactoryModel(ListFactoryModel):
    def __init__(self, values, failure=None, pending=None, relocate=False, container=False):
        self.count = len(values)
        # GC0 lists use container locks: the unpublished list is private,
        # so the factory moves owners without the graph lock.
        self.container = container
        self.locked = False
        self.moves = 0
        self.children = Counter(values)
        self.publications = 0
        super().__init__(failure, pending, relocate)
        self.output = self.stack(8)
        self.frames[self.output] = 1
        self.memory[self.output] = None
        self.sources = self.stack(8 * len(values))
        self.source_slots = []
        for index, value in enumerate(values):
            slot = self.stack(8)
            self.source_slots.append(slot)
            self.frames[slot] = 1
            self.memory[slot] = value
            self.memory[self.sources + index * 8] = slot
        self.initial_frames = dict(self.frames)
        self.memory[3] = int(container)
        self.ns.update({
            'global_addr': lambda name: {'pcc_list_construction_error_map': 1,
                                         'pcc_gc_list_container_mode': 3}.get(name, 2),
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            'pcc_gc_resolve_root_slot_unlocked': self.resolve,
            'pcc_gc_backend': lambda: 4 if self.relocate else 0,
            'pcc_gc_note_store': lambda: None,
            'pcc_gc_note_slot_write_barrier': self.barrier,
            'py_gc_track': self.publish,
            'pcc_gc_publish_initialized': self.publish,
            'py_runtime_error_if_unset': self.error_if_unset,
        })
        _functions(
            RUNTIME / 'py_list.py',
            {'_list_allocate_unpublished', '_list_owned_construction_failed', 'py_list_from_owned_roots'},
            self.ns,
        )

    def check_shape(self, obj):
        length = self.memory.get(obj + 16)
        assert length is not None and 0 <= length <= self.count
        assert self.memory.get(obj + 24) == max(4, self.count)
        assert obj + 32 in self.memory
        if length:
            items = self.memory[obj + 32]
            assert items is not None
            for index in range(length):
                assert self.memory[items + index * 8] in self.children

    def owned_children(self):
        result = Counter()
        for root, count in self.frames.items():
            for index in range(count):
                value = self.memory.get(root + index * 8)
                if value in self.children:
                    result[value] += 1
        for obj in self.objects:
            for index in range(self.memory[obj + 16]):
                result[self.memory[self.memory[obj + 32] + index * 8]] += 1
        return result

    def boundary(self):
        assert not self.locked, 'parking or publication under the graph lock'
        super().boundary()
        if self.relocate:
            self.moves += 1
            changed = {value: value.split('/')[0] + '/' + str(self.moves) for value in self.children}
            for root, count in self.frames.items():
                for index in range(count):
                    address = root + index * 8
                    value = self.memory.get(address)
                    if value in changed:
                        self.memory[address] = changed[value]
            for obj in self.objects:
                for index in range(self.memory[obj + 16]):
                    address = self.memory[obj + 32] + index * 8
                    self.memory[address] = changed[self.memory[address]]
            self.children = Counter({changed[value]: count for value, count in self.children.items()})
        assert self.owned_children() == self.children

    def lock(self):
        self.boundary()
        self.locked = True

    def unlock(self):
        assert self.locked
        self.locked = False
        self.boundary()

    def resolve(self, source, borrowed):
        assert self.locked and borrowed == 0 and source in self.source_slots
        return self.memory[source]

    def barrier(self, owner, destination, value):
        if owner is None:
            self.boundary()
            assert destination == self.output and self.memory[destination] == value
        else:
            assert (self.locked or self.container) and owner in self.objects and value in self.children

    def publish(self, obj):
        self.boundary()
        assert self.memory[obj + 16] == self.count, 'partial list exposed by tracking/publication'
        assert all(self.memory[slot] is None for slot in self.source_slots)
        self.publications += 1

    def error_if_unset(self, helper, message):
        self.boundary()
        if self.pending is None:
            self.pending = message


@pytest.mark.parametrize('relocate, container', ((False, False), (False, True), (True, False)))
@pytest.mark.parametrize('values', ((), ('a',), ('a', 'b', 'a'), tuple('a' for _ in range(8))))
def test_owned_list_factory_moves_each_reference_before_publication(values, relocate, container):
    model = OwnedListFactoryModel(values, relocate=relocate, container=container)
    assert model.ns['py_list_from_owned_roots'](model.sources, len(values), model.output) == 0
    owner = model.memory[model.output]
    assert owner in model.objects and model.refs[owner] == 1
    assert model.owned_children() == model.children
    assert model.publications == 2
    assert model.frames == model.initial_frames and not model.leases and not model.pins
    assert model.pending is None


@pytest.mark.parametrize('relocate', (False, True))
@pytest.mark.parametrize('pending', (None, 'original-error'))
@pytest.mark.parametrize('failure', ('header', 'items', 'lease'))
def test_owned_list_factory_failure_preserves_sources_and_pending_error(failure, pending, relocate):
    model = OwnedListFactoryModel(('a', 'b', 'a'), failure, pending, relocate)
    assert model.ns['py_list_from_owned_roots'](model.sources, 3, model.output) == -1
    assert model.memory[model.output] is None
    assert all(model.memory[slot] in model.children for slot in model.source_slots)
    assert model.owned_children() == model.children
    assert model.pending == (pending or 'list: out of memory')
    assert model.disposals == (0 if failure == 'header' else 1)
    assert model.publications == 0
    assert model.frames == model.initial_frames and not model.leases and not model.pins
