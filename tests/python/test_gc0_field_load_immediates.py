"""A GC0 field read of an immediate value takes no graph transaction.

With carriers running, _instance_field_load took the global graph lock
around every GC0 field load and retain, so that a writer could not free the
value between the two; at four workers the instance field loads were a
fifth of the graph lock's wait. NULL, a tagged int and the static None,
True and False are never freed and need no count, so the read returns them
from one word load; any other value still loads and retains inside the
transaction. Only the address is examined, never a header.
test_shared_slot_concurrent_access's mixed shape runs the native race.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_class.py"
STATICS = {"py_none_storage": 0x1000, "py_true_storage": 0x1020, "py_false_storage": 0x1040}


class Field:
    def __init__(self, word):
        self.word = word
        self.events = []
        tree = ast.parse(RUNTIME.read_text())
        node = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_instance_field_load")
        namespace = {
            "store_ptr": self.store,
            "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_eq": lambda left, right: int(left == right),
            "int_to_ptr": lambda word: word,
            "global_addr": lambda name: STATICS[name],
            "atomic_load_i64": self.atomic_load,
            "load_ptr": self.load,
            "pcc_threads_enabled": lambda: 1,
            "_gc_backend_selected_fast": lambda: 0,
            "pcc_py_gc_minor_graph_lock": lambda: self.events.append("lock"),
            "pcc_py_gc_minor_graph_unlock": lambda: self.events.append("unlock"),
            "py_incref": lambda value: self.events.append(("incref", value)),
        }
        code = compile(ast.Module([node], type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name == "_instance_field_load":
                self.load_field = types.FunctionType(const, namespace)
        self.out = None

    def atomic_load(self, inst, offset, order):
        assert (inst, offset, order) == ("inst", 24, "relaxed")
        self.events.append("word")
        return self.word

    def load(self, inst, offset):
        assert (inst, offset) == ("inst", 24)
        self.events.append("locked load")
        return self.word

    def store(self, slot, offset, value):
        assert (slot, offset) == ("out", 0)
        self.out = value

    def run(self):
        assert self.load_field("inst", 24, "out") == 0
        return self.events


@pytest.mark.parametrize("word", (0, 1, (42 << 1) | 1, (-7 << 1) | 1, *STATICS.values()))
def test_an_immediate_or_static_value_is_read_without_the_transaction(word):
    field = Field(word)
    assert field.run() == ["word"]
    assert field.out == word


def test_a_heap_value_is_loaded_and_retained_inside_the_transaction():
    field = Field(0x5000)
    assert field.run() == ["word", "lock", "locked load", ("incref", 0x5000), "unlock"]
    assert field.out == 0x5000


def test_a_value_next_to_a_static_singleton_is_not_mistaken_for_it():
    # Only the exact singleton addresses qualify; an object allocated beside
    # them is an ordinary heap value.
    field = Field(STATICS["py_true_storage"] + 16)
    assert "lock" in field.run()
