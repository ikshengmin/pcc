"""A carrier queues the work it readies on its own queue.

_push_ready_node spread every ready node round-robin over the carrier
queues through one shared counter, so a child a carrier spawned, a joiner
its step woke or a timer it fired ran next on another core, away from the
data that step had just written. A carrier now pushes onto its own queue
and idle carriers steal from the head; work queued from outside the pool
(the main thread before or while the pool runs) is still spread
round-robin, and without carriers everything uses the injection queue.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"
NAMES = ("_push_ready_node", "_queue_push", "_carrier_head_slot", "_carrier_tail_slot")


class Scheduler:
    def __init__(self, carriers, own):
        self.words = {
            "pcc_vthread_carrier_queue_count_py": carriers,
            "pcc_current_virtual_thread_carrier_py": own,
            "pcc_vthread_next_carrier_enqueue_py": 0,
        }
        self.memory = {}
        self.round_robin_adds = 0
        tree = ast.parse(RUNTIME.read_text())
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in NAMES]
        assert {node.name for node in nodes} == set(NAMES)
        namespace = {
            "global_addr": lambda name: (name, 0),
            "ptr_add": lambda base, offset: (base[0], base[1] + offset),
            "load_i32": lambda slot, offset: self.words[slot[0]],
            "_counter": lambda slot: self.words[slot[0]],
            "_counter_add": self.counter_add,
            "load_ptr": lambda slot, offset: self.memory.get((slot, offset)),
            "store_ptr": lambda slot, offset, value: self.memory.__setitem__((slot, offset), value),
            "ptr_is_null": lambda value: int(value is None),
            "null": lambda: None,
        }
        code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name in NAMES:
                namespace[const.co_name] = types.FunctionType(const, namespace)
        self.push = namespace["_push_ready_node"]

    def counter_add(self, slot, value):
        assert slot == ("pcc_vthread_next_carrier_enqueue_py", 0)
        self.round_robin_adds += 1
        self.words[slot[0]] += value
        return self.words[slot[0]]

    def queue(self, index):
        """The nodes queued on carrier ``index``, head first."""
        nodes = []
        node = self.memory.get((("pcc_vthread_carrier_heads_py", index * 8), 0))
        while node is not None:
            nodes.append(node)
            node = self.memory.get((node, 8))
        return nodes

    def injection(self):
        nodes = []
        node = self.memory.get((("pcc_vthread_ready_head_py", 0), 0))
        while node is not None:
            nodes.append(node)
            node = self.memory.get((node, 8))
        return nodes


@pytest.mark.parametrize("own", (0, 2, 3))
def test_a_carrier_queues_what_it_readies_on_its_own_queue(own):
    scheduler = Scheduler(4, own)
    for name in ("child", "joiner", "timer"):
        scheduler.push(name)
    assert scheduler.queue(own) == ["child", "joiner", "timer"]
    assert [scheduler.queue(index) for index in range(4) if index != own] == [[], [], []]
    assert scheduler.round_robin_adds == 0


def test_work_from_outside_the_pool_is_spread_round_robin():
    scheduler = Scheduler(3, -1)
    for name in ("a", "b", "c", "d"):
        scheduler.push(name)
    assert [scheduler.queue(index) for index in range(3)] == [["a", "d"], ["b"], ["c"]]
    assert scheduler.round_robin_adds == 4


def test_a_carrier_index_past_the_open_queues_is_treated_as_outside():
    # A stale index (a carrier of a larger, closed pool) never names a queue
    # the current pool does not have.
    scheduler = Scheduler(2, 5)
    scheduler.push("a")
    scheduler.push("b")
    assert [scheduler.queue(index) for index in range(2)] == [["a"], ["b"]]


def test_without_carriers_everything_uses_the_injection_queue():
    scheduler = Scheduler(0, 1)
    scheduler.push("a")
    scheduler.push("b")
    assert scheduler.injection() == ["a", "b"]
    assert scheduler.round_robin_adds == 0
