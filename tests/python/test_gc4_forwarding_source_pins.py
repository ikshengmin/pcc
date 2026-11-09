"""MODEL: GC4 forwarding-source address pins and fresh retirement grace.

The shared loader executes production retirement, pin/unpin, pin predicate,
conservative owner-chain and same-module retirement helpers. Memory, indexes,
reference accounting, payload planning, world ownership and cross-object
remapping are doubles. This does not exercise public forwarding installation,
stack discovery, native compilation, allocator bytes, or concurrent schedules.
"""
from pathlib import Path

import pytest

from pcc.runtime.py.py_abi_constants import (
    PYOBJECTHEADER_FLAGS_OFFSET,
    PY_FLAG_GC_PINNED,
    PY_TYPE_LIST,
)
from tests.python.runtime_body_model import (
    gc_state_initial_values,
    load_functions,
)


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
FORWARDED = 2048
AGED = 131072
ZPAGE = 65536


class ForwardingPinModel:
    """Real production bodies over exact-address cells and linked indexes."""

    def __init__(self, count_free, backend=4, owns_world=True):
        self.cells = {}
        self.next_address = 4096
        self.globals = {}
        self.objects = {}
        self.edges = {}
        self.identities = {}
        self.events = []
        self.locked = False
        self.in_step = False
        self.world_stopped = owns_world
        self.initially_owns_world = owns_world
        self.scan_sources = []
        for name, value in gc_state_initial_values().items():
            self.store(self.global_addr(name), 0, value)
        self.set_global("pcc_gc_backend_selected", backend)
        self.set_global("pcc_gc_backend4_no_refcounts", count_free)
        self.ns = {
            "i64": int,
            "c_ptr": int,
            "PYOBJECTHEADER_FLAGS_OFFSET": PYOBJECTHEADER_FLAGS_OFFSET,
            "PY_FLAG_GC_PINNED": PY_FLAG_GC_PINNED,
            "load_i32": self.load,
            "load_i64": self.load,
            "load_ptr": self.load,
            "store_i32": self.store,
            "store_i64": self.store,
            "store_ptr": self.store,
            "global_addr": self.global_addr,
            "global_load_ptr": self.get_global,
            "global_store_ptr": self.set_global,
            "null": lambda: 0,
            "ptr_is_null": lambda p: int(p == 0),
            "ptr_eq": lambda a, b: int(a == b),
            "is_tagged_int": lambda p: p & 1,
            "ptr_to_int": lambda p: p,
            "frame_address": lambda: 0x100000,
            "logical_shift_right_i64": lambda n, bits: n >> bits,
            "atomic_rmw_i32": self.atomic,
            "atomic_rmw_i64": self.atomic,
            "stack_alloc": self.allocate,
            "function_addr": lambda name: self.ns[name],
            "cstr": lambda value: value,
            "free": lambda p: self.events.append(("free", p)),
            "pcc_platform_abort": self.abort,
            "pcc_py_gc_defer_tripwire": self.abort,
            # Static literal membership is outside this heap-list model.
            "pcc_gc_static_literal_contains": lambda p: 0,
            "pcc_gc_object_index_find": lambda p: self.objects.get(p, 0),
            "pcc_gc_forwarding_index_find": lambda p: self.edges.get(p, 0),
            "pcc_gc_object_list_head": lambda: self.get_global("pcc_gc_object_head"),
            "pcc_gc_forwarding_list_head": lambda: self.get_global("pcc_gc_forwarding_head"),
            "pcc_gc_object_node_size": lambda node: self.load(node, 8),
            "pcc_gc_object_node_freeing": lambda node: self.load(node, 32),
            "pcc_gc_object_index_remove": self.remove_object,
            "pcc_gc_object_node_unlink": lambda node: self.unlink("pcc_gc_object_head", node),
            "pcc_gc_forwarding_index_remove": self.remove_edge,
            "pcc_gc_forwarding_unlink_main": lambda node: self.unlink("pcc_gc_forwarding_head", node),
            "pcc_gc_forwarding_target_unlink": lambda node: self.events.append(("target_unlink", node)),
            "pcc_gc_identity_detach": self.detach_identity,
            "pcc_gc_granule_is_object_start": lambda p: 0,
            "pcc_gc_granule_object_retire": lambda p: 0,
            "pcc_gc_managed_pointer_index_remove": lambda p: self.record("managed_remove", p, 1),
            "pcc_gc_relocation_retire_source_payload_into_finish": self.retire_payload,
            "pcc_gc_live_bytes_subtract": lambda size: self.events.append(("live_subtract", size)),
            "pcc_gc_gray_count_decrement_acq_rel": lambda: self.events.append(("gray_subtract",)),
            "pcc_gc_backend": lambda: self.get_global("pcc_gc_backend_selected"),
            "pcc_gc_backend4_zpage_clear_active_page": lambda p: self.events.append(("page_clear", p)),
            "pcc_gc_backend4_zpage_unlink_page": lambda p: self.events.append(("page_unlink", p)),
            "pcc_gc_backend4_zpage_destroy": lambda p: self.events.append(("page_destroy", p)),
            "pcc_gc_backend4_zpage_find_page_for_addr": lambda p, size: 0,
            "pcc_gc_refresh_read_barrier": lambda: self.events.append(("refresh_barrier",)),
            "pcc_gc_backend4_remap_referents": lambda p: None,
            "pcc_gc_visit_registered_root_slots": lambda mode, unused: 0,
            "pcc_capi_is_cext_type_tag": lambda tag: 0,
            "pcc_thread_owns_stopped_world": lambda: int(self.world_stopped),
            "pcc_stop_the_world": self.stop_world,
            "pcc_resume_world": self.resume_world,
            "pcc_thread_live_count": lambda: 1,
            "pcc_thread_all_stopped_at_gates": lambda: 1,
            "pcc_dealloc_cascade_active": lambda: 0,
            "pcc_gc_tracing_has_sweep_candidate": lambda: 0,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_gc_pin_conservative_thread_roots": self.scan,
            "pcc_capi_visit_extension_module_state_roots": lambda visitor, context: None,
            "pcc_gc_mark_canonical_root_gray_with_node": lambda obj, node: None,
            "pcc_gc_mark_root_gray_if_known": lambda obj: None,
            "py_incref": lambda p: self.events.append(("incref", p)),
            "py_decref": lambda p: self.events.append(("decref", p)),
            # Cross-object finish owners are recorded, not their allocators.
            "pcc_gc_relocation_finish_source_payloads": lambda plans: None,
            "pcc_gc_identity_finish_detached": lambda nodes: None,
            "pcc_gc_object_node_finish_detached": lambda nodes: None,
            "pcc_gc_backend3_finish_forwarded_source": lambda p: self.events.append(("gc3_finish", p)),
        }
        load_functions(
            RUNTIME / "freestanding_gc_root_operations.py",
            {"pcc_gc_pin", "pcc_gc_unpin", "pcc_gc_object_is_address_pinned"},
            self.ns,
        )
        load_functions(
            RUNTIME / "freestanding_gc_conservative_roots.py",
            {"_pin_owner_chain"},
            self.ns,
        )
        load_functions(
            RUNTIME / "freestanding_gc_forwarding_retirement.py",
            {
                "pcc_gc_backend4_remap_and_retire_unlocked",
                "pcc_gc_backend4_remap_and_retire_stopped_world",
                "_gc3_pin_external_remap_root",
            },
            self.ns,
        )

    def allocate(self, size=128):
        address = self.next_address
        self.next_address += ((size + 15) // 16) * 16
        return address

    def load(self, address, offset):
        # Includes the wrapper's remaining-source predicate after remapping.
        # Header reads during this stopped cut must stay under the graph lock.
        if self.in_step and address in self.objects and offset == 12:
            assert self.locked
        return self.cells.get(address + offset, 0)

    def store(self, address, offset, value):
        self.cells[address + offset] = value

    def global_addr(self, name):
        if name not in self.globals:
            self.globals[name] = self.allocate(8192 if name == "pcc_gc_metric_shards" else 16)
        return self.globals[name]

    def get_global(self, name):
        return self.load(self.global_addr(name), 0)

    def set_global(self, name, value):
        self.store(self.global_addr(name), 0, value)

    def atomic(self, operation, address, offset, value, order):
        old = self.load(address, offset)
        result = {"or": old | value, "and": old & value,
                  "add": old + value, "sub": old - value}[operation]
        self.store(address, offset, result)
        return old

    def record(self, event, address, result=0):
        assert self.locked
        self.events.append((event, address))
        return result

    def abort(self, *args):
        raise AssertionError(args or "unexpected abort")

    def lock(self):
        assert self.world_stopped and not self.locked
        self.locked = True

    def unlock(self):
        assert self.locked
        self.locked = False

    def stop_world(self):
        assert not self.world_stopped
        self.world_stopped = True
        return 0

    def resume_world(self):
        assert self.world_stopped and not self.locked
        self.world_stopped = False
        return 0

    def unlink(self, head_name, node):
        previous = 0
        cursor = self.get_global(head_name)
        while cursor:
            if cursor == node:
                following = self.load(cursor, 16)
                if previous:
                    self.store(previous, 16, following)
                else:
                    self.set_global(head_name, following)
                return
            previous, cursor = cursor, self.load(cursor, 16)
        raise AssertionError(("missing list node", head_name, node))

    def remove_object(self, obj):
        return self.record("object_remove", obj, self.objects.pop(obj))

    def remove_edge(self, obj):
        return self.record("edge_remove", obj, self.edges.pop(obj, 0))

    def detach_identity(self, obj):
        return self.record("identity_remove", obj, self.identities.pop(obj, 0))

    def retire_payload(self, obj, finish):
        return self.record("payload_retire", obj, 1)

    def object(self):
        obj, node = self.allocate(), self.allocate()
        self.store(obj, 8, PY_TYPE_LIST)
        self.store(node, 0, obj)
        self.store(node, 8, 64)
        self.store(node, 16, self.get_global("pcc_gc_object_head"))
        self.set_global("pcc_gc_object_head", node)
        self.objects[obj] = node
        return obj

    def forwarding(self, aged=True):
        old, target = self.object(), self.object()
        edge, page = self.allocate(), self.allocate(400)
        self.store(old, 12, FORWARDED | ZPAGE | (AGED if aged else 0))
        self.store(edge, 0, old)
        self.store(edge, 8, target)
        self.store(edge, 16, self.get_global("pcc_gc_forwarding_head"))
        self.store(edge, 48, page)
        self.store(page, 96, 1)
        self.store(page, 104, 2)
        self.set_global("pcc_gc_forwarding_head", edge)
        self.set_global("pcc_gc_forwarding_population", self.get_global("pcc_gc_forwarding_population") + 1)
        self.edges[old] = edge
        self.identities[old] = self.allocate()
        return old, target, edge, page

    def pin(self, old, kind):
        if kind == "manual":
            self.ns["pcc_gc_pin"](old)
        elif kind == "conservative":
            self.ns["_pin_owner_chain"](self.objects[old], 0)
        elif kind == "lease":
            self.store(self.objects[old], 80, 1)
        else:
            raise AssertionError(kind)

    def scan(self):
        for old in self.scan_sources:
            self.ns["_pin_owner_chain"](self.objects[old], 0)

    def step(self, entry="wrapper"):
        self.in_step = True
        try:
            return self._step(entry)
        finally:
            self.in_step = False

    def _step(self, entry):
        if entry == "wrapper":
            result = self.ns["pcc_gc_backend4_remap_and_retire_stopped_world"]()
            assert not self.locked
            assert self.world_stopped == self.initially_owns_world
            return result
        finish = self.allocate(48)
        self.lock()
        self.ns["pcc_gc_backend4_remap_and_retire_unlocked"](finish)
        self.unlock()
        self.ns["pcc_gc_backend4_finish_remap_retirement"](finish)
        return None

    def assert_preserved(self, old, edge, page):
        assert self.edges[old] == edge
        assert old in self.objects and old in self.identities
        assert self.load(old, 12) & FORWARDED
        assert self.load(old, 12) & AGED == 0
        assert self.load(page, 96) == 1
        assert self.load(page, 104) == 2
        assert self.get_global("pcc_gc_backend4_parked_head") == 0
        assert self.get_global("pcc_gc_backend4_retained_page_head") == 0
        assert not any(event in self.events for event in (
            ("payload_retire", old), ("identity_remove", old),
            ("object_remove", old), ("managed_remove", old),
            ("edge_remove", old), ("target_unlink", edge),
            ("free", edge), ("page_unlink", page),
            ("page_destroy", page), ("free", page),
        ))


@pytest.mark.parametrize("count_free,kind", [(0, "manual"), (1, "manual"), (0, "lease"), (1, "lease"), (1, "conservative")])
@pytest.mark.parametrize("entry", ["unlocked", "wrapper"])
def test_aged_pinned_source_keeps_every_retirement_owner(count_free, kind, entry):
    model = ForwardingPinModel(count_free)
    old, target, edge, page = model.forwarding()
    model.pin(old, kind)
    assert model.ns["pcc_gc_object_is_address_pinned"](old) == 1
    if kind == "manual":
        # Public pin is exact-address, not a canonical-target pin.
        assert model.load(target, 12) & PY_FLAG_GC_PINNED == 0
    if kind == "conservative":
        assert model.load(model.objects[target], 56) & 1
    for _ in range(3):
        result = model.step(entry)
        if entry == "wrapper":
            assert result == 0
        model.assert_preserved(old, edge, page)
        assert model.get_global("pcc_gc_forwarding_population") == 1


@pytest.mark.parametrize("count_free", [0, 1])
@pytest.mark.parametrize("owns_world", [False, True])
def test_unpin_requires_fresh_grace_before_payload_and_page_retirement(count_free, owns_world):
    model = ForwardingPinModel(count_free, owns_world=owns_world)
    old, target, edge, page = model.forwarding()
    model.pin(old, "manual")
    assert model.step() == 0
    model.assert_preserved(old, edge, page)
    model.ns["pcc_gc_unpin"](old)
    assert model.step() == 1
    assert model.load(old, 12) & AGED
    assert old in model.edges and old in model.objects
    assert ("payload_retire", old) not in model.events
    assert model.load(page, 96) == 1
    assert model.step() == 1
    assert old not in model.edges and old not in model.objects
    assert old not in model.identities
    for event in ("payload_retire", "identity_remove", "managed_remove", "object_remove", "edge_remove"):
        assert model.events.count((event, old)) == 1
    assert model.events.count(("decref", target)) == 1
    assert model.events.count(("free", edge)) == 1
    assert model.load(page, 96) == 0
    assert model.get_global("pcc_gc_backend4_parked_head") == page
    assert model.get_global("pcc_gc_forwarding_population") == 0


@pytest.mark.parametrize("count_free", [0, 1])
@pytest.mark.parametrize("pinned_first", [False, True])
def test_mixed_edges_report_only_real_unpinned_progress(count_free, pinned_first):
    model = ForwardingPinModel(count_free)
    first = model.forwarding(aged=False)
    second = model.forwarding(aged=False)
    pinned, moving = (first, second) if pinned_first else (second, first)
    old, target, edge, page = pinned
    moving_old = moving[0]
    model.pin(old, "manual")
    assert model.step() == 1  # Unpinned source acquires its grace epoch.
    model.assert_preserved(old, edge, page)
    assert model.load(moving_old, 12) & AGED
    assert model.step() == 1  # One edge really retires.
    assert moving_old not in model.edges
    # Another edge's page is now parked; the pinned page itself is untouched.
    assert old in model.edges and old in model.objects
    assert model.load(page, 96) == 1 and model.load(page, 104) == 2
    assert ("payload_retire", old) not in model.events
    assert model.step() == 0  # Remaining pinned edge is not progress.
    assert model.get_global("pcc_gc_forwarding_population") == 1


def test_refcounted_conservative_bit_alone_is_not_an_address_pin():
    model = ForwardingPinModel(0)
    old, target, edge, page = model.forwarding()
    model.store(model.objects[old], 56, 1)
    assert model.ns["pcc_gc_object_is_address_pinned"](old) == 0
    assert model.step() == 1
    assert old not in model.edges


def test_gc3_control_keeps_pinned_alias_without_gc4_grace():
    model = ForwardingPinModel(1, backend=3)
    old, target, edge, page = model.forwarding(aged=False)
    model.pin(old, "manual")
    for _ in range(2):
        assert model.step() == 0
        assert model.edges[old] == edge
        assert model.load(old, 12) & AGED == 0
    model.ns["pcc_gc_unpin"](old)
    assert model.step() == 1
    assert old not in model.edges
    assert model.events.count(("gc3_finish", old)) == 1
    assert ("payload_retire", old) not in model.events
