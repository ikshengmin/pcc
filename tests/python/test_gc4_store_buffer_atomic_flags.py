"""The production GC4 enqueue must merge concurrent root and pin updates.

This deterministic host model schedules the second carrier at an allocation
boundary. The queue writer, root publisher, shade and pin bodies are real;
membership indices, allocation and carrier scheduling are explicit doubles.
It does not establish native lifecycle safety or throughput.
"""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import gc_state_initial_values, load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WHITE, GRAY, BLACK, PINNED, OLD, REMEMBERED = 8, 16, 32, 64, 256, 512
COLOR = WHITE | GRAY | BLACK


class EnqueueRace:
    def __init__(self, pin_change="set", fail_allocation=False):
        self.memory = {(name, 0): value for name, value in gc_state_initial_values().items()}
        self.next_address = 0x1000
        self.owner, self.value, self.slot = 0x100, 0x200, 0x300
        self.pin_change = pin_change
        self.fail_allocation = fail_allocation
        self.duplicate = False
        self.publisher = False
        self.published = False
        self.no_park = 0
        self.gray = 0
        self.events = []
        self.commits = []
        self.members = set()
        self.entries = set()
        self.remembered = set()
        self.store(self.owner, 12, WHITE | OLD | (PINNED if pin_change == "clear" else 0))
        self.store("pcc_gc_mark_active", 0, 1)
        self.store("pcc_gc_forwarding_population", 0, 0)
        ns = {
            "i64": int, "c_ptr": int, "null": lambda: 0,
            "PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_GC_PINNED": PINNED,
            "ptr_is_null": lambda p: int(p == 0),
            "ptr_eq": lambda p, q: int(p == q),
            "is_tagged_int": lambda p: int(bool(p & 1)),
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_ptr": self.load, "load_i32": self.load, "load_i64": self.load,
            "store_ptr": self.store, "store_i32": self.store, "store_i64": self.store,
            "atomic_load_i32": lambda p, off, order: self.load(p, off),
            "atomic_cas_i32": self.cas, "atomic_rmw_i32": self.atomic_flags,
            "atomic_rmw_i64": self.metric,
            "frame_address": lambda: 0x400000, "ptr_to_int": int,
            "logical_shift_right_i64": lambda value, shift: value >> shift,
            "malloc": self.allocate,
            "pcc_current_thread_id": lambda: 1,
            "py_incref": lambda p: self.events.append(("retain", p)),
            # The index/remembered-set owners are separate from the flags
            # commit under test. Record their publication before that commit.
            "pcc_gc_store_buffer_slot_pending": lambda slot: int(self.duplicate),
            "_backend4_store_buffer_contains": lambda *args: int(self.duplicate),
            "pcc_gc_store_buffer_member_note": self.members.add,
            "pcc_gc_store_buffer_entry_note": lambda owner, slot: self.entries.add((owner, slot)),
            "_backend4_remembered_set_add": lambda owner, slot: self.remembered.add((owner, slot)),
            "_backend4_store_buffer_owner_fanout": lambda owner: len(self.entries),
            "_backend4_store_buffer_distinct_owners": lambda: len(self.members),
            "_backend4_store_buffer_flush_medium_locked": lambda: self.events.append(("flush",)),
            "pcc_thread_no_park_enter": lambda: self.lease(1),
            "pcc_thread_no_park_exit": lambda: self.lease(-1),
            "pcc_gc_note_store": lambda: None,
            "pcc_gc_object_index_seen_stable": lambda p: int(p == self.owner),
            "pcc_gc_gray_count_increment_acq_rel": lambda: self.count(1),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.count(-1),
            # GC4 does not use the GC1/2 worklist; the shade itself is real.
            "pcc_gc_gray_worklist_note": lambda p: self.events.append(("gray", p)),
        }
        load_functions(RUNTIME / "py_gc_backend.py", {"_backend4_store_buffer_enqueue"}, ns)
        load_functions(RUNTIME / "freestanding_gc_root_operations.py", {
            "pcc_gc_shade_gray_atomic", "pcc_gc_pin", "pcc_gc_unpin",
        }, ns)
        load_functions(RUNTIME / "py_obj.py", {"_store_root_count_free_unforwarded"}, ns)
        self.ns = ns

    def load(self, address, offset):
        return self.memory.get((address, offset), 0)

    def store(self, address, offset, value):
        self.memory[address, offset] = value

    def lease(self, delta):
        assert self.publisher
        self.no_park += delta
        assert self.no_park in (0, 1)

    def count(self, delta):
        assert self.publisher and self.no_park == 1
        self.gray += delta

    def cas(self, address, offset, expected, desired, success, failure):
        assert self.publisher and self.no_park == 1
        assert (success, failure) == ("acq_rel", "acquire")
        old = self.load(address, offset)
        if old == expected:
            self.store(address, offset, desired)
        return old

    def metric(self, operation, address, offset, value, order):
        assert self.publisher and operation in ("add", "sub") and order == "relaxed"
        old = self.load(address, offset)
        self.store(address, offset, old + (value if operation == "add" else -value))
        return old

    def atomic_flags(self, operation, address, offset, value, order):
        if self.publisher:
            assert order == "relaxed"
        else:
            assert (operation, address, offset, value, order) == (
                "or", self.owner, 12, REMEMBERED, "acq_rel",
            )
            self.assert_queue()
            self.commits.append((operation, value))
        old = self.load(address, offset)
        self.store(address, offset, old | value if operation == "or" else old & value)
        return old

    def publish(self):
        assert not self.publisher and not self.published
        self.publisher = self.published = True
        assert self.ns["_store_root_count_free_unforwarded"](0x400, self.owner) == 1
        self.ns["pcc_gc_pin" if self.pin_change == "set" else "pcc_gc_unpin"](self.owner)
        assert self.no_park == 0
        self.publisher = False

    def allocate(self, size):
        assert size == 40 and not self.publisher
        self.events.append(("allocate", size))
        self.publish()  # The old enqueue already read its flags snapshot.
        if self.fail_allocation:
            return 0
        result = self.next_address
        self.next_address += size
        return result

    def assert_queue(self):
        node = self.load("pcc_gc_backend4_store_buffer_medium_head", 0)
        assert node and [self.load(node, offset) for offset in (0, 8, 16, 24, 32)] == [
            self.owner, self.slot, self.value, 0, 1,
        ]
        assert self.load("pcc_gc_backend4_store_buffer_medium_count", 0) == 1
        assert self.members == {self.owner}
        assert self.entries == self.remembered == {(self.owner, self.slot)}

    def enqueue(self, owner=None, value=None):
        return self.ns["_backend4_store_buffer_enqueue"](
            self.owner if owner is None else owner, self.slot,
            self.value if value is None else value,
        )


@pytest.mark.parametrize("pin_change", ["set", "clear"])
def test_enqueue_preserves_concurrent_root_shade_and_pin(pin_change):
    state = EnqueueRace(pin_change)
    assert state.enqueue() == 1
    flags = state.load(state.owner, 12)
    assert flags & COLOR == GRAY
    assert flags & PINNED == (PINNED if pin_change == "set" else 0)
    assert flags & (OLD | REMEMBERED) == OLD | REMEMBERED
    assert state.gray == 1 and state.load(0x400, 0) == state.owner
    assert state.commits == [("or", REMEMBERED)]
    state.assert_queue()
    state.duplicate = True
    previous = dict(state.memory), list(state.events), list(state.commits)
    assert state.enqueue() == 0
    assert state.events == previous[1] and state.commits == previous[2]
    assert state.load(state.owner, 12) == flags
    state.assert_queue()


@pytest.mark.parametrize("pin_change", ["set", "clear"])
def test_failed_enqueue_keeps_concurrent_bits_without_publishing_work(pin_change):
    state = EnqueueRace(pin_change, fail_allocation=True)
    assert state.enqueue() == 0
    assert state.load(state.owner, 12) == GRAY | OLD | (PINNED if pin_change == "set" else 0)
    assert state.gray == 1 and state.commits == []
    assert state.members == state.entries == state.remembered == set()
    assert state.load("pcc_gc_backend4_store_buffer_medium_head", 0) == 0


@pytest.mark.parametrize("owner,value", [(0, 0x200), (1, 0x200), (0x100, 0), (0x100, 1)])
def test_invalid_enqueue_has_no_allocation_or_header_commit(owner, value):
    state = EnqueueRace()
    before = dict(state.memory)
    assert state.enqueue(owner, value) == 0
    assert state.memory == before and state.events == [] and state.commits == []


def test_full_medium_queue_has_no_header_commit():
    state = EnqueueRace()
    state.store("pcc_gc_backend4_store_buffer_medium_count", 0, 32)
    flags = state.load(state.owner, 12)
    assert state.enqueue() == 0
    assert state.events == [("flush",)] and not state.published
    assert state.load(state.owner, 12) == flags and state.commits == []
