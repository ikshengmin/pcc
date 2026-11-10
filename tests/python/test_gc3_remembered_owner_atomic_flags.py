"""Actual GC3 remember/root/shade bodies must preserve concurrent header bits.

Deterministic host model only: intrinsics and carrier scheduling are explicit
boundaries. These checks do not establish native threads, collector liveness,
object survival or throughput. No optimization-helper body participates.
"""
from itertools import product

import pytest

from tests.python.test_gc3_telemetry_reset_remembered_work import (
    OLD, REMEMBERED, RememberedReset, install,
)


WHITE, GRAY, BLACK, PINNED, TRACKED = 8, 16, 32, 64, 2
COLOR = WHITE | GRAY | BLACK


class RememberShade(RememberedReset):
    def __init__(self, allocation="queue", boundary="before-or"):
        self.armed = False
        self.allocation = allocation
        self.boundary = boundary
        self.produced = False
        self.root_lease_depth = 0
        self.root_carrier = False
        self.gray = 0
        self.note_calls = []
        self.atomic_commits = []
        self.malloc_calls = 0
        self.cas_hook = None
        self.cas_attempts = 0
        self.before_commit = None
        super().__init__(0 if allocation == "disabled" else 1)
        self.owner = self.object(WHITE | OLD)
        self.root_slot = super().allocate(8)
        self.snapshot = self.load(self.owner, 12)
        self.store("pcc_gc_mark_active", 0, 1)
        self.store("pcc_gc_forwarding_population", 0, 0)
        self.store("pcc_gc_nonmoving_count_free", 0, 0)
        self.store("pcc_gc_backend3_remembered_scan_cursor", 0, 600)
        self.store("pcc_gc_backend3_remembered_scan_revision", 0, 17)
        self.ns.update({
            "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "atomic_cas_i32": self.cas,
            "pcc_gc_gray_count_increment_acq_rel": lambda: self.count(1),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.count(-1),
            "pcc_thread_no_park_enter": lambda: self.root_lease(1),
            "pcc_thread_no_park_exit": lambda: self.root_lease(-1),
            "pcc_gc_note_store": lambda: None,
            "pcc_gc_object_index_seen_stable": lambda obj: int(obj in self.known),
        })
        install(self.ns, "freestanding_gc_gray_worklist.py", {
            "pcc_gc_gray_worklist_note",
        })
        self.note_body = self.ns["pcc_gc_gray_worklist_note"]
        self.ns["pcc_gc_gray_worklist_note"] = self.note
        install(self.ns, "freestanding_gc_root_operations.py", {
            "pcc_gc_shade_gray_atomic",
        })
        install(self.ns, "py_obj.py", {"_store_root_count_free_unforwarded"})
        self.lock()  # Carrier A owns graph for the entire scheduled transaction.
        self.armed = True

    def allocate(self, size):
        if self.armed and size == 16:
            assert not self.root_carrier and self.depth == 1
            self.malloc_calls += 1
            if self.boundary == "malloc" and not self.produced:
                self.publish_root()
            if self.allocation == "malloc-failure":
                return 0
        return super().allocate(size)

    def root_lease(self, delta):
        assert self.root_carrier and self.depth == 1
        self.root_lease_depth += delta
        assert self.root_lease_depth in (0, 1)

    def count(self, delta):
        assert self.root_carrier and self.root_lease_depth == 1
        self.gray += delta

    def note(self, obj):
        self.note_calls.append(obj)
        # The actual GC3 body returns without a persistent queue token.
        self.note_body(obj)

    def cas(self, obj, offset, expected, desired, success, failure):
        assert self.root_carrier and self.root_lease_depth == 1
        assert (offset, success, failure) == (12, "acq_rel", "acquire")
        self.cas_attempts += 1
        if self.cas_hook is not None:
            hook, self.cas_hook = self.cas_hook, None
            self.root_carrier = False
            hook()
            self.root_carrier = True
        old = self.load(obj, offset)
        if old == expected:
            self.store(obj, offset, desired)
        return old

    def publish_root(self):
        assert not self.root_carrier and not self.produced
        self.produced = True
        self.root_carrier = True
        assert self.ns["_store_root_count_free_unforwarded"](
            self.root_slot, self.owner
        ) == 1
        assert self.root_lease_depth == 0
        self.root_carrier = False
        assert self.load(self.owner, 12) & COLOR == GRAY

    def queue_is_published(self):
        head, overflow, cursor, revision = self.pending()
        if self.allocation == "queue":
            assert head and self.load(head, 0) == self.owner
            assert self.load(head, 8) == 0 and overflow == 0
        else:
            assert (head, overflow, cursor, revision) == (0, 1, 0, 0)

    def atomic_flags(self, operation, obj, offset, value, order):
        assert not self.root_carrier and self.depth == 1
        assert (operation, obj, offset, value, order) == (
            "or", self.owner, 12, REMEMBERED, "acq_rel"
        )
        self.queue_is_published()
        if self.boundary == "before-or" and not self.produced:
            self.publish_root()
        if self.before_commit is not None:
            hook, self.before_commit = self.before_commit, None
            hook()
        old = super().atomic_flags(operation, obj, offset, value, order)
        self.atomic_commits.append((old, self.load(obj, offset)))
        if self.boundary == "after-or" and not self.produced:
            self.publish_root()
        return old

    def remember(self):
        self.ns["pcc_gc_backend3_remember_owner"](self.owner, self.snapshot)

    def assert_preserved(self):
        assert self.produced and self.root_lease_depth == 0 and self.depth == 1
        assert self.load(self.root_slot, 0) == self.owner
        assert self.load(self.owner, 12) & (COLOR | OLD | REMEMBERED) == (
            GRAY | OLD | REMEMBERED
        )
        assert self.gray == 1 and self.note_calls == [self.owner]
        assert len(self.atomic_commits) == 1
        self.queue_is_published()
        before = self.pending(), dict(self.memory), self.malloc_calls
        self.ns["pcc_gc_backend3_remember_owner"](
            self.owner, self.load(self.owner, 12)
        )
        assert (self.pending(), self.memory, self.malloc_calls) == before
        assert len(self.atomic_commits) == 1  # No duplicate node or flag update.


@pytest.mark.parametrize("allocation,boundary", [
    (allocation, boundary)
    for allocation, boundary in product(
        ("queue", "malloc-failure", "disabled"),
        ("malloc", "before-or", "after-or"),
    ) if not (allocation == "disabled" and boundary == "malloc")
])
def test_remember_preserves_real_root_shade_and_publication(allocation, boundary):
    state = RememberShade(allocation, boundary)
    state.remember()
    state.assert_preserved()
    assert state.malloc_calls == int(allocation != "disabled")
    assert state.load("pcc_gc_backend3_remembered_owner_allocation_limit", 0) == (
        1 if allocation == "malloc-failure" else 0
    )


@pytest.mark.parametrize("allocation", ["queue", "malloc-failure", "disabled"])
def test_root_shade_cas_retry_preserves_intervening_remember(allocation):
    state = RememberShade(allocation, boundary="cas-retry")
    state.cas_hook = state.remember
    state.publish_root()
    state.assert_preserved()
    assert state.cas_attempts == 2


@pytest.mark.parametrize("allocation", ["queue", "malloc-failure", "disabled"])
@pytest.mark.parametrize("initial,latest", [
    (0, PINNED | TRACKED), (PINNED | TRACKED, 0),
    (PINNED, TRACKED), (TRACKED, PINNED),
])
def test_remember_merges_current_unrelated_bits(allocation, initial, latest):
    state = RememberShade(allocation)
    state.store(state.owner, 12, state.snapshot | initial)
    state.snapshot |= initial

    def concurrent_atomic_bit_update():
        # Explicit independent atomic pin/tracking writer boundary. Merge
        # against the current shaded word, never the saved caller snapshot.
        now = state.load(state.owner, 12)
        state.store(state.owner, 12, (now & ~(PINNED | TRACKED)) | latest)

    state.before_commit = concurrent_atomic_bit_update
    state.remember()
    state.assert_preserved()
    assert state.load(state.owner, 12) & (PINNED | TRACKED) == latest


@pytest.mark.parametrize("owner,flags", [(0, 0), (1, 0), (400, OLD | REMEMBERED)])
def test_remember_noop_arguments_do_not_allocate_or_touch_flags(owner, flags):
    state = RememberedReset()
    before = dict(state.memory), state.next_address
    state.ns["pcc_gc_backend3_remember_owner"](owner, flags)
    assert (state.memory, state.next_address) == before
