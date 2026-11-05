"""Bound graph-locked GC3 queue traversal, including stale overflow entries.

Actual barrier, overflow scan, enqueue, reset and drain bodies run over the
existing finite raw-memory model. Slot promotion remains an explicit observed
boundary: these tests prove queue work/retirement accounting, not native object
survival, a real stopped-world handshake, concurrency, or throughput.

The stale prefix is produced by an incomplete real overflow scan followed by
successful enqueue of owners ahead of its cursor. No raw queue links or owner
REMEMBERED bits are fabricated to construct that prefix. The desired bounded
traversal assertion is intentionally not weakened to fit current behavior.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc3_telemetry_reset_remembered_work import (
    OLD,
    REMEMBERED,
    YOUNG,
    RememberedReset,
    install,
)


class CountedRememberedQueue(RememberedReset):
    def __init__(self, allocation_limit=-1):
        self.observed_nodes = set()
        self.locked_next_reads = []
        super().__init__(allocation_limit)
        install(self.ns, "freestanding_gc_generational_remembered_owners.py", {
            "pcc_gc_backend3_remembered_scan_probe_config",
        })

    def load(self, address, offset):
        # Drain reads node.next once while detaching a queue node. Cleanup
        # reads it again after unlocking; that second read is not locked work.
        if (address in self.observed_nodes and offset == 8
                and getattr(self, "depth", 0) > 0):
            self.locked_next_reads.append(address)
        return super().load(address, offset)

    def queued_nodes(self):
        result = []
        node = self.load("pcc_gc_backend3_remembered_owner_head", 0)
        while node:
            assert node not in result, "remembered queue contains a cycle"
            result.append(node)
            node = self.load(node, 8)
        return result


def stale_queue_from_real_overflow(count):
    m = CountedRememberedQueue(allocation_limit=0)
    first = m.object(OLD)
    owners = [m.object(OLD) for _ in range(count)]
    child = m.object(YOUNG)
    m.write(first, child)
    assert m.load("pcc_gc_backend3_remembered_overflow", 0) == 1
    # Process only the first graph node, leaving the other owners ahead of
    # the real scan cursor. No competing mutator is injected inside a drain.
    assert m.drain(1) == 1
    assert m.load("pcc_gc_backend3_remembered_scan_cursor", 0) != 0
    m.ns["pcc_gc_backend3_remembered_scan_probe_config"](-1)
    for owner in owners:
        m.write(owner, child)
    queued = m.queued_nodes()
    assert len(queued) == count
    assert all(m.load(owner, 12) & REMEMBERED for owner in owners)
    # This scan covers the enqueued owners and clears their remembered bits.
    # Their successful queue entries remain independently owned by the queue.
    assert m.drain(count + 1) == count + 1
    assert m.load("pcc_gc_backend3_remembered_overflow", 0) == 0
    assert m.load("pcc_gc_backend3_remembered_scan_cursor", 0) == 0
    assert all(not (m.load(owner, 12) & REMEMBERED) for owner in owners)
    assert m.queued_nodes() == queued
    assert m.promotion_requests == [first, *owners]
    m.observed_nodes = set(queued)
    return m, queued


@pytest.mark.parametrize("count,budget", [(2, 1), (17, 4), (65, 16)])
def test_live_remembered_entries_spend_the_queue_budget(count, budget):
    m = CountedRememberedQueue()
    owners = [m.object(OLD) for _ in range(count)]
    child = m.object(YOUNG)
    for owner in owners:
        m.write(owner, child)
    queued = m.queued_nodes()
    m.observed_nodes = set(queued)
    assert m.drain(budget) == budget
    assert len(m.locked_next_reads) == budget
    assert set(m.locked_next_reads) == set(queued[:budget])
    assert set(queued[:budget]) <= m.freed
    assert not (set(queued[budget:]) & m.freed)
    assert m.queued_nodes() == queued[budget:]
    assert m.depth == 0


@pytest.mark.parametrize("count,budget", [(2, 1), (17, 4), (65, 16)])
@pytest.mark.parametrize("resets", [0, 1, 2])
def test_stale_overflow_entries_spend_the_locked_queue_budget(count, budget, resets):
    m, queued = stale_queue_from_real_overflow(count)
    pending = m.pending()
    for _ in range(resets):
        m.reset()
        assert m.pending() == pending
        assert m.queued_nodes() == queued
    promotions_before = list(m.promotion_requests)
    remaining = list(queued)
    # Finite convergence is checked once mutations stop. A stale owner must
    # not be re-promoted merely to make its queue node count toward work.
    for _ in range(count + 1):
        if not remaining:
            break
        read_start = len(m.locked_next_reads)
        freed_before = set(m.freed)
        processed = m.drain(budget)
        examined = m.locked_next_reads[read_start:]
        assert len(examined) <= budget, {
            "budget": budget,
            "locked_nodes_examined": len(examined),
            "reported_processed": processed,
            "queue_nodes_before": len(remaining),
            "resets": resets,
        }
        assert examined, "pending stale queue made no progress"
        assert len(examined) == len(set(examined))
        assert examined == remaining[:len(examined)]
        assert m.freed - freed_before == set(examined)
        remaining = remaining[len(examined):]
        assert m.queued_nodes() == remaining
        assert m.promotion_requests == promotions_before
        assert m.depth == 0
    assert not remaining
    assert m.freed >= set(queued)
    assert m.queued_nodes() == []


@pytest.mark.parametrize("count,budget", [(2, 1), (17, 4), (65, 16)])
def test_stale_return_value_charges_examined_work_and_preserves_tail(count, budget):
    m, queued = stale_queue_from_real_overflow(count)
    promotions = list(m.promotion_requests)
    remaining = list(queued)
    while remaining:
        expected = min(len(remaining), budget)
        read_start = len(m.locked_next_reads)
        freed_before = set(m.freed)
        assert m.drain(budget) == expected
        assert m.locked_next_reads[read_start:] == remaining[:expected]
        assert m.freed - freed_before == set(remaining[:expected])
        remaining = remaining[expected:]
        assert m.queued_nodes() == remaining
        assert m.promotion_requests == promotions
        assert m.depth == 0
    assert m.freed >= set(queued)


def _stale_prefix_with_live_tail(count):
    m = CountedRememberedQueue(allocation_limit=0)
    first = m.object(OLD)
    owners = [m.object(OLD) for _ in range(count)]
    first_child = m.object(YOUNG)
    later_child = m.object(YOUNG)
    m.write(first, first_child)
    assert m.drain(1) == 1
    assert m.load("pcc_gc_backend3_remembered_scan_cursor", 0) != 0
    m.ns["pcc_gc_backend3_remembered_scan_probe_config"](-1)
    # All objects existed before the partial scan, so the object-list revision
    # does not restart it. Redirty the already-scanned owner, then enqueue the
    # owners ahead of the cursor. They will become a stale prefix before the
    # still-dirty tail. These remain barrier-only finite-model publications.
    m.write(first, later_child)
    for owner in owners:
        m.write(owner, later_child)
    queued = m.queued_nodes()
    assert len(queued) == count + 1 and m.load(queued[-1], 0) == first
    assert m.drain(count + 2) == count + 2
    assert m.pending()[1:] == (0, 0, 0)
    assert m.queued_nodes() == queued
    assert all(not (m.load(owner, 12) & REMEMBERED) for owner in owners)
    assert m.load(first, 12) & REMEMBERED
    m.observed_nodes = set(queued)
    return m, queued, first


@pytest.mark.parametrize("count,budget", [(2, 1), (5, 2)])
def test_stale_prefix_keeps_live_tail_for_later_bounded_drains(count, budget):
    m, queued, live_owner = _stale_prefix_with_live_tail(count)
    promotions = list(m.promotion_requests)
    remaining = list(queued)
    while remaining:
        expected = min(len(remaining), budget)
        removed = remaining[:expected]
        read_start = len(m.locked_next_reads)
        freed_before = set(m.freed)
        assert m.drain(budget) == expected
        assert m.locked_next_reads[read_start:] == removed
        assert m.freed - freed_before == set(removed)
        remaining = remaining[expected:]
        assert m.queued_nodes() == remaining
        assert m.promotion_requests == promotions + ([] if remaining else [live_owner])
        assert bool(m.load(live_owner, 12) & REMEMBERED) == bool(remaining)
        assert m.depth == 0
    assert m.freed >= set(queued)


@pytest.mark.parametrize("budget", [1, 2])
def test_unknown_owner_entries_spend_budget_without_reading_owner_headers(budget):
    m = CountedRememberedQueue()
    owners = [m.object(OLD), m.object(OLD)]
    child = m.object(YOUNG)
    for owner in owners:
        m.write(owner, child)
    queued = m.queued_nodes()
    m.observed_nodes = set(queued)
    # Explicit membership-boundary control, not a simulated native retirement.
    # The queue itself was made by real barriers; unknown owners must not have
    # their header read or receive a promotion request when detached.
    m.known.difference_update(owners)

    def reject_unknown_header(address, offset):
        assert not (address in owners and offset == 12)
        return m.load(address, offset)

    m.ns["load_i32"] = reject_unknown_header
    assert m.drain(budget) == budget
    assert m.locked_next_reads == queued[:budget]
    assert m.queued_nodes() == queued[budget:]
    assert set(queued[:budget]) <= m.freed
    assert not (set(queued[budget:]) & m.freed)
    assert m.promotion_requests == [] and m.depth == 0


@pytest.mark.parametrize("budget", [-1, 0])
def test_nonpositive_normal_drain_budget_leaves_stale_work_pending(budget):
    m, queued = stale_queue_from_real_overflow(3)
    pending, freed, promotions = m.pending(), set(m.freed), list(m.promotion_requests)
    assert m.drain(budget) == 0
    assert m.pending() == pending and m.queued_nodes() == queued
    assert m.locked_next_reads == [] and m.freed == freed
    assert m.promotion_requests == promotions and m.depth == 0


@pytest.mark.parametrize("budget", [1, 4, 16, 32])
@pytest.mark.parametrize("already_stopped", [False, True])
@pytest.mark.parametrize("count", [3, 17])
def test_real_step_charges_stale_work_before_remaining_promotion_budget(budget, already_stopped, count):
    m, queued = stale_queue_from_real_overflow(count)
    state = {"stopped": already_stopped, "stops": 0, "resumes": 0}
    root_budgets, worklist_budgets, safepoints = [], [], []

    def stop():
        assert not state["stopped"] and m.depth == 0
        state["stopped"] = True
        state["stops"] += 1
        return 0

    def resume():
        assert state["stopped"] and m.depth == 0
        state["stopped"] = False
        state["resumes"] += 1

    def roots(limit):
        assert state["stopped"] and m.depth == 0
        root_budgets.append(limit)

    def tls_root(cleanup):
        assert state["stopped"] and m.depth > 0 and m.load(cleanup, 0) == 0

    def worklist(limit):
        assert state["stopped"] and m.depth == 0 and limit > 0
        worklist_budgets.append(limit)
        return 0

    def extension_roots(visitor, context):
        assert state["stopped"] and m.depth == 0

    def safepoint():
        assert m.depth == 0 and state["stopped"] == already_stopped
        safepoints.append(True)

    # Run the actual scheduler body around the actual queue drain/cleanup.
    # Root visitors, promotion worklist, and thread lifecycle are explicit
    # observations with no roots; this is caller accounting, not native STW
    # safety or a bound on all root/extension work performed by the step.
    m.ns.update({
        "stack_alloc": m.allocate,
        "pcc_thread_owns_stopped_world": lambda: int(state["stopped"]),
        "pcc_stop_the_world": stop,
        "pcc_resume_world": resume,
        "pcc_thread_all_stopped_at_gates": lambda: 1,
        "pcc_gc_generational_promote_frame_roots": roots,
        "pcc_gc_generational_promote_scheduler_roots": roots,
        "pcc_gc_generational_promote_tls_exception_root": tls_root,
        "pcc_gc_backend3_drain_promotion_worklist": worklist,
        "pcc_capi_visit_extension_module_state_roots": extension_roots,
        "function_addr": lambda name: name,
        "pcc_thread_safepoint": safepoint,
    })
    install(m.ns, "freestanding_gc_generational_scheduler.py", {"pcc_gc_generational_step"})
    expected = min(budget, 16, len(queued))
    promotions = list(m.promotion_requests)
    assert m.ns["pcc_gc_generational_step"](budget, 0) == expected
    assert m.locked_next_reads == queued[:expected]
    assert m.queued_nodes() == queued[expected:]
    assert set(queued[:expected]) <= m.freed
    assert not (set(queued[expected:]) & m.freed)
    assert m.promotion_requests == promotions
    assert root_budgets == [min(budget, 16)] * 2
    assert worklist_budgets == ([budget - expected] if expected < budget else [])
    assert state == {"stopped": already_stopped,
                     "stops": int(not already_stopped), "resumes": int(not already_stopped)}
    assert safepoints == [True] and m.depth == 0
