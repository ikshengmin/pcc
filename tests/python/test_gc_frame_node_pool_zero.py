"""Actual frame-pool bodies over byte-exact memory and isolated TLS cells.

Specification: docs/project-intent.md shared root/lifetime contract. Only the
allocator, raw loads/stores and TLS provider are modeled. This is not native
GC, concurrent scheduling, throughput or bootstrap evidence.
"""
from __future__ import annotations

import ast
from bisect import bisect_right
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/runtime/py/freestanding_gc_frame_registry.py"
PREFIX = "pcc_gc_frame_node_"
FUNCTIONS = {
    PREFIX + suffix for suffix in (
        "bucket", "size", "pool_heads_get", "pool_counts_get", "alloc",
        "release", "tls_pool_cached_count", "tls_pool_drain",
    )
}
TLS = {PREFIX + name for name in ("pool_heads", "pool_counts", "pool_total")}


class PoolMemory:
    def __init__(self, backend=3):
        self.thread = 1
        self.next_address = 4096
        self.starts = []
        self.blocks = {}
        self.cells = {}
        self.freed = []
        self.freed_set = set()
        self.attempts = 0
        self.fail_on = set()
        self.zero_calls = []
        self.namespace = {
            "i64": int, "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "global_addr": self.global_addr,
            "global_load_ptr": lambda name: self.load(self.global_addr(name), 0, 8),
            "global_store_ptr": lambda name, value: self.store(self.global_addr(name), 0, value, 8),
            "load_ptr": lambda base, offset: self.load(base, offset, 8),
            "load_i64": lambda base, offset: self.load(base, offset, 8, signed=True),
            "load_i32": lambda base, offset: self.load(base, offset, 4, signed=True),
            "store_ptr": lambda base, offset, value: self.store(base, offset, value, 8),
            "store_i64": lambda base, offset, value: self.store(base, offset, value, 8),
            "store_i32": lambda base, offset, value: self.store(base, offset, value, 4),
            "malloc": self.malloc, "free": self.free, "memset": self.memset,
        }
        tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
        assert {node.name for node in functions} == FUNCTIONS
        for node in functions:
            node.decorator_list = []
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), self.namespace)
        self.backend = backend

    def reserve(self, size, fill=0):
        address = self.next_address
        self.next_address += size + 32
        self.starts.append(address)
        self.blocks[address] = bytearray([fill]) * size
        return address

    def span(self, base, offset, size):
        address = base + offset
        position = bisect_right(self.starts, address) - 1
        assert position >= 0, (base, offset, size)
        start = self.starts[position]
        assert start not in self.freed_set, "access through a released allocation"
        block = self.blocks[start]
        index = address - start
        assert 0 <= index and index + size <= len(block), (base, offset, size)
        return block, index

    def load(self, base, offset, size, signed=False):
        block, index = self.span(base, offset, size)
        return int.from_bytes(block[index:index + size], "little", signed=signed)

    def store(self, base, offset, value, size):
        block, index = self.span(base, offset, size)
        block[index:index + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def global_addr(self, name):
        key = (self.thread if name in TLS else 0, name)
        if key not in self.cells:
            self.cells[key] = self.reserve(8)
        return self.cells[key]

    @property
    def backend(self):
        return self.load(self.global_addr("pcc_gc_backend_selected"), 0, 4)

    @backend.setter
    def backend(self, value):
        self.store(self.global_addr("pcc_gc_backend_selected"), 0, value, 4)

    def malloc(self, size):
        self.attempts += 1
        if self.attempts in self.fail_on:
            return 0
        return self.reserve(size, 0xCD)

    def free(self, pointer):
        assert pointer in self.blocks and pointer not in self.freed_set
        self.freed.append(pointer)
        self.freed_set.add(pointer)

    def memset(self, base, value, size):
        block, index = self.span(base, 0, size)
        block[index:index + size] = bytes([value & 255]) * size
        self.zero_calls.append((base, value, size))

    def call(self, suffix, *args):
        return self.namespace[PREFIX + suffix](*args)

    def allocation(self, count):
        pointer = self.call("alloc", count)
        assert pointer != 0
        self.assert_zero(pointer, count)
        return pointer

    def assert_zero(self, pointer, count):
        # Check all bytes, including flags padding@52 and stable values@64.
        assert self.blocks[pointer] == bytes(64 + count * 8)

    def poison_and_release(self, pointer, count, poison=0xA5):
        self.blocks[pointer][:] = bytes([poison]) * (64 + count * 8)
        self.store(pointer, 40, count, 8)
        self.call("release", pointer)

    def cached(self):
        return self.call("tls_pool_cached_count")

    def bucket_count(self, count):
        counts = self.namespace["global_load_ptr"](PREFIX + "pool_counts")
        return self.load(counts, count * 8, 8) if counts else 0

    def assert_drained(self):
        assert self.cached() == 0
        assert self.namespace["global_load_ptr"](PREFIX + "pool_heads") == 0
        assert self.namespace["global_load_ptr"](PREFIX + "pool_counts") == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("count", range(17))
def test_cached_nodes_restore_every_byte_without_a_second_zero_fill(backend, count):
    memory = PoolMemory(backend)
    first, second = memory.allocation(count), memory.allocation(count)
    for cycle in range(4):
        memory.poison_and_release(first, count, 0xA1 + cycle)
        memory.poison_and_release(second, count, 0xB1 + cycle)
        assert memory.load(second, 16, 8) == first  # Nonzero pool link.
        assert memory.cached() == memory.bucket_count(count) == 2
        attempts, zeros = memory.attempts, len(memory.zero_calls)
        popped = memory.allocation(count)
        assert popped == second
        assert memory.cached() == memory.bucket_count(count) == 1
        assert memory.allocation(count) == first
        assert memory.cached() == memory.bucket_count(count) == 0
        assert memory.attempts == attempts
        assert len(memory.zero_calls) == zeros
        first, second = second, first
    memory.poison_and_release(first, count)
    memory.poison_and_release(second, count)
    memory.call("tls_pool_drain")
    memory.assert_drained()
    assert first in memory.freed_set and second in memory.freed_set
    fresh = memory.allocation(count)
    assert fresh not in (first, second)
    memory.poison_and_release(fresh, count)
    memory.call("tls_pool_drain")


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("count", (0, 1, 16, 17, 31))
def test_fresh_zeroing_oversize_and_nonmoving_bypass(backend, count):
    memory = PoolMemory(backend)
    node = memory.allocation(count)
    assert (node, 0, 64 + count * 8) in memory.zero_calls
    memory.poison_and_release(node, count)
    pooled = backend in (3, 4) and count <= 16
    assert memory.cached() == int(pooled)
    assert (node in memory.freed_set) != pooled
    if not pooled:
        assert memory.attempts == 1  # No TLS metadata allocation.
    memory.call("tls_pool_drain")
    memory.assert_drained()


@pytest.mark.parametrize("backend", (3, 4))
def test_pool_capacity_spans_all_buckets_and_drain_is_idempotent(backend):
    memory = PoolMemory(backend)
    nodes = [(memory.allocation(index % 17), index % 17) for index in range(1025)]
    for node, count in nodes:
        memory.poison_and_release(node, count)
    assert memory.cached() == 1024
    assert memory.freed == [nodes[-1][0]]
    assert sum(memory.bucket_count(count) for count in range(17)) == 1024
    expected = {node for node, _count in nodes[:-1]}
    popped = [(memory.allocation(count), count) for _node, count in nodes[:-1]]
    assert {node for node, _count in popped} == expected
    assert memory.cached() == 0
    for node, count in popped:
        memory.poison_and_release(node, count)
    memory.call("tls_pool_drain")
    memory.assert_drained()
    assert expected <= memory.freed_set
    freed = list(memory.freed)
    memory.call("tls_pool_drain")
    memory.call("release", 0)
    assert memory.freed == freed


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("failed_call", (1, 2, 3))
def test_allocation_failure_preserves_partial_metadata_and_recovers(backend, failed_call):
    memory = PoolMemory(backend)
    memory.fail_on.add(failed_call)
    node = memory.call("alloc", 4)
    if failed_call == 3:
        assert node == 0  # Metadata exists, fresh node allocation failed.
    else:
        # Failure to allocate either metadata array retains fresh allocation.
        memory.assert_zero(node, 4)
        memory.poison_and_release(node, 4)
        assert memory.cached() == 1
        assert memory.allocation(4) == node
        memory.poison_and_release(node, 4)
    memory.call("tls_pool_drain")
    memory.assert_drained()
    replacement = memory.allocation(4)
    memory.poison_and_release(replacement, 4)
    memory.call("tls_pool_drain")


@pytest.mark.parametrize("failed_metadata_call", (1, 2))
def test_release_metadata_failure_frees_node_without_publishing_it(failed_metadata_call):
    memory = PoolMemory(2)
    node = memory.allocation(3)
    # The transition is a controlled provider change, not a public switch test.
    memory.backend = 3
    memory.fail_on.add(memory.attempts + failed_metadata_call)
    memory.poison_and_release(node, 3)
    assert memory.cached() == 0 and node in memory.freed_set
    memory.call("tls_pool_drain")
    memory.assert_drained()


def test_fresh_node_allocation_failure_with_existing_metadata_is_empty():
    memory = PoolMemory()
    node = memory.allocation(1)
    memory.fail_on.add(memory.attempts + 1)
    assert memory.call("alloc", 1) == 0
    assert memory.cached() == memory.bucket_count(1) == 0
    memory.poison_and_release(node, 1)
    assert memory.allocation(1) == node
    memory.poison_and_release(node, 1)
    memory.call("tls_pool_drain")


@pytest.mark.parametrize("backend", range(3))
def test_nonmoving_allocation_failure_never_creates_pool_metadata(backend):
    memory = PoolMemory(backend)
    memory.fail_on.add(1)
    assert memory.call("alloc", 6) == 0
    assert memory.attempts == 1 and not memory.zero_calls
    memory.assert_drained()
    memory.call("release", 0)
    assert not memory.freed


def test_thread_local_pools_do_not_pop_or_drain_another_carriers_nodes():
    memory = PoolMemory()
    first = memory.allocation(2)
    memory.poison_and_release(first, 2)
    memory.thread = 2
    assert memory.cached() == 0
    second = memory.allocation(2)
    assert second != first
    memory.poison_and_release(second, 2)
    memory.call("tls_pool_drain")
    memory.assert_drained()
    assert first not in memory.freed_set and second in memory.freed_set
    memory.thread = 1
    assert memory.cached() == 1
    assert memory.allocation(2) == first
    memory.poison_and_release(first, 2)
    memory.call("tls_pool_drain")


def test_backend_bypass_leaves_existing_clean_cache_intact():
    memory = PoolMemory(3)
    cached = memory.allocation(5)
    memory.poison_and_release(cached, 5)
    for backend in (0, 1, 2):
        memory.backend = backend
        fresh = memory.allocation(5)
        assert fresh != cached
        memory.poison_and_release(fresh, 5)
        assert memory.cached() == 1 and fresh in memory.freed_set
    memory.backend = 4
    assert memory.allocation(5) == cached
    memory.poison_and_release(cached, 5)
    memory.call("tls_pool_drain")
