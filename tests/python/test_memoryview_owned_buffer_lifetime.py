"""Finite ownership/failure interleavings of the actual buffer runtime bodies.

These models do not implement promotion or certify native movement.
Allocation, root bookkeeping and CAS are deterministic doubles. The GC3
cached-exporter candidate and its tests went with collectors 1-4 (item 2 (d)
U8): the one collector keeps the owned buffer's internal word NULL.
"""

from pathlib import Path
import re

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class BufferModel:
    OUTER, INNER, EXPORTER, SIBLING = 1000, 2000, 3000, 4000

    def __init__(self, *, backend=3, conservative=True, failure=None, persistent_token=1):
        self.memory = {("pcc_gc_backend_selected", 0): backend}
        self.allocations = {}
        self.freed = []
        self.registered = {}
        self.frames = set()
        self.plans = {}
        self.owners = {}
        self.leases = {}
        self.events = []
        self.errors = []
        self.heap_next = 10000
        self.stack_next = 1000000
        self.handle_next = 2000000
        self.depth = 0
        self.no_park = 0
        self.nested_copy_calls = 0
        self.conservative = conservative
        self.failure = failure
        self.persistent_token = persistent_token
        self.before_cas = None
        self.write_observer = None
        self.constants = {
            "object.header.type_tag_offset": 8,
            "object.type.memoryview": abi.PY_TYPE_MEMORYVIEW,
            "object.type.bytes": abi.PY_TYPE_BYTES,
            "object.type.bytearray": abi.PY_TYPE_BYTEARRAY,
            "object.memoryview.base_offset": 16,
            "object.bytes.byte_len_offset": 16,
            "object.bytes.data_offset": 24,
        }
        self.namespace = dict(
            i64=int, c_ptr=int,
            PY_TYPE_MEMORYVIEW=abi.PY_TYPE_MEMORYVIEW,
            PY_TYPE_BYTES=abi.PY_TYPE_BYTES,
            PY_TYPE_BYTEARRAY=abi.PY_TYPE_BYTEARRAY,
            null=lambda: 0, ptr_is_null=lambda pointer: pointer == 0,
            ptr_eq=lambda first, second: first == second,
            ptr_add=lambda base, offset: base + offset,
            ptr_to_int=lambda pointer: pointer, int_to_ptr=lambda bits: bits,
            is_tagged_int=lambda pointer: bool(pointer & 1),
            global_addr=lambda name: name, cstr=lambda value: value,
            abi_constant=self.constants.__getitem__,
            load_ptr=self.load, load_i32=self.load, load_i64=self.load,
            store_ptr=self.store, store_i32=self.store, store_i64=self.store,
            atomic_load_i64=lambda base, offset, order: self.load(base, offset),
            atomic_cas_i64=self.cas,
            memset=self.zero, stack_alloc=self.stack,
            PyMem_Malloc=self.allocate, PyMem_Free=self.free,
            py_mem_free=self.free,
            pcc_gc_load_ptr=lambda owner, slot: self.load(slot),
            pcc_gc_scheduler_root_register_handle=self.register,
            pcc_gc_scheduler_root_unregister_handle=self.unregister,
            pcc_gc_stack_slot_root_is_conservative=self.is_conservative,
            pcc_gc_note_frame_enter_local_checked=self.frame_enter,
            pcc_gc_note_frame_leave_local=self.frame_leave,
            pcc_thread_no_park_enter=lambda: self.park(1),
            pcc_thread_no_park_exit=lambda: self.park(-1),
            pcc_gc_root_copy_borrowed_lease=self.copy_borrowed,
            pcc_gc_root_copy_lease=self.copy_lease,
            pcc_gc_root_copy_owned_prepare_locked=self.prepare_owned,
            pcc_gc_store_root_plan_finish=self.finish_plan,
            pcc_gc_root_move=self.move,
            pcc_gc_store_root=self.store_root,
            pcc_gc_foreign_lease_acquire=self.acquire,
            pcc_gc_foreign_lease_release=self.release,
            pcc_py_gc_minor_graph_lock=lambda: self.lock(1),
            pcc_py_gc_minor_graph_unlock=lambda: self.lock(-1),
            pcc_platform_abort=lambda: pytest.fail("unexpected runtime abort"),
            _runtime_error=lambda message: self.errors.append(("runtime", message)),
            _type_error=lambda message: self.errors.append(("type", message)),
            py_decref=lambda value: self.retain(value, -1),
            pcc_gc_free_object_memory=lambda obj: self.events.append(("free_object", obj)),
        )
        load_functions(
            RUNTIME / "freestanding_gc_object_slots.py",
            {"pcc_gc_memoryview_initialize_owned_buffer",
             "pcc_gc_memoryview_refresh_owned_buffer"}, self.namespace,
        )
        if failure == "initialize":
            self.namespace["pcc_gc_memoryview_initialize_owned_buffer"] = lambda *args: 0
        load_functions(
            RUNTIME / "py_capi_buffer_runtime.py",
            {"pcc_PyMemoryView_GET_BUFFER", "pcc_memoryview_free_owned_buffer"},
            self.namespace,
        )
        load_functions(
            RUNTIME / "py_obj_stubs.py", {"py_dealloc_memoryview"}, self.namespace,
        )
        for obj, base in ((self.OUTER, self.INNER),
                          (self.INNER, self.EXPORTER),
                          (self.SIBLING, self.EXPORTER)):
            self.store(obj, 8, abi.PY_TYPE_MEMORYVIEW)
            self.store(obj, 16, base)
            self.store(obj, 24, 0)
        self.store(self.EXPORTER, 8, abi.PY_TYPE_BYTES)
        self.store(self.EXPORTER, 16, 7)
        self.events.clear()

    def key(self, base, offset=0):
        return (base, offset) if isinstance(base, str) else base + offset

    def load(self, base, offset=0):
        return self.memory.get(self.key(base, offset), 0)

    def store(self, base, offset, value):
        key = self.key(base, offset)
        self.memory[key] = value
        self.events.append(("write", key, value))
        if self.write_observer:
            self.write_observer(key, value)

    def zero(self, base, value, size):
        for offset in range(size):
            self.store(base, offset, value)

    def stack(self, size):
        pointer = self.stack_next
        self.stack_next += (size + 15) // 16 * 16
        if size == 128:
            # This models entry into the scratch helper, not branch-sensitive
            # native allocation. The IR test must keep that helper separate.
            assert self.depth == 1
            self.plans[pointer] = False
            self.events.append(("plan_alloc", pointer))
        return pointer

    def allocate(self, size):
        if self.failure == f"malloc-{size}":
            return 0
        pointer = self.heap_next
        self.heap_next += (size + 15) // 16 * 16
        self.allocations[pointer] = size
        # Uninitialized malloc data must not be treated as a metadata owner.
        for offset in range(size):
            self.memory[pointer + offset] = 0xBAD0
        return pointer

    def free(self, pointer):
        if pointer:
            assert pointer in self.allocations and pointer not in self.freed
            self.events.append(("free", pointer))
            self.freed.append(pointer)

    def register(self, slot):
        if self.failure == "register":
            return 0
        self.handle_next += 16
        self.registered[self.handle_next] = slot
        self.events.append(("register", slot, self.handle_next))
        return self.handle_next

    def unregister(self, handle):
        slot = self.registered.pop(handle)
        assert self.load(slot) == 0
        self.events.append(("unregister", handle))

    def is_conservative(self, slot):
        return int(self.conservative and slot >= 1000000)

    def frame_enter(self, frame_map, slots):
        kind = "borrowed" if frame_map.endswith("borrowed_map") else "owned"
        if self.failure == f"frame-{kind}":
            self.events.append(("frame_enter", frame_map, slots, 0))
            return 0
        self.frames.add(slots)
        self.events.append(("frame_enter", frame_map, slots, 1))
        return 1

    def frame_leave(self, slots):
        assert self.load(slots) == 0
        self.frames.remove(slots)
        self.events.append(("frame_leave", slots))

    def park(self, delta):
        self.no_park += delta
        assert self.no_park >= 0

    def lock(self, delta):
        if delta < 0:
            assert all(self.plans.values())
        self.depth += delta
        assert self.depth >= 0
        self.events.append(("graph_lock" if delta > 0 else "graph_unlock",))

    def retain(self, obj, delta):
        if obj:
            self.owners[obj] = self.owners.get(obj, 0) + delta

    def store_root(self, slot, value):
        self.retain(self.load(slot), -1)
        self.retain(value, 1)
        self.store(slot, 0, value)
        self.events.append(("root", slot, value))

    def acquire(self, slot):
        obj = self.load(slot)
        persistent = slot in self.registered.values()
        if not obj or obj & 1:
            self.events.append(("acquire", slot, obj, 0, persistent))
            return 0
        if persistent:
            assert self.depth > 0
            if self.failure == "persistent-lease":
                self.events.append(("acquire", slot, obj, -2, True))
                return -2
        token = self.persistent_token if persistent else (0 if self.is_conservative(slot) else 1)
        if token:
            self.leases[obj] = self.leases.get(obj, 0) + 1
        self.events.append(("acquire", slot, obj, token, persistent))
        return token

    def release(self, slot, token):
        obj = self.load(slot)
        if token:
            assert token == 1 and self.leases.get(obj, 0) > 0
            self.leases[obj] -= 1
        self.events.append(("release", slot, obj, token))
        return 0

    def copy_lease(self, destination, source):
        self.nested_copy_calls += 1
        if self.failure == f"nested-copy-{self.nested_copy_calls}":
            owner = source - 16  # The current view's real base slot.
            self.events.append(("nested_copy_failed", source,
                                self.owners.get(owner, 0),
                                self.leases.get(owner, 0)))
            return -1
        return self.copy_source(destination, source)

    def copy_source(self, destination, source):
        assert self.load(destination) == 0
        # This double covers a leased memoryview's real heap base slot.
        # Ordinary heap values take a source lease; NULL/tagged values are
        # no-ops. Production's stack-source token-0 destination reacquire is
        # outside this model's call shape.
        assert not self.is_conservative(source)
        token = self.acquire(source)
        self.store_root(destination, self.load(source))
        return token

    def copy_borrowed(self, destination, source):
        assert self.load(destination) == 0
        self.events.append(("borrowed_copy", destination, source))
        # Unlike owning copy_lease, the borrowed API first publishes an
        # independent owner, then acquires through DESTINATION. A failed
        # acquire rolls that destination back before returning its token.
        self.store_root(destination, self.load(source))
        token = -1 if self.failure == "borrowed" else self.acquire(destination)
        if token < 0:
            self.store_root(destination, 0)
        return token

    def prepare_owned(self, destination, source, plan):
        assert self.depth > 0 and self.load(destination) == 0
        assert plan in self.plans and not self.plans[plan]
        # The real prepare initializes the full plan even on failure. Keep
        # that cross-object contract explicit rather than modeling a collector.
        self.plans[plan] = True
        self.events.append(("prepare", destination, source, plan))
        if self.failure == "prepare":
            return -1
        self.store_root(destination, self.load(source))
        return 0

    def finish_plan(self, plan):
        assert self.depth == 0
        assert self.plans.pop(plan)
        self.events.append(("finish", plan))

    def move(self, destination, source):
        assert not self.load(destination)
        if self.failure == "move":
            obj = self.load(source)
            self.events.append(("move_failed", source, obj,
                                self.owners.get(obj, 0),
                                self.leases.get(obj, 0)))
            return -1
        self.store(destination, 0, self.load(source))
        self.store(source, 0, 0)
        return 0

    def cas(self, obj, offset, expected, desired, success, failure):
        if self.before_cas:
            hook, self.before_cas = self.before_cas, None
            hook()
        observed = self.load(obj, offset)
        if observed == expected:
            self.store(obj, offset, desired)
        return observed

    def get_public(self, obj=None):
        return self.namespace["pcc_PyMemoryView_GET_BUFFER"](
            self.OUTER if obj is None else obj,
        )

    def dispose(self, buffer):
        self.namespace["pcc_memoryview_free_owned_buffer"](buffer)

    def balanced_temporaries(self):
        assert self.depth == self.no_park == 0
        assert not self.frames
        assert not self.plans

    def assert_no_resources(self):
        self.balanced_temporaries()
        assert not self.registered
        assert not any(self.leases.values())
        assert not any(self.owners.values())
        assert set(self.allocations) == set(self.freed)


@pytest.mark.parametrize("backend", (0,))
def test_malformed_public_export_never_frees_uninitialized_internal(backend):
    model = BufferModel(backend=backend)
    model.store(model.INNER, 16, 0)
    assert model.get_public() == 0 and model.errors
    model.assert_no_resources()


def test_fresh_initializer_clears_internal_before_early_failure():
    model = BufferModel()
    buffer = model.allocate(96)
    assert model.load(buffer, 72) == 0xBAD0
    assert model.namespace["pcc_gc_memoryview_initialize_owned_buffer"](0, buffer) == 0
    assert model.load(buffer, 72) == 0
    model.dispose(buffer)
    model.assert_no_resources()


@pytest.mark.parametrize("backend", (0, 1, 2, 4))
def test_other_collectors_keep_refresh_contract_without_gc3_metadata(backend):
    model = BufferModel(backend=backend)
    buffer = model.get_public()
    assert model.allocations[buffer] == 96 and model.load(buffer, 72) == 0
    assert model.load(buffer, 48) == buffer + 80
    assert model.load(buffer, 56) == buffer + 88
    assert model.get_public() == buffer  # Existing cached-address refresh contract.
    assert not model.registered and not any(model.leases.values())
    model.dispose(buffer)
    model.assert_no_resources()
