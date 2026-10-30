"""Threaded GC0 dict get/set run as one graph transaction each.

On the threaded runtime the unlocked GC0 probe is unsafe (another carrier
may rehash the table), so get and set went through the general rooted
operation: a slot frame, leased roots and a store plan per call, about 5% of
a request (module attribute lookups and dict displays). The probe for a str
or tagged-int key runs no callback, so the lookup, the retain of a found
value (pcc_gc_try_init_ptr_locked) and an insert or replacement fit in one
graph transaction; a replaced value is released after the unlock, where its
finalizer may run. Anything the transaction cannot finish takes the rooted
operation from the start. A dict built from static pairs is private until it
is returned, so it keeps the unlocked insert.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.runtime.py import py_abi_constants as abi
from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
DICT_SOURCE = ROOT / "pcc/runtime/py/py_dict.py"


class Heap:
    def __init__(self):
        self.words = {}
        self.next = 1 << 20
        self.refs = {}
        self.calls = []
        self.depth = 0
        self.flags = {
            "pcc_gc_refcount_atomic": 1,
            "pcc_diagnostics_runtime_log_fast_state": 0,
            "pcc_gc_backend_selected": 0,
            "pcc_gc_foreign_lease_active": 0,
        }
        self.rejected = set()
        self.keys = {}
        self.needs_eq = False

    def alloc(self, size):
        address = self.next
        self.next += size + 64
        return address

    def obj(self):
        address = self.alloc(32)
        self.refs[address] = 1
        return address

    def load(self, address, offset=0, *_):
        if isinstance(address, str):
            return self.flags[address]
        return self.words.get(address + offset, 0)

    def store(self, address, offset, value):
        self.words[address + offset] = value

    def dict(self, pairs, capacity=8):
        d = self.alloc(64)
        entries = self.alloc(capacity * abi.DICTENTRY_SIZE)
        indices = self.alloc(capacity * 8)
        for index, (key, value) in enumerate(pairs):
            base = entries + index * abi.DICTENTRY_SIZE
            self.store(base, abi.DICTENTRY_KEY_OFFSET, key)
            self.store(base, abi.DICTENTRY_VALUE_OFFSET, value)
            self.keys[key] = index
        self.store(d, abi.PYDICTOBJECT_ENTRIES_OFFSET, entries)
        self.store(d, abi.PYDICTOBJECT_INDICES_OFFSET, indices)
        self.store(d, abi.PYDICTOBJECT_CAPACITY_OFFSET, capacity)
        self.store(d, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET, len(pairs))
        self.store(d, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET, len(pairs))
        return d

    def probe(self, d, key, kind, hash_value, insert_slot):
        assert self.depth == 1, "probe outside the graph transaction"
        self.store(insert_slot, 0, -1)
        if self.needs_eq:
            return -3
        if key in self.keys:
            return self.keys[key]
        self.store(insert_slot, 0, 5)
        return -1

    def try_init(self, destination, source):
        assert self.depth == 1, "retain outside the graph transaction"
        value = self.load(source)
        if value in self.rejected:
            return 0
        if value:
            self.refs[value] += 1
        self.store(destination, 0, value)
        return 1

    def decref(self, obj):
        assert self.depth == 0, "release inside the graph transaction"
        self.calls.append(("decref", obj))
        self.refs[obj] -= 1

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        self.calls.append(("lock",))

    def unlock(self):
        assert self.depth == 1
        self.depth = 0
        self.calls.append(("unlock",))


def _functions(heap, names):
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "load_i32": heap.load, "load_i64": heap.load, "load_ptr": heap.load,
        "atomic_load_i64": heap.load,
        "store_ptr": heap.store, "store_i64": heap.store,
        "ptr_add": lambda pointer, offset: pointer + offset,
        "ptr_is_null": lambda pointer: pointer == 0,
        "ptr_eq": lambda left, right: left == right,
        "null": lambda: 0,
        "global_addr": lambda name: name,
        "stack_alloc": heap.alloc,
        "pcc_py_gc_minor_graph_lock": heap.lock,
        "pcc_py_gc_minor_graph_unlock": heap.unlock,
        "pcc_gc_try_init_ptr_locked": heap.try_init,
        "py_decref": heap.decref,
        "_dict_fast0_hash": lambda key, kind: 77,
        "_dict_probe_fast0": heap.probe,
        "_maybe_grow": lambda d: heap.calls.append(("grow", d)),
    })
    tree = ast.parse(DICT_SOURCE.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert sorted(node.name for node in nodes) == sorted(names)
    code = compile(ast.Module(nodes, type_ignores=[]), str(DICT_SOURCE), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace


SET_NAMES = ["_dict_gc0_admitted_locked", "_dict_set_gc0_locked"]
GET_NAMES = ["_dict_gc0_admitted_locked", "_dict_get_gc0_locked"]


def _entry(heap, d, index):
    base = heap.load(d, abi.PYDICTOBJECT_ENTRIES_OFFSET) + index * abi.DICTENTRY_SIZE
    return heap.load(base, abi.DICTENTRY_KEY_OFFSET), heap.load(base, abi.DICTENTRY_VALUE_OFFSET)


def test_replacement_retains_inside_and_releases_after_the_transaction():
    heap = Heap()
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 1
    assert _entry(heap, d, 0) == (key, new)
    assert heap.refs[new] == 2 and heap.refs[old] == 0
    assert heap.calls == [("lock",), ("unlock",), ("decref", old)]


def test_storing_the_same_value_changes_nothing():
    heap = Heap()
    key, value = heap.obj(), heap.obj()
    d = heap.dict([(key, value)])
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, value, 2) == 1
    assert heap.refs[value] == 1
    assert heap.calls == [("lock",), ("unlock",)]


def test_insert_publishes_retained_key_and_value_then_grows_outside():
    heap = Heap()
    key, value, other = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(other, other)])
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, value, 2) == 1
    assert _entry(heap, d, 1) == (key, value)
    assert heap.refs[key] == 2 and heap.refs[value] == 2
    assert heap.load(d, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET) == 2
    assert heap.load(d, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET) == 2
    indices = heap.load(d, abi.PYDICTOBJECT_INDICES_OFFSET)
    assert heap.load(indices, 5 * 8) == 1
    entries = heap.load(d, abi.PYDICTOBJECT_ENTRIES_OFFSET)
    assert heap.load(entries, abi.DICTENTRY_SIZE + abi.DICTENTRY_HASH_OFFSET) == 77
    assert heap.calls == [("lock",), ("unlock",), ("grow", d)]


def test_full_table_takes_the_rooted_operation_unchanged():
    heap = Heap()
    key, value = heap.obj(), heap.obj()
    others = [heap.obj() for _ in range(2)]
    d = heap.dict([(o, o) for o in others], capacity=2)
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, value, 2) == 0
    assert heap.refs[key] == 1 and heap.refs[value] == 1
    assert heap.load(d, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET) == 2


def test_refused_value_retain_releases_the_key_after_the_transaction():
    heap = Heap()
    key, value, other = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(other, other)])
    heap.rejected.add(value)
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, value, 2) == 0
    assert _entry(heap, d, 1) == (0, 0)
    assert heap.refs[key] == 1 and heap.refs[value] == 1
    assert heap.load(d, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET) == 1
    assert heap.calls == [("lock",), ("unlock",), ("decref", key)]


@pytest.mark.parametrize("flag,value", [
    ("pcc_gc_refcount_atomic", 0),
    ("pcc_diagnostics_runtime_log_fast_state", 1),
    ("pcc_gc_backend_selected", 3),
    ("pcc_gc_foreign_lease_active", -1),
])
def test_unadmitted_transactions_change_nothing(flag, value):
    heap = Heap()
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    heap.flags[flag] = value
    ns = _functions(heap, SET_NAMES + ["_dict_get_gc0_locked"])
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 0
    out = heap.alloc(8)
    assert ns["_dict_get_gc0_locked"](d, key, 2, out) == -1
    assert _entry(heap, d, 0) == (key, old)
    assert heap.refs[old] == 1 and heap.refs[new] == 1
    assert heap.calls == [("lock",), ("unlock",), ("lock",), ("unlock",)]


def test_equality_callbacks_take_the_rooted_operation():
    heap = Heap()
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    heap.needs_eq = True
    ns = _functions(heap, SET_NAMES + ["_dict_get_gc0_locked"])
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 0
    assert ns["_dict_get_gc0_locked"](d, key, 2, heap.alloc(8)) == -1
    assert heap.refs[new] == 1


def test_get_retains_a_found_value_inside_the_transaction():
    heap = Heap()
    key, value, missing = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, value)])
    ns = _functions(heap, GET_NAMES)
    out = heap.alloc(8)
    assert ns["_dict_get_gc0_locked"](d, key, 2, out) == 1
    assert heap.load(out) == value and heap.refs[value] == 2
    assert ns["_dict_get_gc0_locked"](d, missing, 2, heap.alloc(8)) == 0
    heap.rejected.add(value)
    assert ns["_dict_get_gc0_locked"](d, key, 2, heap.alloc(8)) == -1
    assert heap.refs[value] == 2


PROGRAM = r'''
events = []


class Tracked:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        events.append("del " + self.name)


def main():
    d = {}
    for index in range(40):
        d["k" + str(index)] = index
        d[index] = "v" + str(index)
    print(len(d), d["k7"], d[39], d.get("missing"), d.get(41, "default"))
    # Collisions: hash(-1) == hash(-2) in CPython.
    d[-1] = "minus one"
    d[-2] = "minus two"
    print(d[-1], d[-2])
    # Keys equal across types take the general path.
    d[1] = "int"
    d[True] = "bool"
    d[1.0] = "float"
    print(d[1], len(d))
    # Replacement releases the old value at once.
    holder = {"slot": Tracked("first")}
    events.append("replace")
    holder["slot"] = Tracked("second")
    events.append("after")
    holder["slot"] = holder["slot"]
    del holder
    print(events)
    total = 0
    round_index = 0
    while round_index < 2000:
        profile = {"name": "Ada", "plan": "pro"}
        profile["plan"] = profile["plan"] + "!"
        total += len(profile["name"]) + len(profile["plan"])
        round_index += 1
    print(total, profile)


main()
'''


@pytest.mark.integration
def test_dict_get_set_match_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "dict_get_set.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "dict_get_set"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
