from pathlib import Path
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.research.confined_jdk.waitset import EventPool, Memory, WaitsetHarness


@pytest.fixture
def source():
    root = Path(os.environ.get("PCC_RESEARCH_BASELINE_SOURCE", str(ROOT)))
    return root / "pcc" / "runtime" / "py" / "freestanding_io_waitset.py"


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("pooled", [False, True])
def test_actual_prepare_finish_ready_timeout_generation(source, backend, pooled):
    harness = WaitsetHarness(source, pooled=pooled, backend=backend)
    owner = object()
    ready = harness.add(7, deadline=100)
    harness.add(8, deadline=100)
    stale = harness.add(9)
    fresh = harness.add(9)
    assert fresh != stale
    harness.kernel_events = [(7, ready, 1), (9, stale, 1)]
    batch = harness.prepare(owner)
    harness.start(batch, owner)
    assert harness.block_returned(batch, owner) == 0
    result = harness.finish(batch, owner)
    assert result == (0, ((7, 1),), (8,))
    assert harness.runtime["pcc_io_waitset_count"](harness.waitset) == 1
    assert harness.blocking_depth == 0
    harness.dispose()
    assert not harness.memory.blocks


def test_no_alias_reentry_and_stale_lease(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    first = h.prepare(owner)
    second = h.prepare(owner)
    assert first.pointer != second.pointer
    h.memory.clear(first.pointer, 113, 24)
    h.finish(first, owner, discard=True)
    reused = h.prepare(owner)
    assert reused.pointer == first.pointer
    assert h.read_lease(reused, owner) == 0
    with pytest.raises(RuntimeError, match="closed lease"):
        h.read_lease(first, owner)
    h.finish(second, owner, discard=True)
    h.finish(reused, owner, discard=True)
    h.dispose()


def test_cancel_requires_backend_return_acknowledgement(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    batch = h.prepare(owner)
    h.start(batch, owner)
    h.cancel(batch, owner)
    with pytest.raises(RuntimeError, match="backend still retains"):
        h.finish(batch, owner, discard=True)
    with pytest.raises(RuntimeError, match="outstanding lease"):
        h.dispose()
    other = h.prepare(owner)
    assert batch.pointer != other.pointer
    h.block_returned(batch, owner)
    h.finish(batch, owner, discard=True)
    h.finish(other, owner, discard=True)
    h.dispose()


def test_wrong_owner_and_task_migration(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    batch = h.prepare(owner)
    with pytest.raises(RuntimeError, match="different logical-task"):
        h.start(batch, object())
    # One logical owner continues on another actual host thread; no carrier pin.
    def migrated():
        h.start(batch, owner)
        h.block_returned(batch, owner)
        return h.finish(batch, owner)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(migrated).result() == (0, (), ())
    h.dispose()


def test_exception_at_prepared_boundary_and_allocation_failure(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    batch = h.prepare(owner)
    with pytest.raises(ValueError, match="injected"):
        try:
            raise ValueError("injected application exception")
        finally:
            h.finish(batch, owner, discard=True)
    h.pool.dispose(h.waitset)
    h.dispose()
    failed = WaitsetHarness(source, pooled=True)
    failed.memory.fail_next_event = True
    with pytest.raises(MemoryError):
        failed.prepare(owner)
    assert not failed.pool.active
    failed.dispose()


@pytest.mark.parametrize("pooled", [False, True])
def test_eintr_and_kernel_error_cleanup(source, pooled):
    h = WaitsetHarness(source, pooled=pooled)
    owner = object()
    h.kernel_results = [-4, -5]
    # EINTR observed at/after the effective deadline is an empty successful wait.
    batch = h.prepare(owner)
    h.start(batch, owner)
    assert h.block_returned(batch, owner) == 0
    assert h.finish(batch, owner) == (0, (), ())
    batch = h.prepare(owner)
    h.start(batch, owner)
    assert h.block_returned(batch, owner) == -1
    assert h.finish(batch, owner) == (-1, (), ())
    assert h.blocking_depth == 0
    h.dispose()


def test_large_capacity_reuse_retains_only_one_idle_buffer(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    for fd in range(300):
        h.add(fd)
    batch = h.prepare(owner)
    assert h.runtime["load_i64"](batch.address, 8) == 257
    assert h.memory.event_requested_bytes == 257 * 12
    h.finish(batch, owner, discard=True)
    for _ in range(5):
        batch = h.prepare(owner)
        h.finish(batch, owner, discard=True)
    assert h.memory.event_allocations == 1
    assert h.memory.event_live_bytes == 3084
    h.dispose()
    assert h.memory.event_live_bytes == 0


def test_simultaneous_pool_borrowers_are_distinct():
    from threading import Barrier
    memory = Memory()
    pool = EventPool(memory)
    barrier = Barrier(2)
    def borrow():
        pointer = pool.acquire(123, 64)
        barrier.wait(timeout=5)
        assert len(pool.active) == 2
        barrier.wait(timeout=5)
        pool.release(pointer)
        return pointer
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(borrow), executor.submit(borrow)
        assert first.result() != second.result()
    assert not pool.active and len(pool.idle) == 1
    pool.dispose(123)
    assert memory.event_live_bytes == 0


def test_copy_out_survives_reuse(source):
    h = WaitsetHarness(source, pooled=True)
    owner = object()
    results = []
    for fd in (7, 8):
        generation = h.add(fd)
        h.kernel_events = [(fd, generation, 1)]
        batch = h.prepare(owner)
        h.start(batch, owner)
        h.block_returned(batch, owner)
        results.append(h.finish(batch, owner))
    assert results == [(0, ((7, 1),), ()), (0, ((8, 1),), ())]
    h.dispose()
