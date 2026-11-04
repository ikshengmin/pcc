"""Actual object-index bodies under backward-shift/publication interleavings.

The memory primitives inject another graph-serialized writer between reader
loads. No lookup result or generation completion is stubbed. Contract:
docs/project-intent.md shared GC roots and ordinary object lifetime.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


def load_bodies(namespace, path, names=None):
    tree = ast.parse(path.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (names is None or node.name in names):
            node.decorator_list = []
            selected.append(node)
    exec(compile(ast.Module(selected, type_ignores=[]), str(path), "exec"), namespace)


class Index:
    def __init__(self, *, publication_oom=False):
        self.words = {}
        self.blocks = {}
        self.next = 0x100000
        self.hook = None
        self.in_hook = False
        self.fences = []
        self.fail_size = 16 if publication_oom else 0
        self.reads = []
        self.generation_stores = []
        self.ns = {
            'i64': int, 'c_ptr': int, 'null': lambda: 0,
            'ptr_is_null': lambda value: int(value == 0),
            'is_tagged_int': lambda value: value & 1,
            'ptr_eq': lambda left, right: int(left == right),
            'ptr_diff': lambda left, right: left - right,
            'ptr_add': lambda base, offset: base + offset,
            'int_to_ptr': lambda value: value,
            'logical_shift_right_i64': lambda value, shift: (value & ((1 << 64) - 1)) >> shift,
            'wrapping_mul_i64': lambda left, right: ((left * right + (1 << 63)) % (1 << 64)) - (1 << 63),
            'global_addr': lambda name: name,
            'global_load_ptr': lambda name: self.load(name),
            'global_store_ptr': lambda name, value: self.store(name, 0, value),
            'load_ptr': self.load, 'load_i64': self.load, 'load_i32': self.load,
            'store_ptr': self.store, 'store_i64': self.store,
            'atomic_load_i64': self.load, 'atomic_store_i64': self.store,
            'atomic_fence': self.fences.append,
            'calloc': self.calloc, 'free': lambda value: None,
            'pcc_platform_abort': lambda: (_ for _ in ()).throw(RuntimeError('abort')),
        }
        load_bodies(self.ns, ROOT / 'freestanding_gc_index_table.py')
        slots = self.calloc(8, 16)
        self.call('_index_table_publish', 'pcc_py_gc_object_slots', 'pcc_py_gc_object_cap', slots, 8)
        self.fail_size = 0
        self.slots = slots
        bins = {}
        for key in range(0x1000, 0x10000, 16):
            home = self.call('pcc_gc_index_py_hash_ptr', key) & 7
            bins.setdefault(home, []).append(key)
        self.removed, self.kept = bins[0][:2]
        self.call('pcc_gc_object_index_insert', self.removed, 0xA000)
        self.call('pcc_gc_object_index_insert', self.kept, 0xB000)
        self.reads.clear()

    def call(self, name, *args):
        return self.ns[name](*args)

    def generation(self):
        return self.words.get('pcc_gc_object_index_generation', 0)

    def calloc(self, count, size):
        size *= count
        if size == self.fail_size:
            self.fail_size = 0
            return 0
        base = self.next
        self.next += size + 32
        self.blocks[base] = size
        return base

    def load(self, base, offset=0, *order):
        if self.hook is not None and not self.in_hook:
            self.in_hook = True
            try:
                self.hook(base, offset, order)
            finally:
                self.in_hook = False
        self.reads.append((base, offset, order))
        if isinstance(base, int) and base in self.blocks:
            assert 0 <= offset < self.blocks[base], ('out-of-bounds', base, offset, self.blocks[base])
        key = base if isinstance(base, str) and offset == 0 else base + offset
        return self.words.get(key, 0)

    def store(self, base, offset, value, *order):
        key = base if isinstance(base, str) and offset == 0 else base + offset
        self.words[key] = value
        if base == 'pcc_gc_object_index_generation':
            self.generation_stores.append((value, order))

    def shift_before_second_key(self):
        def shift(base, offset, order):
            if base == self.slots and offset == 16:
                self.hook = None
                assert self.call('pcc_gc_object_index_remove', self.removed) == 0xA000
        self.hook = shift


def test_original_node_lookup_can_pair_the_found_key_with_an_empty_shifted_slot():
    model = Index()
    def shift_before_value(base, offset, order):
        if base == model.slots and offset == 24:
            model.hook = None
            model.call('pcc_gc_object_index_remove', model.removed)
    model.hook = shift_before_value
    assert model.call('pcc_gc_object_index_find', model.kept) == 0
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1


def test_positive_only_probe_reproduces_miss_when_removal_moves_key_behind_reader():
    model = Index()
    model.shift_before_second_key()
    assert model.call('pcc_gc_object_index_seen', model.kept) == 0
    assert model.call('pcc_gc_object_index_seen', model.kept) == 1


def test_stable_probe_recovers_the_same_backward_shift_miss():
    model = Index()
    before = model.generation()
    model.shift_before_second_key()
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1
    assert model.generation() == before + 2
    assert not any(base in (0xA000, 0xB000) for base, _, _ in model.reads)


def test_miss_during_validated_fallback_probe_retries_again():
    model = Index(publication_oom=True)
    assert model.load('pcc_py_gc_object_published') == 0
    before = model.generation()
    model.shift_before_second_key()
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1
    assert model.generation() == before + 2
    assert model.fences.count('acquire') >= 4


def test_publication_record_oom_never_turns_a_live_key_into_absence():
    model = Index(publication_oom=True)
    assert model.call('pcc_gc_object_index_seen', model.kept) == 0
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1
    assert model.call('pcc_gc_object_index_seen_stable', 0xF0000) == 0


def test_mixed_small_table_large_capacity_is_rejected_before_any_probe():
    model = Index(publication_oom=True)
    old_slots = model.slots
    def grow_before_capacity(base, offset, order):
        if base == 'pcc_py_gc_object_cap' and order:
            model.hook = None
            model.call('pcc_gc_index_py_rehash_slots', 'pcc_py_gc_object_slots',
                       'pcc_py_gc_object_cap', 'pcc_py_gc_object_used', 64)
    model.hook = grow_before_capacity
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1
    assert model.load('pcc_py_gc_object_cap') == 64
    assert model.load('pcc_py_gc_object_slots') != old_slots
    # The first optimistic slots load may see the old table, but the pair's
    # generation changed before capacity was read. It cannot be dereferenced.
    assert not any(base == old_slots and order for base, _, order in model.reads)


def test_clear_then_insert_is_detected_before_an_absent_result():
    model = Index(publication_oom=True)
    wanted = 0x40000
    def replace_after_pair(base, offset, order):
        if base == model.slots and order:
            model.hook = None
            model.call('pcc_gc_object_index_clear')
            model.call('pcc_gc_object_index_insert', wanted, 0xC000)
    model.hook = replace_after_pair
    assert model.call('pcc_gc_object_index_seen_stable', wanted) == 1


@pytest.mark.parametrize('kind', ['insert', 'upsert', 'replace', 'remove', 'clear', 'rehash', 'plan', 'preallocated'])
def test_every_object_mutation_balances_one_outer_generation(kind):
    model = Index()
    before = model.generation()
    cells = ('pcc_py_gc_object_slots', 'pcc_py_gc_object_cap',
             'pcc_py_gc_object_count', 'pcc_py_gc_object_used')
    if kind == 'insert':
        model.call('pcc_gc_index_py_insert', *cells, 0x50000, 0xC000, 1, 1, 8)
    elif kind == 'upsert':
        model.call('pcc_gc_index_py_upsert', *cells, model.kept, 0xC000, 1, 8)
    elif kind == 'replace':
        model.call('pcc_gc_index_py_replace_raw', *cells, model.kept, 0xC000, 8)
    elif kind == 'remove':
        model.call('pcc_gc_object_index_remove', model.removed)
    elif kind == 'clear':
        model.call('pcc_gc_object_index_clear')
    elif kind == 'rehash':
        model.call('pcc_gc_index_py_rehash_slots', cells[0], cells[1], cells[3], 32)
    elif kind == 'plan':
        io = model.calloc(1, 8)
        replacement = model.calloc(16384, 16)
        model.store(io, 0, replacement)
        assert model.call('pcc_gc_object_index_plan_commit', io, 16384, 8) == 1
    else:
        assert model.call('pcc_gc_object_index_insert_preallocated', 0x50000, 0xC000) == 1
    assert model.generation() == before + 2
    assert model.generation() & 1 == 0


@pytest.mark.parametrize('kind', ['bad_insert', 'bad_upsert', 'bad_replace', 'bad_remove', 'bad_plan', 'bad_preallocated', 'rehash_oom'])
def test_failure_returns_never_leave_a_writer_open(kind):
    model = Index()
    cells = ('pcc_py_gc_object_slots', 'pcc_py_gc_object_cap',
             'pcc_py_gc_object_count', 'pcc_py_gc_object_used')
    if kind == 'bad_insert':
        assert model.call('pcc_gc_index_py_insert', *cells, 0, 0xC000, 1, 1, 8) == -1
    elif kind == 'bad_upsert':
        assert model.call('pcc_gc_index_py_upsert', *cells, 0, 0xC000, 1, 8) == -1
    elif kind == 'bad_replace':
        assert model.call('pcc_gc_index_py_replace_raw', *cells, 0, 0xC000, 8) == 0xC000
    elif kind == 'bad_remove':
        assert model.call('pcc_gc_object_index_remove', 0) == 0
    elif kind == 'bad_plan':
        assert model.call('pcc_gc_object_index_plan_commit', 0, 0, 1) == -1
    elif kind == 'bad_preallocated':
        assert model.call('pcc_gc_object_index_insert_preallocated', 0, 0xC000) == -1
    else:
        model.fail_size = 32 * 16
        assert model.call('pcc_gc_index_py_rehash_slots', cells[0], cells[1], cells[3], 32) == -1
    assert model.generation() & 1 == 0


def test_other_index_mutation_leaves_object_generation_unchanged():
    model = Index()
    before = model.generation()
    model.call('pcc_gc_index_py_insert', 'other_slots', 'other_cap', 'other_count',
               'other_used', 0x50000, 0xC000, 1, 1, 8)
    assert model.generation() == before


def test_positive_probe_does_not_read_generation_or_nodes():
    model = Index()
    model.reads.clear()
    assert model.call('pcc_gc_object_index_seen_stable', model.kept) == 1
    assert not any(base == 'pcc_gc_object_index_generation' or base in (0xA000, 0xB000)
                   for base, _, _ in model.reads)


def test_generation_exhaustion_refuses_writes_before_mutation():
    model = Index()
    model.store('pcc_gc_object_index_generation', 0, 9223372036854775806)
    before = dict(model.words)
    with pytest.raises(RuntimeError, match='abort'):
        model.call('pcc_gc_object_index_remove', model.removed)
    assert model.words == before
