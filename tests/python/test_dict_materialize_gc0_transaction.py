"""GC0 eager dict keys/values/items: one mode reservation, no slot frame.

The general materialization registers a 16-slot root group, copies the
receiver and every selected child into leased roots, builds each pair from
owned roots and appends through the leased list protocol: about 30k
instructions for list(d.items()) of two entries. GC0 never relocates and its
cycle collector derives roots from reference counts, so a counted reference
held by the runtime keeps a private partial result alive. One mode-lease
reservation keeps a backend switch out; each entry is retained inside one
graph transaction by pcc_gc_try_init_ptr_locked, the GC0 list append's own
retain. A retain miss releases everything and the general path starts over.
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
KINDS = {"keys": 0, "values": 1, "items": 2}


class Heap:
    """Word memory with counted objects; strings name runtime globals."""

    def __init__(self):
        self.words = {}
        self.next = 1 << 20
        self.refs = {}
        self.kinds = {}
        self.freed = set()
        self.calls = []
        self.depth = 0
        self.error = None
        self.flags = {
            "pcc_gc_refcount_atomic": 1,
            "pcc_diagnostics_runtime_log_fast_state": 0,
            "pcc_gc_backend_selected": 0,
        }
        self.rejected = set()
        self.fail_alloc = None
        self.reserve_result = 0
        self.after_reserve = None
        self.on_lock = None

    def alloc(self, size, kind):
        address = self.next
        self.next += size + 64
        self.refs[address] = 1
        self.kinds[address] = kind
        return address

    def load(self, address, offset=0):
        if isinstance(address, str):
            return self.flags[address]
        return self.words.get(address + offset, 0)

    def store(self, address, offset, value):
        self.words[address + offset] = value

    def obj(self, kind="str"):
        return self.alloc(32, kind)

    def dict(self, pairs):
        receiver = self.alloc(64, "dict")
        entries = self.alloc(max(1, len(pairs)) * abi.DICTENTRY_SIZE, "entries")
        for index, (key, value) in enumerate(pairs):
            base = entries + index * abi.DICTENTRY_SIZE
            self.store(base, abi.DICTENTRY_KEY_OFFSET, key)
            self.store(base, abi.DICTENTRY_VALUE_OFFSET, value)
        self.store(receiver, abi.PYDICTOBJECT_ENTRIES_OFFSET, entries)
        self.store(receiver, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET, len(pairs))
        return receiver

    def list_new(self, capacity):
        self.calls.append(("list_new", capacity))
        if self.fail_alloc == "list":
            self.error = (19, "list")
            return 0
        lst = self.alloc(48, "list")
        self.store(lst, abi.PYLISTOBJECT_ITEMS_OFFSET, self.alloc(capacity * 8, "items"))
        self.store(lst, abi.PYLISTOBJECT_CAPACITY_OFFSET, capacity)
        self.store(lst, abi.PYLISTOBJECT_LENGTH_OFFSET, 0)
        return lst

    def tuple_new(self, count):
        self.calls.append(("tuple_new", count))
        if self.fail_alloc == "tuple":
            self.error = (19, "tuple")
            return 0
        pair = self.alloc(abi.PYTUPLEOBJECT_ITEMS_OFFSET + count * 8, "tuple")
        self.store(pair, abi.PYTUPLEOBJECT_LEN_OFFSET, count)
        return pair

    def children(self, obj):
        if self.kinds[obj] == "list":
            items = self.load(obj, abi.PYLISTOBJECT_ITEMS_OFFSET)
            return [self.load(items, index * 8)
                    for index in range(self.load(obj, abi.PYLISTOBJECT_LENGTH_OFFSET))]
        if self.kinds[obj] == "tuple":
            return [self.load(obj, abi.PYTUPLEOBJECT_ITEMS_OFFSET + index * 8)
                    for index in range(self.load(obj, abi.PYTUPLEOBJECT_LEN_OFFSET))]
        return []

    def decref(self, obj):
        self.calls.append(("decref", obj))
        assert obj not in self.freed and self.refs[obj] > 0
        self.refs[obj] -= 1
        if self.refs[obj] == 0:
            self.freed.add(obj)
            for child in self.children(obj):
                if child:
                    self.decref(child)

    def try_init(self, destination, source):
        assert self.depth == 1, "retain outside the graph transaction"
        value = self.load(source)
        if value in self.rejected:
            return 0
        if value:
            assert value not in self.freed
            self.refs[value] += 1
        self.store(destination, 0, value)
        return 1

    def reserve(self, shard):
        self.calls.append(("reserve", shard))
        if self.after_reserve:
            self.after_reserve()
        return self.reserve_result

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        self.calls.append(("lock",))
        if self.on_lock:
            self.on_lock(sum(call == ("lock",) for call in self.calls))

    def unlock(self):
        assert self.depth == 1
        self.depth = 0

    def raise_owned(self, exc):
        self.error = exc


def _materializer(heap):
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    tree = ast.parse(DICT_SOURCE.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("_DICT_MATERIALIZE_")):
            assert isinstance(node.value, ast.Constant)
            namespace[node.targets[0].id] = node.value.value

    def abort():
        raise AssertionError("abort")

    namespace.update({
        "load_i32": heap.load, "load_i64": heap.load, "load_ptr": heap.load,
        "store_ptr": heap.store, "store_i64": heap.store,
        "ptr_add": lambda pointer, offset: pointer + offset,
        "ptr_is_null": lambda pointer: pointer == 0,
        "null": lambda: 0,
        "global_addr": lambda name: name,
        "cstr": lambda text: text,
        "pcc_gc_mode_lease_shard_index": lambda pointer: 5,
        "pcc_gc_mode_lease_reserve_shard": heap.reserve,
        "pcc_gc_mode_lease_release_shard": lambda shard: heap.calls.append(("release", shard)) or 0,
        "pcc_py_gc_minor_graph_lock": heap.lock,
        "pcc_py_gc_minor_graph_unlock": heap.unlock,
        "py_list_new": heap.list_new,
        "py_tuple_new": heap.tuple_new,
        "pcc_gc_try_init_ptr_locked": heap.try_init,
        "pcc_tuple_item_can_participate_in_cycle":
            lambda item: 1 if heap.kinds.get(item) in ("list", "dict") else 0,
        "py_gc_track": lambda obj: heap.calls.append(("track", obj)),
        "pcc_gc_publish_initialized": lambda obj: heap.calls.append(("publish", obj)),
        "py_decref": heap.decref,
        "py_err_occurred": lambda: 1 if heap.error is not None else 0,
        "py_raise_owned": heap.raise_owned,
        "py_exc_new": lambda code, message: (code, message),
        "pcc_platform_abort": abort,
    })
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "_dict_gc0_materialize"]
    assert len(nodes) == 1
    code = compile(ast.Module(nodes, type_ignores=[]), str(DICT_SOURCE), "exec")
    function = next(types.FunctionType(const, namespace) for const in code.co_consts
                    if isinstance(const, types.CodeType) and const.co_name == "_dict_gc0_materialize")

    def run(receiver, kind):
        output = heap.alloc(8, "slot")
        heap.calls.clear()
        status = function(receiver, KINDS[kind], output)
        assert heap.depth == 0
        return status, heap.load(output)
    return run


def _sample(heap):
    a, b, c, x, y = (heap.obj() for _ in range(5))
    inner = heap.obj("list")
    # The third entry was deleted: its key is NULL and it is skipped.
    receiver = heap.dict([(a, x), (b, inner), (0, 0), (c, y)])
    return receiver, (a, b, c), (x, inner, y)


def _baseline(heap):
    # Counted objects only: item arrays, entry tables and slots are raw memory.
    return {address: count for address, count in heap.refs.items()
            if address not in heap.freed and heap.kinds[address] in ("str", "list", "tuple", "dict")}


def test_gc0_items_are_one_reservation_and_one_transaction_per_entry():
    heap = Heap()
    run = _materializer(heap)
    receiver, keys, values = _sample(heap)
    before = _baseline(heap)
    status, out = run(receiver, "items")
    assert status == 1
    pairs = heap.children(out)
    assert [heap.children(pair) for pair in pairs] == [list(pair) for pair in zip(keys, values)]
    assert [call for call in heap.calls if call[0] in ("reserve", "release")] == [("reserve", 5), ("release", 5)]
    # One snapshot of the entry count plus one transaction per used entry.
    assert heap.calls.count(("lock",)) == 1 + 4
    # Only the pair holding a container is tracked; every pair is published.
    assert [call[1] for call in heap.calls if call[0] == "track"] == [pairs[1]]
    assert [call[1] for call in heap.calls if call[0] == "publish"] == pairs
    for child in keys + values:
        assert heap.refs[child] == before[child] + 1
    heap.decref(out)
    assert _baseline(heap) == before


@pytest.mark.parametrize("kind", ["keys", "values"])
def test_gc0_keys_and_values_retain_selected_children_only(kind):
    heap = Heap()
    run = _materializer(heap)
    receiver, keys, values = _sample(heap)
    before = _baseline(heap)
    status, out = run(receiver, kind)
    assert status == 1
    selected, other = (keys, values) if kind == "keys" else (values, keys)
    assert heap.children(out) == list(selected)
    assert not any(call[0] in ("tuple_new", "track") for call in heap.calls)
    assert all(heap.refs[child] == before[child] + 1 for child in selected)
    assert all(heap.refs[child] == before[child] for child in other)
    heap.decref(out)
    assert _baseline(heap) == before


@pytest.mark.parametrize("flag,value", [
    ("pcc_gc_refcount_atomic", 0),
    ("pcc_diagnostics_runtime_log_fast_state", 1),
    ("pcc_gc_backend_selected", 3),
])
def test_other_configurations_take_the_general_protocol(flag, value):
    heap = Heap()
    run = _materializer(heap)
    receiver, _, _ = _sample(heap)
    heap.flags[flag] = value
    assert run(receiver, "items") == (0, 0)
    assert heap.calls == []


def test_refused_reservation_takes_the_general_protocol():
    heap = Heap()
    run = _materializer(heap)
    receiver, _, _ = _sample(heap)
    heap.reserve_result = -1
    assert run(receiver, "items") == (0, 0)
    assert heap.calls == [("reserve", 5)]


def test_switch_completed_before_the_reservation_releases_it():
    heap = Heap()
    run = _materializer(heap)
    receiver, _, _ = _sample(heap)
    heap.after_reserve = lambda: heap.flags.update(pcc_gc_backend_selected=4)
    assert run(receiver, "items") == (0, 0)
    assert heap.calls == [("reserve", 5), ("release", 5)]


@pytest.mark.parametrize("kind", ["items", "values"])
@pytest.mark.parametrize("refused", [0, 1, 2])
def test_retain_miss_releases_the_partial_result(kind, refused):
    heap = Heap()
    run = _materializer(heap)
    receiver, _, values = _sample(heap)
    before = _baseline(heap)
    heap.rejected.add(values[refused])
    created = heap.next
    assert run(receiver, kind) == (0, 0)
    assert heap.calls[-1] == ("release", 5)
    assert _baseline(heap) == before
    # Every list and pair this attempt allocated is gone again.
    assert all(address in heap.freed for address, kind_name in heap.kinds.items()
               if address >= created and kind_name in ("list", "tuple"))


def test_entry_removed_between_transactions_is_skipped():
    heap = Heap()
    run = _materializer(heap)
    receiver, keys, values = _sample(heap)
    entries = heap.load(receiver, abi.PYDICTOBJECT_ENTRIES_OFFSET)

    def delete_second(lock_number):
        # Lock 1 snapshots the count; lock 3 reads the second entry.
        if lock_number == 3:
            base = entries + abi.DICTENTRY_SIZE
            heap.store(base, abi.DICTENTRY_KEY_OFFSET, 0)
            heap.store(base, abi.DICTENTRY_VALUE_OFFSET, 0)
    heap.on_lock = delete_second
    status, out = run(receiver, "items")
    assert status == 1
    assert [heap.children(pair) for pair in heap.children(out)] == [
        [keys[0], values[0]], [keys[2], values[2]]]


@pytest.mark.parametrize("stage", ["list", "tuple"])
def test_allocation_failure_raises_and_releases(stage):
    heap = Heap()
    run = _materializer(heap)
    receiver, _, _ = _sample(heap)
    before = _baseline(heap)
    heap.fail_alloc = stage
    assert run(receiver, "items") == (-1, 0)
    assert heap.error is not None
    assert heap.calls[-1] == ("release", 5)
    assert _baseline(heap) == before


PROGRAM = r'''
import gc
import weakref

events = []


class Box:
    def __init__(self, name):
        self.name = name
        self.payload = None

    def __del__(self):
        events.append("del " + self.name)


def views(d):
    return list(d.keys()), list(d.values()), list(d.items())


def main():
    print(views({}))
    print(views({"name": "Ada", "plan": "pro"}))
    print(sorted({"profile": {"name": "Ada", "plan": "pro"},
                  "notifications": ["Your report is ready"]}.items()))
    sparse = {}
    for index in range(40):
        sparse[index] = str(index)
    for index in range(0, 40, 3):
        del sparse[index]
    print(views(sparse))
    mixed = {"small": 7, "big": 10 ** 30, "float": 2.5, "none": None, "flag": True,
             "bytes": b"x", "tuple": (1, "a"), "list": [1, [2]], "dict": {"k": "v"}}
    print(views(mixed))
    print(views({(1, "a"): "first", (2, "b"): ["second"]}))
    print(views({"before": 1, "dots": ..., "after": NotImplemented, "end": [3]}))
    total = 0
    round_index = 0
    while round_index < 3000:
        d = {"profile": {"name": "Ada", "plan": "pro"},
             "notifications": ["Your report is ready"], "n": round_index}
        for key, value in sorted(d.items()):
            total += len(key)
        total += len(d.keys()) + len(d.values()) + len(d["profile"].items())
        round_index += 1
    print(total)
    # A cycle through materialized pairs: holder -> pairs -> holder.
    d = {"self": None}
    holder = Box("holder")
    d["holder"] = holder
    holder.payload = d.items()
    probe = weakref.ref(holder)
    del holder
    del d
    gc.collect()
    print(probe() is None, events)


main()
'''


@pytest.mark.integration
def test_materialized_views_match_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "views.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "views"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
