"""Production object-node queries across graph-serialized index mutations.

The queried object has a caller-owned lifetime; the neighboring object does
not. These finite memory interleavings exercise actual index, known-query,
node-release and caller bodies. They are not native execution, a scheduler
model, arbitrary-pointer lifetime protection or throughput evidence.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_stable_object_membership import Index, ROOT, load_bodies


class Nodes(Index):
    def __init__(self, *, publication_oom=False, neighbor=True):
        self.freed = set()
        self.events = []
        self.depth = 0
        super().__init__(publication_oom=publication_oom)
        self.ns.update(store_i32=self.store, free=self.free,
                       atomic_rmw_i32=self.atomic_and,
                       pcc_py_gc_minor_graph_lock=self.lock,
                       pcc_py_gc_minor_graph_unlock=self.unlock)
        load_bodies(self.ns, ROOT / 'freestanding_gc_root_operations.py', {
            'pcc_gc_object_is_known_no_lock', 'pcc_gc_object_is_known_stable',
        })
        load_bodies(self.ns, ROOT / 'freestanding_gc_object_nodes.py', {
            '_clear_promotion_state', 'pcc_gc_object_node_release',
        })
        load_bodies(self.ns, ROOT / 'freestanding_gc_forwarding_identity.py', {
            '_graph_lock', '_graph_unlock', 'pcc_gc_forwarding_find',
            'pcc_gc_backend4_slot_needs_resolve',
            '_note_relocation_read_unlocked', 'pcc_gc_note_relocation_read',
        })
        self.nodes = {}
        self.add_node(self.removed)
        self.add_node(self.kept)
        self.neighbor = 0
        if neighbor:
            self.neighbor = next(key for key in range(0x1000, 0x10000, 16)
                                 if key not in self.nodes
                                 and self.call('pcc_gc_index_py_hash_ptr', key) & 7 == 0)
            self.add_node(self.neighbor)
        # This models the caller's pre-existing lease, not one created by
        # the new query. Writers in this fixture remove only other objects.
        self.store(self.nodes[self.kept], 80, 1)
        self.store('pcc_gc_object_node_free_count', 0, 8192)
        self.store('pcc_gc_backend_selected', 0, 4)
        self.reads.clear()

    def add_node(self, obj):
        node = self.calloc(1, 88)
        self.nodes[obj] = node
        self.store(node, 0, obj)
        self.store(obj, 12, 0)
        assert self.call('pcc_gc_index_py_upsert',
                         'pcc_py_gc_object_slots', 'pcc_py_gc_object_cap',
                         'pcc_py_gc_object_count', 'pcc_py_gc_object_used',
                         obj, node, 1, 8) in (0, 1)

    def load(self, base, offset=0, *order):
        value = super().load(base, offset, *order)
        assert base not in self.freed, ('freed-node read', base, offset)
        return value

    def free(self, node):
        assert node in self.blocks and node not in self.freed
        self.freed.add(node)
        self.events.append(('free', node))

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        self.events.append('lock')

    def unlock(self):
        assert self.depth == 1
        self.depth = 0
        self.events.append('unlock')

    def atomic_and(self, operation, base, offset, value, order):
        assert operation == 'and' and order == 'relaxed'
        old = self.load(base, offset)
        self.store(base, offset, old & value)
        return old

    def arm_shift(self, *, retire_neighbor=False):
        assert self.load(self.slots, 16) == self.kept
        before = self.generation()

        def retire_before_next_read(base, offset, order):
            self.hook = None
            self.lock()
            try:
                node = self.call('pcc_gc_object_index_remove', self.neighbor)
                assert node == self.nodes[self.neighbor]
                self.call('pcc_gc_object_node_release', node)
            finally:
                self.unlock()

        def shift_before_value(base, offset, order):
            if base != self.slots or offset != 24:
                return
            self.hook = None
            self.lock()
            try:
                assert self.call('pcc_gc_object_index_remove', self.removed) == self.nodes[self.removed]
            finally:
                self.unlock()
            # The original key has moved to slot zero. The reader's old
            # slot now contains the unrelated node, or NULL without it.
            assert self.generation() == before + 2
            if retire_neighbor:
                assert self.neighbor
                self.hook = retire_before_next_read

        self.hook = shift_before_value


def test_original_published_known_can_dereference_a_freed_neighbors_node():
    model = Nodes()
    assert model.load('pcc_py_gc_object_published') != 0
    model.arm_shift(retire_neighbor=True)
    with pytest.raises(AssertionError, match='freed-node read'):
        model.call('pcc_gc_object_is_known_no_lock', model.kept)
    assert model.nodes[model.neighbor] in model.freed
    assert model.nodes[model.kept] not in model.freed


@pytest.mark.parametrize('publication_oom', (False, True))
def test_stable_known_rejects_wrong_node_before_dereferencing_it(publication_oom):
    model = Nodes(publication_oom=publication_oom)
    assert bool(model.load('pcc_py_gc_object_published')) is not publication_oom
    model.arm_shift(retire_neighbor=True)
    assert model.call('pcc_gc_object_is_known_stable', model.kept) == 1
    neighbor_node = model.nodes[model.neighbor]
    assert neighbor_node in model.freed
    # The actual release body reads lease@80 before freeing. The known
    # query must never read this other node's freeing@32 field.
    assert not any(base == neighbor_node and offset == 32
                   for base, offset, _ in model.reads)
    assert model.load(model.nodes[model.kept], 80) == 1


@pytest.mark.parametrize('publication_oom', (False, True))
def test_stable_known_recovers_null_node_after_backward_shift(publication_oom):
    model = Nodes(publication_oom=publication_oom, neighbor=False)
    model.arm_shift()
    assert model.call('pcc_gc_object_is_known_stable', model.kept) == 1
    assert model.call('pcc_gc_object_index_find_stable', model.kept) == model.nodes[model.kept]


@pytest.mark.parametrize('state', ('null', 'tagged', 'absent', 'freeing', 'dying', 'alive'))
def test_stable_known_preserves_header_guards(state):
    model = Nodes()
    obj = model.kept
    if state == 'null':
        obj = 0
    elif state == 'tagged':
        obj = 35
    elif state == 'absent':
        obj = 0xF0000
    elif state == 'freeing':
        model.store(model.nodes[obj], 32, 1)
    elif state == 'dying':
        model.store(obj, 12, 524288)
    model.reads.clear()
    assert model.call('pcc_gc_object_is_known_stable', obj) == int(state == 'alive')
    if state in ('null', 'tagged'):
        assert model.reads == []
    if state in ('absent', 'freeing'):
        assert not any(base == obj and offset == 12 for base, offset, _ in model.reads)


# Only GC4 relocates and takes the relocation read (03cbd1849).
@pytest.mark.parametrize('backend', (4,))
def test_public_read_keeps_its_own_node_across_other_object_release(backend):
    model = Nodes()
    model.store('pcc_gc_backend_selected', 0, backend)
    model.arm_shift(retire_neighbor=True)
    assert model.call('pcc_gc_note_relocation_read', model.kept) == model.kept
    assert model.nodes[model.neighbor] in model.freed
    # Only the two injected writers took the graph lock; a known live,
    # unforwarded object still returns before the read's locked resolver.
    assert model.events.count('lock') == model.events.count('unlock') == 2


def test_slot_hint_does_not_lose_known_object_after_shift():
    model = Nodes(neighbor=False)
    model.store(model.kept, 12, 2048)
    model.arm_shift()
    assert model.call('pcc_gc_backend4_slot_needs_resolve', model.kept) == 1


def test_gc4_release_does_not_skip_decref_after_shift():
    model = Nodes(neighbor=False)
    decrefs = []
    reads = []
    model.ns.update(_gc_backend_fast=lambda: 4,
                    _gc_forwarding_population=lambda: 1,
                    _gc_relocation_candidate=lambda obj: 1,
                    py_decref=decrefs.append)
    # Isolate the second, post-resolver known check in release. The public
    # relocation-read body and its own lookup are exercised separately above.
    model.ns['pcc_gc_note_relocation_read'] = lambda obj: reads.append(obj) or obj
    load_bodies(model.ns, ROOT / 'py_obj.py', {'pcc_gc_release'})
    model.arm_shift()
    model.call('pcc_gc_release', model.kept)
    assert reads == [model.kept] and decrefs == [model.kept]


@pytest.mark.parametrize('entry', ('pcc_capi_register_module_state', 'PyType_FromModuleAndSpec'))
def test_capi_module_pin_is_not_skipped_after_shift(entry):
    model = Nodes(neighbor=False)
    pinned = []
    retained = []
    model.ns.update(PyMem_Calloc=model.calloc, pcc_gc_pin=pinned.append,
                    py_incref=retained.append, PY_TYPE_CEXT_TAG_BASE=1000)
    load_bodies(model.ns, ROOT / 'py_capi_module_state_runtime.py', {entry})
    if entry == 'PyType_FromModuleAndSpec':
        type_obj = model.calloc(1, 32)
        table = model.calloc(1024, 8)
        model.store(type_obj, 8, 1000)
        model.ns['PyType_FromSpec'] = lambda spec: type_obj
        model.ns['global_addr'] = lambda name: table if name == 'pcc_capi_cext_type_modules' else name
    model.arm_shift()
    result = model.call(entry, model.kept, 0, 0)
    assert pinned == [model.kept]
    if entry == 'pcc_capi_register_module_state':
        assert result == 0 and retained == []
        node = model.load('pcc_capi_module_states')
        assert model.load(node, 0) == model.kept
    else:
        assert result == type_obj and retained == [model.kept]
        assert model.load(table, 0) == model.kept
