"""Count/retention controls, not native timing or a five-GC qualification."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier

import pytest

from scripts.research.confined_jdk.policy import BoundedEventPool
from scripts.research.confined_jdk.waitset import EventPool, Memory, WaitsetHarness


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("budget", [-1, 1.5, "64"])
def test_invalid_budget_fails_before_allocating(budget):
    memory = Memory()
    with pytest.raises(ValueError):
        BoundedEventPool(memory, budget)
    assert not memory.blocks


def test_aggregate_idle_budget_and_pressure_trim_preserve_active_lease():
    memory = Memory()
    pool = BoundedEventPool(memory, 96)
    first, second = pool.acquire(1, 64), pool.acquire(2, 64)
    pool.release(first)
    pool.release(second)
    assert pool.retained_bytes == memory.event_live_bytes == 64
    assert pool.dropped_returns == 1
    active = pool.acquire(1, 64)
    another = pool.acquire(3, 32)
    pool.release(another)
    assert pool.trim_idle() == 32
    assert active in pool.active and memory.event_live_bytes == 64
    memory.store("<Q", active, 0, 123)
    assert memory.load("<Q", active) == 123
    with pytest.raises(RuntimeError, match="backend still retains"):
        pool.dispose(1)
    pool.release(active)
    pool.dispose(1)
    pool.dispose(2)
    pool.dispose(3)
    assert memory.event_live_bytes == 0


@pytest.mark.parametrize("budget", [0, 64])
def test_capacity_clear_happens_before_reuse_or_buffer_is_freed(budget):
    memory = Memory()
    pool = BoundedEventPool(memory, budget)
    first = pool.acquire(1, 64)
    memory.clear(first, 0xAB, 64)
    pool.release(first)
    second = pool.acquire(1, 8)
    assert memory.load("<Q", second) == 0
    assert (second == first) is (budget == 64)
    pool.release(second)
    pool.dispose(1)
    assert memory.event_live_bytes == 0


def test_concurrent_returns_obey_global_budget():
    memory = Memory()
    pool = BoundedEventPool(memory, 64)
    barrier = Barrier(2)

    def borrower(owner):
        pointer = pool.acquire(owner, 64)
        barrier.wait(timeout=5)
        pool.release(pointer)
        return pointer

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(borrower, 1), executor.submit(borrower, 2)
        assert first.result() != second.result()
    assert not pool.active and pool.retained_bytes == 64
    assert pool.dropped_returns == 1
    pool.dispose(1)
    pool.dispose(2)
    assert memory.event_live_bytes == 0


@pytest.mark.parametrize("backend,large,small", [(1, 160, 96), (2, 36, 24)])
def test_actual_waitset_highwater_then_small_has_explicit_retention_tradeoff(backend, large, small):
    root = Path(os.environ.get("PCC_RESEARCH_BASELINE_SOURCE", str(ROOT)))
    source = root / "pcc/runtime/py/freestanding_io_waitset.py"
    # Two descriptors execute the same highwater-to-idle policy boundary.
    # Removing 256 descriptors repeats the adapter's compaction O(n^2) and is
    # inappropriate for a short correctness control; maximum-sized returned
    # buffers are covered separately by the direct 16416-byte outlier case.
    observations = []
    for bounded in (False, True):
        harness = WaitsetHarness(source, pooled=True, backend=backend)
        if bounded:
            harness.pool = BoundedEventPool(harness.memory, 128)
            harness.runtime["acquire_events"] = harness.pool.acquire
            harness.runtime["release_events"] = harness.pool.release
        owner = object()
        for fd in range(2):
            harness.add(fd)
        batch = harness.prepare(owner)
        harness.finish(batch, owner, discard=True)
        for fd in range(2):
            assert harness.runtime["pcc_io_waitset_remove"](harness.waitset, fd) == 1
        batch = harness.prepare(owner)
        harness.finish(batch, owner, discard=True)
        observations.append((harness.memory.event_allocations, harness.memory.event_live_bytes))
        harness.dispose()
        assert not harness.memory.blocks
    assert observations[0] == (1, large)
    # A bounded policy pays another allocation to relinquish a kqueue highwater;
    # epoll already fits this budget and does not acquire a fictitious new win.
    assert observations[1] == ((2, small) if large > 128 else (1, large))


def test_duplicate_return_is_rejected_without_damaging_idle_memory():
    memory = Memory()
    pool = BoundedEventPool(memory, 64)
    pointer = pool.acquire(1, 64)
    pool.release(pointer)
    with pytest.raises(KeyError):
        pool.release(pointer)
    assert pool.retained_bytes == 64 and memory.event_live_bytes == 64
    pool.dispose(1)
    assert not memory.blocks


def test_outlier_return_does_not_evict_existing_small_idle_buffer():
    memory = Memory()
    pool = BoundedEventPool(memory, 64)
    small = pool.acquire(1, 32)
    outlier = pool.acquire(1, 16416)
    pool.release(small)
    pool.release(outlier)
    assert pool.idle[1] == (small, 32)
    assert pool.dropped_returns == 1 and memory.event_live_bytes == 32
    pool.dispose(1)


def test_concurrent_close_and_return_never_free_active_storage():
    memory = Memory()
    pool = BoundedEventPool(memory, 64)
    pointer = pool.acquire(1, 64)
    barrier = Barrier(2)

    def close():
        barrier.wait(timeout=5)
        try:
            pool.dispose(1)
        except RuntimeError as error:
            assert "backend still retains" in str(error)
            return "blocked"
        return "closed"

    def release():
        barrier.wait(timeout=5)
        pool.release(pointer)

    with ThreadPoolExecutor(max_workers=2) as executor:
        closing, returning = executor.submit(close), executor.submit(release)
        outcome = closing.result()
        returning.result()
    assert not pool.active
    assert memory.event_live_bytes == (64 if outcome == "blocked" else 0)
    pool.dispose(1)
    with pytest.raises(RuntimeError, match="closed waitset"):
        pool.acquire(1, 8)
    assert not memory.blocks


def test_negative_control_raw_pointer_cannot_distinguish_stale_return_after_reuse():
    memory = Memory()
    pool = BoundedEventPool(memory, 64)
    old_pointer = pool.acquire(1, 64)
    pool.release(old_pointer)
    current_pointer = pool.acquire(1, 64)
    assert current_pointer == old_pointer
    # The raw allocator API has no generation token. This operation is forbidden
    # by the external linear-lease protocol, but the pool alone cannot reject it.
    # This passing negative control is NOT an ABA-safety qualification.
    pool.release(old_pointer)
    assert current_pointer not in pool.active
    pool.dispose(1)
    assert not memory.blocks
