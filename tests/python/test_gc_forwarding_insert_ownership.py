"""New forwarding nodes must acquire exact index ownership before commit.

Actual installer, identity and index bodies run over modeled memory, graph
exclusion, allocator and counted-reference primitives. An absent publication
is injected after a valid install to exercise defensive duplicate ownership;
new publication-OOM rejection is covered in test_gc_forwarding_publication_oom.
No lookup or insert result is replaced. This is not collector qualification.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_stable_object_membership import Index, ROOT, load_bodies


class Install(Index):
    def __init__(self, backend):
        super().__init__()
        self.depth = 0
        self.allocations = []
        self.freed = []
        self.fail_record = None
        self.failed_records = []
        self.rc_events = []
        self.ns.update({
            'store_i32': self.store,
            'atomic_store_i32': self.store,
            'atomic_rmw_i32': self.atomic_rmw,
            'malloc': self.allocate,
            'calloc': self.allocate_zeroed,
            'free': self.release,
            'py_incref': lambda obj: self.change_count(obj, 1),
            'py_decref': lambda obj: self.change_count(obj, -1),
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            'pcc_gc_config_ensure': lambda: backend,
            # The fixture uses ordinary live LIST objects. Pin admission and
            # type support are outside this index-ownership regression.
            'pcc_gc_object_is_address_pinned': lambda obj: 0,
            'pcc_gc_generational_object_is_address_pinned': lambda obj: 0,
            'pcc_gc_generational_oldify_supported_tag': lambda tag: int(tag == 6),
            'abi_constant': lambda name: 9999,
        })
        self.store('pcc_gc_backend_selected', 0, backend)
        self.store('pcc_gc_config_initialized', 0, 1)
        load_bodies(self.ns, ROOT / 'freestanding_gc_root_operations.py',
                    {'pcc_gc_object_is_known_no_lock', 'pcc_gc_object_is_known_stable'})
        load_bodies(self.ns, ROOT / 'freestanding_gc_forwarding_identity.py')
        self.a, self.b, self.t, self.u = [self.object() for _ in range(4)]

    def object(self):
        obj = self.calloc(1, 40)
        node = self.calloc(1, 88)
        self.store(obj, 0, 3)
        self.store(obj, 8, 6)
        self.store(node, 0, obj)
        self.store(node, 8, 40)
        assert self.call('pcc_gc_object_index_insert', obj, node) == 1
        return obj

    def lock(self):
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.depth -= 1

    def allocate(self, size):
        result = self.calloc(1, size)
        self.allocations.append((result, size))
        return result

    def allocate_zeroed(self, count, size):
        if self.fail_record and count * size == 16:
            kind = self.fail_record
            # _index_table_publish installs its slots before requesting the
            # optional 16-byte record. Other tables may allocate first.
            if (self.load('pcc_py_gc_' + kind + '_slots') != 0
                    and self.load('pcc_py_gc_' + kind + '_published') == 0):
                self.failed_records.append(kind)
                self.fail_record = None
                return 0
        return self.calloc(count, size)

    def release(self, block):
        if block:
            assert block not in self.freed
            self.freed.append(block)

    def change_count(self, obj, delta):
        assert self.depth > 0
        count = self.load(obj, 0) + delta
        assert count > 0  # No finalizer/callback in this counted-RC fixture.
        self.store(obj, 0, count)
        self.rc_events.append((obj, delta))

    def atomic_rmw(self, operation, obj, offset, operand, order):
        assert operation == 'or' and order == 'relaxed'
        before = self.load(obj, offset)
        self.store(obj, offset, before | operand)
        return before

    def install(self, source, target):
        result = self.call('pcc_gc_install_forwarding', source, target)
        assert self.depth == 0
        return result

    def exact(self, kind, key):
        # Inspect the real authoritative table while modeling graph ownership.
        self.lock()
        try:
            return self.call('pcc_gc_index_py_find',
                             'pcc_py_gc_' + kind + '_slots',
                             'pcc_py_gc_' + kind + '_cap', key, 1)
        finally:
            self.unlock()

    def edge_state(self):
        head = self.load('pcc_gc_forwarding_head')
        chain = []
        node = head
        while node:
            assert len(chain) < 8
            chain.append((node, tuple(self.load(node, offset)
                                      for offset in (0, 8, 16, 24, 32, 40))))
            node = self.load(node, 16)
        objects = (self.a, self.b, self.t, self.u)
        return (
            head, chain,
            [self.exact('forwarding', obj) for obj in objects],
            [self.exact('forwarding_target', obj) for obj in objects],
            [(self.load('pcc_py_gc_' + kind + '_count'),
              self.load('pcc_py_gc_' + kind + '_used'))
             for kind in ('forwarding', 'forwarding_target')],
            [(self.load(obj, 0), self.load(obj, 12)) for obj in objects],
            self.load('pcc_gc_forwarding_population'),
            self.load('pcc_gc_relocation_forwards'),
        )


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('kind', ('forwarding', 'forwarding_target'))
def test_hidden_duplicate_rejects_new_node_without_damaging_existing_edge(backend, kind):
    model = Install(backend)
    assert model.install(model.a, model.t) == 0
    # Simulate the old invalid/hidden-table state. Valid new installs reject
    # record OOM before publication, rather than creating this state.
    model.store('pcc_py_gc_' + kind + '_published', 0, 0)
    assert model.load('pcc_py_gc_' + kind + '_published') == 0
    key = model.a if kind == 'forwarding' else model.t
    assert model.exact(kind, key) != 0
    # The published-reader miss is real; the node still belongs to the index.
    assert model.call('pcc_gc_' + kind + '_index_find', key) == 0
    before = model.edge_state()
    allocations_before = len(model.allocations)
    freed_before = list(model.freed)
    source, target = ((model.a, model.u) if kind == 'forwarding'
                      else (model.b, model.t))

    assert model.install(source, target) == -1
    assert model.edge_state() == before
    nodes = [block for block, size in model.allocations[allocations_before:]
             if size == 56]
    assert len(nodes) == 1
    assert model.freed == freed_before + nodes
    assert model.rc_events[-2:] == [(target, 1), (target, -1)]
    # Existing identity preparation and conservative barrier enabling are
    # intentionally not claimed to be a whole-memory rollback.


@pytest.mark.parametrize('backend', (3, 4))
def test_hidden_target_duplicate_does_not_retarget_an_existing_source(backend):
    model = Install(backend)
    assert model.install(model.a, model.t) == 0
    assert model.install(model.b, model.u) == 0
    model.store('pcc_py_gc_forwarding_target_published', 0, 0)
    before = model.edge_state()
    rc_before, freed_before = list(model.rc_events), list(model.freed)

    assert model.install(model.b, model.t) == -1
    assert model.edge_state() == before
    assert model.rc_events == rc_before and model.freed == freed_before


@pytest.mark.parametrize('backend', (3, 4))
def test_visible_target_upsert_zero_still_links_both_source_nodes(backend):
    model = Install(backend)
    assert model.install(model.a, model.t) == 0
    first = model.exact('forwarding', model.a)
    assert model.install(model.b, model.t) == 0
    second = model.exact('forwarding', model.b)
    assert first and second and first != second
    assert model.exact('forwarding_target', model.t) == second
    assert model.load(second, 32) == first and model.load(first, 40) == second
    assert model.load(second, 40) == model.load(first, 32) == 0
    assert model.load('pcc_gc_forwarding_head') == second
    assert model.load(second, 16) == first and model.load(first, 24) == second
    assert model.load(model.t, 0) == 5
    assert model.load('pcc_gc_forwarding_population') == 2
    assert model.load('pcc_gc_relocation_forwards') == 2
    assert model.load('pcc_py_gc_forwarding_count') == 2
    assert model.load('pcc_py_gc_forwarding_used') == 2
    assert model.load('pcc_py_gc_forwarding_target_count') == 1
    assert model.load('pcc_py_gc_forwarding_target_used') == 1
    assert not model.freed


@pytest.mark.parametrize('backend', (3, 4))
def test_visible_source_retarget_keeps_its_existing_node(backend):
    model = Install(backend)
    assert model.install(model.a, model.t) == 0
    node = model.exact('forwarding', model.a)
    assert model.install(model.a, model.u) == 0
    assert model.exact('forwarding', model.a) == node
    assert model.load(node, 8) == model.u
    assert model.exact('forwarding_target', model.t) == 0
    assert model.exact('forwarding_target', model.u) == node
    assert model.load(model.t, 0) == 3 and model.load(model.u, 0) == 4
    assert model.load('pcc_gc_forwarding_population') == 1
    assert model.load('pcc_gc_relocation_forwards') == 2
    for kind in ('forwarding', 'forwarding_target'):
        assert model.load('pcc_py_gc_' + kind + '_count') == 1
        assert model.load('pcc_py_gc_' + kind + '_used') == 1
    assert not model.freed
