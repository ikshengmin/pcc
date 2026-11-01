"""Execute the runtime's ready-entry ownership transitions at publication edges.

The scheduler mutex still serializes the production queues. These deterministic
interleavings check the CAS protocol needed before splitting that mutex: a
consumer may run as soon as the node becomes visible, and another publisher
must not overwrite its claim. Native carrier/GC tests cover the emitted route.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"
VTHREAD_TYPE = 113
STATE = 32
RUN_MARK = 40


class Cell:
    def __init__(self, fields=None):
        self.fields = dict(fields or {})
        self.references = 0
        self.alive = True


class Model:
    def __init__(self):
        self.head = Cell()
        self.tail = Cell()
        self.count = Cell({0: 0})
        self.released = []
        self.events = []
        self.visible = None
        self.allocation_fails = False
        self.registration_fails = False
        self.freed = []
        self.on_lock = None
        self.namespace = {
            "atomic_cas_i64": self.cas,
            "atomic_load_i64": lambda cell, offset, order: self.load(cell, offset),
            "atomic_store_i64": self.atomic_store,
            "load_i64": self.load,
            "load_i32": self.load,
            "load_ptr": self.load,
            "store_ptr": self.store,
            "store_i64": self.store,
            "null": lambda: None,
            "ptr_is_null": lambda value: value is None,
            "ptr_eq": lambda left, right: left is right,
            "is_tagged_int": lambda value: False,
            "global_addr": self.global_slot,
            "_counter_add": self.add,
            "_ready_alloc": lambda: None if self.allocation_fails else Cell(),
            "pcc_gc_scheduler_root_register_handle": self.register,
            "pcc_gc_store_root": lambda slot, value: self.store(slot, 0, value),
            "pcc_gc_load_ptr": lambda owner, slot: self.load(slot, 0),
            "_ready_release": self.release,
            "_join_release": self.release,
            "free": self.freed.append,
            "py_incref": lambda value: self.retain(value, 1),
            "py_decref": lambda value: self.retain(value, -1),
            "_push_ready_node": self.push,
            "_scheduler_signal": lambda: self.events.append(("signal",)),
            "_scheduler_lock": self.lock,
            "_scheduler_unlock": lambda: self.events.append(("unlock",)),
            "_effect": lambda *event: self.events.append(event),
            "PY_TYPE_VIRTUAL_THREAD": VTHREAD_TYPE,
        }
        names = {"_enqueue", "_publish_ready_node", "_dequeue_from", "_step_finished",
                 "_ready_node_take", "_join_wake_all"}
        nodes = [node for node in ast.parse(SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        exec(compile(ast.Module(nodes, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    @staticmethod
    def thread(state=1, mark=0):
        return Cell({8: VTHREAD_TYPE, STATE: state, RUN_MARK: mark})

    @staticmethod
    def node(thread):
        return Cell({0: thread, 8: None})

    @staticmethod
    def load(cell, offset):
        assert cell.alive, "read after the published task was freed"
        return cell.fields.get(offset)

    @staticmethod
    def store(cell, offset, value):
        assert cell.alive, "write after the published task was freed"
        cell.fields[offset] = value

    def global_slot(self, name):
        assert name == "pcc_vthread_ready_count_py"
        return self.count

    def cas(self, cell, offset, expected, replacement, success, failure):
        assert (success, failure) == ("acq_rel", "acquire")
        previous = self.load(cell, offset)
        if previous == expected:
            self.store(cell, offset, replacement)
        return previous

    def atomic_store(self, cell, offset, value, order):
        assert order == "release"
        self.store(cell, offset, value)

    def add(self, cell, delta):
        value = self.load(cell, 0) + delta
        assert value >= 0, "visible node was not counted before its consumer"
        self.store(cell, 0, value)
        return value

    def register(self, node):
        return None if self.registration_fails else node

    def release(self, node):
        assert node not in self.released, "scheduler root retired twice"
        self.released.append(node)

    @staticmethod
    def retain(value, delta):
        value.references += delta
        assert value.references >= 0

    def push(self, node):
        tail = self.load(self.tail, 0)
        if tail is None:
            self.store(self.head, 0, node)
        else:
            self.store(tail, 8, node)
        self.store(node, 8, None)
        self.store(self.tail, 0, node)
        if self.visible:
            callback, self.visible = self.visible, None
            callback()

    def lock(self):
        self.events.append(("lock",))
        if self.on_lock:
            callback, self.on_lock = self.on_lock, None
            callback()
        return 0

    def publish(self, thread, node=None):
        self.namespace["_publish_ready_node"](thread, node or self.node(thread))

    def pop(self):
        return self.namespace["_dequeue_from"](self.head, self.tail)

    def finish(self, thread):
        return self.namespace["_step_finished"](thread)


@pytest.mark.parametrize("entrypoint", ["_enqueue", "_publish_ready_node"])
def test_publisher_cannot_overwrite_an_immediate_consumers_running_claim(entrypoint):
    model = Model()
    thread = model.thread()
    observed = []

    def consume():
        assert thread.fields[RUN_MARK] == 1
        assert model.count.fields[0] == 1
        observed.append(model.pop())

    model.visible = consume
    if entrypoint == "_enqueue":
        assert model.namespace[entrypoint](thread) == 0
    else:
        model.publish(thread)
    assert observed == [thread]
    assert thread.fields[STATE] == 2
    assert thread.fields[RUN_MARK] == 2
    assert model.count.fields[0] == 0
    assert thread.references == 1
    assert len(model.released) == 1


def test_duplicate_publisher_retires_only_its_unpublished_root():
    model = Model()
    thread = model.thread()
    first, duplicate = model.node(thread), model.node(thread)
    model.visible = lambda: model.publish(thread, duplicate)
    model.publish(thread, first)
    assert model.released == [duplicate]
    assert model.count.fields[0] == 1
    assert model.pop() is thread
    assert model.pop() is None
    assert model.released == [duplicate, first]
    assert model.count.fields[0] == 0


@pytest.mark.parametrize("mark", [1, 2])
def test_publishing_a_queued_or_running_thread_does_not_replace_its_claim(mark):
    model = Model()
    thread = model.thread(mark=mark)
    node = model.node(thread)
    model.publish(thread, node)
    assert thread.fields[RUN_MARK] == mark
    assert model.count.fields[0] == 0
    assert model.released == [node]
    assert model.pop() is None
    assert ("signal",) not in model.events


@pytest.mark.parametrize("state, mark", [(1, 0), (1, 2), (2, 2), (3, 1), (4, 1)])
def test_stale_entry_does_not_resume_or_erase_another_carriers_claim(state, mark):
    model = Model()
    thread = model.thread(state=state, mark=mark)
    node = model.node(thread)
    model.count.fields[0] = 1
    model.push(node)
    assert model.pop() is None
    assert thread.fields[STATE] == state
    assert thread.fields[RUN_MARK] == (0 if mark == 1 else mark)
    assert thread.references == 0
    assert model.count.fields[0] == 0
    assert model.released == [node]


def test_step_exit_publishes_the_wakeup_left_on_its_running_thread():
    model = Model()
    thread = model.thread(state=1, mark=2)
    assert model.finish(thread) == 0
    assert thread.fields[RUN_MARK] == 1
    assert model.count.fields[0] == 1
    assert model.pop() is thread
    assert thread.fields[STATE] == 2


def test_step_exit_does_not_clear_an_already_published_node():
    model = Model()
    thread = model.thread()
    model.publish(thread)
    assert model.finish(thread) == 0
    assert thread.fields[RUN_MARK] == 1
    assert model.count.fields[0] == 1
    assert model.pop() is thread


def test_completed_step_needs_no_scheduler_lock():
    model = Model()
    assert model.finish(model.thread(state=4, mark=0)) == 0
    assert model.events == []


def test_local_stale_claim_consumes_a_wakeup_before_retiring_its_mark():
    model = Model()
    thread = model.thread(state=3, mark=1)
    original, wakeup = model.node(thread), model.node(thread)
    model.count.fields[0] = 1

    def wake():
        # The waker publishes READY while this consumer owns mark 2, so its
        # spare root is retired. The consumer must recheck after locking.
        thread.fields[STATE] = 1
        model.publish(thread, wakeup)

    model.on_lock = wake
    assert model.namespace["_ready_node_take"](original, 0) is thread
    assert thread.fields[STATE] == 2
    assert thread.fields[RUN_MARK] == 2
    assert model.count.fields[0] == 0
    assert model.released == [original, wakeup]
    assert model.events.count(("lock",)) == model.events.count(("unlock",)) == 1


def test_successful_local_claim_does_not_take_the_scheduler_mutex():
    model = Model()
    thread = model.thread(state=1, mark=1)
    model.count.fields[0] = 1
    assert model.namespace["_ready_node_take"](model.node(thread), 0) is thread
    assert ("lock",) not in model.events


def test_join_waker_does_not_read_a_task_after_transferring_its_last_root():
    model = Model()
    waiter = model.thread(state=3, mark=0)
    node = model.node(waiter)
    waiter.fields.update({104: node, 120: 4})
    target = Cell({88: node, 96: node})

    def finish_waiter():
        assert model.pop() is waiter
        waiter.references = 0
        waiter.alive = False

    model.visible = finish_waiter
    assert model.namespace["_join_wake_all"](target) == 0
    assert target.fields == {88: None, 96: None}
    assert (6, 1, 0, 1) in model.events


@pytest.mark.parametrize("failure", ["allocation_fails", "registration_fails"])
def test_failed_reservation_does_not_claim_a_queue_entry(failure):
    model = Model()
    setattr(model, failure, True)
    thread = model.thread()
    assert model.namespace["_enqueue"](thread) == -1
    assert thread.fields[RUN_MARK] == 0
    assert model.count.fields[0] == 0
    assert model.pop() is None
    assert len(model.freed) == (failure == "registration_fails")
