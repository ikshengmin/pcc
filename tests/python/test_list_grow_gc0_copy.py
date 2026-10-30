"""A threaded GC0 list grows its item array with one copy.

Growth publishes a replacement array under the graph lock. For every item it
read the old slot through pcc_gc_load_ptr, recorded an old/new slot pair for
backend 4's retargeting and noted a write barrier, all no-ops on GC0 (no read
barrier, no relocation, no concurrent marking), after allocating and
zeroing the pair table. GC0 now copies the owners as raw words inside the
same locked revalidation; the other collectors keep their barriers.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import types

import pytest

from pcc.runtime.py import py_abi_constants as abi
from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
LIST_SOURCE = ROOT / "pcc/runtime/py/py_list.py"


class Memory:
    def __init__(self, backend):
        self.words = {}
        self.next = 1 << 20
        self.backend = backend
        self.calls = []

    def malloc(self, size):
        self.calls.append(("malloc", size))
        address = self.next
        self.next += size + 64
        return address

    def free(self, address):
        if address:
            self.calls.append(("free", address))

    def load(self, address, offset=0):
        key = (address, offset) if isinstance(address, str) else address + offset
        return self.words.get(key, 0)

    def store(self, address, offset, value):
        key = (address, offset) if isinstance(address, str) else address + offset
        self.words[key] = value

    def memset(self, address, value, size):
        for offset in range(0, size, 8):
            self.words[address + offset] = value

    def memmove(self, destination, source, size):
        self.calls.append(("copy", size))
        values = [self.load(source, offset) for offset in range(0, size, 8)]
        for offset, value in zip(range(0, size, 8), values):
            self.words[destination + offset] = value


def _grow(backend):
    memory = Memory(backend)
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}

    def barrier_load(owner, slot):
        memory.calls.append(("barrier_load", slot))
        return memory.load(slot)

    def write_barrier(owner, slot, value):
        memory.calls.append(("write_barrier", slot))

    namespace.update({
        "pcc_gc_backend": lambda: memory.backend,
        "pcc_threads_enabled": lambda: 1,
        "_ptr_is_list": lambda pointer: True,
        "_debug_bad_container": lambda pointer, code: None,
        "stack_alloc": lambda size: 1 << 16,
        "pcc_gc_scheduler_root_register_handle": lambda slot: 7,
        "pcc_gc_scheduler_root_unregister_handle": lambda handle: None,
        "pcc_py_gc_minor_graph_lock": lambda: memory.calls.append(("lock",)),
        "pcc_py_gc_minor_graph_unlock": lambda: memory.calls.append(("unlock",)),
        # The list's transaction lock (container or graph lock).
        "_list_lock": lambda _list: memory.calls.append(("lock",)),
        "_list_unlock": lambda _list: memory.calls.append(("unlock",)),
        "pcc_gc_load_ptr": barrier_load,
        "pcc_gc_note_slot_write_barrier": write_barrier,
        "pcc_gc_backend4_retarget_mutator_payload_locked": lambda *args: 1,
        "pcc_gc_backend4_zpage_register_owner_payload_span": lambda *args: None,
        "load_ptr": memory.load, "load_i64": memory.load, "load_i32": memory.load,
        "store_ptr": memory.store, "store_i64": memory.store,
        "ptr_add": lambda pointer, offset: pointer + offset,
        "ptr_is_null": lambda pointer: pointer == 0,
        "ptr_eq": lambda left, right: left == right,
        "null": lambda: 0,
        "global_addr": lambda name: name,
        "malloc": memory.malloc, "free": memory.free,
        "memset": memory.memset, "memmove": memory.memmove,
    })
    # Globals are addressed by name; runtime diagnostics stay off.
    memory.store("pcc_diagnostics_runtime_log_fast_state", 0, 0)
    nodes = [node for node in ast.parse(LIST_SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name == "_grow_if_needed"]
    assert len(nodes) == 1
    code = compile(ast.Module(nodes, type_ignores=[]), str(LIST_SOURCE), "exec")
    grow = next(types.FunctionType(const, namespace) for const in code.co_consts
                if isinstance(const, types.CodeType) and const.co_name == "_grow_if_needed")
    lst = memory.malloc(48)
    items = memory.malloc(32)
    for index in range(4):
        memory.store(items, index * 8, 101 + index)
    memory.store(lst, abi.PYLISTOBJECT_ITEMS_OFFSET, items)
    memory.store(lst, abi.PYLISTOBJECT_CAPACITY_OFFSET, 4)
    memory.store(lst, abi.PYLISTOBJECT_LENGTH_OFFSET, 4)
    memory.calls.clear()
    memory.items = items
    assert grow(lst, 5) == lst
    new_items = memory.load(lst, abi.PYLISTOBJECT_ITEMS_OFFSET)
    assert new_items != items
    assert memory.load(lst, abi.PYLISTOBJECT_CAPACITY_OFFSET) == 8
    assert [memory.load(new_items, index * 8) for index in range(4)] == [101, 102, 103, 104]
    assert ("free", items) in memory.calls
    return memory.calls


def test_gc0_grow_copies_owners_without_barriers_or_pair_table():
    calls = _grow(0)
    assert ("copy", 32) in calls
    assert not any(call[0] in ("barrier_load", "write_barrier") for call in calls)
    assert [call for call in calls if call[0] == "malloc"] == [("malloc", 64)]


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_other_collectors_keep_their_grow_barriers(backend):
    calls = _grow(backend)
    # GC3/GC4 also reload the list owner through the barrier; count items.
    item_loads = [call for call in calls if call[0] == "barrier_load" and call[1] != 1 << 16]
    assert len(item_loads) == 4
    assert sum(call[0] == "write_barrier" for call in calls) == 4
    assert [call for call in calls if call[0] == "malloc"] == [("malloc", 64), ("malloc", 64)]


PROGRAM = r'''
import pcc.virtual_thread as vt

shared = []


def grower(seed):
    mine = []
    for index in range(3000):
        mine.append(seed * 100000 + index)
        if index % 7 == 0:
            shared.append(seed)
        if index % 97 == 0:
            vt.yield_now()
    return len(mine), sum(mine)


def main():
    threads = [vt.spawn(grower, seed) for seed in range(12)]
    vt.run(4, 1000000)
    print([vt.result(thread) for thread in threads])
    counts = [0] * 12
    for seed in shared:
        counts[seed] += 1
    print(len(shared), counts)


main()
'''

EXPECTED = (
    "[" + ", ".join(f"(3000, {seed * 100000 * 3000 + 4498500})" for seed in range(12)) + "]\n"
    + "5148 [" + ", ".join(["429"] * 12) + "]\n"
)


@pytest.mark.integration
def test_concurrent_growth_on_four_carriers_and_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "list_grow.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "list_grow"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, attempt)
