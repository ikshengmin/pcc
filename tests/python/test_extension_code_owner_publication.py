"""Real owner-list functions under forced concurrent, modeled atomic operations.

This is host concurrency evidence. Native memory ordering and complete import
serialization/five-GC behavior require separate emitted-program gates.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.python.test_owned_sys_modules_cache import ExtensionMemory


class ConcurrentOwners(ExtensionMemory):
    def __init__(self, workers):
        super().__init__()
        self.guard = threading.Lock()
        self.rendezvous = threading.Barrier(workers, timeout=5)
        self.local = threading.local()
        self.collisions = 0
        self.initial_reads = []
        # Also instrument the pre-fix plain load for an honest negative control.
        self.ns["global_load_ptr"] = self.legacy_load

    def legacy_load(self, symbol):
        if symbol == "pcc_extension_code_owners":
            return self.extension_atomic_load(symbol, 0, "acquire")
        return self.globals.get(symbol)

    def extension_atomic_load(self, slot, offset, order):
        if slot != "pcc_extension_code_owners":
            return super().extension_atomic_load(slot, offset, order)
        with self.guard:
            previous = super().extension_atomic_load(slot, offset, order)
        # Every publisher first observes the same empty list. The losing CAS
        # must relink its still-private record rather than overwriting the head.
        if not getattr(self.local, "read", False):
            self.local.read = True
            self.initial_reads.append(previous)
            self.rendezvous.wait()
        return previous

    def extension_atomic_cas(self, slot, offset, expected, value, *orders):
        if slot != "pcc_extension_code_owners":
            return super().extension_atomic_cas(slot, offset, expected, value, *orders)
        with self.guard:
            previous = super().extension_atomic_cas(slot, offset, expected, value, *orders)
            if previous is not expected:
                self.collisions += 1
            return previous


@pytest.mark.parametrize("workers", [2, 4, 8])
def test_distinct_publishers_keep_every_code_owner_after_forced_head_collision(workers):
    memory = ConcurrentOwners(workers)
    names = [memory.cstr("extension_" + str(i)) for i in range(workers)]
    handles = [memory.malloc(8) for _ in names]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(memory.ns["_retain_extension_code"], name, handle)
                   for name, handle in zip(names, handles)]
        assert [future.result(timeout=10) for future in futures] == [0] * workers
    assert memory.initial_reads == [None] * workers
    assert memory.collisions >= workers - 1
    # Only publishers rendezvous. The main thread now executes the real reader.
    memory.local.read = True
    owners = [memory.ns["_find_code_owner"](name) for name in names]
    assert len(set(map(id, owners))) == workers
    assert all(owner is not None for owner in owners)
    for owner, handle in zip(owners, handles):
        assert memory.read(owner, 8) is handle
    current = memory.globals["pcc_extension_code_owners"]
    seen = []
    while current is not None:
        assert current not in seen, "owner list must remain acyclic"
        seen.append(current)
        current = memory.read(current, 16)
    assert len(seen) == workers
    assert not memory.closed


def test_owner_allocation_failure_does_not_publish_or_close_caller_handle():
    memory = ExtensionMemory()
    handle = memory.malloc(8)
    memory.ns["malloc"] = lambda size: None
    assert memory.ns["_retain_extension_code"](memory.cstr("extension"), handle) == -1
    assert memory.globals.get("pcc_extension_code_owners") is None
    assert handle.alive and not memory.closed


def test_name_allocation_failure_releases_only_private_record():
    memory = ExtensionMemory()
    name = memory.cstr("extension")
    handle = memory.malloc(8)
    records = []
    def allocate(size):
        if records:
            return None
        record = memory.malloc(size)
        records.append(record)
        return record
    memory.ns["malloc"] = allocate
    assert memory.ns["_retain_extension_code"](name, handle) == -1
    assert len(records) == 1 and not records[0].alive
    assert memory.globals.get("pcc_extension_code_owners") is None
    assert handle.alive and not memory.closed
