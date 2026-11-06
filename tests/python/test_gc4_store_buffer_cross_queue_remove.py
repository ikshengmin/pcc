"""Cross-queue recursive disposal observes fully detached GC4 buffer entries.

Execute the production removal and index bodies with checked host memory.
These tests cover callback ordering and index accounting, not native allocation,
count-free collection or concurrent execution.
"""

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.python.test_gc_store_buffer_membership_lifecycle import _Memory


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_gc_backend.py"


class Removal:
    def __init__(self, placement, reverse):
        # A holds B; B holds C. Removing A recursively removes B and C.
        # Two entries belonging to D must survive in their original queues.
        self.fields = {
            1: [100, 1008, 200, 0],
            2: [200, 2008, 300, 0],
            3: [100, 1016, 400, 0],
            4: [300, 3008, 500, 0],
            5: [600, 6008, 700, 0],
            6: [600, 6016, 800, 0],
        }
        order = list(reversed(self.fields)) if reverse else list(self.fields)
        self.queues = {
            name: [node for node in order
                   if bool(placement & (1 << (node - 1))) == medium]
            for name, medium in (("medium", True), ("global", False))
        }
        self.heads = {}
        for name, nodes in self.queues.items():
            self.heads[name] = nodes[0] if nodes else 0
            for node, following in zip(nodes, nodes[1:] + [0]):
                self.fields[node][3] = following
        self.pending = len(self.fields)
        self.medium_count = len(self.queues["medium"])
        self.members = {fields[0] for fields in self.fields.values()}
        self.slots = {fields[1]: fields[0] for fields in self.fields.values()}
        self.owner_counts = Counter(self.slots.values())
        self.index = _Memory()
        for slot, owner in self.slots.items():
            self.index.call("pcc_gc_store_buffer_member_note", owner)
            self.index.call("pcc_gc_store_buffer_entry_note", owner, slot)
        self.dead = set()
        self.releases = []
        self.removing = []
        self.namespace = {
            "null": lambda: 0,
            "ptr_is_null": lambda value: value == 0,
            "is_tagged_int": lambda value: value & 1,
            "ptr_eq": lambda left, right: left == right,
            "load_ptr": self.load,
            "store_ptr": self.store,
            "pcc_gc_store_buffer_member_maybe": self.index.maybe,
            "pcc_gc_store_buffer_member_forget": self.forget_member,
            "pcc_gc_store_buffer_entry_forget": self.forget_entry,
            "_store_buffer_medium_head": lambda: self.heads["medium"],
            "_store_buffer_head": lambda: self.heads["global"],
            "_set_store_buffer_medium_head": lambda node: self.heads.__setitem__("medium", node),
            "_set_store_buffer_head": lambda node: self.heads.__setitem__("global", node),
            "_backend4_store_buffer_medium_count": lambda: self.medium_count,
            "_backend4_store_buffer_medium_set_count": self.set_medium_count,
            "_backend4_store_buffer_dec": self.decrement,
            "py_decref": self.release,
            "free": self.free,
        }
        definitions = [node for node in ast.parse(SOURCE.read_text()).body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_backend4_store_buffer_remove"]
        assert len(definitions) == 1
        definitions[0].decorator_list = []
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), "exec"),
             self.namespace)

    def load(self, node, offset):
        assert node not in self.dead, ("read after free", node, offset)
        return self.fields[node][offset // 8]

    def store(self, node, offset, value):
        assert node not in self.dead, ("write after free", node, offset)
        self.fields[node][offset // 8] = value

    def free(self, node):
        assert node not in self.dead, ("double free", node)
        self.dead.add(node)

    def forget_entry(self, owner, slot):
        assert self.slots.pop(slot) == owner
        self.index.call("pcc_gc_store_buffer_entry_forget", owner, slot)
        self.owner_counts[owner] -= 1
        if self.owner_counts[owner] == 0:
            del self.owner_counts[owner]

    def forget_member(self, owner):
        assert owner not in self.owner_counts
        self.members.remove(owner)
        self.index.call("pcc_gc_store_buffer_member_forget", owner)

    def set_medium_count(self, count):
        self.medium_count = count

    def decrement(self):
        assert self.pending > 0
        self.pending -= 1

    def public_nodes(self, queue):
        nodes = []
        node = self.heads[queue]
        while node:
            assert node not in nodes, ("queue cycle", queue, node)
            nodes.append(node)
            node = self.load(node, 24)
        return nodes

    def assert_consistent(self):
        medium = self.public_nodes("medium")
        globally = self.public_nodes("global")
        assert set(medium).isdisjoint(globally)
        nodes = medium + globally
        assert self.medium_count == len(medium)
        assert self.pending == len(nodes)
        expected_slots = {self.load(node, 8): self.load(node, 0) for node in nodes}
        assert self.slots == expected_slots
        assert self.owner_counts == Counter(expected_slots.values())
        assert self.members == set(expected_slots.values())
        # The dictionaries above are the independent reference accounting;
        # the production open-addressed indexes must agree at every callback.
        assert self.index.count() == len(self.members)
        for owner in (100, 200, 300, 600):
            assert self.index.maybe(owner) == int(owner in self.members)
            assert self.index.call("pcc_gc_store_buffer_owner_entries", owner) == self.owner_counts[owner]
        for fields in self.fields.values():
            slot = fields[1]
            assert self.index.call("pcc_gc_store_buffer_slot_pending", slot) == int(slot in self.slots)
        # A callback must not see any entry from ANY active removal, even
        # when the outer removal is suspended inside another owner's decref.
        assert not self.members.intersection(self.removing)

    def release(self, value):
        self.assert_consistent()
        self.releases.append(value)
        if value in (200, 300):
            self.remove(value)
        self.assert_consistent()

    def remove(self, owner):
        self.removing.append(owner)
        self.namespace["_backend4_store_buffer_remove"](owner)
        self.assert_consistent()
        assert self.removing.pop() == owner


@pytest.mark.parametrize("placement", range(64), ids=lambda value: f"queues-{value:06b}")
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_recursive_disposal_detaches_both_queues_and_indexes(placement, reverse):
    model = Removal(placement, reverse)
    model.assert_consistent()
    outer_order = [node for queue in ("medium", "global")
                   for node in model.queues[queue] if node in (1, 3)]
    expected_releases = [value for node in outer_order
                         for value in ([200, 300, 500] if node == 1 else [400])]

    model.remove(100)
    assert model.releases == expected_releases
    assert model.dead == {1, 2, 3, 4}
    assert model.slots == {6008: 600, 6016: 600}
    assert model.members == {600} and model.pending == 2
    for queue in ("medium", "global"):
        assert model.public_nodes(queue) == [node for node in model.queues[queue]
                                             if node in (5, 6)]

    # Repeating a removed owner is harmless; the surviving owner's entries
    # remain discoverable and can subsequently be removed exactly once.
    model.remove(100)
    assert model.releases == expected_releases
    survivor_values = [model.fields[node][2] for queue in ("medium", "global")
                       for node in model.public_nodes(queue)]
    model.remove(600)
    assert model.releases == expected_releases + survivor_values
    assert model.dead == set(model.fields)
    assert model.heads == {"medium": 0, "global": 0}
    assert model.pending == model.medium_count == 0
