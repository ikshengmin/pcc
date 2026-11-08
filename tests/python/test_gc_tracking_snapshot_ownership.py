"""Actual snapshot/materialization bodies over bounded host allocation faults.

Raw memory, index publication and Python list allocation are explicit seams.
No production fault switches are used. Native pointer lifetime and finalizer
reentry are exercised separately by test_gc_snapshot_materialization_native.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.python.test_gc_mark_color_atomic import _load

RUNTIME = Path(__file__).resolve().parents[2] / 'pcc/runtime/py'
TRACKING = RUNTIME / 'freestanding_gc_tracking.py'
SNAPSHOT = RUNTIME / 'py_obj_gc.py'
pytestmark = pytest.mark.xdist_group('gc_tracking_snapshot_ownership')


class SnapshotRuntime:
    def __init__(self, *, flag_mode=1, backend=0):
        self.words = {}
        self.globals = {}
        self.next_address = 8192
        self.allocations = set()
        self.freed = set()
        self.index = {}
        self.small = []
        self.large = []
        self.enumerations = []
        self.malloc_calls = self.insert_calls = 0
        self.fail_malloc = self.fail_insert = 0
        self.changed_fill = None
        self.world = False
        self.events = []
        self.lists = {}
        self.exception = None
        self.list_fault = None
        self.ns = dict(i64=int, c_ptr=int, null=lambda: 0,
                       ptr_is_null=lambda p: int(p == 0),
                       is_tagged_int=lambda p: 0,
                       ptr_add=lambda p, n: p + n, ptr_diff=lambda p, q: p - q,
                       logical_shift_right_i64=lambda n, bits: (n & ((1 << 64) - 1)) >> bits,
                       global_addr=self.address, global_load_ptr=self.global_get,
                       global_store_ptr=self.global_set,
                       malloc=self.malloc, free=self.free,
                       atomic_load_i32=lambda p, o, order: self.load(p, o),
                       atomic_load_i64=lambda p, o, order: self.load(p, o),
                       atomic_store_i32=lambda p, o, v, order: self.store(p, o, v),
                       atomic_store_i64=lambda p, o, v, order: self.store(p, o, v),
                       atomic_cas_i32=self.cas, atomic_rmw_i32=self.rmw,
                       pcc_threads_enabled=lambda: 1, gc_backend_current=lambda: backend,
                       pcc_current_native_thread_token=lambda: 77,
                       pcc_platform_abort=lambda: pytest.fail('unexpected abort'),
                       pcc_thread_no_park_enter=lambda: None,
                       pcc_thread_no_park_exit=lambda: None,
                       pcc_thread_safepoint=lambda: None,
                       py_gc_index_insert=self.insert, py_gc_index_remove=self.remove,
                       pcc_allocator_object_cells_enumerate=lambda out, cap, flags: self.enumerate('small', out, cap, flags),
                       pcc_gc_managed_pointer_index_enumerate=lambda out, cap, flags: self.enumerate('large', out, cap, flags),
                       pcc_gc_fix_backend=lambda: None,
                       pcc_thread_owns_stopped_world=lambda: int(self.world),
                       pcc_stop_the_world=self.stop, pcc_resume_world=self.resume,
                       py_list_new=self.list_new, py_list_append=self.list_append,
                       py_decref=self.decref, py_err_occurred=lambda: int(self.exception is not None),
                       py_exc_new=lambda code, message: MemoryError(message) if code == 19 else pytest.fail(code),
                       py_raise_owned=self.raise_owned, cstr=lambda s: s,
                       PYOBJECTHEADER_FLAGS_OFFSET=12, PYOBJECTHEADER_REFCOUNT_OFFSET=0,
                       PY_FLAG_GC_DEALLOCATING=524288, PY_FLAG_IMMORTAL=1)
        for name in ('load_i32', 'load_i64', 'load_ptr'):
            self.ns[name] = self.load
        for name in ('store_i32', 'store_i64', 'store_ptr'):
            self.ns[name] = self.store
        tree = ast.parse(TRACKING.read_text())
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
                self.ns[node.target.id] = node.value.value
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                _load(TRACKING, node.name, self.ns)
        for name in ('_tracked_snapshot_enter', '_tracked_snapshot_leave', 'py_gc_get_objects'):
            _load(SNAPSHOT, name, self.ns)
        self.global_set('pcc_gc_tracking_flag_mode', flag_mode)

    def address(self, name):
        if name not in self.globals:
            self.globals[name] = self.reserve(8192)
        return self.globals[name]

    def reserve(self, size):
        result = self.next_address
        self.next_address += (size + 127) & ~63
        return result

    def global_get(self, name):
        return self.load(self.address(name), 0)

    def global_set(self, name, value):
        self.store(self.address(name), 0, value)

    def load(self, pointer, offset):
        assert pointer not in self.freed, 'read after node free'
        return self.words.get(pointer + offset, 0)

    def store(self, pointer, offset, value):
        assert pointer not in self.freed, 'write after node free'
        self.words[pointer + offset] = value

    def cas(self, pointer, offset, expected, value, success, failure):
        previous = self.load(pointer, offset)
        if previous == expected:
            self.store(pointer, offset, value)
        return previous

    def rmw(self, operation, pointer, offset, value, order):
        previous = self.load(pointer, offset)
        result = {'add': lambda: previous + value, 'sub': lambda: previous - value,
                  'or': lambda: previous | value, 'and': lambda: previous & value}[operation]()
        self.store(pointer, offset, result)
        return previous

    def malloc(self, size):
        self.malloc_calls += 1
        if self.malloc_calls == self.fail_malloc:
            return 0
        pointer = self.reserve(size)
        self.allocations.add(pointer)
        return pointer

    def free(self, pointer):
        assert pointer in self.allocations and pointer not in self.freed
        self.freed.add(pointer)

    def insert(self, obj, node):
        self.insert_calls += 1
        if self.insert_calls == self.fail_insert:
            return -1
        if obj in self.index:
            return 0
        self.index[obj] = node
        return 1

    def remove(self, obj):
        return self.index.pop(obj, 0)

    def enumerate(self, origin, out, capacity, flags):
        assert self.world and self.global_get('pcc_gc_table_lock_depth') > 0
        self.enumerations.append((origin, bool(out)))
        objects = [obj for obj in getattr(self, origin) if self.load(obj, 12) & flags == flags]
        for i, obj in enumerate(objects[:capacity]):
            self.store(out, i * 8, obj)
        return len(objects) + int(bool(out) and self.changed_fill == origin)

    def object(self, *, large=False, flags=64):
        obj = self.reserve(64)
        self.store(obj, 0, 1)
        self.store(obj, 12, flags)
        (self.large if large else self.small).append(obj)
        self.ns['py_gc_track'](obj)
        return obj

    def seed(self):
        return [self.object(), self.object(flags=32), self.object(large=True, flags=128)]

    def stop(self):
        assert not self.world
        self.world = True
        self.events.append('stop')
        return 0

    def resume(self):
        assert self.world
        self.world = False
        self.events.append('resume')

    def enter(self):
        self.stop()
        self.ns['pcc_gc_default_table_lock']()

    def leave(self):
        self.ns['pcc_gc_default_table_unlock']()
        self.resume()

    def list_new(self, capacity):
        self.events.append('list_new')
        if self.list_fault == 'new':
            self.exception = MemoryError('list allocation')
            return 0
        obj = self.object()
        self.lists[obj] = []
        return obj

    def list_append(self, out, obj):
        if self.list_fault == 'append':
            self.exception = MemoryError('list append')
        else:
            self.lists[out].append(obj)

    def decref(self, obj):
        self.ns['py_gc_untrack'](obj)
        self.lists.pop(obj, None)

    def raise_owned(self, error):
        self.events.append('raise')
        self.exception = error

    def members(self):
        result = {}
        node = self.global_get('py_gc_head')
        previous = 0
        while node:
            assert self.load(node, 24) == previous
            obj = self.load(node, 0)
            assert obj not in result and self.index[obj] == node
            result[obj] = node
            previous, node = node, self.load(node, 32)
        assert result == self.index
        return result

    def drain_pool(self):
        self.ns['pcc_gc_tracked_node_pool_drain']()
        assert self.allocations == self.freed


@pytest.mark.parametrize('fault', ['array', 'node1', 'node2', 'node3', 'index1', 'index2', 'index3', 'small', 'large'])
def test_materialization_failure_rolls_back_exact_membership_and_can_retry(fault):
    rt = SnapshotRuntime()
    objects = rt.seed()
    flags = [rt.load(obj, 12) for obj in objects]
    rt.enter()
    if fault == 'array':
        rt.fail_malloc = 1
    elif fault.startswith('node'):
        rt.fail_malloc = 1 + int(fault[-1])
    elif fault.startswith('index'):
        rt.fail_insert = int(fault[-1])
    else:
        rt.changed_fill = fault
    failed = rt.ns['pcc_gc_tracked_materialize']()
    assert failed == -1
    assert rt.members() == {}
    assert rt.global_get('pcc_gc_tracking_materialized') == 0
    assert rt.global_get('py_gc_tracked_count') == 3
    assert [rt.load(obj, 12) for obj in objects] == flags
    rt.fail_malloc = rt.fail_insert = 0
    rt.changed_fill = None
    acquired = rt.ns['pcc_gc_tracked_materialize']()
    assert acquired == 3
    before = rt.members()
    assert set(before) == set(objects)
    # A failed owner must not tear down a later successful acquisition.
    rt.ns['pcc_gc_tracked_dematerialize'](failed)
    assert rt.members() == before
    rt.ns['pcc_gc_tracked_dematerialize'](acquired)
    assert rt.members() == {}
    assert rt.global_get('py_gc_tracked_count') == 3
    assert [rt.load(obj, 12) for obj in objects] == flags
    rt.leave()
    rt.drain_pool()


def test_nested_snapshot_borrows_nodes_and_preserves_deferred_dead_node_lifetime():
    rt = SnapshotRuntime()
    objects = rt.seed()
    rt.enter()
    acquired = rt.ns['pcc_gc_tracked_materialize']()
    before = rt.members()
    enumerations = list(rt.enumerations)
    # A collector may already have populated traversal metadata on its nodes.
    for node in before.values():
        rt.store(node, 8, 17)
        rt.store(node, 16, 23)
    borrowed = rt.ns['pcc_gc_tracked_materialize']()
    assert borrowed == 3 and rt.enumerations == enumerations
    rt.ns['pcc_gc_tracked_dematerialize'](0)
    out = rt.ns['py_gc_get_objects']()
    assert set(rt.lists[out]) == set(objects) and out not in rt.lists[out]
    assert all(rt.index[obj] == node for obj, node in before.items())
    assert all((rt.load(n, 8), rt.load(n, 16)) == (17, 23) for n in before.values())
    assert rt.world and rt.global_get('pcc_gc_table_lock_depth') == 1
    out_node = rt.index[out]
    rt.decref(out)
    assert rt.load(out_node, 0) == 0 and out_node not in rt.freed
    rt.ns['pcc_gc_tracked_dematerialize'](acquired)
    assert rt.global_get('py_gc_tracked_count') == 3 and rt.members() == {}
    assert out_node not in rt.freed
    rt.leave()
    assert out_node in rt.freed
    rt.drain_pool()


@pytest.mark.parametrize('borrow_world', [False, True])
@pytest.mark.parametrize('fault', ['array', 'node', 'index'])
def test_snapshot_failure_sets_memory_error_and_releases_only_its_owners(borrow_world, fault):
    rt = SnapshotRuntime()
    rt.seed()
    if borrow_world:
        rt.enter()
    if fault == 'index':
        rt.fail_insert = 2
    else:
        rt.fail_malloc = 1 if fault == 'array' else 3
    assert rt.ns['py_gc_get_objects']() == 0
    assert isinstance(rt.exception, MemoryError)
    assert 'list_new' not in rt.events
    assert rt.global_get('pcc_gc_tracking_materialized') == 0 and not rt.index
    assert rt.world == borrow_world
    assert rt.global_get('pcc_gc_table_lock_depth') == int(borrow_world)
    assert rt.events[-1] == 'raise'
    if not borrow_world:
        assert rt.events[-2] == 'resume'
    else:
        assert 'resume' not in rt.events
        rt.leave()
    rt.drain_pool()


@pytest.mark.parametrize('fault', ['new', 'append'])
def test_nested_list_failure_does_not_release_outer_materialization(fault):
    rt = SnapshotRuntime()
    objects = rt.seed()
    rt.enter()
    acquired = rt.ns['pcc_gc_tracked_materialize']()
    before = rt.members()
    rt.list_fault = fault
    assert rt.ns['py_gc_get_objects']() == 0
    assert isinstance(rt.exception, MemoryError)
    assert rt.members() == before
    assert rt.global_get('py_gc_tracked_count') == len(objects)
    assert rt.world and rt.global_get('pcc_gc_table_lock_depth') == 1
    rt.ns['pcc_gc_tracked_dematerialize'](acquired)
    rt.leave()
    rt.drain_pool()


def test_empty_view_is_owned_and_duplicate_origins_are_not_duplicate_nodes():
    rt = SnapshotRuntime()
    rt.enter()
    assert rt.ns['pcc_gc_tracked_materialize']() == 0
    assert rt.ns['pcc_gc_tracked_materialize']() == 0
    rt.ns['pcc_gc_tracked_dematerialize'](1)
    obj = rt.object()
    rt.large.append(obj)
    assert rt.ns['pcc_gc_tracked_materialize']() == 1
    assert set(rt.members()) == {obj}
    assert rt.global_get('py_gc_tracked_count') == 1
    rt.ns['pcc_gc_tracked_dematerialize'](1)
    rt.leave()
    rt.drain_pool()


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_ordinary_tracking_does_not_acquire_or_release_a_temporary_view(backend):
    rt = SnapshotRuntime(flag_mode=0, backend=backend)
    rt.seed()
    rt.enter()
    before = rt.members()
    assert rt.ns['pcc_gc_tracked_materialize']() == 0
    rt.ns['pcc_gc_tracked_dematerialize'](0)
    assert rt.members() == before and rt.enumerations == []
    rt.leave()


def test_candidate_collection_without_acquire_cannot_release_outer_view():
    rt = SnapshotRuntime()
    rt.seed()
    rt.enter()
    acquired = rt.ns['pcc_gc_tracked_materialize']()
    before = rt.members()
    dead_obj, dead_node = next(iter(before.items()))
    rt.ns['py_gc_untrack'](dead_obj)
    before.pop(dead_obj)
    rt.ns.update(pcc_stop_the_world=lambda: 0, pcc_resume_world=lambda: None,
                 pcc_platform_monotonic_us=lambda: 1,
                 pcc_gc_record_collection_stw_pause=lambda a, b: None,
                 pcc_gc_reclaim_retired_index_tables=lambda: None,
                 _prepare_subset=lambda pairs, count: 0, _finish_subset=lambda: None)
    _load(RUNTIME / 'freestanding_gc_backend0_collector.py', '_collect', rt.ns)
    assert rt.ns['_collect'](123, 1) == 0
    assert rt.members() == before and rt.global_get('pcc_gc_tracking_materialized') == 1
    assert dead_node not in rt.freed and rt.load(dead_node, 0) == 0
    rt.ns['pcc_gc_tracked_dematerialize'](acquired)
    rt.leave()
    rt.drain_pool()


def test_snapshot_memory_error_uses_the_public_exception_abi():
    import re
    from pcc.frontends.python.codegen.builtin_exceptions import BUILTIN_EXC_TAG
    header = (RUNTIME.parent / 'include/py_runtime.h').read_text()
    assert re.search(r"PY_EXC_MEMORYERROR\s*=\s*19\b", header)
    assert BUILTIN_EXC_TAG['MemoryError'] == 19


def test_public_snapshot_of_empty_heap_releases_its_new_empty_view():
    rt = SnapshotRuntime()
    out = rt.ns['py_gc_get_objects']()
    assert out and rt.lists[out] == []
    assert rt.global_get('py_gc_tracked_count') == 1
    assert rt.global_get('pcc_gc_tracking_materialized') == 0
    assert rt.global_get('pcc_gc_table_lock_depth') == 0 and not rt.world
    assert not rt.index and rt.members() == {}
    rt.decref(out)
    assert rt.global_get('py_gc_tracked_count') == 0
    rt.drain_pool()


def test_full_collector_releases_owned_empty_view_without_allocating_candidates():
    rt = SnapshotRuntime()
    rt.ns.update(pcc_platform_monotonic_us=lambda: 1,
                 pcc_gc_record_collection_stw_pause=lambda a, b: None,
                 pcc_gc_reclaim_retired_index_tables=lambda: None,
                 _finish_subset=lambda: None)
    _load(RUNTIME / 'freestanding_gc_backend0_collector.py', '_collect', rt.ns)
    assert rt.ns['_collect'](0, 0) == 0
    assert rt.global_get('pcc_gc_tracking_materialized') == 0
    assert rt.global_get('pcc_gc_table_lock_depth') == 0 and not rt.world
    assert rt.global_get('py_gc_collecting') == 0
    assert rt.malloc_calls == 0 and not rt.index


def test_cold_snapshot_fixes_backend_before_retaining_pre_freeze_nodes():
    rt = SnapshotRuntime(flag_mode=0)
    obj = rt.object(flags=1)
    old_node = rt.index[obj]

    def fix_backend():
        assert not rt.world and rt.global_get('pcc_gc_table_lock_depth') == 0
        rt.ns['pcc_gc_tracking_enter_flag_mode']()

    rt.ns['pcc_gc_fix_backend'] = fix_backend
    out = rt.ns['py_gc_get_objects']()
    assert rt.lists[out] == [obj] and out not in rt.lists[out]
    assert rt.global_get('pcc_gc_tracking_flag_mode') == 1
    assert rt.global_get('pcc_gc_tracking_materialized') == 0 and not rt.index
    assert rt.load(obj, 12) == 3 and rt.global_get('py_gc_tracked_count') == 2
    assert old_node not in rt.index.values()
    rt.decref(out)
    rt.drain_pool()
