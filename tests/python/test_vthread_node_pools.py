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
    def __init__(self):
        self.memory = {}
        self.atomic = threading.Lock()
        self.local = threading.local()
        self.next_pointer = 4096
        self.freed = []
        self.retired = []
        self.fail_allocation = False
        self.yield_on_atomic = False
        self.namespace = {
            "global_addr": lambda name: (name, 0),
            "global_load_ptr": lambda name: self.load((name, 0), 0),
            "global_store_ptr": lambda name, value: self.store((name, 0), 0, value),
            "ptr_add": lambda base, offset: (base[0], base[1] + offset),
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
            "pcc_gc_scheduler_root_unregister_handle": self.retire,
            "pcc_gc_store_root": self.store_root,
            "_effect": lambda *args: None,
            "pcc_vthread_effect_note_waiter_root_leave": lambda: None,
            "pcc_vthread_waiter_pool_note_reuse": lambda: None,
            "pcc_vthread_waiter_pool_note_allocation": lambda: None,
            "pcc_vthread_waiter_pool_note_cached": lambda count: None,
        }
        names = {"_raw_queue_lock", "_raw_queue_unlock", "_node_pool_lock_word",
                 "_ready_alloc", "_ready_release", "_join_alloc", "_join_recycle",
                 "_channel_group_alloc", "_channel_group_recycle"}
        nodes = [node for node in ast.parse(SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        exec(compile(ast.Module(nodes, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def load(self, pointer, offset):
        return self.memory.get((pointer, offset), 0)

    def store(self, pointer, offset, value):
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
        assert size in (32, 80)
        if self.fail_allocation:
            return 0
        with self.atomic:
            self.next_pointer += 128
            return self.next_pointer

    def free(self, node):
        self.outside()
        self.freed.append(node)

    def retire(self, handle):
        self.outside()
        assert handle not in self.retired
        self.retired.append(handle)

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
def test_transferred_ready_nodes_return_to_their_pool_after_retiring_the_root(kind):
    pools = Pools()
    node = pools.acquire(kind)
    pools.store(node, 0, "thread")
    pools.store(node, 16, "root handle")
    pools.release(node)
    assert pools.retired == ["root handle"]
    assert pools.cached(kind) == 1
    assert pools.acquire(kind) == node
    assert [pools.load(node, offset) for offset in (0, 8, 16, 24)] == [0, 0, 0, kind]
    assert pools.cached(kind) == 0
    pools.outside()


@pytest.mark.parametrize("kind", KINDS)
def test_full_pool_releases_raw_storage_after_unlocking(kind):
    pools = Pools()
    node = pools.acquire(kind)
    pools.store((KINDS[kind][1], 0), 0, 4096)
    pools.release(node)
    assert pools.freed == [node]
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
    drained = [pools.acquire(kind) for _ in range(count)]
    assert len(set(drained)) == count
    assert pools.cached(kind) == 0
