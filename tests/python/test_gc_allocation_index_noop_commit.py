"""Allocation registration skips only an unchanged, normal index plan.

Contract: docs/project-intent.md shared roots, provenance and object lifetime.
Execute the complete production registration body, real ObjectNode helpers,
and real index planning/publication/insertion. Memory and graph-lock primitives
are modeled; GC4 page allocation is a boundary stub, its index is real.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.python.test_gc_stable_object_membership import Index, ROOT, load_bodies


class Registration(Index):
    def __init__(self, backend=1, *, pooled_node=True, mark_active=0):
        super().__init__()
        self.backend = backend
        self.locked = False
        self.acquires = 0
        self.commits = 0
        self.zpage_commits = 0
        self.freed = []
        self.prepared = []
        self.pending = 0
        self.on_relock = None
        self.fail_malloc = 0
        self.short_commit = False
        self.fail_zpage_commit = False
        self.removed_provenance = []
        self.selections = []
        self.object = self.calloc(1, 40)
        self.page = self.calloc(1, 80)
        self.znode = self.calloc(1, 80)
        self.store(self.object, 8, 6)  # Exact LIST, not a graph leaf.
        self.store(self.object, 12, 0x400000)
        for name, value in {
            'pcc_gc_config_initialized': 1,
            'pcc_gc_backend_selected': backend,
            'pcc_gc_backend4_no_refcounts': 0,
            'pcc_gc_mark_active': mark_active,
        }.items():
            self.store(name, 0, value)
        self.ns.update({
            # One modeled thread publishes each live-byte change at once.
            '_live_bytes_note': lambda delta: self.store(
                'pcc_gc_live_bytes', 0, self.load('pcc_gc_live_bytes') + delta),
            'store_i32': self.store,
            'atomic_store_i32': self.store,
            'stack_alloc': lambda size: self.calloc(1, size),
            'malloc': self.prepare,
            'free': self.release,
            'memset': self.zero,
            '_init_config': lambda: backend,
            'pcc_py_gc_minor_graph_lock_for': self.lock,
            '_object_graph_unlock': self.unlock,
            '_pending_minor_block': lambda: self.pending,
            '_set_pending_minor_block': self.set_pending,
            '_backend3_graph_leaf_tag': lambda tag: 0,
            'pcc_gc_index_slot_size': lambda: 16,
            'pcc_gc_granule_is_object_start': lambda obj: 1,
            'pcc_gc_managed_pointer_index_remove': self.removed_provenance.append,
            '_backend4_zpage_node_plan_requires_prepare': lambda: 0,
            '_backend4_zpage_find_page_for_addr': lambda obj, size: self.page,
            '_backend4_zpage_find_reusable_page': lambda obj, size: self.page,
            '_backend4_zpage_track_alloc_selected': self.track_zpage,
        })
        load_bodies(self.ns, ROOT / 'freestanding_gc_object_nodes.py')
        aliases = {
            '_object_node_prepare': 'pcc_gc_object_node_prepare',
            '_object_node_plan_requires_prepare': 'pcc_gc_object_node_plan_requires_prepare',
            '_object_node_take_prepared': 'pcc_gc_object_node_take_prepared',
            '_object_head': 'pcc_gc_object_list_head',
            '_set_object_head': 'pcc_gc_object_set_list_head',
            '_set_object_node_prev': 'pcc_gc_object_node_set_prev',
            '_set_object_node_gc_refs': 'pcc_gc_object_node_set_gc_refs',
            '_set_object_node_young_next': 'pcc_gc_object_node_set_young_next',
            '_set_object_node_young_prev': 'pcc_gc_object_node_set_young_prev',
            '_set_object_node_zpage': 'pcc_gc_object_node_set_zpage',
            '_backend3_young_link_head': 'pcc_gc_backend3_young_link_head',
        }
        self.ns.update({alias: self.ns[name] for alias, name in aliases.items()})
        if pooled_node:
            self.store('pcc_gc_object_node_free_head', 0, self.calloc(1, 88))
            self.store('pcc_gc_object_node_free_count', 0, 1)
        self.original_plan = self.ns['pcc_gc_object_index_plan_capacity']
        self.original_commit = self.ns['pcc_gc_object_index_plan_commit']
        self.original_zpage_commit = self.ns['pcc_gc_zpage_owner_index_plan_commit']
        self.ns['pcc_gc_object_index_plan_capacity'] = self.plan
        self.ns['pcc_gc_object_index_plan_commit'] = self.commit
        self.ns['pcc_gc_zpage_owner_index_plan_commit'] = self.commit_zpage
        # Registration failure revokes the independently proved young domain;
        # execute that cross-object body rather than stubbing the new boundary.
        load_bodies(self.ns, ROOT / 'freestanding_gc_generational_scheduler.py',
                    {'pcc_gc_backend3_revoke_closed_young_domain'})
        load_bodies(self.ns, ROOT / 'py_gc_backend.py',
                    {'pcc_gc_note_object_allocated_sized'})

    def lock(self, kind):
        assert kind == 2 and not self.locked
        self.acquires += 1
        if self.acquires == 2 and self.on_relock is not None:
            self.on_relock()
        self.locked = True

    def unlock(self):
        assert self.locked
        self.locked = False

    def plan(self, extra):
        assert self.locked
        return self.original_plan(extra)

    def commit(self, owner, capacity, extra):
        assert self.locked
        self.commits += 1
        if self.short_commit:
            capacity //= 2  # Fault injection: real commit rejects the buffer.
        return self.original_commit(owner, capacity, extra)

    def commit_zpage(self, owner, capacity, extra):
        assert self.locked
        self.zpage_commits += 1
        if self.fail_zpage_commit:
            return -1
        return self.original_zpage_commit(owner, capacity, extra)

    def prepare(self, size):
        assert not self.locked, 'metadata preparation must remain outside lock'
        if size == self.fail_malloc:
            self.fail_malloc = 0
            return 0
        result = self.calloc(1, size)
        self.prepared.append(result)
        return result

    def release(self, block):
        if block:
            assert not self.locked, 'unused preparation must be freed after unlock'
            assert block not in self.freed, 'double free'
            self.freed.append(block)

    def zero(self, base, value, size):
        assert value == 0
        for offset in range(0, size, 8):
            self.store(base, offset, 0)

    def set_pending(self, block):
        self.pending = block

    def track_zpage(self, obj, size, node_owner, page_owner, from_free, page, offset):
        assert self.locked and obj == self.object and size == 40
        self.selections.append((page, offset))
        assert self.call('pcc_gc_object_index_find', obj) != 0
        assert self.call('pcc_gc_zpage_owner_index_upsert_preallocated',
                         obj, self.znode) >= 0
        return self.znode

    def fill_index(self):
        self.call('pcc_gc_object_index_insert', 0x50000, 0xC000)
        self.call('pcc_gc_object_index_insert', 0x60000, 0xD000)
        assert self.original_plan(1) > 0

    def register(self):
        self.call('pcc_gc_note_object_allocated_sized', self.object, 40)

    def assert_published(self):
        assert not self.locked and self.pending == 0
        node = self.call('pcc_gc_object_index_find', self.object)
        assert node and self.load('pcc_gc_object_head') == node
        assert self.load(node, 0) == self.object and self.load(node, 8) == 40
        assert self.load(node, 32) == self.load(node, 80) == 0
        assert self.load('pcc_gc_live_bytes') == 40
        assert self.load('pcc_gc_last_alloc') == self.object
        if self.backend == 4:
            # GC4 objects are allocator cells: no logical zpage node and no
            # zpage owner entry (GC4's pages are the object slabs).
            assert self.load(node, 48) == 0
            assert self.zpage_commits == 0 and self.selections == []


@pytest.mark.parametrize('backend', (1, 2, 3, 4))
@pytest.mark.parametrize('mark_active', (0, 1))
def test_capacity_already_available_has_only_actual_insert_generation(backend, mark_active):
    model = Registration(backend, mark_active=mark_active)
    before = model.generation()
    model.generation_stores.clear()
    model.register()
    model.assert_published()
    assert model.commits == 0
    assert model.generation_stores == [
        (before + 1, ('relaxed',)), (before + 2, ('release',)),
    ]
    assert model.load(model.object, 12) & 56 == (32 if mark_active else 8)
    assert model.load(model.object, 12) & 0x400000
    assert model.load('pcc_gc_cycle_requested') == 1


@pytest.mark.parametrize('backend', (1, 2, 3, 4))
def test_growth_keeps_real_plan_commit_and_transfers_buffer_ownership(backend):
    model = Registration(backend)
    model.fill_index()
    before = model.generation()
    model.register()
    model.assert_published()
    assert model.commits == 1 and model.generation() == before + 4
    assert model.load('pcc_py_gc_object_cap') >= 16384
    table = model.load('pcc_py_gc_object_slots')
    assert table in model.prepared and table not in model.freed
    for key in (model.kept, model.removed, 0x50000, 0x60000):
        assert model.call('pcc_gc_object_index_find', key) != 0


def test_competing_growth_replans_and_frees_the_now_unused_prepared_table():
    model = Registration()
    model.fill_index()
    def competing_growth():
        model.call('pcc_gc_index_py_rehash_slots', 'pcc_py_gc_object_slots',
                   'pcc_py_gc_object_cap', 'pcc_py_gc_object_used', 32)
    model.on_relock = competing_growth
    model.register()
    model.assert_published()
    assert model.acquires == 2 and model.commits == 0
    assert len(model.prepared) == 1 and model.freed == model.prepared
    assert model.load('pcc_py_gc_object_cap') == 32


def test_non_slab_provenance_is_retired_only_after_exact_index_publication():
    model = Registration()
    model.ns['pcc_gc_granule_is_object_start'] = lambda obj: 0
    def retire_provenance(obj):
        assert model.locked and model.call('pcc_gc_object_index_find', obj) != 0
        model.removed_provenance.append(obj)
    model.ns['pcc_gc_managed_pointer_index_remove'] = retire_provenance
    model.register()
    model.assert_published()
    assert model.removed_provenance == [model.object]


@pytest.mark.parametrize('failure', ('node', 'table', 'commit'))
def test_preparation_and_commit_failures_do_not_publish_partial_object(failure):
    model = Registration(1, pooled_node=False)
    if failure in ('table', 'commit'):
        model.fill_index()
    if failure == 'node':
        model.fail_malloc = 88
    elif failure == 'table':
        model.fail_malloc = 16384 * 16
    else:
        model.short_commit = True
    model.register()
    assert not model.locked and model.pending == 0
    assert model.call('pcc_gc_object_index_find', model.object) == 0
    assert model.load('pcc_gc_object_head') == model.load('pcc_gc_live_bytes') == 0
    assert model.load(model.object, 12) == 0x400000
    assert set(model.freed) == set(model.prepared)
    assert model.generation() & 1 == 0


@pytest.mark.parametrize('backend,acquires', ((1, 1), (2, 1), (3, 1)))
def test_empty_node_pool_registers_nonmoving_objects_in_one_graph_acquisition(
    backend, acquires
):
    # The pool keeps at most 8192 nodes and is empty between sweeps. The
    # nonmoving GC1-GC3 (GC3 since 03cbd1849) prepare the node before the
    # first acquisition (unlocked pool-head hint); GC4 keeps the planned
    # unlock/prepare/relock.
    model = Registration(backend, pooled_node=False)
    model.register()
    model.assert_published()
    assert model.acquires == acquires
    node = model.call('pcc_gc_object_index_find', model.object)
    assert model.prepared == [node] and model.freed == []


def test_pool_refilled_after_the_hint_wins_and_the_prepared_node_is_freed():
    # The hint is not the authority: the locked plan uses a node pooled
    # meanwhile, and the node prepared for nothing is freed after unlock.
    model = Registration(1, pooled_node=False)
    pooled = model.calloc(1, 88)
    original_lock = model.lock

    def lock_after_refill(kind):
        if model.acquires == 0:
            model.store('pcc_gc_object_node_free_head', 0, pooled)
            model.store('pcc_gc_object_node_free_count', 0, 1)
        original_lock(kind)

    model.ns['pcc_py_gc_minor_graph_lock_for'] = lock_after_refill
    model.register()
    model.assert_published()
    assert model.acquires == 1
    assert model.call('pcc_gc_object_index_find', model.object) == pooled
    assert len(model.prepared) == 1 and model.freed == model.prepared
    assert model.load('pcc_gc_object_node_free_head') == 0


def test_failed_hint_still_registers_from_a_refilled_pool_without_a_retry():
    # A failed hint is the round trip's one attempt (the 'node' case above
    # fails as before). It must not fail a registration the plan can serve.
    model = Registration(2, pooled_node=False)
    model.fail_malloc = 88
    pooled = model.calloc(1, 88)
    original_lock = model.lock

    def lock_after_refill(kind):
        if model.acquires == 0:
            model.store('pcc_gc_object_node_free_head', 0, pooled)
            model.store('pcc_gc_object_node_free_count', 0, 1)
        original_lock(kind)

    model.ns['pcc_py_gc_minor_graph_lock_for'] = lock_after_refill
    model.register()
    model.assert_published()
    assert model.acquires == 1 and model.fail_malloc == 0
    assert model.call('pcc_gc_object_index_find', model.object) == pooled
    assert model.prepared == [] and model.freed == []


@pytest.mark.parametrize('generation', (-2, -1, 9223372036854775806))
def test_bad_generation_still_aborts_before_any_object_publication(generation):
    model = Registration()
    model.store('pcc_gc_object_index_generation', 0, generation)
    before = dict(model.words)
    with pytest.raises(RuntimeError, match='abort'):
        model.register()
    assert model.commits == 1 and model.generation() == generation
    assert model.load('pcc_gc_object_head') == 0
    assert model.load(model.object, 12) == before[model.object + 12]
    assert model.load('pcc_gc_object_node_free_head') == before['pcc_gc_object_node_free_head']


def test_odd_generation_keeps_existing_nested_writer_semantics():
    model = Registration()
    model.store('pcc_gc_object_index_generation', 0, 7)
    model.generation_stores.clear()
    model.register()
    model.assert_published()
    assert model.commits == 1 and model.generation() == 7
    assert model.generation_stores == []


def test_last_fast_generation_still_publishes_the_actual_insertion():
    model = Registration()
    model.store('pcc_gc_object_index_generation', 0, 9223372036854775802)
    model.register()
    model.assert_published()
    assert model.commits == 0 and model.generation() == 9223372036854775804


def test_registration_invalidates_a_stable_readers_already_probed_negative():
    model = Registration()
    before = model.generation()
    checks = []
    def register_before_final_sequence_check(base, offset, order):
        if base == 'pcc_gc_object_index_generation' and order == ('relaxed',):
            checks.append(base)
            # The first check validates slots/capacity. The second follows
            # the completed negative probe, just before it may return zero.
            if len(checks) == 2:
                model.hook = None
                model.register()
    model.hook = register_before_final_sequence_check
    assert model.call('pcc_gc_object_index_seen_stable', model.object) == 1
    assert len(checks) == 2
    model.assert_published()
    assert model.commits == 0 and model.generation() == before + 2


def test_near_wrap_keeps_noop_commit_then_insert_exhaustion_behavior():
    model = Registration()
    model.store('pcc_gc_object_index_generation', 0, 9223372036854775804)
    with pytest.raises(RuntimeError, match='abort'):
        model.register()
    assert model.commits == 1 and model.generation() == 9223372036854775806
    assert model.call('pcc_gc_object_index_find', model.object) == 0


def test_gc0_still_returns_without_any_registration_or_generation_change():
    model = Registration(0)
    before = model.generation()
    model.register()
    assert model.acquires == model.commits == 0
    assert model.generation() == before


@pytest.mark.integration
def test_native_paused_worker_warm_allocations_and_nonmoving_growth(
    tmp_path, pcc_runtime_archive,
):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    source = tmp_path / 'allocation_index_noop.c'
    source.write_text(r'''#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_gc_object_index_generation;
extern int64_t pcc_gc_object_index_plan_capacity(int64_t);
extern void *pcc_gc_object_index_find(void *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_cms_stop_worker(void);
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int backend = atoi(argv[1]);
    if (pcc_gc_set_backend(backend) != 0) return 1;
    py_gc_disable();
    /* The existing diagnostic joins the worker and holds lifecycle pause 4,
     * so allocations cannot restart it. gc.disable alone does not stop its
     * retirement drains. Object construction still uses public list APIs.
     */
    if (pcc_gc_cms_stop_worker() != 0) return 2;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(0);
    if (!roots[0]) return 3;
    /* Warm +2 is checked for GC1-4; forced native +4 growth for GC1/2.
     * GC3/4 growth remains model coverage: a long allocation fill may also
     * trigger valid promotion/relocation index writes. This is functional
     * qualification with a paused worker, never a throughput measurement.
     */
    int fast = 0, growth = 0, allocated = 0;
    for (int i = 0; i < 32768; i++) {
        pcc_py_gc_minor_graph_lock();
        int64_t required = backend ? pcc_gc_object_index_plan_capacity(1) : 0;
        int64_t before = pcc_gc_object_index_generation;
        pcc_py_gc_minor_graph_unlock();
        if (required < 0 || (backend >= 3 && required != 0)) return 3;
        roots[1] = py_list_new(0);
        if (!roots[1]) return 4;
        pcc_py_gc_minor_graph_lock();
        int64_t delta = backend ? (required ? 4 : 2) : 0;
        if (pcc_gc_object_index_generation != before + delta) return 5;
        if (backend && !pcc_gc_object_index_find(roots[1])) return 6;
        if (!backend && pcc_gc_object_index_find(roots[1])) return 7;
        pcc_py_gc_minor_graph_unlock();
        if (required) growth++; else fast++;
        py_list_append(roots[0], roots[1]);
        if (py_err_occurred()) return 8;
        pcc_gc_store_root(&roots[1], NULL);
        allocated++;
        if (growth || backend >= 3 || (!backend && allocated == 2)) break;
    }
    if (!fast || ((backend == 1 || backend == 2) && growth != 1)) return 10;
    py_gc_collect();
    if (py_list_len(roots[0]) != allocated) return 11;
    PyObject *last = py_list_get(roots[0], allocated - 1);
    if (!last || py_list_len(last) != 0) return 12;
    py_decref(last);
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("ALLOCATION_INDEX_NOOP_OK");
    return 0;
}
''')
    executable = tmp_path / 'allocation_index_noop'
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    for backend in range(5):
        result = subprocess.run([str(executable), str(backend)], capture_output=True,
                                text=True, timeout=20)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, 'ALLOCATION_INDEX_NOOP_OK\n', '',
        ), (backend, result)


def test_gc4_registration_selects_no_zpage():
    # Every tracing collector runs the count-free protocol, so a GC4 object
    # is an allocator cell: registration plans and commits no logical zpage
    # (no page lookup, zpage node or owner entry) and publishes the object.
    model = Registration(4)
    for hook in ('_backend4_zpage_find_page_for_addr',
                 '_backend4_zpage_find_reusable_page',
                 '_backend4_zpage_track_alloc_selected'):
        model.ns[hook] = lambda *args, hook=hook: pytest.fail(hook + ' called')
    model.register()
    model.assert_published()
    assert model.acquires == 1
