"""Bounded local deques, FIFO stealing and progress for injected work.

Execute the runtime queue/routing functions. Root and state ownership is
covered separately by test_vthread_ready_claims; this model treats a node
claimed for execution as its returned task identity.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"
NAMES = (
    "_push_ready_node", "_queue_push", "_carrier_queue", "_carrier_begin_slot",
    "_carrier_length_slot", "_carrier_turn_slot", "_carrier_ring_slot",
    "_carrier_push", "_carrier_pop", "_carrier_pop_locked", "_carrier_queues_open",
    "_carrier_queues_close", "_dequeue", "_dequeue_from", "_dequeue_carrier",
    "_dequeue_global",
)


class Scheduler:
    def __init__(self, carriers, own):
        self.words = {
            "pcc_vthread_carrier_queue_count_py": carriers,
            "pcc_current_virtual_thread_carrier_py": own,
            "pcc_vthread_ready_count_py": 0,
            "pcc_vthread_carrier_steal_count_py": 0,
            "pcc_vthread_local_poll_budget_py": 0,
        }
        self.memory = {}
        self.queue_locked = None
        self.scheduler_locked = False
        self.scheduler_acquisitions = 0
        self.on_queue_lock = None
        tree = ast.parse(RUNTIME.read_text())
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in NAMES]
        assert {node.name for node in nodes} == set(NAMES)
        namespace = {
            "global_addr": lambda name: (name, 0),
            "ptr_add": lambda base, offset: (base[0], base[1] + offset),
            "load_i32": self.load_word,
            "load_i64": self.load_word,
            "store_i32": self.store_word,
            "store_i64": self.store_word,
            "_counter": lambda slot: self.words[slot[0]],
            "_counter_add": self.counter_add,
            "_counter_set": lambda slot, value: self.words.__setitem__(slot[0], value),
            "load_ptr": lambda slot, offset: self.memory.get((slot, offset)),
            "store_ptr": lambda slot, offset, value: self.memory.__setitem__((slot, offset), value),
            "ptr_is_null": lambda value: int(value is None),
            "null": lambda: None,
            "_raw_queue_lock": self.lock_queue,
            "_raw_queue_unlock": self.unlock_queue,
            "_scheduler_lock": self.lock_scheduler,
            "_scheduler_unlock": self.unlock_scheduler,
            "_ready_node_take": self.claim,
            "pcc_platform_abort": lambda: pytest.fail("nonempty queue reopened"),
        }
        code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name in NAMES:
                namespace[const.co_name] = types.FunctionType(const, namespace)
        self.namespace = namespace

    def load_word(self, slot, offset):
        if slot[0] in self.words:
            return self.words[slot[0]]
        return self.memory.get((slot, offset), 0)

    def store_word(self, slot, offset, value):
        if slot[0] in self.words:
            self.words[slot[0]] = value
        else:
            self.memory[slot, offset] = value

    def lock_queue(self, queue):
        assert self.queue_locked is None, "nested queue lock"
        self.queue_locked = queue
        if self.on_queue_lock:
            callback, self.on_queue_lock = self.on_queue_lock, None
            callback()

    def unlock_queue(self, queue):
        assert self.queue_locked == queue
        self.queue_locked = None

    def lock_scheduler(self):
        assert self.queue_locked is None, "queue -> scheduler lock inversion"
        assert not self.scheduler_locked
        self.scheduler_locked = True
        self.scheduler_acquisitions += 1
        return 0

    def unlock_scheduler(self):
        assert self.scheduler_locked
        self.scheduler_locked = False

    def claim(self, node, locked):
        assert self.queue_locked is None, "root transfer inside queue lock"
        assert bool(locked) == self.scheduler_locked
        self.words["pcc_vthread_ready_count_py"] -= 1
        assert self.words["pcc_vthread_ready_count_py"] >= 0
        return node

    def push(self, node):
        self.words["pcc_vthread_ready_count_py"] += 1
        self.scheduler_locked = True
        self.namespace["_push_ready_node"](node)
        self.scheduler_locked = False

    def pop(self):
        return self.namespace["_dequeue"]()

    def close(self):
        self.scheduler_locked = True
        self.namespace["_carrier_queues_close"]()
        self.scheduler_locked = False

    def counter_add(self, slot, value):
        self.words[slot[0]] += value
        return self.words[slot[0]]

    def queue(self, index):
        """The nodes queued on carrier ``index``, head first."""
        queue = self.namespace["_carrier_queue"](index)
        begin = self.load_word(self.namespace["_carrier_begin_slot"](queue), 0)
        length = self.load_word(self.namespace["_carrier_length_slot"](queue), 0)
        return [self.memory[self.namespace["_carrier_ring_slot"](queue, (begin + i) % 256), 0]
                for i in range(length)]

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
    assert scheduler.injection() == []


def test_work_from_outside_the_pool_keeps_injection_fifo_order():
    scheduler = Scheduler(3, -1)
    for name in ("a", "b", "c", "d"):
        scheduler.push(name)
    assert [scheduler.queue(index) for index in range(3)] == [[], [], []]
    assert scheduler.injection() == ["a", "b", "c", "d"]


def test_a_carrier_index_past_the_open_queues_is_treated_as_outside():
    # A stale index (a carrier of a larger, closed pool) never names a queue
    # the current pool does not have.
    scheduler = Scheduler(2, 5)
    scheduler.push("a")
    scheduler.push("b")
    assert [scheduler.queue(index) for index in range(2)] == [[], []]
    assert scheduler.injection() == ["a", "b"]


def test_without_carriers_everything_uses_the_injection_queue():
    scheduler = Scheduler(0, 1)
    scheduler.push("a")
    scheduler.push("b")
    assert scheduler.injection() == ["a", "b"]
    assert [scheduler.pop(), scheduler.pop(), scheduler.pop()] == ["a", "b", None]


def test_owner_pops_newest_and_thief_steals_oldest_without_scheduler_lock():
    scheduler = Scheduler(2, 0)
    for node in ("a", "b", "c", "d"):
        scheduler.push(node)
    assert scheduler.pop() == "d"
    scheduler.words["pcc_current_virtual_thread_carrier_py"] = 1
    assert scheduler.pop() == "a"
    assert scheduler.pop() == "b"
    assert scheduler.queue(0) == ["c"]
    assert scheduler.scheduler_acquisitions == 0
    assert scheduler.words["pcc_vthread_carrier_steal_count_py"] == 2


def test_ring_wrap_and_overflow_preserve_every_node():
    scheduler = Scheduler(2, 0)
    for node in range(256):
        scheduler.push(node)
    scheduler.words["pcc_current_virtual_thread_carrier_py"] = 1
    assert [scheduler.pop() for _ in range(200)] == list(range(200))
    scheduler.words["pcc_current_virtual_thread_carrier_py"] = 0
    for node in range(256, 460):
        scheduler.push(node)
    assert scheduler.queue(0) == list(range(200, 456))
    assert scheduler.injection() == list(range(456, 460))
    drained = [scheduler.pop() for _ in range(260)]
    assert len(set(drained)) == 260 and sorted(drained) == list(range(200, 460))
    assert scheduler.pop() is None


def test_local_lifo_cannot_starve_older_work_or_injection():
    scheduler = Scheduler(1, 0)
    scheduler.push("old")
    scheduler.push("hot")
    for _ in range(15):
        assert scheduler.pop() == "hot"
        scheduler.push("hot")
    assert scheduler.pop() == "old"
    scheduler.words["pcc_current_virtual_thread_carrier_py"] = -1
    scheduler.push("external")
    scheduler.words["pcc_current_virtual_thread_carrier_py"] = 0
    served = []
    for _ in range(32):
        node = scheduler.pop()
        served.append(node)
        if node == "external":
            break
        scheduler.push(node)
    assert served[-1] == "external"


def test_closing_and_reopening_preserve_queued_roots_and_injection_order():
    scheduler = Scheduler(2, 0)
    for node in range(260):
        scheduler.push(node)
    scheduler.close()
    assert scheduler.words["pcc_vthread_carrier_queue_count_py"] == 0
    assert scheduler.queue(0) == []
    assert scheduler.injection() == list(range(256, 260)) + list(range(256))
    assert scheduler.words["pcc_vthread_ready_count_py"] == 260
    scheduler.scheduler_locked = True
    assert scheduler.namespace["_carrier_queues_open"](2) == 0
    scheduler.scheduler_locked = False
    assert scheduler.queue(0) == scheduler.queue(1) == []
    assert scheduler.pop() == 256


def test_push_revalidates_an_open_queue_after_acquiring_its_lock():
    scheduler = Scheduler(2, 0)
    scheduler.on_queue_lock = lambda: scheduler.words.__setitem__(
        "pcc_vthread_carrier_queue_count_py", 0)
    scheduler.push("late")
    assert scheduler.queue(0) == []
    assert scheduler.injection() == ["late"]


@pytest.mark.integration
def test_carriers_spawn_and_join_children_without_losing_or_repeating_work(
    tmp_path, threaded_pcc_runtime_archive,
):
    import os
    import subprocess

    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "local_children.py"
    source.write_text('''import gc
import pcc.virtual_thread as vt

def child(value):
    vt.yield_now()
    if value % 100 == 0:
        gc.collect()
    return value * value + 7

def parent(seed):
    children = []
    for index in range(300):
        children.append(vt.spawn(child, seed * 300 + index))
    for index in range(300):
        vt.join(children[index])
        value = seed * 300 + index
        if vt.result(children[index]) != value * value + 7:
            raise RuntimeError("child result mismatch")
    return len(children)

def main():
    parents = [vt.spawn(parent, seed) for seed in range(4)]
    vt.run(4, 1000000)
    print([vt.result(parent) for parent in parents])

main()
''', encoding="utf-8")
    binary = tmp_path / "local_children"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for collector in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=90,
                                env=dict(os.environ, PCC_GC_BACKEND=str(collector),
                                         PATH="/nonexistent"))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "[300, 300, 300, 300]\n", "",
        ), (collector, result.stdout, result.stderr)
