"""Forwarding publication metadata is prepared before allocation-free commit.

Contract: docs/project-intent.md requires shared GC roots, ordinary object
lifetimes and recoverable allocation failure. These models run the production
index and installer bodies, with failure only in raw calloc. Memory, graph
exclusion and counted-reference primitives are modeled, not native collectors.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.python.test_gc_forwarding_insert_ownership import Install


PLAN_POINTERS = (0, 8, 16, 24, 40, 56, 72, 80, 88, 96, 104, 112)
PLAN_METADATA = (72, 80, 88, 96, 104, 112)


class Publication(Install):
    def __init__(self, backend=4):
        self.guard_allocators = False
        self.record_failure_after = None
        self.record_failures = 0
        self.record_attempts = 0
        self.calloc_events = []
        super().__init__(backend)
        # Index starts with two objects in cap8; Install adds four and grows
        # that table once. Start this component's retirement checks clean.
        assert self.call('pcc_gc_index_reclaim_retired') == 2

    @contextmanager
    def allocation_free_commit(self):
        self.guard_allocators = True
        self.lock()
        try:
            yield
        finally:
            self.unlock()
            self.guard_allocators = False

    def allocate(self, size):
        assert not (self.guard_allocators and self.depth), 'malloc under graph lock'
        return super().allocate(size)

    def release(self, block):
        assert not (self.guard_allocators and self.depth), 'free under graph lock'
        return super().release(block)

    def allocate_zeroed(self, count, size):
        assert not (self.guard_allocators and self.depth), 'calloc under graph lock'
        if count * size == 16:
            self.record_attempts += 1
            if self.record_failure_after is not None:
                if self.record_failure_after == 0:
                    self.record_failure_after = None
                    self.record_failures += 1
                    self.calloc_events.append((16, 0))
                    return 0
                self.record_failure_after -= 1
        result = self.calloc(count, size)
        self.calloc_events.append((count * size, result))
        return result

    def store(self, base, offset, value, *order):
        if isinstance(base, int):
            owner = next(((start, size) for start, size in self.blocks.items()
                          if start <= base < start + size), None)
            address = base + offset
            assert owner is not None, (base, offset)
            start, size = owner
            assert start <= address and address + 8 <= start + size, (base, offset)
            assert start not in getattr(self, 'freed', ()), (base, offset)
        super().store(base, offset, value, *order)

    def cells(self, kind):
        return tuple('pcc_py_gc_' + kind + '_' + suffix
                     for suffix in ('slots', 'cap', 'count', 'used'))

    def state(self, kind):
        slots, cap, count, used = (self.load(cell) for cell in self.cells(kind))
        record = self.load('pcc_py_gc_' + kind + '_published')
        return (slots, cap, count, used, record,
                tuple(self.load(slots, offset) for offset in range(0, cap * 16, 8)),
                (self.load(record, 0), self.load(record, 8)) if record else (),
                self.load('pcc_py_gc_index_retired_tables'))

    def raw_insert(self, kind, key, node):
        return self.call('pcc_gc_index_py_insert', *self.cells(kind), key, node, 1, 1, 8)

    def fill_small(self, kind):
        entries = [(0x80000 + i * 16, 0x90000 + i * 16) for i in range(4)]
        for key, node in entries:
            assert self.raw_insert(kind, key, node) == 1
        assert self.load(self.cells(kind)[1]) == 8
        return entries

    def find(self, kind, key):
        return self.call('pcc_gc_' + kind + '_index_find', key)


@pytest.mark.parametrize('kind', ('forwarding', 'forwarding_target'))
@pytest.mark.parametrize('growing', (False, True))
def test_record_oom_keeps_old_table_and_releases_private_rehash(kind, growing):
    model = Publication()
    entries = model.fill_small(kind) if growing else []
    before = model.state(kind)
    generation = model.generation()
    freed = list(model.freed)
    allocations = len(model.calloc_events)
    model.record_failure_after = 0

    assert model.raw_insert(kind, 0xA0000, 0xB0000) == -1
    assert model.record_failures == 1
    assert model.state(kind) == before
    assert model.generation() == generation
    attempted = model.calloc_events[allocations:]
    assert len(attempted) == 2 and attempted[-1] == (16, 0)
    private = attempted[0][1]
    assert private and model.freed == freed + [private]
    assert model.find(kind, 0xA0000) == 0
    for key, node in entries:
        assert model.find(kind, key) == node

    assert model.raw_insert(kind, 0xA0000, 0xB0000) == 1
    assert model.find(kind, 0xA0000) == 0xB0000
    assert model.load('pcc_py_gc_' + kind + '_published') != 0
    for key, node in entries:
        assert model.find(kind, key) == node
    assert model.call('pcc_gc_index_reclaim_retired') == (2 if growing else 0)


@pytest.mark.parametrize('kind_id,kind', ((0, 'forwarding'), (1, 'forwarding_target')))
@pytest.mark.parametrize('growing', (False, True))
@pytest.mark.parametrize('missing', (None, 0, 8, 16))
def test_plan_missing_metadata_keeps_zero_buffer_for_same_plan_retry(
    kind_id, kind, growing, missing,
):
    model = Publication()
    entries = model.fill_small(kind) if growing else []
    cap = model.call('pcc_gc_forwarding_plan_index_capacity', kind_id, 1)
    prepared = model.calloc(cap, 16)
    io = model.calloc(1, 8)
    metadata = model.calloc(3, 8)
    nodes = [model.calloc(1, 16) for _ in range(3)]
    for offset, node in zip((0, 8, 16), nodes):
        model.store(metadata, offset, node)
    model.store(io, 0, prepared)
    before = model.state(kind)
    freed = list(model.freed)
    if missing is not None:
        model.store(metadata, missing, 0)
    metadata_before = tuple(model.load(metadata, i) for i in (0, 8, 16))
    with model.allocation_free_commit():
        assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io,
                          cap, 1, 0 if missing is None else metadata) == -1
    assert model.load(io) == prepared
    assert model.state(kind) == before and model.freed == freed
    assert tuple(model.load(metadata, i) for i in (0, 8, 16)) == metadata_before
    assert all(model.load(prepared, offset) == 0 for offset in range(0, cap * 16, 8))
    if missing is not None:
        model.store(metadata, missing, nodes[missing // 8])
    with model.allocation_free_commit():
        assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io,
                          cap, 1, metadata) == 1
    assert model.load(io) == 0
    assert model.load(model.cells(kind)[0]) == prepared
    assert model.load('pcc_py_gc_' + kind + '_published') == nodes[0]
    assert model.load(metadata) == 0
    assert tuple(model.load(metadata, i) for i in (8, 16)) == (
        (0, 0) if growing else tuple(nodes[1:])
    )
    for key, node in entries:
        assert model.find(kind, key) == node
    assert model.call('pcc_gc_forwarding_plan_index_insert', kind_id, 0xA0000, 0xB0000) == 1
    assert model.find(kind, 0xA0000) == 0xB0000
    assert prepared not in model.freed
    assert model.call('pcc_gc_index_reclaim_retired') == (2 if growing else 0)
    for offset in (0, 8, 16):
        model.release(model.load(metadata, offset))
    model.release(metadata)
    model.release(io)


@pytest.mark.parametrize('kind_id,kind', ((0, 'forwarding'), (1, 'forwarding_target')))
@pytest.mark.parametrize('metadata_present', (False, True))
def test_warm_insert_and_noop_plan_leave_all_private_storage_untouched(
    kind_id, kind, metadata_present,
):
    model = Publication()
    assert model.raw_insert(kind, 0x80000, 0x90000) == 1
    io = model.calloc(1, 8)
    unused = model.calloc(8, 16)
    metadata = model.calloc(3, 8) if metadata_present else 0
    nodes = [model.calloc(1, 16) for _ in range(3)] if metadata else []
    for offset, node in zip((0, 8, 16), nodes):
        model.store(metadata, offset, node)
    model.store(io, 0, unused)
    attempts = model.record_attempts
    model.record_failure_after = 0
    assert model.raw_insert(kind, 0x80010, 0x90010) == 1
    before = model.state(kind)
    with model.allocation_free_commit():
        assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io,
                          8, 1, metadata) == 0
    assert model.state(kind) == before
    assert model.load(io) == unused and unused not in model.freed
    assert all(model.load(unused, i) == 0 for i in range(0, 128, 8))
    if metadata:
        assert [model.load(metadata, i) for i in (0, 8, 16)] == nodes
    assert model.record_attempts == attempts and model.record_failures == 0


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('failed_kind', (0, 1))
def test_public_installer_rejects_record_oom_before_exposing_an_edge(backend, failed_kind):
    model = Publication(backend)
    before = model.edge_state()
    model.record_failure_after = failed_kind
    assert model.install(model.a, model.t) == -1
    assert model.record_failures == 1 and model.edge_state() == before
    nodes = [block for block, size in model.allocations if size == 56]
    assert len(nodes) == 1 and model.freed.count(nodes[0]) == 1
    # Identity preparation and an earlier empty source-table capacity commit
    # may remain. Neither is an edge, header flag or target RC transfer.
    assert model.install(model.a, model.t) == 0
    assert model.find('forwarding', model.a) != 0
    assert model.find('forwarding_target', model.t) != 0
    assert model.call('pcc_gc_note_relocation_read', model.a) == model.t
    assert model.load(model.a, 12) & 2048
    if backend == 4:
        assert model.call('pcc_gc_backend4_slot_needs_resolve', model.a) == 1


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('failed_metadata', range(6))
def test_prepare_metadata_oom_releases_every_private_allocation(backend, failed_metadata):
    model = Publication(backend)
    before = model.edge_state()
    tables = [model.state(kind) for kind in ('forwarding', 'forwarding_target')]
    blocks = set(model.blocks)
    model.record_failure_after = failed_metadata
    # Guard allocation/free only while the graph lock is held. Preparation
    # must allocate and unwind outside it, even for initially empty indexes.
    model.guard_allocators = True
    try:
        assert model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t) == 0
    finally:
        model.guard_allocators = False
    assert model.depth == 0 and model.record_failures == 1
    assert model.edge_state() == before
    assert [model.state(kind) for kind in ('forwarding', 'forwarding_target')] == tables
    for block in set(model.blocks) - blocks:
        assert model.freed.count(block) == 1
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan and model.blocks[plan] == 120
    metadata = [model.load(plan, offset) for offset in PLAN_METADATA]
    assert len(set(metadata)) == 6 and all(model.blocks[node] == 16 for node in metadata)
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == 0
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == 1
    for kind, key in (('forwarding', model.a), ('forwarding_target', model.t)):
        assert model.find(kind, key) != 0
        assert model.load(model.cells(kind)[0]) not in model.freed
        assert model.load('pcc_py_gc_' + kind + '_published') not in model.freed
    assert model.load('pcc_gc_forwarding_population') == 1
    assert model.call('pcc_gc_note_relocation_read', model.a) == model.t


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('missing_offset', PLAN_METADATA)
@pytest.mark.parametrize('retry', (False, True))
def test_missing_plan_metadata_can_retry_or_finish(backend, missing_offset, retry):
    model = Publication(backend)
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan and model.depth == 0
    before = model.edge_state()
    saved = model.load(plan, missing_offset)
    assert saved
    model.store(plan, missing_offset, 0)
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == -1
    assert model.record_failures == 0 and model.edge_state() == before
    model.store(plan, missing_offset, saved)
    if retry:
        with model.allocation_free_commit():
            assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                              model.a, model.t, plan) == 0
        assert model.call('pcc_gc_note_relocation_read', model.a) == model.t
        assert model.load('pcc_gc_forwarding_population') == 1
    owned = [model.load(plan, offset) for offset in PLAN_POINTERS]
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    for block in [plan] + [block for block in owned if block]:
        assert model.freed.count(block) == 1
    for kind in ('identity', 'forwarding', 'forwarding_target'):
        slots = model.load('pcc_py_gc_' + kind + '_slots')
        if slots:
            assert slots not in model.freed
    for kind in ('forwarding', 'forwarding_target'):
        record = model.load('pcc_py_gc_' + kind + '_published')
        if record:
            assert record not in model.freed


@pytest.mark.parametrize('backend', (0, 1, 2, 3))
def test_nonmoving_backends_reject_forwarding_plan_without_side_effects(backend):
    model = Publication(backend)
    # A private plan proves rejection does not depend on a null plan. Every
    # non-GC4 backend rejects before consuming any ownership cell.
    plan = model.calloc(1, 120)
    before = model.edge_state()
    kinds = ('identity', 'forwarding', 'forwarding_target')
    tables = [model.state(kind) for kind in kinds]
    words = dict(model.words)
    blocks, freed = dict(model.blocks), list(model.freed)

    def forbidden_allocator(*args):
        pytest.fail('non-GC4 early rejection allocated or freed')

    allocators = {name: model.ns[name] for name in ('malloc', 'calloc', 'free')}
    model.ns.update({name: forbidden_allocator for name in allocators})
    try:
        assert model.call('pcc_gc_forwarding_install_plan_prepare',
                          model.a, model.t) == 0
        assert model.depth == 0
        assert model.words == words and model.blocks == blocks
        assert model.freed == freed
        assert model.edge_state() == before
        assert [model.state(kind) for kind in kinds] == tables
        model.lock()
        try:
            assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                              model.a, model.t, plan) == -1
        finally:
            model.unlock()
    finally:
        model.ns.update(allocators)
    assert model.depth == 0
    assert model.words == words and model.blocks == blocks
    assert model.freed == freed
    assert model.edge_state() == before
    assert [model.state(kind) for kind in kinds] == tables
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed == freed + [plan]


@pytest.mark.parametrize('initially_empty', (False, True))
def test_reader_snapshots_survive_plan_finish_and_intervening_writer(initially_empty):
    model = Publication()
    if not initially_empty:
        for kind in ('forwarding', 'forwarding_target'):
            model.fill_small(kind)
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan
    if initially_empty:
        # A serialized writer creates small tables after the empty snapshot.
        # The prepared plan must still own both retirement nodes per index.
        for kind in ('forwarding', 'forwarding_target'):
            model.fill_small(kind)
    snapshots = [model.state(kind) for kind in ('forwarding', 'forwarding_target')]
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == 0
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    for state in snapshots:
        slots, cap, _, _, record, words, record_words, _ = state
        assert slots not in model.freed and record not in model.freed
        assert tuple(model.load(slots, i) for i in range(0, cap * 16, 8)) == words
        assert (model.load(record), model.load(record, 8)) == record_words
    assert model.call('pcc_gc_note_relocation_read', model.a) == model.t
    # Explicitly model all readers stopping before reclamation, not finish.
    assert model.call('pcc_gc_index_reclaim_retired') == 4
    for state in snapshots:
        assert model.freed.count(state[0]) == model.freed.count(state[4]) == 1


@pytest.mark.parametrize('kind_id,kind', ((0, 'forwarding'), (1, 'forwarding_target')))
def test_stale_capacity_rejects_before_touching_plan_metadata(kind_id, kind):
    model = Publication()
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan
    slots_offset, cap_offset, metadata_offset = ((40, 48, 72) if kind_id == 0 else (56, 64, 96))
    cap = model.load(plan, cap_offset)
    prepared = model.load(plan, slots_offset)
    for i in range(cap // 2):
        assert model.raw_insert(kind, 0x80000 + i * 16, 0x90000 + i * 16) == 1
    assert model.call('pcc_gc_forwarding_plan_index_capacity', kind_id, 1) > cap
    before = model.state(kind)
    metadata = tuple(model.load(plan, metadata_offset + i) for i in (0, 8, 16))
    with model.allocation_free_commit():
        assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id,
                          plan + slots_offset, cap, 1, plan + metadata_offset) == -1
    assert model.state(kind) == before and model.load(plan, slots_offset) == prepared
    assert tuple(model.load(plan, metadata_offset + i) for i in (0, 8, 16)) == metadata
    assert all(model.load(prepared, i) == 0 for i in range(0, cap * 16, 8))
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == model.freed.count(prepared) == 1
    assert all(model.freed.count(node) == 1 for node in metadata)


def test_identity_growth_returns_old_slots_for_free_after_unlock():
    model = Publication()
    entries = model.fill_small('identity')
    old_slots = model.load(model.cells('identity')[0])
    cap = model.call('pcc_gc_forwarding_plan_index_capacity', 2, 1)
    prepared = model.calloc(cap, 16)
    io = model.calloc(1, 8)
    model.store(io, 0, prepared)
    freed = list(model.freed)
    with model.allocation_free_commit():
        assert model.call('pcc_gc_forwarding_plan_index_commit',
                          2, io, cap, 1, 0) == 1
        assert model.load(io) == old_slots
        assert model.freed == freed
        assert model.load(model.cells('identity')[0]) == prepared
    assert model.depth == 0
    model.release(model.load(io))
    model.store(io, 0, 0)
    assert model.freed == freed + [old_slots]
    assert prepared not in model.freed
    for key, node in entries:
        assert model.exact('identity', key) == node
    assert model.call('pcc_gc_forwarding_plan_index_insert', 2, 0xA0000, 0xB0000) == 1
    assert model.exact('identity', 0xA0000) == 0xB0000
    assert model.load(model.cells('identity')[0]) == prepared
    model.release(io)


@pytest.mark.parametrize('kind', ('forwarding', 'forwarding_target'))
def test_retirement_oom_does_not_hide_the_successfully_published_table(kind):
    model = Publication()
    entries = model.fill_small(kind)
    old_record = model.load('pcc_py_gc_' + kind + '_published')
    old_slots = model.load(model.cells(kind)[0])
    # First 16B request is the required record; fail the old record's retire
    # node instead. Its intentional OOM leak must not undo publication.
    model.record_failure_after = 1
    assert model.raw_insert(kind, 0xA0000, 0xB0000) == 1
    assert model.record_failures == 1
    assert model.load('pcc_py_gc_' + kind + '_published') not in (0, old_record)
    assert model.find(kind, 0xA0000) == 0xB0000
    for key, node in entries:
        assert model.find(kind, key) == node
    assert model.call('pcc_gc_index_reclaim_retired') == 1
    assert old_slots in model.freed and old_record not in model.freed


NATIVE_HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fail_after = -1;
static int failures;
static int commit_locked;
static int locked_allocator_calls;
/* This standalone harness owns its raw allocator. No linker wrapping or
   platform allocator symbols are required. Storage is bounded and aligned;
   free records the forbidden lock boundary but does not recycle the arena. */
static union { int64_t align; unsigned char bytes[8388608]; } arena;
static size_t arena_used;
static void *freed_pointers[4096];
static size_t freed_count;
static int free_count(void *pointer) {
    int count = 0;
    for (size_t n = 0; n < freed_count; n++) {
        if (freed_pointers[n] == pointer) count++;
    }
    return count;
}
void *malloc(size_t size) {
    if (commit_locked) locked_allocator_calls++;
    if (size > sizeof(arena.bytes) - 16) return NULL;
    size_t rounded = (size + 15) & ~(size_t)15;
    if (rounded > sizeof(arena.bytes) - arena_used) return NULL;
    void *result = arena.bytes + arena_used;
    arena_used += rounded ? rounded : 16;
    return result;
}
void free(void *pointer) {
    if (commit_locked) locked_allocator_calls++;
    if (pointer) {
        if (freed_count >= 4096) abort();
        freed_pointers[freed_count++] = pointer;
    }
}

/* Standalone raw allocator fault only. Lookups, table commits and retirement
   use the emitted production index object. This is not a collector harness. */
void *calloc(size_t count, size_t size) {
    if (commit_locked) locked_allocator_calls++;
    if (count && size > ((size_t)-1) / count) return NULL;
    size_t bytes = count * size;
    if (bytes == 16 && fail_after >= 0) {
        if (fail_after == 0) {
            fail_after = -1;
            failures++;
            return NULL;
        }
        fail_after--;
    }
    void *result = malloc(bytes);
    if (result) memset(result, 0, bytes);
    return result;
}

void *pcc_allocator_object_side_slot(void *key, int64_t create) {
    (void)key; (void)create; return NULL;
}
void *pcc_allocator_granule_object_slot(void *key) { (void)key; return NULL; }
void *pcc_allocator_object_node_record_find(void *key) {
    (void)key; return NULL;
}
static int64_t primary[32][4];
void *pcc_gc_tracked_primary_index_cells(void *key) {
    uintptr_t bits = (uintptr_t)key;
    return primary[((bits >> 4) ^ (bits >> 11) ^ (bits >> 19)) & 31];
}

#define DECLARE(kind) \
extern void *pcc_py_gc_##kind##_slots; \
extern void *pcc_py_gc_##kind##_published; \
extern int64_t pcc_py_gc_##kind##_cap; \
extern int64_t pcc_py_gc_##kind##_count; \
extern int64_t pcc_py_gc_##kind##_used; \
void *pcc_gc_##kind##_index_find(void *key); \
int64_t pcc_gc_##kind##_index_insert(void *key, void *node); \
void pcc_gc_##kind##_index_clear(void)
DECLARE(forwarding);
DECLARE(forwarding_target);
DECLARE(object);
extern void *pcc_py_gc_index_retired_tables;
extern void *pcc_py_gc_managed_pointer_published;
int64_t pcc_gc_managed_pointer_index_insert(void *key);
int64_t pcc_gc_managed_pointer_index_contains(void *key);
int64_t pcc_gc_managed_pointer_index_seen(void *key);
int64_t pcc_gc_forwarding_plan_index_capacity(int64_t kind, int64_t extra);
int64_t pcc_gc_forwarding_plan_index_commit(int64_t kind, void **io, int64_t cap,
                                            int64_t extra, void **metadata);
int64_t pcc_gc_index_py_rehash_slots(void **slots, int64_t *cap, int64_t *used, int64_t requested);
int64_t pcc_gc_index_reclaim_retired(void);

#define CHECK(test, code) do { if (!(test)) { \
    fprintf(stderr, "forwarding-oom line=%d code=%d\n", __LINE__, code); return code; \
} } while (0)

static void *key_for(int n) { return (void *)(uintptr_t)(0x800000 + n * 16); }
static void *node_for(int n) { return (void *)(uintptr_t)(0x900000 + n * 16); }
static int64_t insert(int kind, int n) {
    if (kind == 0) return pcc_gc_forwarding_index_insert(key_for(n), node_for(n));
    return pcc_gc_forwarding_target_index_insert(key_for(n), node_for(n));
}
static void *find(int kind, int n) {
    if (kind == 0) return pcc_gc_forwarding_index_find(key_for(n));
    return pcc_gc_forwarding_target_index_find(key_for(n));
}

static int check_kind(int kind) {
    void **slots = kind == 0 ? &pcc_py_gc_forwarding_slots : &pcc_py_gc_forwarding_target_slots;
    void **published = kind == 0 ? &pcc_py_gc_forwarding_published : &pcc_py_gc_forwarding_target_published;
    int64_t *cap = kind == 0 ? &pcc_py_gc_forwarding_cap : &pcc_py_gc_forwarding_target_cap;
    int64_t *count = kind == 0 ? &pcc_py_gc_forwarding_count : &pcc_py_gc_forwarding_target_count;
    int64_t *used = kind == 0 ? &pcc_py_gc_forwarding_used : &pcc_py_gc_forwarding_target_used;
    int before_failures = failures;
    fail_after = 0;
    CHECK(insert(kind, 1) == -1 && failures == before_failures + 1, 10);
    CHECK(!*slots && !*published && !*cap && !*count && !*used, 11);
    CHECK(!pcc_py_gc_index_retired_tables && !find(kind, 1), 12);
    CHECK(insert(kind, 1) == 1 && find(kind, 1) == node_for(1), 13);
    CHECK(*cap == 256 && *published, 14);
    for (int n = 2; n <= 128; n++) CHECK(insert(kind, n) == 1, 15);

    void *old_slots = *slots;
    void *old_record = *published;
    fail_after = 0;
    CHECK(insert(kind, 129) == -1, 16);
    CHECK(*slots == old_slots && *published == old_record && *cap == 256, 17);
    CHECK(*count == 128 && *used == 128 && !pcc_py_gc_index_retired_tables, 18);
    CHECK(!find(kind, 129), 19);
    for (int n = 1; n <= 128; n++) CHECK(find(kind, n) == node_for(n), 20);
    CHECK(insert(kind, 129) == 1 && *cap == 512 && *published != old_record, 21);
    CHECK(pcc_gc_index_reclaim_retired() == 2, 22);

    int64_t extra = *cap;
    int64_t needed = pcc_gc_forwarding_plan_index_capacity(kind, extra);
    void *prepared = calloc((size_t)needed, 16);
    void *io = prepared;
    CHECK(prepared && needed > *cap, 23);
    old_slots = *slots;
    old_record = *published;
    void *metadata[3];
    for (int n = 0; n < 3; n++) {
        metadata[n] = calloc(1, 16);
        CHECK(metadata[n], 24);
    }
    fail_after = 0; /* Commit must not consume even one allocator request. */
    commit_locked = 1;
    int64_t status = pcc_gc_forwarding_plan_index_commit(kind, &io, needed, extra, NULL);
    commit_locked = 0;
    CHECK(status == -1 && !locked_allocator_calls && fail_after == 0, 24);
    for (int missing = 0; missing < 3; missing++) {
        void *saved = metadata[missing];
        metadata[missing] = NULL;
        commit_locked = 1;
        status = pcc_gc_forwarding_plan_index_commit(kind, &io, needed, extra, metadata);
        commit_locked = 0;
        CHECK(status == -1 && !locked_allocator_calls && fail_after == 0, 24);
        CHECK(!metadata[missing], 25);
        metadata[missing] = saved;
        CHECK(io == prepared && *slots == old_slots && *published == old_record, 25);
        CHECK(*cap == 512 && *count == 129 && *used == 129, 26);
        for (int64_t n = 0; n < needed * 2; n++) CHECK(((uintptr_t *)prepared)[n] == 0, 27);
    }
    commit_locked = 1;
    status = pcc_gc_forwarding_plan_index_commit(kind, &io, needed, extra, metadata);
    commit_locked = 0;
    CHECK(status == 1 && !locked_allocator_calls && fail_after == 0, 28);
    CHECK(!metadata[0] && !metadata[1] && !metadata[2], 28);
    CHECK(!io && *slots == prepared && *cap == needed && *published, 29);
    for (int n = 1; n <= 129; n++) CHECK(find(kind, n) == node_for(n), 30);
    CHECK(((void **)old_record)[0] == old_slots && ((int64_t *)old_record)[1] == 512, 30);
    CHECK(!free_count(old_record) && !free_count(old_slots), 30);
    CHECK(pcc_gc_index_reclaim_retired() == 2, 31);
    CHECK(free_count(old_record) == 1 && free_count(old_slots) == 1, 31);
    CHECK(!free_count(prepared) && !free_count(*published), 31);
    fail_after = -1;

    prepared = calloc(8, 16);
    io = prepared;
    fail_after = 0;
    commit_locked = 1;
    status = pcc_gc_forwarding_plan_index_commit(kind, &io, 8, 1, NULL);
    commit_locked = 0;
    CHECK(status == 0 && !locked_allocator_calls, 32);
    CHECK(io == prepared && fail_after == 0, 33);
    CHECK(insert(kind, 130) == 1 && fail_after == 0, 34);
    fail_after = -1;
    free(prepared);

    old_record = *published;
    fail_after = 1; /* Publish the new record; fail the old record's retire node. */
    CHECK(pcc_gc_index_py_rehash_slots(slots, cap, used, *cap * 2) == 0, 35);
    CHECK(*published && *published != old_record, 36);
    for (int n = 1; n <= 130; n++) CHECK(find(kind, n) == node_for(n), 37);
    CHECK(pcc_gc_index_reclaim_retired() == 1, 38);

    fail_after = 0; /* Clearing needs no reader record; retirement may leak. */
    if (kind == 0) pcc_gc_forwarding_index_clear();
    else pcc_gc_forwarding_target_index_clear();
    CHECK(!*slots && !*published && !*cap && !*count && !*used, 39);
    CHECK(pcc_gc_index_reclaim_retired() == 1, 40);
    return 0;
}

int main(void) {
    int result = check_kind(0);
    if (result) return result;
    result = check_kind(1);
    if (result) return result;
    fail_after = 0;
    CHECK(pcc_gc_object_index_insert(key_for(1), node_for(1)) == 1, 50);
    CHECK(!pcc_py_gc_object_published, 51);
    CHECK(pcc_gc_object_index_find(key_for(1)) == node_for(1), 52);
    fail_after = 0;
    CHECK(pcc_gc_managed_pointer_index_insert(key_for(2)) == 1, 53);
    CHECK(!pcc_py_gc_managed_pointer_published, 54);
    CHECK(pcc_gc_managed_pointer_index_contains(key_for(2)) == 1, 55);
    CHECK(pcc_gc_managed_pointer_index_seen(key_for(2)) == 0, 56);
    CHECK(failures == 10, 57);
    puts("forwarding-publication-oom-ok");
    return 0;
}
"""


def test_owned_native_forwarding_publication_oom(tmp_path):
    import subprocess
    from pathlib import Path
    from unittest.mock import patch

    from pcc import build
    from tests.python.test_gc_index_unlocked_readers import (
        INDEX_DEPENDENCIES,
        REPO_ROOT,
        _index_object,
    )

    harness = tmp_path / 'forwarding_publication_oom.c'
    harness.write_text(NATIVE_HARNESS, encoding='utf-8')
    with (
        patch('subprocess.Popen', side_effect=AssertionError('external build process')),
        patch('pcc.frontends.python.owned_runtime_build.ensure_target_runtime',
              side_effect=AssertionError('standalone index linked the full runtime')),
    ):
        obj = _index_object(tmp_path)
        artifact = build(
            [harness, INDEX_DEPENDENCIES], backend='self', kind='exe', optimize=1,
            include_dirs=[REPO_ROOT / 'utils/fake_libc_include'],
            link_args=[str(obj)], out_dir=tmp_path / 'owned', use_compile_cache=False,
        )
    run = subprocess.run([str(Path(artifact.output_path))], capture_output=True,
                         text=True, timeout=30)
    assert run.returncode == 0, f'{run.returncode}: {run.stdout} {run.stderr}'
    assert run.stdout == 'forwarding-publication-oom-ok\n'
