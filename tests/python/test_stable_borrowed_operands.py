"""Non-consuming helper inputs can reuse an unchanged borrowed parameter root."""
import re

import pytest

from tests.python.test_foreign_address_leases import Model, _mode_setter
from tests.python.test_slot_call_operand_roots import _calls, _emit, _probe_function
from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc


def test_append_reuses_stable_parameter_roots_and_keeps_address_leases():
    body = _probe_function(_emit("def probe(value, item):\n    value.append(item)\n"))
    leases = _calls(body, "pcc_gc_borrowed_lease_acquire")
    # Type guard and generic getattr still need a receiver address lease.
    # The exact-list runtime helper consumes authoritative slot addresses,
    # so it protects/reloads both inputs itself instead of leasing raw args.
    assert len(leases) == 2
    assert all("value.addr" in call for call in leases)
    assert not any("item.addr" in call for call in leases)
    assert re.search(r"@py_list_append_from_slots\(ptr %value.addr[^,]*, "
                     r"ptr %item.addr[^,]*, i64 3\)", body)
    assert _calls(body, "pcc_gc_foreign_lease_release")
    assert "list.append.receiver.operand" not in body
    assert "list.append.item.operand" not in body
    for call in _calls(body, "pcc_gc_store_root"):
        assert "value.addr" not in call and "item.addr" not in call


@pytest.mark.parametrize("source", [
    "def probe(value, item):\n    value = []\n    value.append(item)\n",
    "shared = []\ndef probe(item):\n    shared.append(item)\n",
    "def probe(value, item):\n    def change():\n        nonlocal value\n        value = []\n    value.append(change())\n",
])
def test_rebound_global_and_cell_receivers_keep_independent_owners(source):
    body = _probe_function(_emit(source))
    leases = _calls(body, "pcc_gc_borrowed_lease_acquire")
    assert not any("value.addr" in call or "shared" in call for call in leases)


@pytest.mark.parametrize("backend", range(5))
def test_borrowed_lease_preserves_owner_counts_and_blocks_mode_reset(backend):
    model = Model(backend)
    slot = model.object(flags=262144)
    model.store(1000, 0, 7)
    token = model.ns["pcc_gc_borrowed_lease_acquire"](slot)
    assert token == 1 and model.count() == 1 and model.active() == 1
    assert model.load(1000, 0) == 7
    assert _mode_setter(model)((backend + 1) % 5) == -1
    assert model.release(slot, token) == 0
    assert model.count() == 0 and model.active() == 0 and model.load(1000, 0) == 7


@pytest.mark.parametrize("backend", (3, 4))
def test_borrowed_lease_reloads_after_parking_without_transferring_ownership(backend):
    model = Model(backend)
    slot = model.object(1000, flags=262144)
    model.object(2000, flags=262144)
    model.store(1000, 0, 5)
    model.store(2000, 0, 9)
    model.store(3000, 8, 2000)
    def parked():
        model.ns["pcc_gc_forwarding_index_find"] = lambda value: 3000 if value == 1000 else 0
    model.on_lock = parked
    # The owning resolver can transfer counts; a borrowed input cannot use it.
    original = model.ns["pcc_gc_resolve_root_slot_unlocked"]
    model.ns["pcc_gc_resolve_root_slot_unlocked"] = lambda *args: (_ for _ in ()).throw(AssertionError("owning resolver"))
    token = model.ns["pcc_gc_borrowed_lease_acquire"](slot)
    assert token == 1 and model.load(slot, 0) == 2000
    assert model.count(1000) == 0 and model.count(2000) == 1
    assert model.load(1000, 0) == 5 and model.load(2000, 0) == 9
    model.ns["pcc_gc_resolve_root_slot_unlocked"] = original
    assert model.release(slot, token) == 0 and model.active() == 0


PROGRAM = r'''import gc
import weakref
events = []
keepers = []
refs = []
class Item:
    def __del__(self):
        events.append("drop")
        gc.collect()
def make():
    result = Item()
    refs.append(weakref.ref(result))
    return result
def collect_arg(value):
    keepers.clear()
    gc.collect()
    assert refs[-1]() is value
    return value
def append_pair(target, value):
    target.append(collect_arg(value))
    target.append(value)
    gc.collect()
    assert target[0] is value and target[1] is value
    return target
class Other:
    def append(self, value):
        gc.collect()
        events.append("custom")
        return value
def custom(target, item):
    return target.append(item)
def fail():
    gc.collect()
    raise ValueError("argument")
def fail_before_call(target):
    target.append(fail())
def rebound(target, item):
    target = []
    target.append(item)
    return target
def string_ops(value):
    gc.collect()
    return value.upper(), value.replace("é", "e"), value.removeprefix("ca")
def main():
    keepers.append(make())
    result = append_pair([], keepers[0])
    assert events == [] and result[0] is result[1]
    result.clear()
    gc.collect()
    assert refs[-1]() is None and events == ["drop"]
    assert custom(Other(), 1 << 100) == 1 << 100
    assert events == ["drop", "custom"]
    empty = []
    try:
        fail_before_call(empty)
    except ValueError as error:
        assert str(error) == "argument"
    assert empty == []
    assert rebound(empty, 1 << 100) == [1 << 100] and empty == []
    assert string_ops("café") == ("CAFÉ", "cafe", "fé")
    print("STABLE_BORROWED_INPUT_OK")
main()
'''


# The full original program remains a required regression below: both
# preimage and candidate currently fail its non-ASCII upper() assertion.
# Keep that failure separate from the input ownership paths so those paths
# can be executed through completion, including Unicode replace/prefix.
OWNERSHIP_PROGRAM = PROGRAM.replace(
    'assert string_ops("café") == ("CAFÉ", "cafe", "fé")',
    'assert string_ops("cafe") == ("CAFE", "cafe", "fe")\n'
    '    assert "café".replace("é", "e") == "cafe"\n'
    '    assert "café".removeprefix("ca") == "fé"',
)


@pytest.mark.integration
def test_stable_borrowed_input_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
                       OWNERSHIP_PROGRAM, "STABLE_BORROWED_INPUT_OK")


@pytest.mark.integration
def test_stable_borrowed_unicode_upper_contract_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
                       PROGRAM, "STABLE_BORROWED_INPUT_OK")


THREAD_PROGRAM = '''import gc
import threading
def push(shared, item):
    index = 0
    while index < 200:
        shared.append(item)
        if index % 13 == 0:
            gc.collect()
        index += 1
def main():
    shared = []
    first = [1 << 100, "first"]
    second = [-(1 << 100), "second"]
    left = threading.Thread(target=push, args=(shared, first))
    right = threading.Thread(target=push, args=(shared, second))
    left.start()
    right.start()
    left.join()
    right.join()
    first_count = 0
    second_count = 0
    for item in shared:
        if item is first:
            first_count += 1
        elif item is second:
            second_count += 1
        else:
            raise AssertionError("borrowed item identity changed")
    assert first_count == 200 and second_count == 200
    assert first == [1 << 100, "first"] and second == [-(1 << 100), "second"]
    shared.clear()
    gc.collect()
    assert first == [1 << 100, "first"] and second == [-(1 << 100), "second"]
    print("BORROWED_THREADS_OK")
main()
'''


@pytest.mark.integration
def test_stable_borrowed_inputs_native_concurrent_collection(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
                       THREAD_PROGRAM, "BORROWED_THREADS_OK")
