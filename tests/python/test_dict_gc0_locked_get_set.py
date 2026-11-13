"""Threaded GC0 dict get/set run as one transaction each.

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

In container mode (GC0 and the count-free tracers, without the probe) the
lookup and the set of an ordinary dict hold only the dict's own container
lock, so carriers working on different dicts do not queue on the graph
lock; every other transaction on the tables takes that lock inside the
graph lock. A class namespace's set keeps the graph lock outside the
container lock: attribute lookup reads namespace slots under the graph
lock alone.
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
        self.table = 0
        self.flags = {
            "pcc_gc_refcount_atomic": 1,
            "pcc_diagnostics_runtime_log_fast_state": 0,
            "pcc_gc_backend_selected": 0,
            "pcc_gc_foreign_lease_active": 0,
            "pcc_gc_list_container_mode": 1,
            "pcc_gc_nonmoving_count_free": 0,
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
        assert self.locked(), "probe outside the dict's transaction"
        self.store(insert_slot, 0, -1)
        if self.needs_eq:
            return -3
        if key in self.keys:
            return self.keys[key]
        self.store(insert_slot, 0, 5)
        return -1

    def try_init(self, destination, source):
        assert self.locked(), "retain outside the dict's transaction"
        value = self.load(source)
        if value in self.rejected:
            return 0
        if value:
            self.refs[value] += 1
        self.store(destination, 0, value)
        return 1

    def decref(self, obj):
        assert self.depth == 0 and self.table == 0, "release inside the transaction"
        self.calls.append(("decref", obj))
        self.refs[obj] -= 1

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        self.calls.append(("lock",))

    def unlock(self):
        assert self.depth == 1 and self.table == 0
        self.depth = 0
        self.calls.append(("unlock",))

    def locked(self):
        """Inside a transaction on the dict: its container lock in container
        mode, the graph lock otherwise."""
        if self.flags["pcc_gc_list_container_mode"]:
            return self.table == 1
        return self.depth == 1

    def table_lock(self, d):
        assert self.table == 0, "a container lock holder takes no second one"
        self.table = 1
        self.calls.append(("table_lock", d))

    def table_unlock(self, d):
        assert self.table == 1
        self.table = 0
        self.calls.append(("table_unlock", d))

    def rmw(self, op, address, offset, value, order):
        assert op == "or" and order == "release"
        old = self.load(address, offset)
        self.store(address, offset, old | value)
        return old


def _functions(heap, names):
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "load_i32": heap.load, "load_i64": heap.load, "load_ptr": heap.load,
        "atomic_load_i32": heap.load, "atomic_load_i64": heap.load,
        "atomic_rmw_i32": heap.rmw,
        "_ptr_is_dict": lambda pointer: pointer != 0,
        "store_ptr": heap.store, "store_i64": heap.store,
        "ptr_add": lambda pointer, offset: pointer + offset,
        "ptr_is_null": lambda pointer: pointer == 0,
        "ptr_eq": lambda left, right: left == right,
        "null": lambda: 0,
        "global_addr": lambda name: name,
        "stack_alloc": heap.alloc,
        "pcc_py_gc_minor_graph_lock": heap.lock,
        "pcc_py_gc_minor_graph_unlock": heap.unlock,
        "pcc_container_lock": heap.table_lock,
        "pcc_container_unlock": heap.table_unlock,
        "pcc_gc_try_init_ptr_locked": heap.try_init,
        "py_decref": heap.decref,
        "_dict_fast0_hash": lambda key, kind: 77,
        "_dict_probe_fast0": heap.probe,
        "_maybe_grow": lambda d: heap.calls.append(("grow", d)),
    })
    tree = ast.parse(DICT_SOURCE.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert sorted(node.name for node in nodes) == sorted(names)
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_DICT_CLASS_NAMESPACE_FLAG"):
            namespace["_DICT_CLASS_NAMESPACE_FLAG"] = ast.literal_eval(node.value)
    code = compile(ast.Module(nodes, type_ignores=[]), str(DICT_SOURCE), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace


LOCK_NAMES = ["_dict_table_lock", "_dict_table_unlock", "_dict_read_lock", "_dict_read_unlock"]
SET_NAMES = ["_dict_gc0_admitted_locked", "_dict_set_gc0_locked",
             "_dict_set_needs_graph_lock"] + LOCK_NAMES
GET_NAMES = ["_dict_gc0_admitted_locked", "_dict_get_gc0_locked"] + LOCK_NAMES


def _set_calls(d):
    """A set of an ordinary dict: the dict's container lock alone."""
    return [("table_lock", d), ("table_unlock", d)]


def _namespace_set_calls(d):
    """A set of a class namespace: the graph lock, and inside it the
    dict's container lock."""
    return [("lock",), ("table_lock", d), ("table_unlock", d), ("unlock",)]


def _get_calls(d):
    """A lookup: the dict's container lock alone."""
    return [("table_lock", d), ("table_unlock", d)]


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
    assert heap.calls == _set_calls(d) + [("decref", old)]


def test_storing_the_same_value_changes_nothing():
    heap = Heap()
    key, value = heap.obj(), heap.obj()
    d = heap.dict([(key, value)])
    ns = _functions(heap, SET_NAMES)
    assert ns["_dict_set_gc0_locked"](d, key, value, 2) == 1
    assert heap.refs[value] == 1
    assert heap.calls == _set_calls(d)


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
    assert heap.calls == _set_calls(d) + [("grow", d)]


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
    assert heap.calls == _set_calls(d) + [("decref", key)]


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
    assert heap.calls == _set_calls(d) + _get_calls(d)


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
    # Three lookups, none of them under the graph lock.
    assert heap.calls == _get_calls(d) * 3


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_the_count_free_tracers_share_both_transactions(backend):
    # Their retain is reachability: the same lookup and set are admitted
    # with the collector fixed, whatever the refcount mode says.
    heap = Heap()
    heap.flags.update({"pcc_gc_nonmoving_count_free": 1, "pcc_gc_refcount_atomic": 0,
                       "pcc_gc_backend_selected": backend})
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    ns = _functions(heap, SET_NAMES + ["_dict_get_gc0_locked"])
    out = heap.alloc(8)
    assert ns["_dict_get_gc0_locked"](d, key, 2, out) == 1
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 1
    assert heap.load(out) == old and _entry(heap, d, 0) == (key, new)
    assert heap.calls == _get_calls(d) + _set_calls(d) + [("decref", old)]


def test_without_container_mode_every_transaction_is_a_graph_transaction():
    # The provenance probe (and a collector not yet fixed) keeps the graph
    # lock as the dict's only lock, for readers and writers alike.
    heap = Heap()
    heap.flags["pcc_gc_list_container_mode"] = 0
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    ns = _functions(heap, SET_NAMES + ["_dict_get_gc0_locked"])
    out = heap.alloc(8)
    assert ns["_dict_get_gc0_locked"](d, key, 2, out) == 1
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 1
    assert heap.load(out) == old and _entry(heap, d, 0) == (key, new)
    assert heap.calls == [("lock",), ("unlock",), ("lock",), ("unlock",), ("decref", old)]


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
                   ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    expected_lines = expected.stdout.splitlines()
    events_line = expected_lines.index("['replace', 'del first', 'after', 'del second']")
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        if backend == 0:
            # The counting collector releases a replaced value at once, as
            # CPython does.
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
            continue
        # The count-free tracers finalize at a collection, not at the last
        # release; finalization timing is not a language guarantee. Every
        # other line is CPython's, and the events keep their program order
        # with each finalizer at most once.
        assert (ran.returncode, ran.stderr) == (0, ""), backend
        lines = ran.stdout.splitlines()
        assert lines[:events_line] + lines[events_line + 1:] == (
            expected_lines[:events_line] + expected_lines[events_line + 1:]
        ), backend
        events = ast.literal_eval(lines[events_line])
        program = [event for event in events if not event.startswith("del ")]
        finalized = [event for event in events if event.startswith("del ")]
        assert program == ["replace", "after"], backend
        assert len(set(finalized)) == len(finalized) and set(finalized) <= {"del first", "del second"}, backend


def test_class_namespace_set_keeps_the_graph_lock():
    # Attribute lookup reads a namespace's entry slots and copies the
    # selected value under the graph lock alone (py_class
    # _special_lookup_locked), so a namespace's set must exclude it.
    heap = Heap()
    key, old, new = heap.obj(), heap.obj(), heap.obj()
    d = heap.dict([(key, old)])
    ns = _functions(heap, SET_NAMES + ["py_dict_mark_class_namespace"])
    ns["py_dict_mark_class_namespace"](d)
    assert heap.load(d, abi.PYOBJECTHEADER_FLAGS_OFFSET) == 2097152
    assert ns["_dict_set_gc0_locked"](d, key, new, 2) == 1
    assert _entry(heap, d, 0) == (key, new)
    assert heap.refs[new] == 2 and heap.refs[old] == 0
    assert heap.calls == _namespace_set_calls(d) + [("decref", old)]


def test_namespace_flag_is_a_type_specific_dict_bit():
    # Type-specific header bits start at 2097152 (classes use this value for
    # PY_FLAG_CLASS_SLOTS_ONLY); no flag shared by every object uses it.
    shared = (abi.PY_FLAG_IMMORTAL | abi.PY_FLAG_GC_TRACKED | abi.PY_FLAG_FINALIZED
              | abi.PY_FLAG_GC_PINNED | abi.PY_FLAG_GC_MALLOC_ALLOC
              | abi.PY_FLAG_GC_DEALLOCATING)
    heap = Heap()
    ns = _functions(heap, ["_dict_set_needs_graph_lock"])
    flag = ns["_DICT_CLASS_NAMESPACE_FLAG"]
    assert flag == abi.PY_FLAG_CLASS_SLOTS_ONLY and flag & shared == 0


def test_every_graph_transaction_on_dict_tables_holds_the_table_lock():
    # A set of an ordinary dict holds only the table lock, so a transaction
    # that reads the tables under the graph lock alone could copy a value
    # the set is releasing. The graph lock appears only in the lock helpers,
    # in a function that also takes the table lock, or around a root store.
    tree = ast.parse(DICT_SOURCE.read_text())
    helpers = {"_dict_read_lock", "_dict_read_unlock", "_dict_graph_table_lock",
               "_dict_graph_table_unlock"}
    root_only = {"_dict_slot_adopt"}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name in helpers:
            continue
        called = {call.func.id for call in ast.walk(node)
                  if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}
        if "pcc_py_gc_minor_graph_lock" not in called or node.name in root_only:
            continue
        assert called & {"_dict_table_lock", "_dict_graph_table_lock"}, node.name
