"""Relocation raw storage must fit the current continuation chunk layout.

Real raw snapshot/prepare/publish/finish and continuation deallocation bodies
run over byte-addressed, bounds-checked allocations. Root registration,
payload-span ownership and child decrefs are explicit finite doubles. This
does not prove forwarding, root retargeting, native execution or concurrency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.runtime_body_model import load_functions


PY = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class CheckedMemory:
    def __init__(self):
        self.regions = {}
        self.next_address = 4096
        self.freed = set()
        self.allocations = []

    def malloc(self, size):
        assert size > 0
        address = self.next_address
        self.next_address += ((size + 15) // 16) * 16 + 16
        self.regions[address] = bytearray([0xA5]) * size
        self.allocations.append((address, size))
        return address

    def region(self, address, size):
        assert size >= 0
        for base, data in self.regions.items():
            if base <= address < base + len(data):
                assert base not in self.freed, ("use after free", address, size)
                offset = address - base
                assert offset + size <= len(data), ("out of bounds", base, offset, size)
                return data, offset
        raise AssertionError(("unknown address", address, size))

    def load(self, width, address, offset):
        data, index = self.region(address + offset, width)
        return int.from_bytes(data[index:index + width], "little")

    def store(self, width, address, offset, value):
        data, index = self.region(address + offset, width)
        data[index:index + width] = (value % (1 << (width * 8))).to_bytes(width, "little")

    def memset(self, address, value, size):
        data, index = self.region(address, size)
        data[index:index + size] = bytes([value]) * size

    def memmove(self, target, source, size):
        destination, start = self.region(target, size)
        origin, offset = self.region(source, size)
        destination[start:start + size] = bytes(origin[offset:offset + size])

    def free(self, address):
        if address:
            assert address in self.regions and address not in self.freed
            self.freed.add(address)


@pytest.mark.parametrize("slot_count", [0, 1])
@pytest.mark.parametrize("mounted", [0, 1])
def test_prepared_continuation_chunk_can_be_deallocated_within_its_allocation(
    slot_count, mounted
):
    memory = CheckedMemory()
    read = lambda address, offset=0: memory.load(8, address, offset)
    write = lambda address, offset, value: memory.store(8, address, offset, value)
    source, target, chunk = memory.malloc(48), memory.malloc(48), memory.malloc(32)
    for address in (source, target, chunk):
        memory.memset(address, 0, len(memory.regions[address]))
    slots = memory.malloc(8) if slot_count else 0
    if slots:
        write(slots, 0, 3)  # A tagged integer, not an independently retained child.
    root_node = memory.malloc(32)
    tag = ABI_CONSTANTS["object.type.continuation"]
    memory.store(4, source, 8, tag)
    write(source, 24, chunk)
    write(source, 32, mounted)
    memory.store(4, chunk, 0, slot_count)
    write(chunk, 8, slot_count)
    write(chunk, 16, slots)
    # Even a nonzero source handle must not be cloned into a private target.
    write(chunk, 24, root_node if slots and not mounted else 0)
    original_chunk = bytes(memory.regions[chunk])
    registry = {slots: root_node} if slots and not mounted else {}
    original_registry = dict(registry)
    globals_, unregister_requests, child_drops, spans = {}, [], [], []

    def publish_spans(obj, head, count, total):
        assert obj == target and count == 1 and total == slot_count * 8
        assert read(head, 40) == 0
        assert read(head, 16) == slot_count * 8
        spans.append(head)
        return 1

    def unregister(values):
        unregister_requests.append(values)
        registry.pop(values, None)

    def unregister_node(node):
        raise AssertionError(("private copy acquired a source registration", node))

    def free_object(obj):
        assert obj == target
        for span in spans:
            memory.free(span)
        memory.free(obj)

    ns = {
        "i64": int,
        "abi_constant": ABI_CONSTANTS.__getitem__,
        "null": lambda: 0,
        "ptr_is_null": lambda value: int(value == 0),
        "ptr_eq": lambda left, right: int(left == right),
        "ptr_add": lambda address, offset: address + offset,
        "is_tagged_int": lambda value: value & 1,
        "load_i32": lambda address, offset: memory.load(4, address, offset),
        "load_i64": read,
        "load_ptr": read,
        "store_i32": lambda address, offset, value: memory.store(4, address, offset, value),
        "store_i64": write,
        "store_ptr": write,
        "malloc": memory.malloc,
        "free": memory.free,
        "memset": memory.memset,
        "memmove": memory.memmove,
        "global_load_ptr": lambda name: globals_.get(name, 0),
        "global_store_ptr": lambda name, value: globals_.__setitem__(name, value),
        "pcc_gc_backend4_zpage_publish_relocation_payload_spans_locked": publish_spans,
        "pcc_gc_retain_plan_finish": lambda plan: None,
        "pcc_gc_load_ptr": lambda owner, slot: read(slot),
        "pcc_gc_unregister_continuation_root": unregister,
        "pcc_gc_unregister_continuation_root_node": unregister_node,
        "py_decref": child_drops.append,
        "pcc_gc_free_object_memory": free_object,
    }
    load_functions(PY / "freestanding_gc_relocation_payload.py", {
        "pcc_gc_relocation_payload_plan_prepare",
        "pcc_gc_relocation_payload_raw_snapshot_locked",
        "pcc_gc_relocation_payload_raw_prepare",
        "_relocate_raw_publish_locked",
        "pcc_gc_relocation_payload_plan_finish",
    }, ns)
    load_functions(PY / "py_coroutine.py", {"py_dealloc_continuation"}, ns)
    plan = ns["pcc_gc_relocation_payload_plan_prepare"](0)
    assert ns["pcc_gc_relocation_payload_raw_snapshot_locked"](source, tag, 48, plan) == 1
    assert read(plan, 152 + 8) == 32
    assert ns["pcc_gc_relocation_payload_raw_prepare"](plan) == 1
    memory.memmove(target, source, 48)
    assert ns["_relocate_raw_publish_locked"](target, plan) == 1
    copied_chunk = read(target, 24)
    copied_slots = read(copied_chunk, 16)
    assert copied_chunk != chunk and len(memory.regions[copied_chunk]) == 32
    assert read(copied_chunk, 24) == 0
    assert read(copied_chunk, 8) == slot_count
    if slots:
        assert copied_slots != slots and read(copied_slots) == 3
    else:
        assert copied_slots == 0
    # Match the public transaction's cleanup order: the plan no longer owns
    # transferred buffers when the failed private target is destroyed.
    ns["pcc_gc_relocation_payload_plan_finish"](plan)
    assert copied_chunk not in memory.freed
    ns["py_dealloc_continuation"](target)
    assert unregister_requests == ([copied_slots] if not mounted else [])
    assert registry == original_registry and bytes(memory.regions[chunk]) == original_chunk
    assert child_drops == ([3] if slot_count else [])
    assert copied_chunk in memory.freed and target in memory.freed
    assert not ({source, chunk, root_node} & memory.freed)
    if slots:
        assert copied_slots in memory.freed and slots not in memory.freed
    expected_live = {source, chunk, root_node} | ({slots} if slots else set())
    assert set(memory.regions) - memory.freed == expected_live
