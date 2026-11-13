"""Raising and clearing an exception take no process-wide lock in container mode.

Every raise and every clear exchanges the thread-local exception slot with
an owned root (py_substrate.py_tls_exc_swap_slot). That exchange took the
global graph lock, and the slot's root node was registered with the global
scheduler-root list on every raise and unregistered on every clear, so with
four workers the raise primitive spent half its time queued on those two
locks and ran at 0.3-0.5x of one worker. In container mode (every collector
without the provenance probe) roots are scanned in a stopped world and a
stored value is shaded during a mark, and no poll separates the exchange's
two stores, so the graph lock is kept only outside container mode. A thread
started by pcc_thread_start keeps its root node while its slot is empty and
retires it when it unregisters; any other thread (no unregistering exit)
still releases it whenever the slot empties. Constructing an exception
stores into an object only this thread can name, and a raise with no
handled exception has no context to chain: neither takes the graph lock.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_class_namespace_writer import load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"


def _load(path, names, namespace):
    load_functions(RUNTIME / path, names, namespace)
    return namespace


class Thread:
    def __init__(self, container_mode, exit_unregisters):
        self.container_mode = container_mode
        self.exit_unregisters = exit_unregisters
        self.tls = {"py_tls_current_exc_storage": 0, "py_tls_current_exc_root_handle": 0}
        self.roots = {}
        self.events = []
        self.registered = set()
        self.pending = 0

    def namespace(self):
        return _load("py_substrate.py", (
            "py_tls_exc_swap_slot", "_exc_root_handle_released_when_empty",
            "py_tls_exc_retire_root", "_exc_pending_transition",
        ), {
            "global_load_ptr": self.tls.__getitem__,
            "global_store_ptr": self.tls.__setitem__,
            "global_addr": lambda name: name,
            "load_i32": self.load_i32,
            "load_ptr": lambda slot, offset: self.roots.get(slot, 0),
            "store_ptr": lambda slot, offset, value: self.roots.__setitem__(slot, value),
            "pcc_gc_load_ptr": lambda owner, slot: (
                self.tls[slot] if slot in self.tls else self.roots.get(slot, 0)
            ),
            "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "pcc_gc_note_slot_write_barrier": lambda *_: None,
            "pcc_gc_scheduler_root_register_handle": self.register,
            "pcc_gc_scheduler_root_unregister_handle": self.unregister,
            "pcc_py_gc_minor_graph_lock": lambda: self.events.append("graph_lock"),
            "pcc_py_gc_minor_graph_unlock": lambda: self.events.append("graph_unlock"),
            "pcc_thread_exit_unregisters": lambda: self.exit_unregisters,
            "pcc_platform_abort": lambda: pytest.fail("abort"),
            "atomic_rmw_i64": self.count_pending,
        })

    def load_i32(self, name, offset):
        assert name == "pcc_gc_list_container_mode" and offset == 0
        return self.container_mode

    def register(self, slot):
        assert slot == "py_tls_current_exc_storage"
        self.events.append("register")
        self.registered.add(slot)
        return 77

    def unregister(self, handle):
        assert handle == 77
        self.events.append("unregister")
        self.registered.discard("py_tls_current_exc_storage")

    def count_pending(self, op, address, offset, delta, order):
        self.pending += delta


def _raise_then_clear(thread, ns, rounds=3):
    for index in range(rounds):
        thread.roots["slot"] = 100 + index
        ns["py_tls_exc_swap_slot"]("slot")
        assert thread.tls["py_tls_current_exc_storage"] == 100 + index
        assert thread.roots["slot"] == 0
        ns["py_tls_exc_swap_slot"]("slot")
        assert thread.tls["py_tls_current_exc_storage"] == 0
        assert thread.roots["slot"] == 100 + index
        thread.roots["slot"] = 0
    assert thread.pending == 0


def test_started_thread_swaps_without_global_locks_and_keeps_its_root():
    thread = Thread(container_mode=1, exit_unregisters=1)
    ns = thread.namespace()
    _raise_then_clear(thread, ns)
    # One registration for the thread's life; no graph lock at all.
    assert thread.events == ["register"]
    assert thread.tls["py_tls_current_exc_root_handle"] == 77
    ns["py_tls_exc_retire_root"]()
    assert thread.events == ["register", "unregister"]
    assert thread.tls["py_tls_current_exc_root_handle"] == 0 and not thread.registered


def test_thread_without_an_unregistering_exit_releases_its_root_when_empty():
    thread = Thread(container_mode=1, exit_unregisters=0)
    ns = thread.namespace()
    _raise_then_clear(thread, ns, rounds=2)
    assert thread.events == ["register", "unregister"] * 2
    assert thread.tls["py_tls_current_exc_root_handle"] == 0 and not thread.registered


def test_without_container_mode_the_exchange_keeps_the_graph_lock():
    thread = Thread(container_mode=0, exit_unregisters=1)
    ns = thread.namespace()
    _raise_then_clear(thread, ns, rounds=1)
    assert thread.events == ["register", "graph_lock", "graph_unlock", "graph_lock", "graph_unlock"]


def test_retiring_with_a_pending_exception_aborts():
    thread = Thread(container_mode=1, exit_unregisters=1)
    ns = thread.namespace()
    thread.tls["py_tls_current_exc_storage"] = 5
    ns["pcc_platform_abort"] = lambda: thread.events.append("abort")
    ns["py_tls_exc_retire_root"]()
    assert thread.events == ["abort"]


def _constructed_store(container_mode):
    events = []
    memory = {"owned": "exc", ("values", 0): "cls"}
    ns = _load("py_exc_objects.py", ("_exc_store_constructed_slot",), {
        "load_i32": lambda name, offset: container_mode,
        "global_addr": lambda name: name,
        "pcc_py_gc_minor_graph_lock": lambda: events.append("graph_lock"),
        "pcc_py_gc_minor_graph_unlock": lambda: events.append("graph_unlock"),
        "null": lambda: None,
        "ptr_add": lambda base, offset: (base, offset),
        "pcc_gc_load_ptr": lambda owner, slot: memory[slot],
        "pcc_gc_store_ptr": lambda owner, slot, value: events.append(("store", owner, slot, value)),
    })
    ns["_exc_store_constructed_slot"]("owned", "values", 0, 16)
    return events


def test_constructing_an_exception_stores_without_the_graph_lock_in_container_mode():
    assert _constructed_store(1) == [("store", "exc", ("exc", 16), "cls")]
    assert _constructed_store(0) == [
        "graph_lock", ("store", "exc", ("exc", 16), "cls"), "graph_unlock",
    ]


def test_a_raise_with_no_handled_exception_sets_no_context_and_takes_no_lock():
    events = []
    ns = _load("py_exc_objects.py", ("py_exc_set_implicit_context_slots",), {
        "ptr_is_null": lambda value: value is None,
        "load_ptr": lambda slot, offset: slot[0],
        "stack_alloc": lambda size: pytest.fail("no plan for an empty context"),
        "pcc_py_gc_minor_graph_lock": lambda: events.append("graph_lock"),
    })
    ns["py_exc_set_implicit_context_slots"](["exc"], [None])
    assert events == []
