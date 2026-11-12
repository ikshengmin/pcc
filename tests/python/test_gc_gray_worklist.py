"""Persistent gray batches over unchanged production bodies and owned threads.

The host seam replaces raw memory, TLS, atomic instructions and allocation,
not the queue or shade algorithms. It rejects uninitialized/out-of-bounds and
retired-block access, and producer access after ownership is published. Host
threads exercise CAS interleaving but are not a weak-memory proof.

The integration item drives the actual compiled module in an owned runtime
with real pthread producers and graph-serialized consumers. Opaque, unmapped
address tokens deliberately do not represent live GC objects. Neither test
layer establishes collector termination, safe object-index lookup/reclamation,
C-extension handoff, full-runtime GC correctness, native-pcc1 or performance.
"""
from __future__ import annotations

import ast
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
import time

import pytest

from tests.python.runtime_body_model import gc_state_cells, load_functions


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/runtime/py/freestanding_gc_gray_worklist.py"
ROOT_OPERATIONS = ROOT / "pcc/runtime/py/freestanding_gc_root_operations.py"
PREFIX = "pcc_gc_gray_worklist_"
FUNCTIONS = {PREFIX + name for name in ("note", "flush", "take_locked", "unregister", "clear_stopped")}
LOCAL = "pcc_gc_gray_local_batch"
PUBLISHED = "pcc_gc_gray_published_batches"
PENDING = "pcc_gc_gray_pending_batches"
FAILURES = "pcc_gc_gray_worklist_allocation_failures"
REQUEST = "pcc_gc_gray_worklist_flush_requested"
FALLBACK = "pcc_gc_gray_worklist_fallback_required"
# The count-free collectors GC1, GC2 and GC4 queue their shades. Only the
# nonmoving ones (GC1/GC2) may run a slice from the queue alone; GC3 marks
# concurrently and keeps the object-list walk.
MODE = "pcc_gc_backend4_no_refcounts"
NONMOVING = "pcc_gc_nonmoving_count_free"
BACKEND = "pcc_gc_backend_selected"
EPOCH = "pcc_gc_tracing_cycle_epoch"
NEXT, BATCH_EPOCH, COUNT, READ, ITEMS, CAPACITY, BYTES = 0, 8, 16, 24, 32, 64, 544
WHITE, GRAY, BLACK, DYING = 8, 16, 32, 524288


def _tree(path=SOURCE):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


GC_STATE_CELLS = gc_state_cells()


def _install(namespace, path, names):
    """Remove only export decorators; preserve every production statement."""
    load_functions(path, names, namespace)


class _BatchMemory:
    """Exact-width memory with private TLS and explicit block ownership."""

    def __init__(self):
        self.lock = threading.RLock()
        self.graph = threading.RLock()
        self.thread = threading.local()
        self.tls = {}
        self.cells = {
            MODE: bytearray((1).to_bytes(4, "little")),
            NONMOVING: bytearray((1).to_bytes(4, "little")),
            BACKEND: bytearray((2).to_bytes(4, "little")),
            EPOCH: bytearray((17).to_bytes(8, "little")),
            PUBLISHED: bytearray(8), PENDING: bytearray(8), FAILURES: bytearray(8),
            REQUEST: bytearray(4), FALLBACK: bytearray(4),
        }
        self.blocks = {}
        self.initialized = {}
        self.owners = {}
        self.retired = set()
        self.next_address = 0x200000000
        self.allocations = []
        self.freed = []
        self.accesses = []
        self.cas_attempts = []
        self.exchanges = []
        self.before_cas = None
        self.after_epoch_load = None
        self.fail_allocations = 0
        self.headers = {}
        self.gray = 0
        self.gray_changes = []
        self.namespace = {
            "i64": int,
            "global_addr": self.global_addr,
            "global_load_ptr": self.global_load,
            "global_store_ptr": self.global_store,
            "load_i32": lambda pointer, offset: self.load(pointer, offset, 4),
            "load_i64": self.load,
            "load_ptr": self.load,
            "store_i64": self.store,
            "store_ptr": self.store,
            "atomic_load_i64": self.atomic_load,
            "atomic_cas_i64": self.cas,
            "atomic_rmw_i64": self.rmw,
            "atomic_store_i32": self.atomic_store32,
            "malloc": self.malloc,
            "free": self.free,
            "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_to_int": lambda value: value,
            "int_to_ptr": lambda value: value,
        }
        _install(self.namespace, SOURCE, FUNCTIONS)

    @property
    def tid(self):
        return getattr(self.thread, "name", threading.get_ident())

    @contextmanager
    def as_thread(self, name):
        previous = self.tid
        self.thread.name = name
        try:
            yield
        finally:
            self.thread.name = previous

    def call(self, name, *args):
        return self.namespace[PREFIX + name](*args)

    def locked_call(self, name):
        with self.graph:
            self.thread.graph_owned = True
            try:
                return self.call(name)
            finally:
                self.thread.graph_owned = False

    def take(self):
        return self.locked_call("take_locked")

    def drain(self):
        result = []
        while (address := self.take()) != 0:
            result.append(address)
            assert len(result) < 100000, "worklist failed to drain"
        return result

    def global_addr(self, name):
        if name not in self.cells and name in GC_STATE_CELLS:
            # A GC state global the model did not size starts at its declared
            # width and value; any other unknown name still fails closed.
            width, initial = GC_STATE_CELLS[name]
            self.cells[name] = bytearray(initial.to_bytes(width, "little", signed=True))
        assert name in self.cells, ("unknown global", name)
        return name

    def global_load(self, name):
        with self.lock:
            if name == LOCAL:
                return self.tls.get(self.tid, 0)
            assert name == PENDING, ("unexpected non-atomic shared load", name)
            return self.load(name, 0)

    def global_store(self, name, value):
        with self.lock:
            if name == LOCAL:
                self.tls[self.tid] = value
            else:
                assert name == PENDING
                assert getattr(self.thread, "graph_owned", False)
                self.store(name, 0, value)

    def _check(self, pointer, offset, width, *, reading):
        assert width in (4, 8)
        assert offset >= 0 and offset % width == 0
        if isinstance(pointer, str):
            data = self.cells[pointer]
        else:
            assert pointer not in self.retired, ("retired block access", pointer)
            assert pointer in self.blocks, ("dereferenced an opaque object address", pointer)
            data = self.blocks[pointer]
            owner = self.owners[pointer]
            if owner == "pending":
                assert getattr(self.thread, "graph_owned", False)
            else:
                assert owner == ("local", self.tid), ("access after publication", pointer, owner)
            if reading:
                assert set(range(offset, offset + width)) <= self.initialized[pointer]
        assert offset + width <= len(data), (pointer, offset, width, len(data))
        return data

    def load(self, pointer, offset, width=8):
        with self.lock:
            data = self._check(pointer, offset, width, reading=True)
            value = int.from_bytes(data[offset:offset + width], "little", signed=True)
            self.accesses.append((self.tid, "read", pointer, offset, width, value))
            return value

    def store(self, pointer, offset, value, width=8):
        with self.lock:
            data = self._check(pointer, offset, width, reading=False)
            data[offset:offset + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
            if not isinstance(pointer, str):
                self.initialized[pointer].update(range(offset, offset + width))
            self.accesses.append((self.tid, "write", pointer, offset, width, value))

    def scalar(self, name):
        return self.load(name, 0, len(self.cells[name]))

    def set_scalar(self, name, value):
        self.store(name, 0, value, len(self.cells[name]))

    def local(self):
        return self.tls.get(self.tid, 0)

    def local_count(self):
        return self.load(self.local(), COUNT) if self.local() else 0

    def malloc(self, size):
        with self.lock:
            assert size == BYTES
            if self.fail_allocations:
                self.fail_allocations -= 1
                self.allocations.append((self.tid, size, 0))
                return 0
            address = self.next_address
            self.next_address += 0x1000
            self.blocks[address] = bytearray(b"\xa5" * size)
            self.initialized[address] = set()
            self.owners[address] = ("local", self.tid)
            self.allocations.append((self.tid, size, address))
            return address

    def free(self, pointer):
        with self.lock:
            self._check(pointer, 0, 8, reading=True)
            assert self.owners[pointer] == "pending" or self.load(pointer, COUNT) == 0
            assert pointer not in self.tls.values(), "freed a still-owned TLS block"
            assert self.scalar(PENDING) != pointer
            self.retired.add(pointer)
            self.freed.append(pointer)

    def atomic_store32(self, pointer, offset, value, order):
        assert pointer in (REQUEST, FALLBACK) and offset == 0 and order == "release"
        assert value in (0, 1)
        self.store(pointer, offset, value, 4)

    def atomic_load(self, pointer, offset, order):
        if pointer == EPOCH:
            assert (offset, order) == (0, "acquire")
            observed = self.load(pointer, offset)
            if self.after_epoch_load is not None:
                self.after_epoch_load(observed)
            return observed
        assert (pointer, offset, order) == (PUBLISHED, 0, "relaxed")
        return self.load(pointer, offset)

    def cas(self, pointer, offset, expected, desired, success, failure):
        assert (pointer, offset, success, failure) == (PUBLISHED, 0, "release", "relaxed")
        if self.before_cas is not None:
            self.before_cas(expected, desired)
        with self.lock:
            observed = self.scalar(PUBLISHED)
            self.cas_attempts.append((self.tid, expected, desired, observed))
            if observed == expected:
                assert self.local() == 0, "TLS must relinquish before publication"
                assert 1 <= self.load(desired, COUNT) <= CAPACITY
                assert self.load(desired, READ) == 0
                assert self.load(desired, NEXT) == expected
                count = self.load(desired, COUNT)
                self.load(desired, BATCH_EPOCH)
                for index in range(count):
                    self.load(desired, ITEMS + index * 8)
                self.set_scalar(PUBLISHED, desired)
                self.owners[desired] = "published"
            return observed

    def rmw(self, operation, pointer, offset, value, order):
        with self.lock:
            observed = self.scalar(pointer)
            if operation == "add":
                assert (pointer, offset, value, order) == (FAILURES, 0, 1, "relaxed")
                self.set_scalar(pointer, observed + value)
            else:
                assert (operation, pointer, offset, value, order) == ("xchg", PUBLISHED, 0, 0, "acquire")
                assert getattr(self.thread, "graph_owned", False)
                self.exchanges.append((self.tid, observed))
                self.set_scalar(pointer, 0)
                # Model the ownership transfer at the atomic instruction only.
                # Queue traversal and reclamation still execute production code.
                address = observed
                visited = set()
                while address:
                    assert address not in visited and self.owners[address] == "published"
                    visited.add(address)
                    self.owners[address] = "pending"
                    address = int.from_bytes(self.blocks[address][NEXT:NEXT + 8], "little")
            return observed

    def install_shader(self):
        self.namespace.update({
            "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "atomic_load_i32": self.header_load,
            "atomic_cas_i32": self.header_cas,
            "pcc_gc_gray_count_increment_acq_rel": lambda: self.change_gray(1),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.change_gray(-1),
        })
        _install(self.namespace, ROOT_OPERATIONS, {"pcc_gc_shade_gray_atomic"})

    def header_load(self, obj, offset, order):
        assert offset == 12 and order == "acquire"
        return (obj if isinstance(obj, dict) else self.headers[obj])["flags"]

    def header_cas(self, obj, offset, expected, desired, success, failure):
        assert (offset, success, failure) == (12, "acq_rel", "acquire")
        header = obj if isinstance(obj, dict) else self.headers[obj]
        observed = header["flags"]
        if observed == expected:
            header["flags"] = desired
        return observed

    def change_gray(self, delta):
        self.gray += delta
        self.gray_changes.append(delta)
        assert self.gray >= 0


def test_batch_layout_exports_and_storage_owners_are_explicit():
    tree = _tree()
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id.startswith("_BATCH_")}
    assert constants == {"_BATCH_NEXT": NEXT, "_BATCH_EPOCH": BATCH_EPOCH,
                         "_BATCH_COUNT": COUNT, "_BATCH_READ": READ,
                         "_BATCH_ITEMS": ITEMS, "_BATCH_CAPACITY": CAPACITY,
                         "_BATCH_BYTES": BYTES}
    assert BYTES == ITEMS + CAPACITY * 8
    definitions = {node.value.args[0].value: node.value.func.id for node in tree.body
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id.startswith("define_")}
    assert definitions == {LOCAL: "define_thread_local_ptr_null",
                           PUBLISHED: "define_global_ptr_null",
                           PENDING: "define_global_ptr_null", FAILURES: "define_global_i64",
                           REQUEST: "define_global_i32", FALLBACK: "define_global_i32"}
    exports = {node.name: ast.literal_eval(node.decorator_list[0].args[0])
               for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert exports == {name: name for name in FUNCTIONS}


# GC3 runs the count-free nonmoving protocol with GC1/GC2 (03cbd1849): with
# the mode on only GC0 is unselected.
@pytest.mark.parametrize("backend,enabled", [(backend, 0) for backend in range(5)] + [(0, 1)])
def test_unselected_collectors_do_not_allocate_or_take(backend, enabled):
    memory = _BatchMemory()
    memory.set_scalar(BACKEND, backend)
    memory.set_scalar(MODE, enabled)
    memory.call("note", 0xDEAD0000)
    assert memory.take() == 0
    assert not memory.allocations and not memory.cas_attempts and not memory.exchanges
    assert memory.scalar(FAILURES) == 0


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_full_batch_publishes_exactly_at_64_without_replacement_allocation(backend):
    memory = _BatchMemory()
    memory.set_scalar(BACKEND, backend)
    tokens = [0x100000000 + index * 8 for index in range(CAPACITY + 1)]
    for token in tokens[:CAPACITY - 1]:
        memory.call("note", token)
    block = memory.local()
    assert memory.local_count() == CAPACITY - 1
    assert memory.scalar(PUBLISHED) == 0 and len(memory.allocations) == 1
    memory.call("note", tokens[CAPACITY - 1])
    assert memory.local() == 0 and memory.scalar(PUBLISHED) == block
    assert len(memory.allocations) == len(memory.cas_attempts) == 1
    memory.call("note", tokens[-1])
    assert memory.local() != block and memory.local_count() == 1
    assert len(memory.allocations) == 2
    # Local children take priority, then the immutable published batch in order.
    assert memory.drain() == [tokens[-1], *tokens[:-1]]
    assert memory.freed == [block]
    memory.call("unregister")
    assert len(memory.freed) == 2 and memory.local() == 0


def test_empty_flush_does_not_allocate_and_reusable_empty_block_is_not_published():
    memory = _BatchMemory()
    memory.call("flush")
    assert not memory.allocations and memory.scalar(PUBLISHED) == 0
    memory.call("note", 0xDEAD0008)
    block = memory.local()
    assert memory.take() == 0xDEAD0008 and memory.local_count() == 0
    memory.call("flush")
    assert memory.local() == block and not memory.cas_attempts
    memory.call("note", 0xDEAD0010)
    assert memory.local() == block and len(memory.allocations) == 1
    assert memory.take() == 0xDEAD0010
    memory.call("unregister")
    assert memory.freed == [block] and memory.local() == 0
    memory.call("unregister")
    assert memory.freed == [block]


def test_partial_flush_is_idempotent_and_unregister_does_not_touch_published_block():
    memory = _BatchMemory()
    with memory.as_thread("producer"):
        for token in (0xBAD0008, 0xBAD0010, 0xBAD0018):
            memory.call("note", token)
        block = memory.local()
        memory.call("flush")
        snapshot = bytes(memory.blocks[block])
        accesses = len(memory.accesses)
        memory.call("flush")
        memory.call("unregister")
        assert memory.local() == 0 and memory.scalar(PUBLISHED) == block
        assert not any(item[2] == block for item in memory.accesses[accesses:])
        assert bytes(memory.blocks[block]) == snapshot and not memory.freed
    with memory.as_thread("consumer"):
        assert memory.drain() == [0xBAD0008, 0xBAD0010, 0xBAD0018]
    assert memory.freed == [block]


def test_unregister_publishes_partial_tls_and_does_not_steal_another_threads_local():
    memory = _BatchMemory()
    with memory.as_thread("first"):
        memory.call("note", 0x11110000)
        first = memory.local()
    with memory.as_thread("second"):
        assert memory.local() == 0
        memory.call("note", 0x22220000)
        second = memory.local()
        assert second != first
        memory.call("unregister")
        assert memory.local() == 0 and memory.scalar(PUBLISHED) == second
    with memory.as_thread("first"):
        assert memory.local() == first and memory.local_count() == 1
        assert memory.take() == 0x11110000
        memory.call("unregister")
    assert memory.freed == [first]
    assert memory.drain() == [0x22220000]
    assert Counter(memory.freed) == Counter([first, second])


def test_shared_partial_batch_can_move_between_consumers_and_outlives_their_tls():
    memory = _BatchMemory()
    with memory.as_thread("producer"):
        for token in (1016, 1024, 1032):
            memory.call("note", token)
        block = memory.local()
        memory.call("unregister")
    with memory.as_thread("cms"):
        assert memory.take() == 1016
        assert memory.scalar(PENDING) == block and not memory.freed
        assert memory.local() == 0
        memory.call("unregister")
    with memory.as_thread("mutator_assist"):
        assert memory.take() == 1024
        assert memory.scalar(PENDING) == block
    with memory.as_thread("stopped_world"):
        assert memory.take() == 1032
        assert memory.take() == 0
    assert memory.freed == [block] and memory.scalar(PENDING) == 0


def test_failed_publish_cas_links_to_the_observed_competitor_without_losing_either():
    memory = _BatchMemory()
    with memory.as_thread("first"):
        memory.call("note", 1008)
        first = memory.local()
    with memory.as_thread("competitor"):
        memory.call("note", 2008)
        competitor = memory.local()

    def compete(expected, desired):
        memory.before_cas = None
        assert expected == 0 and desired == first
        with memory.as_thread("competitor"):
            memory.call("flush")

    memory.before_cas = compete
    with memory.as_thread("first"):
        memory.call("flush")
    attempts = [row for row in memory.cas_attempts if row[0] == "first"]
    assert attempts == [("first", 0, first, competitor),
                        ("first", competitor, first, competitor)]
    assert memory.drain() == [1008, 2008]
    assert Counter(memory.freed) == Counter([first, competitor])


def test_consumer_can_reclaim_competing_head_while_a_producer_retries_cas():
    memory = _BatchMemory()
    with memory.as_thread("published_producer"):
        memory.call("note", 3008)
        old = memory.local()
        memory.call("flush")
    with memory.as_thread("retrying_producer"):
        memory.call("note", 4008)
        new = memory.local()

    def reclaim(expected, desired):
        memory.before_cas = None
        assert expected == old and desired == new
        with memory.as_thread("consumer"):
            assert memory.drain() == [3008]
            assert old in memory.retired

    memory.before_cas = reclaim
    with memory.as_thread("retrying_producer"):
        memory.call("flush")
    attempts = [row for row in memory.cas_attempts if row[0] == "retrying_producer"]
    assert attempts == [("retrying_producer", old, new, 0),
                        ("retrying_producer", 0, new, 0)]
    assert memory.drain() == [4008]
    assert Counter(memory.freed) == Counter([old, new])


def test_note_epoch_change_publishes_old_local_without_mixing_new_epoch():
    memory = _BatchMemory()
    memory.call("note", 5008)
    old = memory.local()
    memory.set_scalar(EPOCH, 18)
    memory.call("note", 6008)
    new = memory.local()
    assert new != old and memory.scalar(PUBLISHED) == old
    assert memory.load(new, BATCH_EPOCH) == 18 and memory.local_count() == 1
    accesses = len(memory.accesses)
    assert memory.drain() == [6008]
    assert not any(row[2] == old and row[3] >= ITEMS for row in memory.accesses[accesses:])
    assert memory.freed == [old]
    memory.call("unregister")


def test_take_discards_stale_local_without_reading_its_entries_then_reuses_storage():
    memory = _BatchMemory()
    memory.call("note", 7008)
    old = memory.local()
    memory.set_scalar(EPOCH, 18)
    accesses = len(memory.accesses)
    assert memory.take() == 0 and memory.local_count() == 0
    assert memory.local() == old and not memory.freed
    assert not any(row[2] == old and row[3] >= ITEMS for row in memory.accesses[accesses:])
    memory.call("note", 8008)
    assert memory.local() == old and memory.load(old, BATCH_EPOCH) == 18
    assert memory.take() == 8008 and len(memory.allocations) == 1
    memory.call("unregister")
    assert memory.freed == [old]


def test_epoch_change_drops_already_partial_shared_batch_and_new_publication_survives():
    memory = _BatchMemory()
    with memory.as_thread("old_producer"):
        for token in (9008, 9016, 9024):
            memory.call("note", token)
        old = memory.local()
        memory.call("flush")
    assert memory.take() == 9008 and memory.scalar(PENDING) == old
    memory.set_scalar(EPOCH, 18)
    with memory.as_thread("new_producer"):
        memory.call("note", 10008)
        memory.call("unregister")
    accesses = len(memory.accesses)
    assert memory.drain() == [10008]
    assert not any(row[2] == old and row[3] >= ITEMS for row in memory.accesses[accesses:])
    assert memory.freed[0] == old and len(memory.freed) == 2


def test_old_producer_can_publish_after_the_consumer_has_changed_epoch():
    memory = _BatchMemory()
    with memory.as_thread("delayed"):
        memory.call("note", 11008)
        old = memory.local()
    memory.set_scalar(EPOCH, 18)
    assert memory.take() == 0
    with memory.as_thread("fresh"):
        memory.call("note", 12008)
        memory.call("flush")
    with memory.as_thread("delayed"):
        memory.call("unregister")
    accesses = len(memory.accesses)
    assert memory.drain() == [12008]
    assert not any(row[2] == old and row[3] >= ITEMS for row in memory.accesses[accesses:])


def test_allocator_failure_is_counted_per_attempt_and_a_later_batch_recovers():
    memory = _BatchMemory()
    memory.fail_allocations = 2
    memory.set_scalar(FAILURES, (1 << 32) + 7)
    memory.call("note", 13008)
    memory.call("note", 13016)
    assert memory.local() == 0 and memory.scalar(PUBLISHED) == 0
    assert memory.scalar(FAILURES) == (1 << 32) + 9
    assert not memory.freed and not memory.cas_attempts
    assert memory.scalar(FALLBACK) == 1
    memory.call("note", 13024)
    assert memory.local_count() == 1 and len(memory.allocations) == 3
    assert memory.drain() == [13024]
    assert memory.scalar(FALLBACK) == 1
    memory.set_scalar(EPOCH, 18)
    assert memory.scalar(FALLBACK) == 1
    memory.locked_call("clear_stopped")
    assert memory.scalar(FALLBACK) == 0
    memory.call("unregister")


def test_actual_successful_gray_transition_survives_batch_oom_without_a_count_rollback():
    memory = _BatchMemory()
    memory.install_shader()
    memory.fail_allocations = 1
    obj = {"flags": WHITE | 4096}
    memory.namespace["pcc_gc_shade_gray_atomic"](obj, 0)
    assert obj["flags"] == GRAY | 4096
    assert memory.gray == 1 and memory.gray_changes == [1]
    assert memory.scalar(FAILURES) == 1 and memory.local() == 0
    # An already-gray observation neither allocates again nor changes the count.
    memory.namespace["pcc_gc_shade_gray_atomic"](obj, 0)
    assert memory.gray == 1 and memory.gray_changes == [1]
    assert len(memory.allocations) == 1 and memory.scalar(FAILURES) == 1


def test_opaque_dangling_duplicate_addresses_are_never_dereferenced_by_the_queue():
    memory = _BatchMemory()
    tokens = [0x7FFFDEAD0000, 0x7FFFDEAD0000, 0x100000008]
    assert not set(tokens) & set(memory.blocks)
    with memory.as_thread("producer"):
        for token in tokens:
            memory.call("note", token)
        memory.call("unregister")
    assert memory.drain() == tokens
    assert not any(row[2] in tokens for row in memory.accesses)
    # Membership/gray rechecking and duplicate retirement belong to the real
    # consumer, not to this intentionally opaque queue API.


def test_real_host_threads_have_private_tls_and_forced_mpsc_cas_contention():
    memory = _BatchMemory()
    barrier = threading.Barrier(2, timeout=5)
    first_attempts = set()
    first_lock = threading.Lock()
    failures = []

    def contend(expected, desired):
        with first_lock:
            first = memory.tid not in first_attempts
            first_attempts.add(memory.tid)
        if first:
            assert expected == 0
            barrier.wait()

    memory.before_cas = contend

    def produce(number):
        try:
            with memory.as_thread(number):
                assert memory.local() == 0
                for index in range(CAPACITY + 7):
                    memory.call("note", 0x300000000 + (number * 1000 + index) * 8)
                memory.call("unregister")
                assert memory.local() == 0
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=produce, args=(number,), daemon=True)
               for number in range(2)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 7
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    assert not any(thread.is_alive() for thread in threads), "MPSC producer failed to finish"
    assert not failures, failures
    assert any(expected != observed for _, expected, _, observed in memory.cas_attempts)
    expected = [0x300000000 + (number * 1000 + index) * 8
                for number in range(2) for index in range(CAPACITY + 7)]
    assert Counter(memory.drain()) == Counter(expected)
    assert len(memory.freed) == len(memory.allocations) == 4
    assert len(set(memory.freed)) == 4


def test_clear_stopped_drops_local_pending_and_published_without_reading_tokens():
    memory = _BatchMemory()
    with memory.as_thread("old_producer"):
        for token in (14008, 14016, 14024):
            memory.call("note", token)
        pending = memory.local()
        memory.call("unregister")
    assert memory.take() == 14008
    with memory.as_thread("new_producer"):
        memory.call("note", 15008)
        published = memory.local()
        memory.call("unregister")
    memory.call("note", 16008)
    local = memory.local()
    assert memory.scalar(PENDING) == pending and memory.scalar(PUBLISHED) == published
    accesses = len(memory.accesses)
    memory.locked_call("clear_stopped")
    assert memory.scalar(EPOCH) == 17
    assert memory.scalar(PUBLISHED) == memory.scalar(PENDING) == 0
    assert memory.local() == local and memory.local_count() == 0
    assert memory.load(local, READ) == 0
    assert memory.freed == [pending, published]
    assert not any(row[1] == "read" and row[2] in (local, pending, published)
                   and row[3] >= ITEMS for row in memory.accesses[accesses:])
    memory.locked_call("clear_stopped")
    assert memory.take() == 0 and memory.freed == [pending, published]
    memory.call("unregister")
    assert memory.freed == [pending, published, local]


def test_same_epoch_clear_and_actual_reshading_do_not_replay_previous_entries():
    memory = _BatchMemory()
    memory.install_shader()
    tokens = [0x600000008, 0x600000010, 0x600000018]
    memory.headers = {token: {"flags": WHITE} for token in tokens}
    shade = memory.namespace["pcc_gc_shade_gray_atomic"]
    with memory.as_thread("producer"):
        for token in tokens[:2]:
            shade(token, 0)
        memory.call("unregister")
    assert memory.take() == tokens[0]  # Leave old shared partial work.
    shade(tokens[2], 0)  # Leave old local work as well.
    assert memory.gray == 3 and memory.gray_changes == [1, 1, 1]
    memory.locked_call("clear_stopped")
    assert memory.gray == 3 and memory.gray_changes == [1, 1, 1]
    assert memory.scalar(EPOCH) == 17 and memory.take() == 0
    # This is the external whitening/reset seam, not a substitute collector.
    # clear_stopped must not change color/count; the phase owner resets them.
    for header in memory.headers.values():
        header["flags"] = WHITE
    memory.gray = 0
    memory.gray_changes.clear()
    for token in tokens:
        shade(token, 0)
        shade(token, 0)
    assert memory.gray == 3 and memory.gray_changes == [1, 1, 1]
    assert memory.drain() == list(reversed(tokens))
    assert memory.gray == 3, "the queue itself must not retire gray objects"
    memory.call("unregister")


NATIVE_SOURCE = r'''
#include "py_runtime.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_backend_selected;
extern int64_t pcc_gc_tracing_cycle_epoch;
extern int32_t pcc_gc_gray_worklist_fallback_required;
extern void *pcc_gc_gray_published_batches;
extern void pcc_gc_gray_worklist_note(void *);
extern void pcc_gc_gray_worklist_flush(void);
extern void *pcc_gc_gray_worklist_take_locked(void);
extern void pcc_gc_gray_worklist_unregister(void);
extern void pcc_gc_gray_worklist_clear_stopped(void);
extern void pcc_container_lock(void *);
extern void pcc_container_unlock(void *);
extern void pcc_container_lock_pair(void *, void *);
extern void pcc_container_unlock_pair(void *, void *);

enum { PRODUCERS = 4, CONSUMERS = 2, EACH = 4099, TOTAL = PRODUCERS * EACH };
static int32_t start, ready, done, failure;
static int64_t taken;
static int32_t seen[TOTAL];
struct Producer { int64_t id; };
static struct Producer producers[PRODUCERS];
static int64_t consumed[CONSUMERS];

static void fail(int32_t code) {
    __atomic_store_n(&failure, code, __ATOMIC_RELEASE);
}
static int failed(void) {
    return __atomic_load_n(&failure, __ATOMIC_ACQUIRE) != 0;
}
static int wait_start(void) {
    int64_t deadline = pcc_runtime_monotonic_us() + 5000000;
    __atomic_fetch_add(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&start, __ATOMIC_ACQUIRE)) {
        if (failed()) return 0;
        if (pcc_runtime_monotonic_us() >= deadline) { fail(20); return 0; }
        pcc_thread_safepoint();
    }
    return !failed();
}
static void finish_thread(void) {
    /* Raw pthread callbacks do not run the pcc_thread_start trampoline.
       Leave partial batches intact so the real unregister_current ->
       gc_thread_unregister_buffers hook must publish each producer's tail.
       The exact token count and subsequent STW also require proper detach. */
    pcc_thread_unregister_current();
}
static void *producer(void *argument) {
    struct Producer *worker = (struct Producer *)argument;
    pcc_current_thread_id();
    if (wait_start()) {
        pcc_thread_no_park_enter();
        for (int64_t index = 0; index < EACH && !failed(); index++) {
            uintptr_t token = (uintptr_t)4294967296 + (worker->id * EACH + index) * 8;
            pcc_gc_gray_worklist_note((void *)token);
            /* Include full 64-entry blocks and explicitly published partial tails. */
            if (index % 151 == 150) pcc_gc_gray_worklist_flush();
        }
        pcc_thread_no_park_exit();
    }
    finish_thread();
    __atomic_fetch_add(&done, 1, __ATOMIC_RELEASE);
    return 0;
}
static void *consumer(void *argument) {
    int64_t id = (int64_t)(uintptr_t)argument;
    pcc_current_thread_id();
    if (!wait_start()) { finish_thread(); return 0; }
    int64_t deadline = pcc_runtime_monotonic_us() + 10000000;
    while (!failed()) {
        pcc_py_gc_minor_graph_lock();
        uintptr_t token = (uintptr_t)pcc_gc_gray_worklist_take_locked();
        if (token) {
            if (token < (uintptr_t)4294967296 || (token - (uintptr_t)4294967296) % 8) {
                fail(21);
            } else {
                int64_t index = (int64_t)((token - (uintptr_t)4294967296) / 8);
                if (index >= TOTAL) fail(22);
                else if (seen[index] != 0) fail(23);
                else { seen[index] = 1; taken++; consumed[id]++; }
            }
        }
        int complete = taken == TOTAL && __atomic_load_n(&done, __ATOMIC_ACQUIRE) == PRODUCERS;
        pcc_py_gc_minor_graph_unlock();
        if (complete) break;
        if (pcc_runtime_monotonic_us() >= deadline) { fail(24); break; }
    }
    finish_thread();
    return 0;
}
static void *take_one(void *unused) {
    (void)unused;
    pcc_current_thread_id();
    pcc_py_gc_minor_graph_lock();
    void *token = pcc_gc_gray_worklist_take_locked();
    pcc_py_gc_minor_graph_unlock();
    finish_thread();
    return token;
}
static int expect_take(uintptr_t expected, int code) {
    pcc_py_gc_minor_graph_lock();
    uintptr_t actual = (uintptr_t)pcc_gc_gray_worklist_take_locked();
    pcc_py_gc_minor_graph_unlock();
    return actual == expected ? 0 : code;
}
static int requested_release_boundaries(void) {
    static char first[64], second[64];
    for (int mode = 0; mode < 4; mode++) {
        uintptr_t token = (uintptr_t)0x52345008 + mode * 8;
        __atomic_store_n(&pcc_gc_gray_worklist_flush_requested, 0, __ATOMIC_RELEASE);
        if (mode == 0) {
            pcc_thread_no_park_enter();
            pcc_thread_no_park_enter();
        } else if (mode == 1) {
            pcc_py_gc_minor_graph_lock();
            pcc_py_gc_minor_graph_lock();
        } else if (mode == 2) {
            pcc_container_lock(first);
        } else {
            pcc_container_lock_pair(first, second);
        }
        pcc_gc_gray_worklist_note((void *)token);
        __atomic_store_n(&pcc_gc_gray_worklist_flush_requested, 1, __ATOMIC_RELEASE);
        int error = 0;
        /* The real safepoint must suppress even publication within a lease.
           Finish the lease before reporting a failed observation. Read the
           raw head's complete uintptr_t bits, as the runtime's i64 atomics
           do; a narrow non-null test could miss a nonzero high half. */
        pcc_thread_safepoint();
        if (__atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches, __ATOMIC_ACQUIRE)) error = 50;
        if (mode == 0) {
            pcc_thread_no_park_exit();
            if (__atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches, __ATOMIC_ACQUIRE)) error = 51;
            pcc_thread_no_park_exit();
        } else if (mode == 1) {
            pcc_py_gc_minor_graph_unlock();
            if (__atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches, __ATOMIC_ACQUIRE)) error = 52;
            pcc_py_gc_minor_graph_unlock();
        } else if (mode == 2) {
            pcc_container_unlock(first);
        } else {
            pcc_container_unlock_pair(first, second);
        }
        if (error) return error;
        if (!__atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches, __ATOMIC_ACQUIRE)) return 53;
        if (__atomic_load_n(&pcc_gc_gray_worklist_flush_requested, __ATOMIC_ACQUIRE) != 1) return 54;
        if (pcc_thread_stop_requested != 0 || pcc_thread_owns_stopped_world()) return 55;
        __atomic_store_n(&pcc_gc_gray_worklist_flush_requested, 0, __ATOMIC_RELEASE);
        int check = expect_take(token, 56);
        if (check) return check;
        check = expect_take(0, 57);
        if (check) return check;
    }
    /* A stopped clear, not an epoch change alone, clears both controls. */
    __atomic_store_n(&pcc_gc_gray_worklist_flush_requested, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_gc_gray_worklist_fallback_required, 1, __ATOMIC_RELEASE);
    if (pcc_stop_the_world() != 0) return 58;
    pcc_gc_gray_worklist_clear_stopped();
    int invalid = __atomic_load_n(&pcc_gc_gray_worklist_flush_requested, __ATOMIC_ACQUIRE)
        || __atomic_load_n(&pcc_gc_gray_worklist_fallback_required, __ATOMIC_ACQUIRE);
    int resumed = pcc_resume_world();
    if (invalid || resumed != 0) return 59;
    return 0;
}
static int sequential_boundaries(void) {
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x12345008);
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x12345010);
    int check = expect_take((uintptr_t)0x12345010, 30);
    if (check) return check;
    check = expect_take((uintptr_t)0x12345008, 31);
    if (check) return check;
    check = expect_take(0, 32);
    if (check) return check;
    pcc_gc_gray_worklist_unregister();
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x22345008);
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x22345010);
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x22345018);
    pcc_gc_gray_worklist_flush();
    pthread_t helper;
    void *result = 0;
    if (pthread_create(&helper, 0, take_one, 0) != 0) return 33;
    if (pthread_join(helper, &result) != 0 || (uintptr_t)result != (uintptr_t)0x22345008) return 34;
    check = expect_take((uintptr_t)0x22345010, 35);
    if (check) return check;
    check = expect_take((uintptr_t)0x22345018, 36);
    if (check) return check;
    check = expect_take(0, 37);
    if (check) return check;
    /* No worker exists while changing epoch or clearing the stopped queues. */
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x32345008);
    pcc_gc_tracing_cycle_epoch++;
    check = expect_take(0, 38);
    if (check) return check;
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x42345008);
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x42345010);
    pcc_gc_gray_worklist_flush();
    check = expect_take((uintptr_t)0x42345008, 39);
    if (check) return check;
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x42345018);
    pcc_gc_gray_worklist_flush();
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x42345020);
    if (pcc_stop_the_world() != 0) return 40;
    pcc_gc_gray_worklist_clear_stopped();
    check = pcc_gc_gray_worklist_take_locked() ? 41 : 0;
    int resumed = pcc_resume_world();
    if (check) return check;
    if (resumed != 0) return 42;
    pcc_gc_gray_worklist_note((void *)(uintptr_t)0x42345020);
    check = expect_take((uintptr_t)0x42345020, 43);
    if (check) return check;
    check = expect_take(0, 44);
    if (check) return check;
    pcc_gc_gray_worklist_unregister();
    return 0;
}
static int run_workers(void) {
    pthread_t producer_threads[PRODUCERS], consumer_threads[CONSUMERS];
    int producers_started = 0, consumers_started = 0;
    for (int i = 0; i < PRODUCERS; i++) {
        producers[i].id = i;
        if (pthread_create(&producer_threads[i], 0, producer, &producers[i]) != 0) {
            fail(3);
            break;
        }
        producers_started++;
    }
    if (!failed()) {
        for (int i = 0; i < CONSUMERS; i++) {
            if (pthread_create(&consumer_threads[i], 0, consumer, (void *)(uintptr_t)i) != 0) {
                fail(4);
                break;
            }
            consumers_started++;
        }
    }
    int64_t deadline = pcc_runtime_monotonic_us() + 5000000;
    while (!failed() && __atomic_load_n(&ready, __ATOMIC_ACQUIRE) != PRODUCERS + CONSUMERS) {
        if (pcc_runtime_monotonic_us() >= deadline) { fail(5); break; }
        pcc_thread_safepoint();
    }
    /* Also release startup waiters on failure, then join every created
       thread before returning. Never start STW while raw joins are pending. */
    __atomic_store_n(&start, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < producers_started; i++) {
        void *result = 0;
        if (pthread_join(producer_threads[i], &result) != 0 || result) fail(6);
    }
    for (int i = 0; i < consumers_started; i++) {
        void *result = 0;
        if (pthread_join(consumer_threads[i], &result) != 0 || result) fail(7);
    }
    return __atomic_load_n(&failure, __ATOMIC_ACQUIRE);
}
static int finish_main(int status) {
    if (pcc_thread_owns_stopped_world()) {
        int resumed = pcc_resume_world();
        if (!status) status = 47;
        if (resumed != 0) return status;
    }
    finish_thread();
    return status;
}
int main(int argc, char **argv) {
    if (argc != 2 || pcc_threads_enabled() != 1) return 1;
    int backend = argv[1][0] - '0';
    if (backend != 1 && backend != 2 && backend != 4) return 2;
    pcc_current_thread_id();
    /* Set the real queue controls quiescently; no collector is started here. */
    pcc_gc_backend4_no_refcounts = 1;
    pcc_gc_backend_selected = backend;
    pcc_gc_tracing_cycle_epoch = 17;
    int initial = sequential_boundaries();
    if (initial) return finish_main(initial);
    initial = requested_release_boundaries();
    if (initial) return finish_main(initial);
    int ran = run_workers();
    if (ran) return finish_main(ran);
    if (taken != TOTAL || consumed[0] + consumed[1] != TOTAL) return finish_main(8);
    for (int i = 0; i < TOTAL; i++) if (seen[i] != 1) return finish_main(9);
    int check = expect_take(0, 10);
    if (check) return finish_main(check);
    /* Both consumer and producer callbacks have detached, not just returned
       to pthread. This second stop catches any leftover RUNNING records. */
    if (pcc_stop_the_world() != 0) return finish_main(45);
    if (pcc_resume_world() != 0) return finish_main(46);
    int finished = finish_main(0);
    if (finished) return finished;
    printf("GRAY_WORKLIST_OK backend=%d producers=%d consumers=%d items=%d\n",
           backend, PRODUCERS, CONSUMERS, TOTAL);
    return 0;
}
'''


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_owned_native_gray_batches_mpsc_tls_and_consumer_handoff(
    tmp_path, threaded_pcc_runtime_archive,
):
    """One build, serial backend-1/2 executions; explicit current-archive input."""
    from tests.owned_c_execution import compile_owned_c_with_runtime
    from tests.python.process_timeout import run_process_group_timeout

    assert _linux_x86_64(), "the native worklist harness requires x86-64 Linux"
    closure = _assert_native_owner_closure(threaded_pcc_runtime_archive)
    source = tmp_path / "gray_worklist_threads.c"
    source.write_text(NATIVE_SOURCE, encoding="utf-8")
    binary = tmp_path / "gray_worklist_threads"
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive)
    receipt = {
        "owner_closure": closure,
        "worklist_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(threaded_pcc_runtime_archive.read_bytes()).hexdigest(),
        "runs": [],
    }
    for backend in (1, 2):
        result = run_process_group_timeout(
            [str(binary), str(backend)], timeout=20,
            env=dict(os.environ, PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic"),
        )
        (tmp_path / f"gc{backend}.stdout").write_text(result.stdout)
        (tmp_path / f"gc{backend}.stderr").write_text(result.stderr)
        receipt["runs"].append({"backend": backend, "returncode": result.returncode,
                                "stdout": result.stdout, "stderr": result.stderr})
        (tmp_path / "gray-worklist-threads.json").write_text(json.dumps(receipt, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (
            0, f"GRAY_WORKLIST_OK backend={backend} producers=4 consumers=2 items=16396\n", "",
        ), backend


class _RawCollectorMemory(_BatchMemory):
    """Object/index/slot providers for real queue, shade and common drains.

    All queue entries and object pointers are raw addresses. Index lookup is
    a provider; membership checking and its header access order are production
    code. Objects can be unmapped and their exact addresses reused.
    """

    def __init__(self):
        super().__init__()
        from tests.python.test_gc_trace_workstack import COLOR_FUNCTIONS, COMMON_FUNCTIONS

        self.heap = {}
        self.dead_objects = set()
        self.object_data = {}
        self.index = {}
        self.index_lookups = []
        self.traces = Counter()
        self.increfs = Counter()
        self.next_object = 0x600000000
        self.next_node = 0x700000000
        self.last_node = 0
        self.cells.update({
            "pcc_gc_trace_stack": bytearray(4096 * 8),
            "pcc_gc_trace_stack_count": bytearray(8),
            "pcc_gc_trace_stack_owner": bytearray(4),
            "pcc_gc_list_container_mode": bytearray(4),
            "pcc_gc_trace_cext_pending_epoch": bytearray(8),
            "pcc_gc_trace_cext_pending_backend": bytearray(8),
            "pcc_gc_object_head": bytearray(8),
            "pcc_gc_trace_cext_pending_obj": bytearray(8),
        })
        self.namespace.update({
            "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "load_i32": lambda pointer, offset: self.load(pointer, offset, 4),
            "store_i32": lambda pointer, offset, value: self.store(pointer, offset, value, 4),
            "atomic_load_i32": self.atomic_load32,
            "atomic_cas_i32": self.atomic_cas32,
            "ptr_eq": lambda first, second: int(first == second),
            "is_tagged_int": lambda pointer: int(bool(pointer & 1)),
            "pcc_gc_gray_count_increment_acq_rel": lambda: self.change_gray(1),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.change_gray(-1),
            "pcc_gc_gray_count_load_acquire": lambda: self.gray,
            "pcc_gc_object_index_find": self.find,
            "pcc_gc_object_node_is_active": lambda node: int(self.load(node, 32) == 0),
            "pcc_gc_forwarding_index_find": lambda obj: 0,
            "pcc_gc_load_ptr": lambda owner, slot: slot[0],
            "pcc_gc_visit_object_slots": self.visit,
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == 10001),
            "py_incref": lambda obj: self.increfs.update([obj]),
        })
        _install(self.namespace, ROOT_OPERATIONS,
                 COLOR_FUNCTIONS | {"pcc_gc_object_is_known_no_lock"})
        _install(self.namespace, SOURCE.with_name("freestanding_gc_common_mark_cycle.py"),
                 COMMON_FUNCTIONS)

    def _check(self, pointer, offset, width, *, reading):
        if pointer in self.heap:
            assert pointer not in self.dead_objects, ("dereferenced freed object", pointer)
            data = self.heap[pointer]
            assert width in (4, 8) and offset >= 0 and offset % width == 0
            assert offset + width <= len(data)
            if reading:
                assert set(range(offset, offset + width)) <= self.initialized[pointer]
            return data
        return super()._check(pointer, offset, width, reading=reading)

    def global_load(self, name):
        if name in (LOCAL, PENDING):
            return super().global_load(name)
        assert name in ("pcc_gc_object_head", "pcc_gc_trace_cext_pending_obj")
        return self.load(name, 0)

    def global_store(self, name, value):
        if name in (LOCAL, PENDING):
            return super().global_store(name, value)
        assert name in ("pcc_gc_object_head", "pcc_gc_trace_cext_pending_obj")
        self.store(name, 0, value)

    def atomic_load32(self, pointer, offset, order):
        assert order == "acquire"
        return self.load(pointer, offset, 4)

    def atomic_cas32(self, pointer, offset, expected, desired, success, failure):
        assert (success, failure) == ("acq_rel", "acquire")
        observed = self.load(pointer, offset, 4)
        if observed == expected:
            self.store(pointer, offset, desired, 4)
        return observed

    def object(self, name, *, address=None, cext=False):
        if address is None:
            address = self.next_object
            self.next_object += 0x1000
        assert address not in self.index
        self.dead_objects.discard(address)
        self.heap[address] = bytearray(16)
        self.initialized[address] = set()
        self.store(address, 8, 10001 if cext else 100, 4)
        self.store(address, 12, WHITE, 4)
        self.object_data[address] = {"name": name, "edges": [], "borrowed": []}
        node = self.next_node
        self.next_node += 0x1000
        self.heap[node] = bytearray(40)
        self.initialized[node] = set(range(40))  # Registration supplies a zeroed node.
        self.store(node, 0, address)
        if self.last_node:
            self.store(self.last_node, 16, node)
        else:
            self.set_scalar("pcc_gc_object_head", node)
        self.last_node = node
        self.index[address] = node
        return address

    def retire(self, obj):
        node = self.index.pop(obj)
        self.store(node, 32, 1)
        if self.load(obj, 12, 4) & GRAY:
            # Object retirement is an external provider, not queue retirement.
            self.change_gray(-1)
        self.dead_objects.add(obj)

    def find(self, obj):
        self.index_lookups.append(obj)
        return self.index.get(obj, 0)

    def visit(self, obj, visitor, context):
        assert obj in self.index and obj not in self.dead_objects
        assert not self.load(obj, 12, 4) & DYING
        assert self.load(obj, 8, 4) != 10001
        data = self.object_data[obj]
        self.traces[data["name"]] += 1
        for role, children in ((1, data["edges"]), (3, data["borrowed"])):
            for child in children:
                visitor({0: child}, role, context)
        return 1

    def shade(self, obj):
        self.namespace["pcc_gc_shade_gray_atomic"](obj, 0)

    def mark(self, walk):
        with self.graph:
            self.thread.graph_owned = True
            try:
                return self.namespace["pcc_gc_" + walk]()
            finally:
                self.thread.graph_owned = False


@pytest.mark.parametrize("backend", [1, 2, 4])
@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_actual_common_consumer_traces_a_queued_root_and_live_child_without_list_fallback(
    walk, backend,
):
    memory = _RawCollectorMemory()
    # GC4 queues its shades as GC1/GC2 do: a seeded root is taken from the
    # queue instead of being searched for from the object-list head.
    memory.set_scalar(BACKEND, backend)
    root, child, borrowed = [memory.object(name) for name in ("root", "child", "borrowed")]
    memory.object_data[root]["edges"] = [child, child, root]
    memory.object_data[root]["borrowed"] = [borrowed]
    memory.shade(root)
    # No object-list walk can rescue the root; only the production queue can.
    memory.set_scalar("pcc_gc_object_head", 0)
    assert memory.mark(walk) == 2
    assert memory.traces == {"root": 1, "child": 1}
    assert memory.gray == memory.scalar("pcc_gc_trace_stack_count") == 0
    assert memory.scalar("pcc_gc_trace_stack_owner") == 0
    assert memory.gray_changes == [1, 1, -1, -1]
    assert memory.load(root, 12, 4) & 56 == BLACK
    assert memory.load(child, 12, 4) & 56 == BLACK
    assert memory.load(borrowed, 12, 4) & 56 == WHITE
    memory.locked_call("clear_stopped")
    memory.call("unregister")


@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_actual_consumer_rejects_retired_address_before_header_access_or_count_retirement(walk):
    memory = _RawCollectorMemory()
    live, dead = memory.object("live"), memory.object("dead")
    memory.shade(live)
    memory.shade(dead)
    memory.retire(dead)
    memory.gray_changes.clear()
    memory.set_scalar("pcc_gc_object_head", 0)
    accesses = len(memory.accesses)
    assert memory.mark(walk) == 1
    assert memory.index_lookups == [dead, live]
    assert not any(row[2] == dead for row in memory.accesses[accesses:])
    assert memory.traces == {"live": 1} and memory.gray == 0
    assert memory.gray_changes == [-1]
    memory.locked_call("clear_stopped")
    memory.call("unregister")


@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_actual_consumer_treats_reused_address_as_current_member_and_retires_it_once(walk):
    memory = _RawCollectorMemory()
    guard, old = memory.object("guard"), memory.object("retired")
    memory.shade(guard)
    memory.shade(old)
    memory.retire(old)
    current = memory.object("current", address=old)
    memory.shade(current)
    memory.gray_changes.clear()
    memory.set_scalar("pcc_gc_object_head", 0)
    assert memory.mark(walk) == 2
    # The newer and older tokens both resolve to the current index entry. The
    # second sees BLACK and cannot retire that object's gray count again.
    assert memory.index_lookups == [current, current, guard]
    assert memory.traces == {"current": 1, "guard": 1}
    assert memory.gray_changes == [-1, -1] and memory.gray == 0
    memory.locked_call("clear_stopped")
    memory.call("unregister")


@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_actual_consumer_object_list_fallback_finishes_a_successful_shade_lost_to_oom(walk):
    memory = _RawCollectorMemory()
    root, child = memory.object("root"), memory.object("child")
    memory.object_data[root]["edges"] = [child]
    memory.fail_allocations = 1
    memory.shade(root)
    assert memory.gray == 1 and memory.scalar(FAILURES) == 1 and memory.local() == 0
    assert memory.mark(walk) == 2
    assert memory.traces == {"root": 1, "child": 1} and memory.gray == 0
    assert memory.gray_changes == [1, 1, -1, -1]
    memory.locked_call("clear_stopped")
    memory.call("unregister")


@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_gc3_shades_take_a_token_and_the_queue_alone_finds_them(walk):
    # GC3 shades like GC1/GC2 (03cbd1849): each shade publishes a token, so
    # the queue finds the root even when no object-list walk can.
    memory = _RawCollectorMemory()
    memory.set_scalar(BACKEND, 3)
    root, child = memory.object("root"), memory.object("child")
    memory.object_data[root]["edges"] = [child]
    memory.shade(root)
    assert memory.gray == 1 and memory.local_count() == 1
    memory.set_scalar("pcc_gc_object_head", 0)
    assert memory.mark(walk) == 2
    assert memory.traces == {"root": 1, "child": 1} and memory.gray == 0


@pytest.mark.parametrize("walk", ["drain_all_gray_unlocked", "drain_all_gray_locked_slice"])
def test_a_moved_objects_stale_token_is_dropped_and_the_walk_traces_its_gray_copy(walk):
    memory = _RawCollectorMemory()
    memory.set_scalar(BACKEND, 4)
    source, child = memory.object("source"), memory.object("child")
    memory.shade(source)
    # A moving collector copied the shaded object: the old address is no
    # longer a known object, and the copy carries the gray color without a
    # token of its own.
    memory.retire(source)
    copy = memory.object("copy")
    memory.object_data[copy]["edges"] = [child]
    memory.store(copy, 12, (memory.load(copy, 12, 4) & ~56) | GRAY, 4)
    memory.change_gray(1)
    lookups = len(memory.index_lookups)
    assert memory.mark(walk) == 2
    # The token was checked against the index and never dereferenced; the
    # object-list walk found the copy.
    assert source in memory.index_lookups[lookups:]
    assert memory.traces == {"copy": 1, "child": 1} and memory.gray == 0
    assert memory.load(copy, 12, 4) & 56 == BLACK
    memory.locked_call("clear_stopped")
    memory.call("unregister")


@pytest.mark.parametrize("cut", ["after_shade_cas", "after_note_epoch_load"])
def test_epoch_reset_interleavings_leave_color_authoritative_and_fallback_work_visible(cut):
    memory = _RawCollectorMemory()
    old, live = memory.object("old_white"), memory.object("live")
    resets = []

    def reset_phase():
        # Controlled reset/whitening providers. An epoch acquire/release alone
        # does not make a shader, queue append and all reset work transactional.
        assert memory.load(old, 12, 4) & 56 == GRAY
        resets.append(memory.scalar(EPOCH))
        memory.set_scalar(EPOCH, 18)
        memory.store(old, 12, WHITE, 4)
        memory.gray = 0
        memory.gray_changes.clear()

    if cut == "after_shade_cas":
        original = memory.namespace["atomic_cas_i32"]

        def cas_then_reset(*args):
            observed = original(*args)
            if not resets and args[0] == old and observed == args[2]:
                reset_phase()
            return observed

        memory.namespace["atomic_cas_i32"] = cas_then_reset
    else:
        def read_then_reset(epoch):
            memory.after_epoch_load = None
            assert epoch == 17
            reset_phase()

        memory.after_epoch_load = read_then_reset
    memory.shade(old)
    assert resets == [17] and memory.gray == 0
    old_block = memory.local()
    assert memory.load(old_block, BATCH_EPOCH) == (18 if cut == "after_shade_cas" else 17)
    memory.call("flush")
    # New work is intentionally absent from the queue. The actual fallback
    # must still find it after rejecting the old/white queue candidate.
    memory.fail_allocations = 1
    memory.shade(live)
    assert memory.gray == 1 and memory.scalar(FAILURES) == 1
    accesses = len(memory.accesses)
    assert memory.mark("drain_all_gray_locked_slice") == 1
    assert memory.traces == {"live": 1} and memory.gray == 0
    assert memory.gray_changes == [1, -1]
    if cut == "after_note_epoch_load":
        assert not any(row[1] == "read" and row[2] == old_block and row[3] >= ITEMS
                       for row in memory.accesses[accesses:])
    memory.locked_call("clear_stopped")
    memory.call("unregister")


def test_queue_cross_object_abi_is_unique_bounded_and_registered_with_correct_storage():
    from pcc.frontends.python.codegen.runtime_abi import (
        FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
        FREESTANDING_GC_I32_GLOBALS,
        FREESTANDING_GC_I64_GLOBALS,
        FREESTANDING_GC_PTR_GLOBALS,
        FREESTANDING_GC_THREAD_LOCAL_GLOBALS,
    )
    from pcc.frontends.python.owned_runtime_build import runtime_modules

    expected = {name: ((), "c_void") for name in FUNCTIONS}
    expected[PREFIX + "note"] = (("c_ptr",), "c_void")
    expected[PREFIX + "take_locked"] = ((), "c_ptr")
    for name, signature in expected.items():
        assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[name] == signature
    assert {REQUEST, FALLBACK} <= FREESTANDING_GC_I32_GLOBALS
    assert not {REQUEST, FALLBACK} & FREESTANDING_GC_THREAD_LOCAL_GLOBALS
    assert LOCAL in FREESTANDING_GC_THREAD_LOCAL_GLOBALS
    assert not {PUBLISHED, PENDING} & FREESTANDING_GC_THREAD_LOCAL_GLOBALS
    assert {PUBLISHED, PENDING} <= FREESTANDING_GC_PTR_GLOBALS
    assert FAILURES in FREESTANDING_GC_I64_GLOBALS and FAILURES not in FREESTANDING_GC_I32_GLOBALS
    module = _tree(ROOT / "pcc/frontends/python/codegen/runtime_abi.py")
    chunks = [node for node in module.body if isinstance(node, ast.FunctionDef)
              and node.name.startswith(("_cross_object_signatures_part_", "_runtime_signatures_part_"))]
    found = Counter()
    for chunk in chunks:
        literals = [node for node in ast.walk(chunk) if isinstance(node, ast.Dict)]
        assert len(literals) == 1
        names = [ast.literal_eval(key) for key in literals[0].keys]
        assert len(names) <= 50, (chunk.name, len(names))
        found.update(name for name in names if name in FUNCTIONS)
    assert found == Counter({name: 1 for name in FUNCTIONS})
    modules = runtime_modules(str(ROOT / "pcc/runtime"), "x86_64-unknown-linux-gnu", threads=True)
    assert modules.count(SOURCE.stem) == 1


def _assert_native_owner_closure(archive):
    """Inspect real ELF archive objects with owned readers, never ar/nm/cc."""
    from pcc.backend.elf_x86_64 import (
        SHN_UNDEF,
        STB_GLOBAL,
        STT_TLS,
        parse_relocatable,
        read_archive_payloads,
    )

    selected = FUNCTIONS | {LOCAL, PUBLISHED, PENDING, FAILURES, REQUEST, FALLBACK}
    owners = {name: [] for name in selected}
    users = {name: set() for name in selected}
    owner_name = SOURCE.stem + ".o"
    owner_symbols = None
    owner_imports = None
    for member, payload in read_archive_payloads(archive.read_bytes()):
        obj = parse_relocatable(payload, compact_relocations=True)
        for symbol in obj.symbols:
            if symbol.name not in selected:
                continue
            if symbol.section_index == SHN_UNDEF:
                users[symbol.name].add(member)
            elif symbol.binding == STB_GLOBAL:
                owners[symbol.name].append(member)
        if member == owner_name:
            owner_symbols = {symbol.name: symbol for symbol in obj.symbols}
            owner_imports = {symbol.name for symbol in obj.symbols
                             if symbol.name and symbol.section_index == SHN_UNDEF}
    assert all(owner == [owner_name] for owner in owners.values()), owners
    assert owner_symbols is not None and owner_symbols[LOCAL].type == STT_TLS
    assert all(owner_symbols[name].size == 8 for name in (LOCAL, PUBLISHED, PENDING, FAILURES))
    assert all(owner_symbols[name].size == 4 for name in (REQUEST, FALLBACK))
    assert not users[LOCAL], "TLS owner must be accessed through its exported queue API"
    assert owner_imports == {MODE, BACKEND, EPOCH, "malloc", "free"}
    expected_users = {
        PREFIX + "note": {"freestanding_gc_root_operations.o"},
        PREFIX + "take_locked": {"freestanding_gc_common_mark_cycle.o",
                                  "freestanding_gc_incremental_concurrent_scheduler.o"},
        PREFIX + "flush": {"freestanding_gc_common_mark_cycle.o",
                            "freestanding_gc_incremental_concurrent_scheduler.o",
                            "freestanding_thread_kernel_pthread.o"},
        PREFIX + "unregister": {"py_gc_backend.o"},
        PREFIX + "clear_stopped": {"freestanding_gc_common_mark_cycle.o",
                                    "freestanding_gc_object_root_seeding.o"},
    }
    for name, required in expected_users.items():
        assert required <= users[name], (name, required, users[name])
    return {"owner": owner_name, "exports": sorted(selected),
            "imports": sorted(owner_imports),
            "users": {name: sorted(members) for name, members in users.items()}}


@pytest.mark.parametrize("operation", ["flush", "unregister"])
def test_producer_publication_and_raw_take_never_acknowledge_other_tails(operation):
    memory = _BatchMemory()
    for producer, token in (("a", 0x6008), ("b", 0x6010)):
        with memory.as_thread(producer):
            memory.call("note", token)
    memory.set_scalar(REQUEST, 1)
    with memory.as_thread("a"):
        memory.call(operation)
    assert memory.scalar(REQUEST) == 1
    assert memory.take() == 0x6008 and memory.scalar(REQUEST) == 1
    with memory.as_thread("b"):
        assert memory.local_count() == 1
        memory.call("unregister")
    assert memory.take() == 0x6010 and memory.scalar(REQUEST) == 1
    memory.locked_call("clear_stopped")
    assert memory.scalar(REQUEST) == 0
