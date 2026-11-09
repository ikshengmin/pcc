"""Source-derived regression for publication-OOM provenance loss.

Contract: docs/project-intent.md, shared GC roots and ordinary object lifetime.
This file exercises the actual registration, index and public provenance
bodies. Memory primitives and graph ownership are modeled. This is not a full
collector or performance qualification.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_allocation_index_noop_commit import Registration
from tests.python.test_gc_stable_object_membership import Index, ROOT, load_bodies


def _registered_non_slab_after_publication_oom(backend):
    model = Registration(backend)
    model.fill_index()
    model.ns['pcc_gc_granule_is_object_start'] = lambda obj: 0
    # Replace Registration's observation-only removal with the real provider.
    load_bodies(model.ns, ROOT / 'freestanding_gc_index_table.py',
                {'pcc_gc_managed_pointer_index_remove'})
    assert model.call('pcc_gc_managed_pointer_index_insert', model.object) == 1
    assert model.call('pcc_gc_managed_pointer_index_contains', model.object) == 1

    failed = []
    def publication_oom(count, size):
        # Registration's object node is pooled and its table uses malloc.
        # The next 16B calloc is the new immutable publication record.
        if count * size == 16 and not failed:
            failed.append(True)
            return 0
        return model.calloc(count, size)
    model.ns['calloc'] = publication_oom
    model.register()

    assert failed == [True]
    assert not model.locked
    assert model.load('pcc_py_gc_object_published') == 0
    # The key really reached the authoritative object table.
    assert model.call('pcc_gc_object_index_seen_stable', model.object) == 1
    # Registration retired the temporary exact provenance after insertion.
    assert model.call('pcc_gc_managed_pointer_index_contains', model.object) == 0
    return model


@pytest.mark.parametrize('backend', (1, 2, 3, 4))
def test_publication_oom_locked_object_lookup_keeps_registered_node(backend):
    model = _registered_non_slab_after_publication_oom(backend)
    model.lock(2)
    try:
        # Baseline find_published returned NULL without a fallback.
        assert model.call('pcc_gc_object_index_find', model.object) != 0
    finally:
        model.unlock()


@pytest.mark.parametrize('backend', (1, 2, 3, 4))
def test_publication_oom_public_provenance_keeps_non_slab_object(backend):
    model = _registered_non_slab_after_publication_oom(backend)
    model.ns.update({
        '_object_graph_lock': lambda: model.lock(2),
        'pcc_capi_is_type_object_value': lambda obj: 0,
        '_forwarding_find': lambda obj: 0,
        '_forwarding_target_exists': lambda obj: 0,
    })
    load_bodies(model.ns, ROOT / 'py_gc_backend.py',
                {'_pointer_is_managed_no_lock', 'pcc_gc_pointer_is_managed'})
    # All required raw/large-object provenance checks run through production
    # bodies. This is not an assertion that the optional fast probe must hit.
    assert model.call('pcc_gc_pointer_is_managed', model.object) == 1
    assert not model.locked


def test_initial_publication_oom_preserves_node_and_absent_results():
    model = Index(publication_oom=True)
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert model.call('pcc_gc_object_index_find', 0xF0000) == 0
    # Positive-only membership remains a cheap unknown on publication OOM.
    assert model.call('pcc_gc_object_index_seen', model.kept) == 0
    assert not any(base in (0xA000, 0xB000) for base, _, _ in model.reads)


def test_oom_lookup_rejects_mixed_slots_capacity_before_dereference():
    model = Index(publication_oom=True)
    old_slots = model.slots
    def grow_before_capacity(base, offset, order):
        if base == 'pcc_py_gc_object_cap' and order:
            model.hook = None
            model.call('pcc_gc_index_py_rehash_slots', 'pcc_py_gc_object_slots',
                       'pcc_py_gc_object_cap', 'pcc_py_gc_object_used', 64)
    model.hook = grow_before_capacity
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert model.load('pcc_py_gc_object_cap') == 64
    assert not any(base == old_slots and order for base, _, order in model.reads)


@pytest.mark.parametrize('boundary', ('key', 'value'))
def test_oom_lookup_retries_backward_shift_key_and_value_interleavings(boundary):
    model = Index(publication_oom=True)
    before = model.generation()
    if boundary == 'key':
        model.shift_before_second_key()
    else:
        def shift_before_value(base, offset, order):
            if base == model.slots and offset == 24:
                model.hook = None
                model.call('pcc_gc_object_index_remove', model.removed)
        model.hook = shift_before_value
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert model.generation() == before + 2
    assert model.fences.count('acquire') >= 4


@pytest.mark.parametrize('initially_present', (False, True))
def test_oom_lookup_validates_after_node_or_miss_before_return(initially_present):
    model = Index(publication_oom=True)
    wanted = model.kept if initially_present else 0x40000
    checks = []
    cells = ('pcc_py_gc_object_slots', 'pcc_py_gc_object_cap',
             'pcc_py_gc_object_count', 'pcc_py_gc_object_used')
    def replace_before_final_check(base, offset, order):
        if base == 'pcc_gc_object_index_generation' and order == ('relaxed',):
            checks.append(True)
            if len(checks) == 2:
                model.hook = None
                model.call('pcc_gc_index_py_upsert', *cells, wanted, 0xC000, 1, 8)
    model.hook = replace_before_final_check
    assert model.call('pcc_gc_object_index_find', wanted) == 0xC000
    assert len(checks) == 2
    assert not any(base in (0xA000, 0xB000, 0xC000) for base, _, _ in model.reads)


def test_oom_lookup_retries_clear_and_republication_after_pair_validation():
    model = Index(publication_oom=True)
    wanted = 0x40000
    def clear_and_insert(base, offset, order):
        if base == model.slots and order:
            model.hook = None
            model.call('pcc_gc_object_index_clear')
            model.call('pcc_gc_object_index_insert', wanted, 0xC000)
    model.hook = clear_and_insert
    assert model.call('pcc_gc_object_index_find', wanted) == 0xC000


def test_oom_lookup_waits_for_an_independent_odd_writer_to_finish():
    model = Index(publication_oom=True)
    odd = model.call('_object_index_write_begin')
    loads = []
    def finish_writer(base, offset, order):
        if base == 'pcc_gc_object_index_generation' and order == ('acquire',):
            loads.append(True)
            if len(loads) == 2:
                model.hook = None
                model.call('_object_index_write_end', odd)
    # This provider models another thread finishing between polls. It does
    # not permit a synchronous lookup to wait on its own unfinished writer.
    model.hook = finish_writer
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert len(loads) == 2
    assert model.generation() == odd + 1


def test_oom_lookup_rejects_negative_generation_before_table_read():
    model = Index(publication_oom=True)
    model.store('pcc_gc_object_index_generation', 0, -2)
    model.reads.clear()
    with pytest.raises(RuntimeError, match='abort'):
        model.call('pcc_gc_object_index_find', model.kept)
    assert not any(base == model.slots for base, _, _ in model.reads)


def test_oom_lookup_can_read_the_last_even_generation_without_writing():
    model = Index(publication_oom=True)
    model.store('pcc_gc_object_index_generation', 0, 9223372036854775806)
    model.generation_stores.clear()
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert model.generation_stores == []


@pytest.mark.parametrize('key', (0, 3))
def test_null_and_tagged_lookup_never_read_table_or_generation(key):
    model = Index(publication_oom=True)
    model.reads.clear()
    assert model.call('pcc_gc_object_index_find', key) == 0
    assert model.reads == []


def test_empty_cleared_table_is_an_exact_miss():
    model = Index(publication_oom=True)
    model.call('pcc_gc_object_index_clear')
    assert model.call('pcc_gc_object_index_find', model.kept) == 0


def test_oom_probe_is_bounded_even_if_a_table_has_no_empty_slot():
    model = Index(publication_oom=True)
    for index in range(8):
        model.store(model.slots, index * 16, 0x50000 + index * 16)
        model.store(model.slots, index * 16 + 8, 0xD000 + index * 16)
    model.reads.clear()
    assert model.call('pcc_gc_object_index_find', 0xF0000) == 0
    assert sum(base == model.slots for base, _, _ in model.reads) == 8


def test_other_missing_publication_does_not_enter_object_fallback():
    model = Index(publication_oom=True)
    model.reads.clear()
    assert model.call('pcc_gc_index_py_find_published',
                      'pcc_py_gc_forwarding_published', model.kept) == 0
    assert not any(base == 'pcc_gc_object_index_generation' for base, _, _ in model.reads)


def test_successful_publication_keeps_the_existing_fast_lookup():
    model = Index()
    def forbidden(_):
        raise AssertionError('successful publication entered OOM fallback')
    model.ns['pcc_gc_object_index_find_unpublished'] = forbidden
    model.reads.clear()
    assert model.call('pcc_gc_object_index_find', model.kept) == 0xB000
    assert not any(base == 'pcc_gc_object_index_generation' for base, _, _ in model.reads)
