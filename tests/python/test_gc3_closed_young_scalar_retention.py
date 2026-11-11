"""Cold GC3 scalar retention executes the production promotion bodies.

Contract: docs/project-intent.md shared roots, identity and ordinary lifetime.
The existing finite index/memory scaffold supplies storage, not a replacement
promotion algorithm. Membership, forwarding lookup, pin checks, young unlink,
oldification and promotion queueing are loaded with runtime_body_model. Counts
prove only this modeled call shape, not native timing, concurrency or GC gates.
"""
from collections import Counter
from itertools import product

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import gc_state_initial_values, load_functions
from tests.python.test_gc_stable_object_membership import Index, ROOT


YOUNG, OLD, REMEMBERED, FORWARDED, NURSERY = 128, 256, 512, 2048, 4096
RETAIN = 'pcc_gc_generational_retain_closed_young'
PROMOTE = 'pcc_gc_generational_promote_young_if_known'
SCALARS = (
    abi.PY_TYPE_INT, abi.PY_TYPE_FLOAT, abi.PY_TYPE_STR,
    abi.PY_TYPE_BYTES, abi.PY_TYPE_COMPLEX,
)
OBSERVED = {
    'index': 'pcc_gc_object_index_find',
    'active': 'pcc_gc_object_node_is_active',
    'known': 'pcc_gc_object_is_known_no_lock',
    'forwarding': 'pcc_gc_forwarding_find',
    'generic': PROMOTE,
    'copy': 'pcc_gc_generational_oldify_copy',
    'pin': 'pcc_gc_generational_object_is_address_pinned',
    'address_pin': 'pcc_gc_object_is_address_pinned',
    'unlink': 'pcc_gc_backend3_young_unlink',
    'trace': 'pcc_gc_trace_referents_for_promotion',
    'enqueue': '_enqueue_promotion_owner',
    'refresh': 'pcc_gc_memoryview_refresh_owned_buffer',
}


class Retention(Index):
    def __init__(self, *, backend=3, cut=1, suspended=1):
        self.forbidden_headers = set()
        self.objects = set()
        self.header_reads = []
        self.counts = Counter()
        self.calls = []
        self.forwardings = {}
        super().__init__()
        for name, value in gc_state_initial_values().items():
            self.words.setdefault(name, value)
        for name, value in {
            'pcc_gc_backend_selected': backend,
            'pcc_gc_config_initialized': 1,
            'pcc_gc_backend3_young_cut_active': cut,
            'pcc_gc_backend3_oldify_suspended': suspended,
            'pcc_gc_backend4_no_refcounts': 1,
        }.items():
            self.store(name, 0, value)
        self.ns.update(vars(abi))
        self.ns.update({
            'abi_constant': ABI_CONSTANTS.__getitem__,
            'PYOBJECTHEADER_FLAGS_OFFSET': 12,
            'store_i32': self.store,
            'atomic_load_i32': self.load,
            'atomic_store_i32': self.store,
            'atomic_rmw_i32': self.atomic_flags,
            'cstr': lambda text: text,
            'pcc_gc_config_ensure': lambda: self.load('pcc_gc_backend_selected'),
            'pcc_gc_forwarding_index_find': lambda obj: self.forwardings.get(obj, 0),
            # No static literals or conservative snapshot are installed.
            'pcc_gc_static_literal_contains': lambda obj: 0,
            'pcc_gc_forwarding_target_index_find': lambda obj: 0,
            'pcc_gc_load_ptr': lambda owner, slot: self.load(slot),
            'pcc_py_gc_defer_tripwire': self.tripwire,
            'pcc_gc_backend4_zpage_note_owner_promoted': self.note_gc4_owner,
            'malloc': self.unexpected_allocation,
            'memmove': self.unexpected_copy,
            'py_incref': self.unexpected_ownership,
            'py_decref': self.unexpected_ownership,
            'pcc_cpy_handle_move_owned_ref': self.unexpected_ownership,
        })
        for filename, names in (
            ('freestanding_gc_refcount_roots.py', {
                'pcc_gc_object_node_is_active',
            }),
            ('freestanding_gc_object_nodes.py', {
                'pcc_gc_backend3_young_link_head',
                'pcc_gc_backend3_young_unlink',
            }),
            ('freestanding_gc_root_operations.py', {
                'pcc_gc_object_is_known_no_lock',
                'pcc_gc_object_is_address_pinned',
            }),
            ('freestanding_gc_forwarding_identity.py', {
                'pcc_gc_forwarding_find',
            }),
            ('freestanding_gc_conservative_roots.py', {
                'pcc_gc_generational_object_is_address_pinned',
            }),
            ('freestanding_gc_object_slots.py', {
                'pcc_gc_memoryview_refresh_owned_buffer',
            }),
            ('freestanding_gc_generational_oldification.py', {
                'pcc_gc_generational_oldify_copy',
            }),
            ('freestanding_gc_generational_promotion.py', {RETAIN, PROMOTE}),
        ):
            load_functions(ROOT / filename, names, self.ns)
        for label, name in OBSERVED.items():
            actual = self.ns[name]

            def observe(*args, label=label, actual=actual):
                self.counts[label] += 1
                self.calls.append((label, args))
                return actual(*args)

            self.ns[name] = observe

    def load(self, base, offset=0, *order):
        assert base not in self.forbidden_headers, 'unsafe object dereference'
        if base in self.objects and offset in (8, 12):
            self.header_reads.append((base, offset))
        return super().load(base, offset, *order)

    def atomic_flags(self, operation, obj, offset, value, order):
        before = self.load(obj, offset)
        if operation == 'add':
            after = before + value
        elif operation == 'and':
            after = before & value
        elif operation == 'or':
            after = before | value
        else:
            raise AssertionError(operation)
        self.store(obj, offset, after)
        return before

    def tripwire(self, message, source, line):
        self.counts['tripwire'] += 1
        raise AssertionError(message)

    def note_gc4_owner(self, obj):
        self.counts['gc4_owner'] += 1

    def unexpected_allocation(self, size):
        self.counts['allocation'] += 1
        pytest.fail('retention attempted allocation')

    def unexpected_copy(self, *args):
        self.counts['payload_copy'] += 1
        pytest.fail('retention attempted payload copy')

    def unexpected_ownership(self, *args):
        self.counts['ownership'] += 1
        pytest.fail('retention changed reference ownership')

    def value(self, tag=abi.PY_TYPE_FLOAT, *, flags=8 | YOUNG, linked=True):
        obj, node = self.calloc(1, 64), self.calloc(1, 88)
        self.objects.add(obj)
        self.store(obj, 0, 37)
        self.store(obj, 8, tag)
        self.store(obj, 12, flags)
        self.store(obj, 16, 0x12345678)
        self.store(obj, 24, 0)
        self.store(node, 0, obj)
        self.store(node, 8, 64)
        self.call('pcc_gc_object_index_insert', obj, node)
        if linked:
            self.call('pcc_gc_backend3_young_link_head', node)
        return obj, node

    def run(self, obj, *, entry=RETAIN):
        self.counts.clear()
        self.calls.clear()
        self.header_reads.clear()
        self.call(entry, obj)

    def assert_no_extra_work(self):
        for label in (
            'generic', 'known', 'copy', 'pin', 'address_pin', 'trace',
            'enqueue', 'refresh', 'allocation', 'payload_copy', 'ownership',
        ):
            assert self.counts[label] == 0, (label, self.calls)


@pytest.mark.parametrize('tag', SCALARS)
@pytest.mark.parametrize('nursery', (False, True))
def test_exact_scalar_is_retained_with_one_index_lookup_and_no_queue(tag, nursery):
    m = Retention()
    flags = YOUNG | 8 | REMEMBERED | (NURSERY if nursery else 262144)
    obj, node = m.value(tag, flags=flags)
    before = dict(m.words)
    m.run(obj)
    expected = (flags & ~(YOUNG | (REMEMBERED if nursery else 0))) | OLD
    assert m.load(obj, 12) == expected
    assert m.load('pcc_gc_backend3_young_head') == 0
    assert m.load(node, 64) == m.load(node, 72) == 0
    assert m.load('pcc_gc_backend3_promotion_head') == 0
    assert m.counts['index'] == m.counts['active'] == 1
    assert m.counts['forwarding'] == m.counts['unlink'] == 1
    m.assert_no_extra_work()
    before[obj + 12] = expected
    before['pcc_gc_backend3_young_head'] = 0
    assert m.words == before  # Includes refcount, payload, node and all globals.


@pytest.mark.parametrize('color', (8, 16, 32))
@pytest.mark.parametrize('owner', ('none', 'header-pin', 'node-pin', 'lease', 'immortal'))
@pytest.mark.parametrize('count_free', (0, 1))
def test_scalar_preserves_major_color_and_independent_owners(color, owner, count_free):
    m = Retention()
    m.store('pcc_gc_backend4_no_refcounts', 0, count_free)
    flags = YOUNG | color | REMEMBERED | 1024 | 8192 | 65536 | (1 << 28)
    if owner == 'header-pin':
        flags |= abi.PY_FLAG_GC_PINNED
    if owner == 'immortal':
        flags |= abi.PY_FLAG_IMMORTAL
    obj, node = m.value(flags=flags)
    cursor_word = 23 if owner == 'node-pin' else 22
    lease_count = 7 if owner == 'lease' else 0
    m.store(node, 56, cursor_word)
    m.store(node, 80, lease_count)
    m.store('pcc_gc_gray_count', 0, 19)
    m.run(obj)
    assert m.load(obj, 12) == (flags & ~YOUNG) | OLD
    assert m.load(obj, 0) == 37
    assert m.load(obj, 16) == 0x12345678
    assert m.load(node, 56) == cursor_word
    assert m.load(node, 80) == lease_count
    assert m.load('pcc_gc_gray_count') == 19
    assert m.counts['index'] == 1
    m.assert_no_extra_work()


@pytest.mark.parametrize('position', (0, 1, 2))
def test_retained_scalar_unlinks_head_middle_and_tail(position):
    m = Retention()
    values = [m.value() for _ in range(3)]
    obj, node = values[position]
    m.run(obj)
    expected_nodes = [entry[1] for index, entry in reversed(list(enumerate(values)))
                      if index != position]
    assert m.load('pcc_gc_backend3_young_head') == expected_nodes[0]
    for index, remaining in enumerate(expected_nodes):
        assert m.load(remaining, 72) == (expected_nodes[index - 1] if index else 0)
        assert m.load(remaining, 64) == (expected_nodes[index + 1] if index + 1 < len(expected_nodes) else 0)
    assert m.load(node, 64) == m.load(node, 72) == 0
    assert m.counts['index'] == 1
    m.assert_no_extra_work()


@pytest.mark.parametrize('kind', (
    'null', 'tagged', 'unknown', 'retired', 'pending-retirement', 'null-node-object',
    'tagged-node-object',
))
def test_invalid_or_inactive_pointer_never_dereferences_its_header(kind):
    m = Retention()
    if kind in ('null', 'tagged', 'unknown'):
        obj = {'null': 0, 'tagged': 3, 'unknown': 0xE00000}[kind]
    else:
        obj, node = m.value()
        if kind in ('retired', 'pending-retirement'):
            m.store(node, 32, 1 if kind == 'retired' else -7)
        else:
            m.store(node, 0, 0 if kind == 'null-node-object' else 3)
    m.forbidden_headers.update((obj, 0, 3))
    before = dict(m.words)
    m.run(obj)
    assert m.words == before
    assert not m.header_reads
    assert m.counts['index'] == int(kind not in ('null', 'tagged'))
    assert m.counts['unlink'] == m.counts['forwarding'] == 0
    m.assert_no_extra_work()


@pytest.mark.parametrize('flags', (0, OLD, YOUNG | abi.PY_FLAG_GC_DEALLOCATING))
def test_nonyoung_and_deallocating_objects_keep_flags_and_membership(flags):
    m = Retention()
    obj, _ = m.value(flags=flags)
    before = dict(m.words)
    m.run(obj)
    assert m.words == before
    assert m.counts['index'] == 1
    assert all(offset == 12 for _, offset in m.header_reads)
    assert m.counts['unlink'] == m.counts['forwarding'] == 0
    m.assert_no_extra_work()


@pytest.mark.parametrize('tag', (
    abi.PY_TYPE_LIST, abi.PY_TYPE_BYTEARRAY, abi.PY_TYPE_MEMORYVIEW,
    abi.PY_TYPE_CPY_HANDLE, abi.PY_TYPE_BOOL, 9000,
))
def test_nonexact_types_execute_generic_promotion_and_real_owner_queue(tag):
    m = Retention()
    obj, node = m.value(tag)
    m.run(obj)
    assert m.load(obj, 12) == 8 | OLD
    assert m.load('pcc_gc_backend3_young_head') == 0
    assert m.load('pcc_gc_backend3_promotion_head') == node
    assert m.load('pcc_gc_backend3_promotion_tail') == node
    assert m.counts['generic'] == m.counts['copy'] == 1
    assert m.counts['pin'] == m.counts['address_pin'] == 1
    assert m.counts['trace'] == m.counts['enqueue'] == 1
    assert m.counts['refresh'] == int(tag == abi.PY_TYPE_MEMORYVIEW)
    assert m.counts['ownership'] == m.counts['allocation'] == 0
    assert m.load(obj, 0) == 37


def test_memoryview_fallback_refreshes_real_buffer_aliases():
    m = Retention()
    base, _ = m.value(abi.PY_TYPE_BYTES, flags=OLD | 8, linked=False)
    m.store(base, ABI_CONSTANTS['object.bytes.byte_len_offset'], 6)
    obj, node = m.value(abi.PY_TYPE_MEMORYVIEW)
    buffer_view = m.calloc(1, 96)
    m.store(obj, 16, base)
    m.store(obj, 24, buffer_view)
    m.store(buffer_view, 72, 0x1234)
    m.run(obj)
    assert m.load('pcc_gc_backend3_promotion_head') == node
    assert m.load(buffer_view, 0) == base + ABI_CONSTANTS['object.bytes.data_offset']
    assert m.load(buffer_view, 8) == base
    assert m.load(buffer_view, 16) == 6
    assert m.load(buffer_view, 32) == 1
    assert m.load(buffer_view, 72) == 0x1234
    assert m.counts['generic'] == m.counts['refresh'] == 1
    assert m.counts['ownership'] == 0


@pytest.mark.parametrize('hint', (0, FORWARDED))
def test_forwarding_record_falls_back_and_queues_target_without_promoting_source(hint):
    m = Retention()
    source, source_node = m.value(flags=8 | YOUNG | hint)
    target, target_node = m.value(flags=32 | OLD, linked=False)
    edge = m.calloc(1, 56)
    m.store(edge, 0, source)
    m.store(edge, 8, target)
    m.forwardings[source] = edge
    before = dict(m.words)
    m.run(source)
    assert m.load(source, 12) == before[source + 12]
    assert m.load('pcc_gc_backend3_young_head') == source_node
    assert m.load('pcc_gc_backend3_promotion_head') == target_node
    assert m.load(target, 12) == 32 | OLD
    assert m.load(source, 0) == m.load(target, 0) == 37
    assert m.forwardings == {source: edge}
    assert m.counts['generic'] == m.counts['copy'] == m.counts['enqueue'] == 1
    assert m.counts['unlink'] == m.counts['pin'] == m.counts['ownership'] == 0


@pytest.mark.parametrize('kind', ('stale-hint', 'empty-record'))
def test_uncertain_forwarding_state_keeps_generic_inplace_semantics(kind):
    m = Retention()
    flags = 8 | YOUNG | (FORWARDED if kind == 'stale-hint' else 0)
    obj, node = m.value(flags=flags)
    if kind == 'empty-record':
        m.forwardings[obj] = m.calloc(1, 56)
    m.run(obj)
    assert m.load(obj, 12) == (flags & ~YOUNG) | OLD
    assert m.load('pcc_gc_backend3_promotion_head') == node
    assert m.counts['generic'] == m.counts['copy'] == m.counts['enqueue'] == 1
    assert m.counts['ownership'] == 0


INACTIVE_CONTEXTS = tuple(
    (backend, cut, suspended)
    for backend, cut, suspended in product(range(5), (0, 1), (0, 1))
    if (backend, cut, suspended) != (3, 1, 1)
)


@pytest.mark.parametrize('backend,cut,suspended', INACTIVE_CONTEXTS)
def test_every_inactive_context_delegates_to_unchanged_generic_body(backend, cut, suspended):
    m = Retention(backend=backend, cut=cut, suspended=suspended)
    control = Retention(backend=backend, cut=cut, suspended=suspended)
    # A real header pin makes the unsuspended control deterministic without
    # replacing oldification or its pin test with a successful result stub.
    flags = 8 | YOUNG | abi.PY_FLAG_GC_PINNED
    obj, _ = m.value(flags=flags)
    control_obj, _ = control.value(flags=flags)
    m.run(obj)
    control.run(control_obj, entry=PROMOTE)
    assert m.words == control.words
    assert m.calls == control.calls
    assert m.counts['generic'] == m.counts['copy'] == 1
    assert m.counts['enqueue'] == int(backend == 3)
    assert m.counts['gc4_owner'] == int(backend == 4)
    assert m.counts['ownership'] == 0


def test_invalid_young_old_state_preserves_existing_tripwire():
    m = Retention()
    obj, _ = m.value(flags=8 | YOUNG | OLD)
    before = dict(m.words)
    with pytest.raises(AssertionError, match='YOUNG object already marked OLD'):
        m.run(obj)
    assert m.words == before
    assert m.counts['generic'] == m.counts['copy'] == m.counts['tripwire'] == 1
    assert m.counts['unlink'] == m.counts['enqueue'] == m.counts['ownership'] == 0


def test_invalid_nursery_generation_uses_existing_generic_branch():
    m, control = Retention(), Retention()
    flags = 8 | YOUNG | OLD | NURSERY | REMEMBERED
    obj, _ = m.value(flags=flags)
    control_obj, _ = control.value(flags=flags)
    m.run(obj)
    control.run(control_obj, entry=PROMOTE)
    assert m.words == control.words
    assert m.counts['generic'] == m.counts['copy'] == m.counts['enqueue'] == 1
    assert m.counts['tripwire'] == 0


@pytest.mark.parametrize('borrowed,resolve', product((0, 1), repeat=2))
def test_mode7_uses_current_scalar_without_cache_or_ownership_work(borrowed, resolve):
    m = Retention()
    load_functions(ROOT / 'freestanding_gc_mapped_roots.py', {
        'pcc_gc_visit_mapped_root_slot',
    }, m.ns)
    obj, _ = m.value()
    slot, cache = m.calloc(1, 16), m.calloc(1, 16)
    m.store(slot, 8, obj)
    m.store(cache, 8, obj)  # Equal stale cache must not skip this liveness cut.
    m.counts.clear()
    m.calls.clear()
    assert m.call('pcc_gc_visit_mapped_root_slot', slot, 8, cache,
                  borrowed, 7, resolve) == 0
    assert m.load(obj, 12) == 8 | OLD
    assert m.load(slot, 8) == m.load(cache, 8) == obj
    assert m.counts['index'] == 1
    assert m.counts['unlink'] == 1
    m.assert_no_extra_work()
