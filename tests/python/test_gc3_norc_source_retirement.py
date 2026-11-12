"""GC3 copy/retirement bodies: count-independent bytes, aliases and nursery.

Actual oldification, shared payload planning/slots, forwarding transactions,
conservative scan, remap, root rewrite, typed release and MinorBlock release
run over the existing finite retirement memory/index model. No READY/remap
success stub supplies the collector boundary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_gc_forwarding_gray_retirement_model import RetirementState


PY = Path(__file__).resolve().parents[2] / 'pcc/runtime/py'


@pytest.mark.parametrize('case, expected', [
    ('not-owner', -1), ('disabled', 0), ('missing-record', 0), ('refresh', 1),
])
def test_actual_owner_snapshot_admission_preserves_finite_roots(case, expected):
    state = GC3()
    state.words = [0x1000, 0x2000]
    if case == 'not-owner':
        state.stopped = False
    elif case == 'disabled':
        state.store('pcc_thread_conservative_roots_enabled', 0, 0)
    elif case == 'missing-record':
        state.store('pcc_tls_thread_record_py', 0, 0)
    assert state.ns['pcc_thread_snapshot_stopped_world_owner']() == expected
    assert state.owner_snapshot_refreshes == int(case == 'refresh')
    assert state.words == [0x1000, 0x2000]
    assert not state.owner_snapshot_locked


class GC3(RetirementState):
    def __init__(self, counted=False):
        self.raw = {}
        self.freed = set()
        self.managed = set()
        self.identities = {}
        self.fail_size = 0
        self.fail_node = False
        self.fail_index = False
        self.fail_forward = False
        self.stopped = True
        self.gated = True
        self.words = []
        self.module_roots = []
        self.root_cells = []
        self.owner_snapshot_refreshes = 0
        self.owner_snapshot_locked = False
        self.foreign_releases = []
        self.cext_hook = None
        super().__init__(3)
        self.store('pcc_gc_backend4_no_refcounts', 0, int(not counted))
        self.store('pcc_gc_mark_active', 0, 0)
        self.store('pcc_gc_cycle_requested', 0, 0)
        self.ns.update(vars(abi))
        self.ns.update({
            'abi_constant': ABI_CONSTANTS.__getitem__,
            'ptr_to_int': lambda value: value,
            'int_to_ptr': lambda value: value,
            'logical_shift_right_i64': lambda value, bits: (value % (1 << 64)) >> bits,
            'is_tagged_int': lambda value: int(isinstance(value, int) and value & 1),
            'ptr_add': self.add,
            'ptr_diff': lambda a, b: a - b,
            'malloc': self.malloc,
            'free': self.free,
            'memset': self.zero,
            'stack_alloc': self.allocate,
            'atomic_load_i64': lambda a, o, order: self.load(a, o),
            'atomic_store_i64': lambda a, o, v, order: self.store(a, o, v),
            'atomic_rmw_i64': self.rmw,
            'atomic_rmw_i32': self.rmw,
            'atomic_cas_i64': self.cas,
            'atomic_cas_i32': self.cas,
            'pcc_current_thread_id': lambda: 1,
            'pcc_py_gc_minor_current_get': lambda: self.load('model_minor_current', 0),
            'pcc_py_gc_minor_current_set': lambda block: self.store('model_minor_current', 0, block),
            'pcc_thread_live_count': lambda: 1,
            'pcc_thread_all_stopped_at_gates': lambda: int(self.gated),
            'pcc_thread_owns_stopped_world': lambda: int(self.stopped),
            'pcc_thread_world_stop_epoch_held': lambda: 1 if self.stopped else -1,
            'pcc_stop_the_world': self.stop,
            'pcc_resume_world': self.resume,
            'pcc_dealloc_cascade_active': lambda: 0,
            'pcc_thread_visit_conservative_words': self.visit_words,
            'pcc_capi_visit_extension_module_state_roots': self.visit_modules,
            'pcc_gc_tracing_has_sweep_candidate': self.candidates,
            'pcc_gc_object_index_insert': self.insert_index,
            'pcc_gc_object_index_find': lambda obj: self.index.get(obj, 0),
            # One dictionary probe supplies the stable index boundary, not
            # a native concurrent publication or lifetime proof.
            'pcc_gc_object_index_find_stable': lambda obj: self.index.get(obj, 0),
            'pcc_gc_pointer_is_managed': lambda obj: int(obj in self.index or obj in self.managed),
            'pcc_gc_managed_pointer_index_insert': self.register_pointer,
            'pcc_gc_managed_pointer_index_remove': lambda obj: self.managed.discard(obj),
            'pcc_gc_pointer_unregister': self.unregister_pointer,
            'pcc_gc_granule_is_object_start': lambda obj: -1,
            'pcc_gc_granule_object_retire': lambda obj: 0,
            'pcc_gc_static_literal_contains': lambda obj: 0,
            'pcc_gc_forwarding_index_find': lambda obj: self.forwardings.get(obj, 0),
            'pcc_gc_forwarding_index_insert': self.insert_forward,
            'pcc_gc_forwarding_target_index_find': lambda obj: self.reverse.get(obj, 0),
            'pcc_gc_forwarding_target_index_insert': self.insert_reverse,
            'pcc_gc_forwarding_target_index_upsert': self.upsert_reverse,
            'pcc_gc_identity_index_find': lambda obj: self.identities.get(obj, 0),
            'pcc_gc_identity_index_insert': self.insert_identity,
            'pcc_gc_identity_index_remove': lambda obj: self.identities.pop(obj, 0),
            'pcc_gc_object_is_known_no_lock': self.known,
            'pcc_gc_object_is_known_stable': self.known,
            'pcc_gc_object_node_is_active': lambda node: int(self.load(node, 32) == 0 and not self.load(self.load(node, 0), 12) & 524288),
            'pcc_gc_backend4_zpage_payload_span_preflight_locked': lambda obj, size: int(size == 0),
            'pcc_gc_retain_plan_finish': lambda plan: None,
            'pcc_gc_retire_plan_prepare_locked': lambda *args: 0,
            'pcc_gc_backend4_remembered_set_retarget_slot': lambda *args: None,
            'pcc_gc_backend4_remembered_owner_has_entries': lambda owner: 1,
            'py_gc_untrack': lambda obj: self.events.append(('untrack', obj)),
            'pcc_refcount_forget': lambda obj: None,
            'pcc_gc_note_load': lambda: None,
            'pcc_gc_note_store': lambda: None,
            'pcc_gc_fix_backend': lambda: None,
            'pcc_diagnostics_runtime_log_event_code': lambda *args: None,
            'pcc_capi_visit_cext_object_slots_i64': self.visit_cext,
            'pcc_capi_is_cext_type_tag': lambda tag: int(tag == 9000),
            'pcc_platform_abort': self.abort,
            'pcc_py_gc_defer_tripwire': lambda *args: self.abort(),
            'call_void_ptr1': lambda fn, value: fn(value),
            'call_void_ptr_i64_ptr': lambda fn, slot, role, context: fn(slot, role, context),
            'function_addr': lambda name: self.ns[name],
            'pcc_gc_refresh_read_barrier': lambda: None,
            'pcc_gc_free_watch_begin': lambda *args: None,
            'pcc_gc_free_watch_end': lambda *args: 1,
            '_gc0_terminal_free_has_no_metadata': lambda obj: 0,
            '_cpy_handle_owner_tripwire': lambda *args: self.abort(),
            '_cpy_handle_tripwire': lambda *args: self.abort(),
            '_py_incref_checked_tail': self.counted_incref,
            '_py_decref_checked_tail': self.counted_decref,
        })
        self.load_bodies('freestanding_gc_object_nodes.py')
        self.load_bodies('freestanding_gc_forwarding_identity.py')
        self.load_bodies('freestanding_gc_object_slots.py')
        self.load_bodies('freestanding_gc_relocation_payload.py')
        self.load_bodies('freestanding_gc_relocation_remap.py')
        self.load_bodies('freestanding_gc_forwarding_retirement.py')
        self.load_bodies('freestanding_gc_conservative_roots.py')
        # Keep the actual owner/enable/record checks. Machine-word capture is
        # the same explicit finite boundary as visit_words, not native stack
        # or register evidence and not an unconditional success result.
        self.load_bodies('freestanding_thread_kernel_pthread.py', {
            'pcc_thread_snapshot_stopped_world_owner',
        })
        self.owner_record = self.allocate(216)
        self.world_lock = self.allocate(8)
        self.store('pcc_thread_conservative_roots_enabled', 0, 1)
        self.store('pcc_tls_thread_record_py', 0, self.owner_record)
        self.store('pcc_world_lock_py', 0, self.world_lock)
        self.ns.update({
            'pthread_mutex_lock': self.snapshot_lock,
            'pthread_mutex_unlock': self.snapshot_unlock,
            '_thread_snapshot_roots': self.snapshot_owner_words,
        })
        self.load_bodies('freestanding_gc_generational_oldification.py')
        self.load_bodies('freestanding_gc_generational_promotion.py')
        self.load_bodies('freestanding_gc_root_operations.py', {
            'pcc_gc_object_is_address_pinned', '_resolve_root_slot_mode',
            'pcc_gc_object_node_is_address_pinned', 'pcc_gc_shade_gray_atomic',
            'pcc_gc_resolve_root_slot_unlocked', 'pcc_gc_resolve_borrowed_root_slot_unlocked',
        })
        self.load_bodies('freestanding_gc_mapped_roots.py', {
            'pcc_gc_visit_mapped_root_slot', 'pcc_gc_visit_mapped_root_slots',
        })
        self.load_bodies('py_obj.py', {'py_incref', 'py_decref'})
        self.load_bodies('py_gc_backend.py', {
            '_gc3_forwarded_target_may_die', 'pcc_gc_backend3_finish_forwarded_source',
            'pcc_gc_note_object_freeing', 'pcc_gc_free_object_memory',
            '_minor_release_block', '_minor_block_containing',
            '_minor_blocks_head', '_set_minor_current',
            '_minor_block_base', '_minor_block_size',
            '_minor_block_next', '_minor_block_owner',
            '_atomic_i64_dec_if_positive', '_atomic_i64_load', '_atomic_i64_store', '_atomic_i32_store',
        })
        self.load_bodies('py_obj_dealloc.py', {
            '_dealloc_dispatch', 'py_dealloc_int', 'py_dealloc_float', 'py_dealloc_str',
            'py_dealloc_generic', 'py_dealloc_cpy_handle', 'pcc_cpy_handle_move_owned_ref',
        })
        self.ns.update({
            '_object_graph_lock': self.lock, '_object_graph_unlock': self.unlock,
            '_identity_remove': self.ns['pcc_gc_identity_remove'],
            '_object_node_freeing': self.ns['pcc_gc_object_node_freeing'],
            '_set_object_node_freeing': self.ns['pcc_gc_object_node_set_freeing'],
            '_object_node_size': self.ns['pcc_gc_object_node_size'],
            '_object_node_minor_block': self.ns['pcc_gc_object_node_minor_block'],
            '_object_node_release': self.ns['pcc_gc_object_node_release'],
            '_unlink_object_node': self.ns['pcc_gc_object_node_unlink'],
            '_forwarding_detach_into_finish': self.ns['pcc_gc_forwarding_detach_into_finish'],
            '_forwarding_remove_target': self.ns['pcc_gc_forwarding_remove_target'],
            '_backend4_finish_remap_retirement': self.ns['pcc_gc_backend4_finish_remap_retirement'],
            'pcc_gc_note_relocation_read': self.ns['_note_relocation_read_unlocked'],
            'pcc_gc_visit_registered_root_slots': self.visit_roots,
            'pcc_gc_mark_root_gray_if_known': self.shade,
            # Match this fixture's existing abstract root-mark boundary.
            'pcc_gc_mark_canonical_root_gray_with_node': lambda obj, node: self.shade(obj),
            'pcc_gc_load_ptr': lambda owner, slot: self.load(slot, 0),
            'pcc_capi_dealloc_cext_object': lambda obj, tag: 0,
            '_FREE_WATCH_OBJECT': 8, '_FREE_WATCH_NOTED': 16,
        })
        self.store('py_cpy_handle_release_fn', 0, self.foreign_releases.append)

    def load_bodies(self, filename, names=None):
        load_functions(PY / filename, names, self.ns)

    def load(self, address, offset=0):
        if isinstance(address, tuple):
            address, extra = address
            offset += extra
        return self.memory.get((address + offset, 0) if isinstance(address, int) else (address, offset), 0)

    def store(self, address, offset, value):
        if isinstance(address, tuple):
            address, extra = address
            offset += extra
        self.memory[(address + offset, 0) if isinstance(address, int) else (address, offset)] = value

    def add(self, address, offset):
        return address + offset if isinstance(address, int) else (address, offset)

    def zero(self, address, value, size):
        for offset in range(0, size, 8):
            self.store(address, offset, value)

    def memmove(self, target, source, size):
        for offset in range(0, size, 4):
            self.store(target, offset, self.load(source, offset))

    def malloc(self, size):
        if self.fail_size and size == self.fail_size:
            return 0
        address = self.allocate(size)
        self.raw[address] = size
        return address

    def free(self, address):
        if address:
            assert address not in self.freed
            self.freed.add(address)
            self.events.append(('free', address))

    def cas(self, address, offset, expected, desired, *orders):
        before = self.load(address, offset)
        if before == expected:
            self.store(address, offset, desired)
        return before

    def rmw(self, operation, address, offset, value, order):
        before = self.load(address, offset)
        after = {'xchg': lambda: value, 'add': lambda: before + value, 'sub': lambda: before - value,
                 'and': lambda: before & value, 'or': lambda: before | value}[operation]()
        self.store(address, offset, after)
        return before

    def abort(self):
        raise AssertionError('runtime invariant abort')

    def stop(self):
        self.stopped = True
        return 0

    def resume(self):
        self.stopped = False
        return 0

    def known(self, obj):
        node = self.index.get(obj, 0)
        return int(bool(node) and self.load(node, 32) == 0 and not self.load(obj, 12) & 524288)

    # Match the real index leaf contract: 1=new, 0=existing without
    # replacement, -1=failure. Allocation failure applies only to a new key;
    # object insert requires a node, while the other three inserts do not.
    # These dictionaries do not model index publication or concurrency.
    def _insert_entry(self, table, obj, node, *, fail=False, require_node=False):
        if not obj or obj & 1 or (require_node and not node):
            return -1
        if obj in table:
            return 0
        if fail:
            return -1
        table[obj] = node
        return 1

    def insert_index(self, obj, node):
        return self._insert_entry(self.index, obj, node,
                                  fail=self.fail_index, require_node=True)

    def insert_forward(self, obj, edge):
        return self._insert_entry(self.forwardings, obj, edge, fail=self.fail_forward)

    def insert_reverse(self, obj, edge):
        return self._insert_entry(self.reverse, obj, edge)

    def insert_identity(self, obj, node):
        return self._insert_entry(self.identities, obj, node)

    def upsert_reverse(self, obj, edge):
        if not obj or obj & 1 or not edge:
            return -1
        inserted = int(obj not in self.reverse)
        self.reverse[obj] = edge
        return inserted

    def register_pointer(self, obj):
        self.managed.add(obj)
        return 0

    def unregister_pointer(self, obj):
        self.managed.discard(obj)
        return 0

    def counted_incref(self, obj):
        self.events.append(('incref', obj))
        self.store(obj, 0, self.load(obj, 0) + 1)

    def counted_decref(self, obj):
        self.events.append(('decref', obj))
        self.store(obj, 0, self.load(obj, 0) - 1)

    def shade(self, obj):
        self.store(obj, 12, self.load(obj, 12) & ~56 | 16)
        self.events.append(('shade', obj))

    def visit_words(self, callback, context):
        for word in self.words:
            callback(word, context)
        return len(self.words)

    def snapshot_lock(self, lock):
        assert self.stopped and lock == self.world_lock
        assert not self.owner_snapshot_locked
        self.owner_snapshot_locked = True

    def snapshot_unlock(self, lock):
        assert lock == self.world_lock and self.owner_snapshot_locked
        self.owner_snapshot_locked = False

    def snapshot_owner_words(self, record):
        assert self.stopped and self.owner_snapshot_locked
        assert record == self.owner_record
        # self.words is already the model's current finite machine-word set.
        # Do not add roots, alter headers, or manufacture retention results.
        self.owner_snapshot_refreshes += 1

    def visit_modules(self, callback, context):
        for root in self.module_roots:
            callback(root, context)

    def visit_roots(self, mode, resolve):
        for slot in self.root_cells:
            self.ns['pcc_gc_visit_mapped_root_slot'](slot, 0, 0, 0, mode, resolve)
        return len(self.root_cells)

    def candidates(self):
        return int(any(self.load(node, 32) == 0 and self.load(obj, 12) & 1024
                       for obj, node in self.index.items()))

    def visit_cext(self, obj, callback, context):
        if self.load(obj, 8) != 9000:
            return 0
        if self.cext_hook:
            self.cext_hook()
        callback(obj + 24, 1, context)
        return 1

    def value(self, tag=abi.PY_TYPE_FLOAT):
        size = 40 if tag in (abi.PY_TYPE_STR, abi.PY_TYPE_BYTES, abi.PY_TYPE_BYTEARRAY, abi.PY_TYPE_LIST) else 32
        block = 0
        obj = self.malloc(size)
        node = self.ns['pcc_gc_object_node_alloc']()
        self.zero(node, 0, 88)
        self.store(obj, 0, 1)
        self.store(obj, 8, tag)
        self.store(obj, 12, 8 | 128 | 262144)
        self.store(obj, 16, 271828)
        self.store(node, 0, obj)
        self.store(node, 8, size)
        self.store(node, 24, block)
        old = self.load('pcc_gc_object_head', 0)
        self.store(node, 16, old)
        if old:
            self.store(old, 40, node)
        self.ns['pcc_gc_object_set_list_head'](node)
        self.index[obj] = node
        self.store('pcc_gc_live_bytes', 0, self.load('pcc_gc_live_bytes', 0) + size)
        return obj, node, block

    def copy(self, source):
        with self.locked():
            return self.ns['pcc_gc_generational_oldify_copy'](source)

    def remap(self):
        return self.ns['pcc_gc_backend4_remap_and_retire_stopped_world']()


@pytest.mark.parametrize('name, table_name', [
    ('pcc_gc_object_index_insert', 'index'),
    ('pcc_gc_forwarding_index_insert', 'forwardings'),
    ('pcc_gc_forwarding_target_index_insert', 'reverse'),
    ('pcc_gc_identity_index_insert', 'identities'),
])
def test_gc3_index_insert_preserves_duplicates_and_rejects_invalid_keys(name, table_name):
    m = GC3()
    obj, first, replacement = [m.allocate(32) for _ in range(3)]
    insert = m.ns[name]
    table = getattr(m, table_name)
    assert insert(obj, first) == 1
    assert insert(obj, replacement) == 0
    assert insert(0, first) == -1
    assert insert(obj | 1, first) == -1
    assert table == {obj: first}


@pytest.mark.parametrize('name, table_name, require_node', [
    ('pcc_gc_object_index_insert', 'index', True),
    ('pcc_gc_forwarding_index_insert', 'forwardings', False),
    ('pcc_gc_forwarding_target_index_insert', 'reverse', False),
    ('pcc_gc_identity_index_insert', 'identities', False),
])
def test_gc3_index_insert_matches_null_node_validation(name, table_name, require_node):
    m = GC3()
    obj, first, empty = [m.allocate(32) for _ in range(3)]
    insert = m.ns[name]
    table = getattr(m, table_name)
    assert insert(obj, first) == 1
    # Validation precedes duplicate detection for the object index.
    assert insert(obj, 0) == (-1 if require_node else 0)
    assert table == {obj: first}
    assert insert(empty, 0) == (-1 if require_node else 1)
    if require_node:
        assert table == {obj: first}
    else:
        # A present key with a null value is still a duplicate, not a miss.
        assert insert(empty, first) == 0
        assert table == {obj: first, empty: 0}


@pytest.mark.parametrize('name, table_name, failure_flag', [
    ('pcc_gc_object_index_insert', 'index', 'fail_index'),
    ('pcc_gc_forwarding_index_insert', 'forwardings', 'fail_forward'),
])
def test_gc3_index_failure_preserves_table_and_existing_key(name, table_name, failure_flag):
    m = GC3()
    obj, first, replacement, missing = [m.allocate(32) for _ in range(4)]
    insert = m.ns[name]
    table = getattr(m, table_name)
    setattr(m, failure_flag, True)
    assert insert(obj, first) == -1 and not table
    setattr(m, failure_flag, False)
    assert insert(obj, first) == 1
    setattr(m, failure_flag, True)
    assert insert(missing, replacement) == -1
    assert insert(obj, replacement) == 0
    assert table == {obj: first}


def test_gc3_reverse_upsert_replaces_head_and_rejects_invalid_arguments():
    m = GC3()
    obj, first, replacement = [m.allocate(32) for _ in range(3)]
    upsert = m.ns['pcc_gc_forwarding_target_index_upsert']
    assert upsert(obj, first) == 1
    assert upsert(obj, replacement) == 0
    assert upsert(0, first) == -1
    assert upsert(obj | 1, first) == -1
    assert upsert(obj, 0) == -1
    assert m.reverse == {obj: replacement}


@pytest.mark.parametrize('fail', [False, True])
def test_gc3_public_install_observes_forward_insert_result(fail):
    m = GC3()
    source, source_node, _ = m.value()
    target, target_node, _ = m.value()
    m.fail_forward = fail
    before = set(m.raw)
    result = m.ns['pcc_gc_install_forwarding'](source, target)
    assert result == (-1 if fail else 0)
    assert m.depth == 0
    assert m.index == {source: source_node, target: target_node}
    assert m.load('pcc_gc_forwarding_population', 0) == int(not fail)
    edges = {ptr for ptr in set(m.raw) - before if m.raw[ptr] == 56}
    assert len(edges) == 1
    if fail:
        assert not m.forwardings and not m.reverse
        assert m.load('pcc_gc_forwarding_head', 0) == 0
        assert not m.load(source, 12) & 2048
        assert not m.load(target, 12) & 8192
        assert edges <= m.freed
    else:
        edge = m.forwardings[source]
        assert edges == {edge} and not edges & m.freed
        assert m.reverse == {target: edge}
        assert m.load('pcc_gc_forwarding_head', 0) == edge
        assert m.load(edge, 0) == source and m.load(edge, 8) == target
        assert m.load(source, 12) & 2048 and m.load(target, 12) & 8192


def test_gc3_public_install_reverse_chain_survives_retarget():
    m = GC3()
    first_source, second_source, target, replacement = [m.value()[0] for _ in range(4)]
    install = m.ns['pcc_gc_install_forwarding']
    assert install(first_source, target) == 0
    first = m.forwardings[first_source]
    assert install(second_source, target) == 0
    second = m.forwardings[second_source]
    assert first != second
    assert m.reverse == {target: second}
    assert m.load(second, 32) == first and m.load(first, 40) == second
    assert m.load(first, 32) == 0 and m.load(second, 40) == 0

    assert install(second_source, replacement) == 0
    assert m.forwardings == {first_source: first, second_source: second}
    assert m.reverse == {target: first, replacement: second}
    assert m.load(first, 8) == target and m.load(second, 8) == replacement
    for edge in (first, second):
        assert m.load(edge, 32) == m.load(edge, 40) == 0
        assert edge not in m.freed
    assert m.load('pcc_gc_forwarding_head', 0) == second
    assert m.load(second, 16) == first and m.load(first, 24) == second
    assert m.load('pcc_gc_forwarding_population', 0) == 2
    assert m.depth == 0


@pytest.mark.parametrize('tag', [abi.PY_TYPE_INT, abi.PY_TYPE_FLOAT, abi.PY_TYPE_STR,
                                abi.PY_TYPE_COMPLEX, abi.PY_TYPE_BYTES,
                                abi.PY_TYPE_BYTEARRAY, abi.PY_TYPE_CPY_HANDLE])
@pytest.mark.parametrize('failure', ['plan', 'node', 'index', 'forward'])
def test_every_oldify_failure_prefix_keeps_source_payload_and_discards_only_copy(tag, failure):
    m = GC3()
    source, node, block = m.value(tag)
    before = m.load(source, 16)
    raw_before = set(m.raw)
    live_before = m.load('pcc_gc_live_bytes', 0)
    if failure == 'plan':
        m.fail_size = 416
    elif failure == 'node':
        m.fail_size = 88
    elif failure == 'index':
        m.fail_index = True
    else:
        m.fail_forward = True
    assert m.copy(source) == 0
    assert m.load(source, 16) == before and source in m.index and m.load(node, 32) == 0
    assert not m.foreign_releases and not m.forwardings
    assert m.load('pcc_gc_live_bytes', 0) == live_before
    # All accepted GC3 kinds have inline native payload; CPY_HANDLE stays
    # borrowed until the real move helper, and forward rollback moves it back.
    copied = next(ptr for ptr in m.raw if ptr not in raw_before)
    assert m.raw[copied] == m.load(node, 8) and copied in m.freed
