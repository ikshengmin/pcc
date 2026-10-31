"""Actual tracking functions must preserve concurrent non-TRACKED updates.

The table lock owns list/index membership, not the graph-lock color changes
or atomic pin/lifetime flags. These are bounded host interleavings, not native
collector or object-lifetime qualification.
"""

from __future__ import annotations

import ast

import pytest

from tests.python.test_gc_mark_color_atomic import (
    BLACK, DYING, GRAY, PINNED, RUNTIME, HeaderMemory, _i32, _load,
)


pytestmark = pytest.mark.xdist_group("gc_tracking_atomic")
TRACKING = RUNTIME / "freestanding_gc_tracking.py"
TRACKED = 2
UPDATES = ("black", "pin", "unpin", "dying", "high_bit")


def _layout_constants():
    tree = ast.parse(TRACKING.read_text(encoding="utf-8"))
    return {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }


class TrackingState:
    """Header interleavings with one selected shard and stubbed lock admission.

    Production list/pool transitions and atomic counter operations execute.
    Selecting a shard and acquiring it are explicit model boundaries; native
    shard/collector concurrency belongs to the separate runtime C matrix.
    """

    def __init__(self, initial, update, *, inserted=1, owner=False, present=True,
                 allocation=True, backend=1, flag_mode=0):
        self.memory = HeaderMemory(initial)
        self.namespace = self.memory.namespace()
        self.layout = _layout_constants()
        self.namespace.update(self.layout)
        self.namespace["c_ptr"] = object
        self.update = update
        self.owner = owner
        self.inserted = inserted
        self.present = present
        self.allocation = allocation
        self.shard_index = 3
        self.node = {
            self.layout["_NODE_OBJECT"]: self.memory.obj,
            self.layout["_NODE_GC_REFS"]: 0,
            self.layout["_NODE_REACHABLE"]: 0,
            self.layout["_NODE_SHARD"]: self.shard_index,
            self.layout["_NODE_PREV"]: None,
            self.layout["_NODE_NEXT"]: None,
        }
        head = self.node if initial & TRACKED else None
        self.shard = {
            self.layout["_SHARD_LOCK"]: 0,
            self.layout["_SHARD_HEAD"]: None if owner else head,
            self.layout["_SHARD_POOL"]: None,
            self.layout["_SHARD_POOL_COUNT"]: 0,
        }
        self.globals = {
            "pcc_gc_table_lock_owner_token": {0: 99 if owner else 0},
            "pcc_gc_tracked_node_pool": None,
            "pcc_gc_tracked_node_pool_count": {0: 0},
            "py_gc_head": head if owner else None,
            "py_gc_tracked_count": {0: 1 if initial & TRACKED else 0},
            "pcc_gc_deferred_node_free_head": None,
            "pcc_gc_tracking_flag_mode": {0: flag_mode},
            "pcc_gc_tracking_materialized": {0: 0},
        }
        self.events = []
        self.expected_nontracked = initial & ~TRACKED
        self.namespace.update({
            "ptr_is_null": lambda obj: int(obj is None),
            "is_tagged_int": lambda obj: 0,
            "null": lambda: None,
            "pcc_threads_enabled": lambda: 1,
            "gc_backend_current": lambda: backend,
            "atomic_load_i64": self.load_owner,
            "atomic_load_i32": self.load32,
            "atomic_cas_i32": self.cas32,
            "atomic_rmw_i32": self.rmw32,
            "pcc_current_native_thread_token": lambda: 99,
            "ptr_diff": lambda ptr, base: ptr,
            "_tracked_shard_address": self.shard_address,
            "_tracked_shard_index": self.select_shard,
            "_tracked_shard_lock": self.lock_shard,
            "_tracked_shard_unlock": self.unlock_shard,
            "global_addr": lambda name: self.globals[name],
            "global_load_ptr": lambda name: self.globals[name],
            "global_store_ptr": lambda name, value: self.globals.__setitem__(name, value),
            "load_i32": lambda obj, offset: obj[offset],
            "load_ptr": lambda obj, offset: obj[offset],
            "store_i32": lambda obj, offset, value: obj.__setitem__(offset, _i32(value)),
            "store_i64": lambda obj, offset, value: obj.__setitem__(offset, value),
            "store_ptr": lambda obj, offset, value: obj.__setitem__(offset, value),
            "malloc": self.allocate,
            "free": lambda node: self.events.append("free"),
            "py_gc_index_insert": self.insert,
            "py_gc_index_remove": self.remove,
        })
        for name in ("_tracked_table_owned_by_current", "_flag_only_tracking",
                     "_tracked_growth", "_tracked_node_pool_take",
                     "_tracked_node_recycle", "pcc_gc_default_unlink_tracked_node",
                     "py_gc_track", "py_gc_untrack"):
            _load(TRACKING, name, self.namespace)

    def head(self):
        return (self.globals["py_gc_head"] if self.owner
                else self.shard[self.layout["_SHARD_HEAD"]])

    def pool(self):
        return (self.globals["pcc_gc_tracked_node_pool"] if self.owner
                else self.shard[self.layout["_SHARD_POOL"]])

    def shard_address(self, index):
        assert index == self.shard_index
        return self.shard

    def select_shard(self, obj):
        assert obj is self.memory.obj
        return self.shard_index

    def lock_shard(self, index):
        assert index == self.shard_index
        self.events.append("lock")

    def unlock_shard(self, index):
        assert index == self.shard_index
        self.events.append("unlock")

    def load_owner(self, slot, offset, order):
        assert slot is self.globals["pcc_gc_table_lock_owner_token"]
        assert (offset, order) == (0, "acquire")
        return slot[0]

    def counter(self, slot, offset):
        assert offset == 0
        assert any(slot is self.globals[name] for name in (
            "pcc_gc_tracked_node_pool_count", "py_gc_tracked_count",
        ))
        return slot[offset]

    def load32(self, obj, offset, order):
        if obj is self.memory.obj:
            return self.memory.load(obj, offset, order)
        assert order == "acquire"
        return self.counter(obj, offset)

    def cas32(self, obj, offset, expected, desired, order, fail_order):
        if obj is self.memory.obj:
            return self.memory.cas(obj, offset, expected, desired, order, fail_order)
        assert (order, fail_order) == ("acq_rel", "acquire")
        old = self.counter(obj, offset)
        if old == _i32(expected):
            obj[offset] = _i32(desired)
        return old

    def rmw32(self, operation, obj, offset, value, order):
        if obj is self.memory.obj:
            return self.memory.rmw(operation, obj, offset, value, order)
        assert order == "acq_rel" and operation in ("add", "sub")
        old = self.counter(obj, offset)
        obj[offset] = _i32(old + value if operation == "add" else old - value)
        return old

    def allocate(self, size):
        assert size == 40
        self.events.append("allocate")
        return self.node if self.allocation else None

    def interfere(self):
        # Both index callbacks occur after the header snapshot but before
        # membership publication. The color/pin writer does not take this lock.
        assert self.owner or self.events[0] == "lock"
        obj = self.memory.obj
        if self.update == "black":
            self.namespace["pcc_gc_set_mark_color"](obj, BLACK)
        elif self.update == "pin":
            self.memory.update(("or", PINNED))
        elif self.update == "unpin":
            self.memory.update(("and", ~PINNED))
        elif self.update == "dying":
            self.memory.update(("or", DYING))
        elif self.update == "high_bit":
            self.memory.update(("or", 0x80000000))
        else:
            raise AssertionError(self.update)
        self.expected_nontracked = obj[12] & ~TRACKED
        self.events.append("interference")

    def insert(self, obj, node):
        assert obj is self.memory.obj and node is self.node
        self.events.append("insert")
        self.interfere()
        return self.inserted

    def remove(self, obj):
        assert obj is self.memory.obj
        self.events.append("remove")
        self.interfere()
        return self.node if self.present else None

    def check_locks(self):
        assert self.events.count("lock") == (0 if self.owner else 1)
        assert self.events.count("unlock") == (0 if self.owner else 1)
        if not self.owner:
            assert self.events[0] == "lock" and self.events[-1] == "unlock"


@pytest.mark.parametrize("update", UPDATES)
@pytest.mark.parametrize("inserted", [1, 0])
@pytest.mark.parametrize("owner", [False, True])
def test_track_only_sets_membership_bit(update, inserted, owner):
    initial = GRAY if update == "pin" else GRAY | PINNED
    state = TrackingState(initial, update, inserted=inserted, owner=owner)
    state.namespace["py_gc_track"](state.memory.obj)
    assert state.memory.obj[12] == _i32(state.expected_nontracked | TRACKED)
    assert state.memory.writes[-1] == "or"
    assert state.globals["py_gc_tracked_count"][0] == (1 if inserted > 0 else 0)
    if inserted > 0:
        assert state.head() is state.node
        assert state.node[0] is state.memory.obj
    else:
        assert state.pool() is state.node
        assert state.globals["pcc_gc_tracked_node_pool_count"][0] == 1
    state.check_locks()


@pytest.mark.parametrize("update", UPDATES)
def test_flag_mode_track_is_one_header_bit_counted_once(update):
    # GC0 tracking keeps no nodes between collections: no lock, pool, index
    # or list operation, and a second track neither re-counts nor re-flags.
    initial = GRAY if update == "pin" else GRAY | PINNED
    state = TrackingState(initial, update, flag_mode=1)
    state.namespace["py_gc_track"](state.memory.obj)
    assert state.memory.obj[12] == _i32(state.expected_nontracked | TRACKED)
    assert state.globals["py_gc_tracked_count"][0] == 1
    state.namespace["py_gc_track"](state.memory.obj)
    assert state.globals["py_gc_tracked_count"][0] == 1
    assert state.events == []
    assert state.head() is None and state.pool() is None


@pytest.mark.parametrize("update", UPDATES)
def test_flag_mode_untrack_clears_the_bit_counted_once(update):
    initial = (GRAY if update == "pin" else GRAY | PINNED) | TRACKED
    state = TrackingState(initial, update, flag_mode=1)
    state.namespace["py_gc_untrack"](state.memory.obj)
    assert state.memory.obj[12] == _i32(state.expected_nontracked)
    assert state.globals["py_gc_tracked_count"][0] == 0
    state.namespace["py_gc_untrack"](state.memory.obj)
    assert state.globals["py_gc_tracked_count"][0] == 0
    assert state.events == []


@pytest.mark.parametrize("update", UPDATES)
@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("owner", [False, True])
def test_untrack_only_clears_membership_bit(update, present, owner):
    initial = (GRAY if update == "pin" else GRAY | PINNED) | TRACKED
    state = TrackingState(initial, update,
                          present=present, owner=owner)
    state.namespace["py_gc_untrack"](state.memory.obj)
    assert state.memory.obj[12] == _i32(state.expected_nontracked)
    assert state.memory.writes[-1] == "and"
    assert state.globals["py_gc_tracked_count"][0] == (0 if present else 1)
    if present:
        assert state.head() is None
        if owner:
            assert state.globals["pcc_gc_deferred_node_free_head"] is state.node
            assert state.globals["pcc_gc_tracked_node_pool_count"][0] == 0
        else:
            assert state.pool() is state.node
            assert state.globals["pcc_gc_tracked_node_pool_count"][0] == 1
    state.check_locks()


@pytest.mark.parametrize("name,initial,backend", [
    ("py_gc_track", GRAY | TRACKED, 1),
    ("py_gc_untrack", GRAY, 1),
    ("py_gc_track", GRAY, 4),
    ("py_gc_untrack", GRAY | TRACKED, 4),
])
def test_tracking_early_returns_do_not_mutate_header(name, initial, backend):
    state = TrackingState(initial, "black", backend=backend)
    state.namespace[name](state.memory.obj)
    assert state.memory.obj[12] == initial
    assert not state.memory.writes
    assert "insert" not in state.events and "remove" not in state.events
    assert state.events == ([] if backend == 4 else ["lock", "unlock"])


@pytest.mark.parametrize("allocation", [False, True])
def test_failed_tracking_does_not_publish_membership(allocation):
    state = TrackingState(GRAY | PINNED, "black", inserted=-1, allocation=allocation)
    state.namespace["py_gc_track"](state.memory.obj)
    assert state.memory.obj[12] == _i32(state.expected_nontracked)
    assert "or" not in state.memory.writes and "and" not in state.memory.writes
    assert state.globals["py_gc_tracked_count"][0] == 0
    assert state.head() is None
    state.check_locks()


def test_tracking_header_writes_are_exact_masked_atomic_operations():
    tree = ast.parse(TRACKING.read_text(encoding="utf-8"))
    constants = _layout_constants()
    assert constants["_TRACKED"] == TRACKED

    def mask_value(node):
        if isinstance(node, ast.Name):
            return constants[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return ~mask_value(node.operand)
        return ast.literal_eval(node)

    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "atomic_rmw_i32":
            if ast.unparse(node.args[1]) == "obj":
                assert ast.literal_eval(node.args[2]) == 12
                assert ast.literal_eval(node.args[4]) == "acq_rel"
                writes.append((ast.literal_eval(node.args[0]), mask_value(node.args[3])))
            else:
                # Shared accounting moved to atomics; it is not a header
                # write and must target one of the two named counter cells.
                assert ast.unparse(node.args[1]) in {
                    "global_addr('py_gc_tracked_count')",
                    "global_addr('pcc_gc_tracked_node_pool_count')",
                }
                assert ast.literal_eval(node.args[2]) == 0
                assert ast.literal_eval(node.args[0]) in ("add", "sub")
                assert ast.literal_eval(node.args[4]) == "acq_rel"
        elif node.func.id == "store_i32":
            assert ast.unparse(node.args[0]) != "obj"
    # Node-path untrack/track (two ors: tracked and already-indexed), plus the
    # flag-mode track and untrack.
    assert sorted(writes) == [("and", ~TRACKED), ("and", ~TRACKED),
                              ("or", TRACKED), ("or", TRACKED), ("or", TRACKED)]
