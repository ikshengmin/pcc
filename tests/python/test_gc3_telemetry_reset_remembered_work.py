"""Telemetry reset must preserve GC3's pending old-to-young work.

These checks execute actual barrier, reset and remembered-owner bodies over
finite raw memory. Promotion is an observed boundary, not native collection,
object survival, reclamation, scheduling or performance evidence. GC4 epoch
helpers are explicit opaque boundaries and their behavior is not tested here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
YOUNG, OLD, REMEMBERED = 128, 256, 512


def install(namespace, filename, names):
    return load_functions(RUNTIME / filename, names, namespace)


class RememberedReset:
    def __init__(self, allocation_limit=-1):
        self.memory = {}
        self.next_address = 0x1000
        self.known = set()
        self.object_nodes = []
        self.freed = set()
        self.promotion_requests = []
        self.gc4_epoch_calls = []
        self.depth = 0
        self.store("pcc_gc_config_initialized", 0, 1)
        self.store("pcc_gc_backend_selected", 0, 3)
        self.store("pcc_gc_backend3_remembered_owner_allocation_limit", 0, allocation_limit)
        noop = lambda *args: None
        ns = {
            "i64": int, "c_ptr": int, "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "is_tagged_int": lambda value: int(bool(value & 1)),
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": lambda a, o, order: self.load(a, o),
            "atomic_load_i64": lambda a, o, order: self.load(a, o),
            "atomic_store_i64": lambda a, o, v, order: self.store(a, o, v),
            "atomic_rmw_i32": self.atomic_flags,
            "malloc": self.allocate, "free": self.free,
            "pcc_gc_pointer_is_managed": lambda obj: int(obj in self.known),
            "pcc_gc_object_is_known_no_lock": self.is_known,
            "pcc_gc_object_list_head": lambda: self.object_nodes[0] if self.object_nodes else 0,
            "pcc_gc_object_node_next": lambda node: self.load(node, 16),
            "pcc_gc_object_node_is_active": lambda node: int(self.load(node, 32) == 0),
            "pcc_gc_trace_referents_for_promotion": self.record_promotion_request,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_lock_for": self.lock_for,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "_object_graph_lock": self.lock, "_object_graph_unlock": self.unlock,
            "_init_config": noop, "_counter_global": lambda index: "counter_" + str(index),
            "pcc_gc_metric_shard_reset": noop,
            "_atomic_i32_store": lambda address, value: self.store(address, 0, value),
        }
        for name in ("_backend4_reset_store_buffer_epoch_state",
                     "_backend4_reset_remembered_set_epoch_state",
                     "_backend4_reseed_relocation_epoch_state",
                     "_backend4_clear_large_deferred_flags"):
            ns[name] = lambda name=name: self.gc4_epoch_calls.append(name)
        install(ns, "freestanding_gc_generational_remembered_owners.py", {
            "pcc_gc_backend3_remembered_owner_list_head",
            "pcc_gc_backend3_remembered_owner_list_set_head",
            "pcc_gc_backend3_remember_owner",
            "pcc_gc_backend3_clear_remembered_owners",
            "pcc_gc_backend3_finish_detached_remembered_owners",
            "pcc_gc_backend3_scan_remembered_owners",
            "pcc_gc_backend3_drain_remembered_owners",
        })
        ns["_backend3_clear_remembered_owners"] = ns["pcc_gc_backend3_clear_remembered_owners"]
        ns["_backend3_finish_detached_remembered_owners"] = ns["pcc_gc_backend3_finish_detached_remembered_owners"]
        install(ns, "freestanding_gc_barrier_dispatcher.py", {
            "_selected_backend", "_ptr_can_have_header",
            "pcc_gc_note_slot_write_barrier", "pcc_gc_note_write_barrier",
        })
        install(ns, "py_gc_backend.py", {"pcc_gc_telemetry_reset"})
        self.ns = ns

    def allocate(self, size):
        result = self.next_address
        self.next_address += max(64, size)
        return result

    def load(self, address, offset):
        assert address not in self.freed, "read from a retired remembered node"
        return self.memory.get((address, offset), 0)

    def store(self, address, offset, value):
        assert address not in self.freed, "write to a retired remembered node"
        self.memory[address, offset] = value

    def free(self, address):
        assert self.depth == 0, "remembered-node free while graph lock held"
        assert address not in self.freed
        self.freed.add(address)

    def atomic_flags(self, operation, address, offset, value, order):
        assert operation == "or" and offset == 12 and order == "acq_rel"
        assert self.depth > 0 and address in self.known
        before = self.load(address, offset)
        self.store(address, offset, before | value)
        return before

    def lock_for(self, site):
        assert site == 1
        self.ns["pcc_py_gc_minor_graph_lock"]()

    def lock(self):
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.depth -= 1

    def is_known(self, obj):
        assert self.depth > 0
        return int(obj in self.known)

    def record_promotion_request(self, owner):
        assert self.depth > 0 and owner in self.known
        self.promotion_requests.append(owner)

    def object(self, flags):
        obj, node = self.allocate(64), self.allocate(88)
        self.known.add(obj)
        self.store(obj, 12, flags)
        self.store(node, 0, obj)
        if self.object_nodes:
            self.store(self.object_nodes[-1], 16, node)
        self.object_nodes.append(node)
        self.store("pcc_gc_object_list_revision", 0, len(self.object_nodes))
        return obj

    def write(self, owner, child):
        self.ns["pcc_gc_note_write_barrier"](owner, child)
        assert self.depth == 0

    def reset(self):
        self.store("pcc_gc_metric_step", 0, 7)
        epoch_calls_before = len(self.gc4_epoch_calls)
        self.ns["pcc_gc_telemetry_reset"]()
        assert self.depth == 0
        assert self.load("pcc_gc_metric_step", 0) == 0
        # Preserve invocation of the opaque GC4 epoch boundaries; this does
        # not validate their implementations or permit deleting reset itself.
        assert self.gc4_epoch_calls[epoch_calls_before:] == [
            "_backend4_reset_store_buffer_epoch_state",
            "_backend4_reset_remembered_set_epoch_state",
            "_backend4_reseed_relocation_epoch_state",
            "_backend4_clear_large_deferred_flags",
        ]

    def pending(self):
        return tuple(self.load(name, 0) for name in (
            "pcc_gc_backend3_remembered_owner_head",
            "pcc_gc_backend3_remembered_overflow",
            "pcc_gc_backend3_remembered_scan_cursor",
            "pcc_gc_backend3_remembered_scan_revision",
        ))

    def drain(self, budget):
        detached = self.allocate(8)
        self.store(detached, 0, 0)
        self.lock()
        processed = self.ns["pcc_gc_backend3_drain_remembered_owners"](budget, detached)
        self.unlock()
        self.ns["pcc_gc_backend3_finish_detached_remembered_owners"](self.load(detached, 0))
        return processed


def test_without_reset_remembered_work_drains_and_new_store_requeues():
    state = RememberedReset()
    owner, child = state.object(OLD), state.object(YOUNG)
    state.write(owner, child)
    assert state.pending()[0] != 0
    assert state.drain(1) == 1
    assert state.promotion_requests == [owner]
    assert state.load(owner, 12) & REMEMBERED == 0
    state.write(owner, state.object(YOUNG))
    assert state.pending()[0] != 0
    assert state.drain(1) == 1
    assert state.promotion_requests == [owner, owner]


@pytest.mark.parametrize("resets", [1, 2])
def test_reset_preserves_pending_owner_and_later_requeue(resets):
    state = RememberedReset()
    owner, child = state.object(OLD), state.object(YOUNG)
    state.write(owner, child)
    pending = state.pending()
    assert pending[0] != 0 and state.load(owner, 12) & REMEMBERED
    for _ in range(resets):
        state.reset()
        assert state.pending() == pending
        assert state.load(owner, 12) & REMEMBERED
        assert state.freed == set()
    state.write(owner, state.object(YOUNG))
    assert state.pending() == pending  # Existing pending work covers the new edge.
    assert state.drain(1) == 1
    assert state.promotion_requests == [owner]
    assert state.load(owner, 12) & REMEMBERED == 0
    state.write(owner, state.object(YOUNG))
    assert state.pending()[0] != 0
    assert state.drain(1) == 1
    assert state.promotion_requests == [owner, owner]


def test_reset_preserves_partially_consumed_overflow_scan():
    state = RememberedReset(allocation_limit=0)
    first, second, child = state.object(OLD), state.object(OLD), state.object(YOUNG)
    state.write(first, child)
    state.write(second, child)
    assert state.drain(1) == 1
    assert state.promotion_requests == [first]
    pending = state.pending()
    assert pending[0] == 0 and pending[1] == 1 and pending[2] != 0
    state.reset()
    assert state.pending() == pending
    assert state.load(second, 12) & REMEMBERED
    assert state.drain(1) == 1
    assert state.promotion_requests == [first, second]
    assert state.load(second, 12) & REMEMBERED == 0


def test_reset_with_no_pending_work_keeps_future_barrier_usable():
    state = RememberedReset()
    owner, child = state.object(OLD), state.object(YOUNG)
    state.reset()
    assert state.pending() == (0, 0, 0, 0)
    state.write(owner, child)
    assert state.drain(1) == 1
    assert state.promotion_requests == [owner]
