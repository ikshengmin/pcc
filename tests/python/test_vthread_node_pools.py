"""Runtime free-list ownership, independent of the scheduler mutex."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"
KINDS = {1: ("_ready_alloc", "pcc_vthread_ready_cached_py"),
         2: ("_join_alloc", "pcc_vthread_join_free_count_py"),
         3: ("_channel_group_alloc", "pcc_vthread_channel_free_count_py")}


class Pools:
    def __init__(self, pool_address=104):
        self.pool_address = pool_address
        self.memory = {}
        self.allocation_sizes = {}
        self.atomic = threading.Lock()
        self.local = threading.local()
        self.next_pointer = 4096
        self.freed = []
        self.retired = []
        self.registered = {}
        self.registration_calls = 0
        self.fail_registration = False
        self.fail_allocation = False
        self.yield_on_atomic = False
        self.namespace = {
            "global_addr": lambda name: (name, 0),
            "global_load_ptr": lambda name: self.load((name, 0), 0),
            "global_store_ptr": lambda name, value: self.store((name, 0), 0, value),
            "ptr_add": lambda base, offset: (base[0], base[1] + offset),
            "ptr_to_int": lambda base: self.pool_address + base[1],
            "int_to_ptr": lambda address: ("pcc_vthread_node_pool_locks_py", address - self.pool_address),
            "null": lambda: 0,
            "ptr_is_null": lambda value: value == 0,
            "load_ptr": self.load, "load_i64": self.load,
            "store_ptr": self.store, "store_i64": self.store,
            "atomic_load_i64": self.atomic_load,
            "atomic_store_i64": self.atomic_store,
            "atomic_cas_i64": self.cas,
            "pcc_current_thread_id": threading.get_ident,
            "pcc_thread_no_park_enter": self.enter,
            "pcc_thread_no_park_exit": self.leave,
            "_counter": lambda slot: self.load(slot, 0),
            "_counter_add": self.add,
            "malloc": self.allocate,
            "free": self.free,
            "pcc_gc_scheduler_root_register_handle": self.register,
            "pcc_gc_scheduler_root_unregister_handle": self.retire,
            "pcc_gc_store_root": self.store_root,
            "_effect": lambda *args: None,
            "pcc_vthread_effect_note_waiter_root_leave": lambda: None,
            "pcc_vthread_waiter_pool_note_reuse": lambda: None,
            "pcc_vthread_waiter_pool_note_allocation": lambda: None,
            "pcc_vthread_waiter_pool_note_cached": lambda count: None,
        }
        names = {"_raw_queue_lock", "_raw_queue_unlock", "_node_pool_lock_word",
                 "_pooled_node_root", "_pooled_node_dispose", "_ready_reserve",
                 "_ready_alloc", "_ready_release", "_join_alloc", "_join_recycle",
                 "_channel_group_alloc", "_channel_group_recycle"}
        for node in ast.parse(SOURCE.read_text()).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {
                        "READY_NODE_SIZE", "READY_NODE_PREVIOUS_OFFSET",
                        "READY_NODE_DEADLINE_OFFSET", "READY_NODE_NO_DEADLINE",
                    }):
                self.namespace[node.targets[0].id] = ast.literal_eval(node.value)
        nodes = [node for node in ast.parse(SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        exec(compile(ast.Module(nodes, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def load(self, pointer, offset):
        return self.memory.get((pointer, offset), 0)

    def store(self, pointer, offset, value):
        if pointer in self.allocation_sizes:
            assert 0 <= offset <= self.allocation_sizes[pointer] - 8
        self.memory[pointer, offset] = value

    def enter(self):
        self.local.depth = getattr(self.local, "depth", 0) + 1

    def leave(self):
        assert self.local.depth > 0
        self.local.depth -= 1

    def outside(self):
        assert getattr(self.local, "depth", 0) == 0

    def atomic_load(self, pointer, offset, order):
        assert self.local.depth > 0 and order == "relaxed"
        with self.atomic:
            return self.load(pointer, offset)

    def atomic_store(self, pointer, offset, value, order):
        assert self.local.depth > 0 and order == "release"
        with self.atomic:
            self.store(pointer, offset, value)

    def cas(self, pointer, offset, expected, replacement, success, failure):
        assert self.local.depth > 0 and (success, failure) == ("acquire", "relaxed")
        with self.atomic:
            previous = self.load(pointer, offset)
            if previous == expected:
                self.store(pointer, offset, replacement)
        if self.yield_on_atomic and previous == expected:
            time.sleep(0)
        return previous

    def add(self, pointer, delta):
        with self.atomic:
            value = self.load(pointer, 0) + delta
            assert value >= 0
            self.store(pointer, 0, value)
            return value

    def allocate(self, size):
        self.outside()
        assert size in (32, self.namespace["READY_NODE_SIZE"], 80)
        if self.fail_allocation:
            return 0
        with self.atomic:
            self.next_pointer += 128
            self.allocation_sizes[self.next_pointer] = size
            return self.next_pointer

    def free(self, node):
        self.outside()
        self.freed.append(node)

    def retire(self, handle):
        self.outside()
        assert handle not in self.retired
        assert self.load(self.registered.pop(handle), 0) == 0
        self.retired.append(handle)

    def register(self, node):
        self.outside()
        assert self.load(node, 0) == 0
        if self.fail_registration:
            return 0
        handle = ("root", node)
        with self.atomic:
            assert handle not in self.registered
            self.registered[handle] = node
            self.registration_calls += 1
        return handle

    def store_root(self, node, value):
        self.outside()
        self.store(node, 0, value)

    def acquire(self, kind):
        return self.namespace[KINDS[kind][0]]()

    def release(self, node):
        self.namespace["_ready_release"](node)

    def cached(self, kind):
        return self.load((KINDS[kind][1], 0), 0)


@pytest.mark.parametrize("kind", KINDS)
def test_cached_nodes_keep_one_empty_registration_without_retaining_the_task(kind):
    pools = Pools()
    node = pools.acquire(kind)
    handle = pools.namespace["_pooled_node_root"](node)
    pools.store(node, 0, "thread")
    pools.release(node)
    assert pools.retired == []
    assert pools.registered == {handle: node}
    assert pools.load(node, 0) == 0
    assert pools.cached(kind) == 1
    assert pools.acquire(kind) == node
    assert [pools.load(node, offset) for offset in (0, 8, 16, 24)] == [0, 0, handle, kind]
    assert pools.namespace["_pooled_node_root"](node) == handle
    assert pools.registration_calls == 1
    assert pools.cached(kind) == 0
    pools.outside()


@pytest.mark.parametrize("kind", KINDS)
def test_full_pool_releases_raw_storage_after_unlocking(kind):
    pools = Pools()
    node = pools.acquire(kind)
    handle = pools.namespace["_pooled_node_root"](node)
    pools.store(node, 0, "task")
    pools.store((KINDS[kind][1], 0), 0, 4096)
    pools.release(node)
    assert pools.freed == [node]
    assert pools.retired == [handle]
    assert pools.registered == {}
    assert pools.cached(kind) == 4096
    pools.outside()


@pytest.mark.parametrize("kind", KINDS)
def test_failed_allocation_leaves_no_pool_lock_owned(kind):
    pools = Pools()
    pools.fail_allocation = True
    assert pools.acquire(kind) == 0
    assert pools.cached(kind) == 0
    pools.outside()
    pools.fail_allocation = False
    assert pools.acquire(kind) != 0


def test_failed_registration_keeps_a_retryable_empty_node():
    pools = Pools()
    pools.fail_registration = True
    assert pools.namespace["_ready_reserve"]("task") == 0
    assert pools.cached(1) == 1 and not pools.registered
    pools.fail_registration = False
    node = pools.namespace["_ready_reserve"]("task")
    assert pools.load(node, 0) == "task"
    assert pools.registration_calls == 1
    pools.release(node)
    assert pools.load(node, 0) == 0
    pools.outside()


@pytest.mark.parametrize("residue", range(0, 128, 8))
def test_pool_words_do_not_share_the_preceding_globals_cache_line(residue):
    pools = Pools(pool_address=4096 + residue)
    offsets = [pools.namespace["_node_pool_lock_word"](kind)[1] for kind in KINDS]
    assert all((pools.pool_address + offset) % 128 == 0 for offset in offsets)
    assert offsets[-1] + 8 <= 64 * 8


@pytest.mark.parametrize("kind", KINDS)
def test_four_carriers_cannot_own_the_same_cached_node(kind):
    pools = Pools()
    pools.yield_on_atomic = True
    barrier = threading.Barrier(4)
    owners = set()
    owners_lock = threading.Lock()

    def carrier():
        barrier.wait(timeout=5)
        for _ in range(40):
            node = pools.acquire(kind)
            pools.namespace["_pooled_node_root"](node)
            with owners_lock:
                assert node not in owners
                owners.add(node)
            time.sleep(0)
            with owners_lock:
                owners.remove(node)
            pools.release(node)
            pools.outside()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(carrier) for _ in range(4)]
        for future in futures:
            future.result(timeout=15)
    assert not owners
    count = pools.cached(kind)
    assert 1 <= count <= 4
    assert pools.registration_calls == count
    assert all(pools.load(node, 0) == 0 for node in pools.registered.values())
    drained = [pools.acquire(kind) for _ in range(count)]
    assert len(set(drained)) == count
    assert pools.cached(kind) == 0
