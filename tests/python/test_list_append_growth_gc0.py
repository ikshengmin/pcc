"""A full threaded GC0 list grows and takes the append in one transaction.

An append to a full list took the existing-capacity transaction (full),
an address lease, the rooted append transaction (full), the growth
snapshot and commit transactions and the rooted append again: five graph
lock round trips and a lease, every time json's buf doubled. The
existing-capacity path now doubles the owner array itself: it allocates
outside the lock, rechecks that the list still owns the same array and
capacity, copies the owners as raw words, publishes the new array and
retains the item, then frees the old array. Any change in between leaves
the general growth path.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py import py_abi_constants as abi


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_list.py"


class ListMemory:
    """Word memory: lists are dicts of fields, item arrays are Python lists."""

    def __init__(self, capacity, length):
        self.locks = 0
        self.depth = 0
        self.freed = []
        self.buffers = {}
        self.items = self.buffer(capacity)
        for index in range(length):
            self.items[index] = ("item", index)
        self.list = {abi.PYLISTOBJECT_ITEMS_OFFSET: ("buffer", id(self.items)),
                     abi.PYLISTOBJECT_LENGTH_OFFSET: length,
                     abi.PYLISTOBJECT_CAPACITY_OFFSET: capacity,
                     abi.PYOBJECTHEADER_FLAGS_OFFSET: 0}
        self.list_slot = {0: "list"}
        self.item_slot = {0: ("item", "new")}
        self.change_during_allocation = None

    def buffer(self, words):
        cells = [None] * words
        self.buffers[id(cells)] = cells
        return cells

    def namespace(self):
        memory = self

        def load(base, offset):
            if base == "list" or base is memory.list:
                return memory.list[offset]
            if isinstance(base, dict):
                return base[offset]
            raise AssertionError(base)

        def store(base, offset, value):
            if base == "list":
                memory.list[offset] = value
            else:
                raise AssertionError(base)

        def ptr_add(base, offset):
            assert base[0] == "buffer"
            return ("cell", base[1], offset)

        def malloc(size):
            if memory.change_during_allocation:
                memory.change_during_allocation()
            return ("buffer", id(memory.buffer(size // 8)))

        def memmove(destination, source, size):
            target = memory.buffers[destination[1]]
            origin = memory.buffers[source[1]]
            target[:size // 8] = origin[:size // 8]

        def memset(destination, value, size):
            cells = memory.buffers[destination[1]]
            start = destination[2] // 8
            for index in range(start, start + size // 8):
                cells[index] = None

        def try_init(destination, source):
            assert memory.depth == 1
            cells = memory.buffers[destination[1]]
            cells[destination[2] // 8] = source[0]
            return 1

        def lock():
            assert memory.depth == 0
            memory.depth = 1
            memory.locks += 1

        def unlock():
            assert memory.depth == 1
            memory.depth = 0

        globals_ = {"pcc_gc_backend_selected": 0, "pcc_gc_refcount_atomic": 1,
                    "pcc_diagnostics_runtime_log_fast_state": 0,
                    "pcc_gc_foreign_lease_active": 0}
        namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
        namespace.update({
            "load_ptr": load, "load_i64": load, "load_i32": load,
            "store_ptr": store, "store_i64": store,
            "atomic_load_i64": lambda base, offset, order: globals_[base],
            "global_addr": lambda name: name,
            "ptr_add": ptr_add, "ptr_eq": lambda a, b: int(a == b),
            "ptr_is_null": lambda value: int(value is None),
            "malloc": malloc, "free": memory.freed.append,
            "memmove": memmove, "memset": memset,
            "pcc_gc_try_init_ptr_locked": try_init,
            "pcc_py_gc_minor_graph_lock": lock, "pcc_py_gc_minor_graph_unlock": unlock,
            "_list_is_sane": lambda value, code: True,
            "C_POINTER_SIZE": 8,
        })
        original_load = namespace["load_i32"]
        namespace["load_i32"] = (lambda base, offset: globals_[base]
                                 if isinstance(base, str) and base in globals_ else original_load(base, offset))
        tree = ast.parse(RUNTIME.read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("_append_existing_capacity_gc0", "_append_grow_gc0")]
        assert len(nodes) == 2
        code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                namespace[const.co_name] = types.FunctionType(const, namespace)
        return namespace

    def contents(self):
        cells = self.buffers[self.list[abi.PYLISTOBJECT_ITEMS_OFFSET][1]]
        return cells[:self.list[abi.PYLISTOBJECT_LENGTH_OFFSET]]


def test_full_list_doubles_and_appends_in_two_transactions():
    memory = ListMemory(capacity=4, length=4)
    old = memory.list[abi.PYLISTOBJECT_ITEMS_OFFSET]
    namespace = memory.namespace()
    assert namespace["_append_existing_capacity_gc0"](memory.list_slot, memory.item_slot) == 1
    assert memory.locks == 2 and memory.depth == 0
    assert memory.list[abi.PYLISTOBJECT_CAPACITY_OFFSET] == 8
    assert memory.contents() == [("item", 0), ("item", 1), ("item", 2), ("item", 3), ("item", "new")]
    assert memory.freed == [old]


def test_a_list_changed_during_allocation_leaves_the_general_path():
    memory = ListMemory(capacity=4, length=4)
    namespace = memory.namespace()

    def regrow():
        memory.list[abi.PYLISTOBJECT_ITEMS_OFFSET] = ("buffer", id(memory.buffer(16)))
        memory.list[abi.PYLISTOBJECT_CAPACITY_OFFSET] = 16
    memory.change_during_allocation = regrow
    assert namespace["_append_existing_capacity_gc0"](memory.list_slot, memory.item_slot) == 0
    assert memory.list[abi.PYLISTOBJECT_LENGTH_OFFSET] == 4
    assert len(memory.freed) == 1 and memory.freed[0] != memory.list[abi.PYLISTOBJECT_ITEMS_OFFSET]


def test_spare_capacity_appends_in_one_transaction():
    memory = ListMemory(capacity=8, length=3)
    namespace = memory.namespace()
    assert namespace["_append_existing_capacity_gc0"](memory.list_slot, memory.item_slot) == 1
    assert memory.locks == 1 and memory.freed == []
    assert memory.contents()[-1] == ("item", "new")


PROGRAM = r'''
import pcc.virtual_thread as vt


def grow(seed):
    parts = []
    index = 0
    while index < 5000:
        parts.append(str(seed) + ":" + str(index))
        if index % 700 == 0:
            vt.yield_now()
        index += 1
    return len(parts), parts[0], parts[-1], len("".join(parts))


shared = []


def share(seed):
    index = 0
    while index < 3000:
        shared.append(seed * 100000 + index)
        if index % 500 == 0:
            vt.yield_now()
        index += 1
    return seed


def main():
    threads = [vt.spawn(grow, seed) for seed in range(4)]
    threads += [vt.spawn(share, seed) for seed in range(4)]
    vt.run(4, 100000000)
    print([vt.result(thread) for thread in threads])
    print(len(shared), len(set(shared)), sorted(shared)[:2], sorted(shared)[-1])


main()
'''


@pytest.mark.integration
def test_growing_appends_on_four_carriers_and_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "append_growth.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "append_growth"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    expected = ("[(5000, '0:0', '0:4999', 28890), (5000, '1:0', '1:4999', 28890), "
                "(5000, '2:0', '2:4999', 28890), (5000, '3:0', '3:4999', 28890), 0, 1, 2, 3]\n"
                "12000 12000 [0, 1] 302999\n")
    for backend in range(5):
        for attempt in range(2):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, ""), (backend, attempt)
