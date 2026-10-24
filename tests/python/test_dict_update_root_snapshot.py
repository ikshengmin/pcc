"""Small exact-dict updates keep their snapshot in the existing root frame."""

import os
import subprocess
import sys

import pytest

from tests.python.test_dict_super_slot_roots import DictMemory


@pytest.mark.parametrize("size", (0, 1, 2, 6, 7, 12))
@pytest.mark.parametrize("phase", ("never", "register", "copy", "acquire", "release", "callback"))
def test_snapshot_uses_registered_cells_without_a_temporary_tuple(size, phase):
    memory = DictMemory(phase)
    expected = {"key" + str(index): "value" + str(index) for index in range(size)}
    destination = memory.wrap({"existing": -1})
    source = memory.wrap(expected)
    caller = memory.input_roots((destination, source))
    allocations = []
    original = memory.ns["py_tuple_new"]
    def tuple_new(count):
        allocations.append(count)
        return original(count)
    memory.ns["py_tuple_new"] = tuple_new
    assert memory.ns["py_dict_update_slots"](caller, memory.add(caller, 8)) == 0
    assert memory.error is None
    assert memory.unwrap(memory.read(caller, 0)) == {"existing": -1, **expected}
    assert memory.unwrap(memory.read(caller, 8)) == expected
    assert allocations == ([] if size <= 6 else [size * 2])
    assert all(obj.leases == 0 for obj in memory.objects if obj.alive and obj is not memory.none)


def test_root_snapshot_retires_entries_in_reverse_tuple_order():
    memory = DictMemory("release")
    pairs = [("first", "one"), ("second", "two"), ("third", "three")]
    caller = memory.input_roots((memory.wrap({}), memory.wrap(dict(pairs))))
    update_frame = []
    retired = []
    original_open = memory.ns["_dict_slot_open"]
    original_drop = memory.ns["_dict_slot_drop"]
    def open_frame(slots, tokens, handles, width):
        if width == 16:
            update_frame.append(slots)
        return original_open(slots, tokens, handles, width)
    def drop(slots, tokens, index):
        if update_frame and slots is update_frame[0] and 3 <= index <= 14:
            value = memory.read(slots, index * 8)
            if value is not None:
                retired.append(memory.unwrap(value))
        original_drop(slots, tokens, index)
    memory.ns["_dict_slot_open"] = open_frame
    memory.ns["_dict_slot_drop"] = drop
    assert memory.ns["py_dict_update_slots"](caller, memory.add(caller, 8)) == 0
    assert retired == [item for pair in pairs for item in pair][::-1]


@pytest.mark.parametrize("failure", range(1, 9))
def test_partial_snapshot_copy_failure_preserves_error_and_releases_leases(failure):
    memory = DictMemory("release", fail_copy=failure)
    caller = memory.input_roots((memory.wrap({}), memory.wrap({"a": "one", "b": "two", "c": "three"})))
    assert memory.ns["py_dict_update_slots"](caller, memory.add(caller, 8)) == -1
    assert memory.error is not None and memory.error[0] == 15
    assert all(obj.leases == 0 for obj in memory.objects if obj.alive and obj is not memory.none)


@pytest.mark.parametrize("size", (2, 6, 7))
@pytest.mark.parametrize("fail", (False, True))
def test_snapshot_keeps_source_values_when_equality_clears_source(size, fail):
    from pcc.runtime.py import py_abi_constants as abi
    from tests.python.test_set_call_slot_roots import Object

    memory = DictMemory("callback")
    expected = {"key" + str(index): "value" + str(index) for index in range(size)}
    original = {key: None for key in expected}
    caller = memory.input_roots((memory.wrap(original), memory.wrap(expected)))
    def clear_source():
        source = memory.read(caller, 8)
        entries = memory.read(source, abi.PYDICTOBJECT_ENTRIES_OFFSET)
        used = memory.read(source, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET)
        for index in range(used):
            for offset in (abi.DICTENTRY_KEY_OFFSET, abi.DICTENTRY_VALUE_OFFSET):
                address = index * abi.DICTENTRY_SIZE + offset
                child = memory.read(entries, address)
                if isinstance(child, Object):
                    child.refs -= 1
                memory.write(entries, address, None)
        indices = memory.read(source, abi.PYDICTOBJECT_INDICES_OFFSET)
        for index in range(memory.read(source, abi.PYDICTOBJECT_CAPACITY_OFFSET)):
            memory.write(indices, index * 8, -1)
        memory.write(source, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET, 0)
        memory.write(source, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET, 0)
        memory.collect("callback")
        if fail:
            memory.error = (2, "comparison failed")
    memory.on_equal = clear_source
    status = memory.ns["py_dict_update_slots"](caller, memory.add(caller, 8))
    assert status == (-1 if fail else 0)
    assert memory.error == ((2, "comparison failed") if fail else None)
    assert memory.unwrap(memory.read(caller, 0)) == (original if fail else expected)
    assert memory.unwrap(memory.read(caller, 8)) == {}
    assert all(obj.leases == 0 for obj in memory.objects if obj.alive and obj is not memory.none)


PROGRAM = '''import gc
import threading
active_source = None
drop_source = False
hash_calls = 0
dead = []
class Key:
    def __init__(self, value, role):
        self.value = value
        self.role = role
    def __hash__(self):
        global hash_calls
        hash_calls += 1
        return 5
    def __eq__(self, other):
        global active_source, drop_source
        if drop_source and self.role == "destination":
            drop_source = False
            active_source = None
            gc.collect()
        return self.value == other.value
class Value:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        dead.append(self.value)
def exercise(size):
    global active_source, drop_source
    destination = {}
    original_keys = []
    for index in range(size):
        key = Key(index, "destination")
        original_keys.append(key)
        destination[key] = None
    active_source = {}
    for index in range(size):
        active_source[Key(index, "source")] = Value(index)
    before = hash_calls
    drop_source = size > 0
    destination.update(active_source)
    assert hash_calls == before
    assert len(destination) == size
    keys = list(destination)
    for index in range(size):
        assert keys[index] is original_keys[index]
        assert destination[keys[index]].value == index
    gc.collect()
    assert dead == []
    active_source = None
    destination.clear()
    gc.collect()
    assert sorted(dead) == list(range(size))
    dead.clear()
    ordinary = {"b": [2], "a": [1]}
    copied = {}
    copied.update(ordinary)
    assert list(copied) == ["b", "a"]
    assert copied["a"] is ordinary["a"]
    copied.update(copied)
    assert list(copied) == ["b", "a"]
    ordinary.clear()
    gc.collect()
    assert copied == {"b": [2], "a": [1]}
def worker(seed, result):
    total = 0
    for index in range(100):
        source = {"a": [index], "b": [index + 1]}
        target = {}
        target.update(source)
        assert target["a"] is source["a"]
        source.clear()
        if index % 10 == 0:
            gc.collect()
        total += target["a"][0] + target["b"][0]
    result[seed] = total
def main():
    for size in (0, 1, 2, 6, 7, 32):
        exercise(size)
    result = [-1, -1]
    first = threading.Thread(target=worker, args=(0, result))
    second = threading.Thread(target=worker, args=(1, result))
    first.start()
    second.start()
    first.join()
    second.join()
    assert result == [10000, 10000]
    print("DICT_ROOT_SNAPSHOT_OK")
main()
'''


def test_dict_snapshot_reference(tmp_path):
    source = tmp_path / "reference.py"
    source.write_text(PROGRAM)
    result = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "DICT_ROOT_SNAPSHOT_OK\n"


@pytest.mark.integration
def test_dict_root_snapshot_native_all_gc(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "dict_snapshot.py"
    source.write_text(PROGRAM)
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=40,
                                env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"))
        (tmp_path / ("gc" + str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(gc) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "DICT_ROOT_SNAPSHOT_OK\n"
