"""Forwarding publication is part of the table's fallible commit, not a hint.

Contract: docs/project-intent.md requires shared GC roots, ordinary object
lifetimes and recoverable allocation failure. These models run the production
index and installer bodies, with failure only in raw calloc. Memory, graph
exclusion and counted-reference primitives are modeled, not native collectors.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_forwarding_insert_ownership import Install


class Publication(Install):
    def __init__(self, backend=4):
        self.record_failure_after = None
        self.record_failures = 0
        self.record_attempts = 0
        self.calloc_events = []
        super().__init__(backend)
        # Index starts with two objects in cap8; Install adds four and grows
        # that table once. Start this component's retirement checks clean.
        assert self.call('pcc_gc_index_reclaim_retired') == 2

    def allocate_zeroed(self, count, size):
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
def test_plan_record_oom_leaves_zero_buffer_for_safe_same_plan_retry(kind_id, kind, growing):
    model = Publication()
    entries = model.fill_small(kind) if growing else []
    cap = model.call('pcc_gc_forwarding_plan_index_capacity', kind_id, 1)
    prepared = model.calloc(cap, 16)
    io = model.calloc(1, 8)
    model.store(io, 0, prepared)
    before = model.state(kind)
    freed = list(model.freed)
    model.record_failure_after = 0

    assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io, cap, 1) == -1
    assert model.record_failures == 1
    assert model.load(io) == prepared
    assert model.state(kind) == before and model.freed == freed
    assert all(model.load(prepared, offset) == 0 for offset in range(0, cap * 16, 8))

    assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io, cap, 1) == 1
    assert model.load(io) == 0
    assert model.load(model.cells(kind)[0]) == prepared
    assert model.load('pcc_py_gc_' + kind + '_published') != 0
    for key, node in entries:
        assert model.find(kind, key) == node
    assert model.call('pcc_gc_forwarding_plan_index_insert', kind_id, 0xA0000, 0xB0000) == 1
    assert model.find(kind, 0xA0000) == 0xB0000
    assert prepared not in model.freed
    assert model.call('pcc_gc_index_reclaim_retired') == (2 if growing else 0)


@pytest.mark.parametrize('kind_id,kind', ((0, 'forwarding'), (1, 'forwarding_target')))
def test_warm_insert_and_noop_plan_do_not_request_a_record(kind_id, kind):
    model = Publication()
    assert model.raw_insert(kind, 0x80000, 0x90000) == 1
    io = model.calloc(1, 8)
    unused = model.calloc(8, 16)
    model.store(io, 0, unused)
    attempts = model.record_attempts
    model.record_failure_after = 0
    assert model.raw_insert(kind, 0x80010, 0x90010) == 1
    assert model.call('pcc_gc_forwarding_plan_index_commit', kind_id, io, 8, 1) == 0
    assert model.load(io) == unused and unused not in model.freed
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


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('failed_kind', (0, 1))
def test_preallocated_installer_can_retry_after_record_oom(backend, failed_kind):
    model = Publication(backend)
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan and model.depth == 0
    before = model.edge_state()
    model.record_failure_after = failed_kind
    model.lock()
    try:
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == -1
    finally:
        model.unlock()
    assert model.record_failures == 1 and model.edge_state() == before
    # The first successful capacity commits may remain and consume their IO;
    # retry must accept those no-op capacities and the still-zero failed IO.
    model.lock()
    try:
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == 0
    finally:
        model.unlock()
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == 1
    for kind, key in (('forwarding', model.a), ('forwarding_target', model.t)):
        assert model.find(kind, key) != 0
        assert model.load(model.cells(kind)[0]) not in model.freed
        assert model.load('pcc_py_gc_' + kind + '_published') not in model.freed
    assert model.load('pcc_gc_forwarding_population') == 1
    assert model.call('pcc_gc_note_relocation_read', model.a) == model.t


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('failed_kind', (0, 1))
def test_failed_preallocated_install_can_finish_without_retry(backend, failed_kind):
    model = Publication(backend)
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan and model.depth == 0
    before = model.edge_state()
    model.record_failure_after = failed_kind
    model.lock()
    try:
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == -1
    finally:
        model.unlock()
    assert model.record_failures == 1 and model.edge_state() == before
    owned = [model.load(plan, offset) for offset in (0, 8, 16, 24, 40, 56)]
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

/* Standalone raw allocator fault only. Lookups, table commits and retirement
   use the emitted production index object. This is not a collector harness. */
void *calloc(size_t count, size_t size) {
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
int64_t pcc_gc_forwarding_plan_index_commit(int64_t kind, void **io, int64_t cap, int64_t extra);
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
    fail_after = 0;
    CHECK(pcc_gc_forwarding_plan_index_commit(kind, &io, needed, extra) == -1, 24);
    CHECK(io == prepared && *slots == old_slots && *published == old_record, 25);
    CHECK(*cap == 512 && *count == 129 && *used == 129, 26);
    for (int64_t n = 0; n < needed * 2; n++) CHECK(((uintptr_t *)prepared)[n] == 0, 27);
    CHECK(pcc_gc_forwarding_plan_index_commit(kind, &io, needed, extra) == 1, 28);
    CHECK(!io && *slots == prepared && *cap == needed && *published, 29);
    for (int n = 1; n <= 129; n++) CHECK(find(kind, n) == node_for(n), 30);
    CHECK(pcc_gc_index_reclaim_retired() == 2, 31);

    prepared = calloc(8, 16);
    io = prepared;
    fail_after = 0;
    CHECK(pcc_gc_forwarding_plan_index_commit(kind, &io, 8, 1) == 0, 32);
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
    CHECK(failures == 12, 57);
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
