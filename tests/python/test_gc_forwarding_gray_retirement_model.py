"""Host-only execution of moving-source gray ownership and retirement bodies.

Raw memory, lock, allocator, payload and index providers are finite doubles.
The retirement, remap, unlink, copy, gray primitives and scheduler are actual
source functions. This is not native ABI, concurrency, or full-cycle proof.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WHITE, GRAY, BLACK, OLD, DYING = 8, 16, 32, 256, 524288


def _functions(namespace, filename, names):
    source = RUNTIME / filename
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)


class RetirementState:
    def __init__(self, backend=4):
        self.memory = {}
        self.next_address = 256
        self.index = {}
        self.forwardings = {}
        self.reverse = {}
        self.depth = 0
        self.events = []
        self.trips = []
        self.claims = []
        self.granule_result = 0
        self.granule_live = 1
        self.payload_ok = 1
        self.forwarding_ok = 1
        self.before_atomic_and = None
        self.target = 0
        self.store("pcc_gc_backend_selected", 0, backend)
        self.store("pcc_gc_config_initialized", 0, 1)
        self.store("pcc_gc_mark_active", 0, 1)
        self.store("pcc_gc_tracing_cycle_epoch", 0, 1)
        noop = lambda *args: None
        zero = lambda *args: 0
        ns = {
            "i64": int, "c_ptr": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "PY_TYPE_CLASS": 99, "PY_TYPE_MEMORYVIEW": 98,
            "null": lambda: 0, "ptr_is_null": lambda p: int(p == 0),
            "ptr_eq": lambda a, b: int(a == b), "is_tagged_int": zero,
            "global_addr": lambda name: name, "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": lambda a, o, order: self.load(a, o),
            "atomic_store_i32": lambda a, o, v, order: self.store(a, o, v),
            "atomic_cas_i32": self.cas, "atomic_rmw_i32": self.rmw,
            "stack_alloc": self.allocate, "free": noop, "cstr": lambda s: s,
            "memmove": self.memmove,
            "pcc_gc_backend": lambda: backend, "pcc_gc_config_ensure": lambda: backend,
            "pcc_gc_object_index_find": lambda obj: self.index.get(obj, 0),
            "pcc_gc_object_index_remove": self.remove_index,
            "pcc_gc_identity_detach": zero, "pcc_gc_managed_pointer_index_remove": noop,
            "pcc_gc_managed_pointer_index_insert": zero,
            "pcc_gc_granule_is_object_start": lambda obj: self.granule_live,
            "pcc_gc_granule_object_retire": lambda obj: self.granule_result,
            "pcc_py_gc_defer_tripwire": lambda *args: self.trips.append(args),
            "pcc_py_gc_minor_graph_lock": self.lock, "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_thread_owns_stopped_world": lambda: 1, "pcc_stop_the_world": zero,
            "pcc_resume_world": noop, "pcc_thread_safepoint": noop,
            "pcc_threads_enabled": lambda: 1, "pcc_capi_is_cext_type_tag": zero,
            "_promotion_unlink": noop, "pcc_gc_backend3_young_unlink": noop,
            "_release_retained_pages": zero, "pcc_gc_backend4_drain_parked_pages": noop,
            "pcc_gc_backend4_remap_referents": noop, "pcc_gc_visit_registered_root_slots": zero,
            "pcc_gc_relocation_retire_source_payload_into_finish": lambda *args: self.payload_ok,
            "pcc_gc_relocation_retire_source_payload_for_target_death_into_finish": lambda *args: self.payload_ok,
            "pcc_gc_forwarding_list_head": lambda: self.load("pcc_gc_forwarding_head", 0),
            "pcc_gc_forwarding_index_remove": lambda obj: self.forwardings.pop(obj, 0),
            "pcc_gc_forwarding_target_index_remove": lambda obj: self.reverse.pop(obj, 0),
            "pcc_gc_forwarding_target_unlink": self.unlink_target,
            "pcc_gc_forwarding_unlink_main": self.unlink_forwarding,
            "pcc_gc_backend4_zpage_note_forwarding_removed": noop,
            "pcc_gc_backend4_note_forwarding_removed_on_page": noop,
            "pcc_gc_backend4_finish_retained_page_releases": noop,
            "pcc_gc_relocation_finish_source_payloads": noop,
            "pcc_gc_identity_finish_detached": noop,
            "pcc_gc_object_node_finish_detached": noop,
            "py_decref": self.decref, "py_incref": lambda obj: self.store(obj, 0, self.load(obj, 0) + 1),
            "pcc_gc_forwarding_find": lambda obj: self.forwardings.get(obj, 0),
            "pcc_gc_object_is_address_pinned": zero,
            "pcc_gc_backend4_relocate_copy_supported_tag": lambda tag: 1,
            "pcc_gc_object_known_size": lambda obj: self.load(self.index.get(obj, 0), 8),
            "pcc_gc_relocate_copy_payload_prepared_locked": lambda *args: self.payload_ok,
            "pcc_gc_install_forwarding_preallocated_unlocked": self.install_forwarding,
            "pcc_gc_backend4_zpage_find": zero,
            "pcc_gc_backend4_zpage_detach_for_relocation": zero,
            "pcc_gc_relocation_member_forget": noop,
            "pcc_gc_trace_referents": lambda obj: self.events.append(("scan", obj)),
            "pcc_gc_complete_claimed_tracing_cycle": lambda epoch, kind: self.claims.append((epoch, kind)),
            "_gc_tracks_objects": lambda: 1, "_identity_remove": noop,
            "_relocation_set_remove": noop, "_backend4_store_buffer_remove": noop,
            "_backend4_remembered_set_remove": noop, "_object_node_zpage": zero,
            "_object_node_release": noop,
        }
        self.ns = ns
        _functions(ns, "freestanding_gc_root_operations.py", {
            "pcc_gc_gray_count_load_acquire", "pcc_gc_gray_count_increment_acq_rel",
            "pcc_gc_gray_count_decrement_acq_rel", "pcc_gc_set_mark_color",
        })
        _functions(ns, "freestanding_gc_object_nodes.py", {
            "pcc_gc_object_list_head", "pcc_gc_object_set_list_head",
            "pcc_gc_trace_cursor_load", "pcc_gc_trace_cursor_store",
            "pcc_gc_object_node_size", "pcc_gc_object_node_next", "pcc_gc_object_node_set_next",
            "pcc_gc_object_node_prev", "pcc_gc_object_node_set_prev", "pcc_gc_object_node_freeing",
            "pcc_gc_object_node_set_freeing", "pcc_gc_object_node_minor_block",
            "pcc_gc_object_node_unlink", "pcc_gc_live_bytes_subtract",
        })
        _functions(ns, "freestanding_gc_forwarding_retirement.py", {
            "_retire_forwarded_source_into_finish", "pcc_gc_backend4_remap_and_retire_unlocked",
            "pcc_gc_backend4_remap_and_retire_stopped_world", "pcc_gc_forwarding_remove_target",
            "pcc_gc_forwarding_detach", "pcc_gc_forwarding_detach_into_finish",
            "pcc_gc_forwarding_finish_detached", "pcc_gc_forwarding_finish_dead_targets",
            "pcc_gc_backend4_finish_remap_retirement",
        })
        _functions(ns, "freestanding_gc_relocation_drain.py", {
            "_relocation_set_head", "_note_incomplete_batch", "_relocate_selected",
            "pcc_gc_backend4_evacuation_drain",
        })
        _functions(ns, "freestanding_gc_relocation_copy.py", {
            "pcc_gc_backend4_relocate_copy_preallocated_unlocked",
        })
        _functions(ns, "freestanding_gc_incremental_concurrent_scheduler.py", {"pcc_gc_tracing_step_cycle"})
        _functions(ns, "py_gc_backend.py", {"pcc_gc_note_object_freeing"})
        ns.update({
            "pcc_gc_relocate_copy": self.copy,
            "_object_graph_lock": self.lock, "_object_graph_unlock": self.unlock,
            "_forwarding_detach_into_finish": ns["pcc_gc_forwarding_detach_into_finish"],
            "_forwarding_remove_target": ns["pcc_gc_forwarding_remove_target"],
            "_backend4_finish_remap_retirement": ns["pcc_gc_backend4_finish_remap_retirement"],
            "_object_node_freeing": ns["pcc_gc_object_node_freeing"],
            "_set_object_node_freeing": ns["pcc_gc_object_node_set_freeing"],
            "_object_node_size": ns["pcc_gc_object_node_size"],
            "_object_node_minor_block": ns["pcc_gc_object_node_minor_block"],
            "_gc_gray_count_decrement_acq_rel": ns["pcc_gc_gray_count_decrement_acq_rel"],
            "_live_bytes_subtract": ns["pcc_gc_live_bytes_subtract"],
            "_unlink_object_node": ns["pcc_gc_object_node_unlink"],
        })

    def allocate(self, size):
        address = self.next_address
        self.next_address += max(256, size + 16)
        return address

    def load(self, address, offset):
        return self.memory.get((address, offset), 0)

    def store(self, address, offset, value):
        self.memory[address, offset] = value

    def memmove(self, to, source, size):
        values = [(offset, value) for (address, offset), value in self.memory.items()
                  if address == source and 0 <= offset < size]
        for offset, value in values:
            self.store(to, offset, value)

    def cas(self, address, offset, expected, desired, success, failure):
        assert self.depth > 0
        actual = self.load(address, offset)
        if actual == expected:
            self.store(address, offset, desired)
        return actual

    def rmw(self, operation, address, offset, value, order):
        assert self.depth > 0
        if operation == "and" and self.before_atomic_and:
            hook, self.before_atomic_and = self.before_atomic_and, None
            hook()
        old = self.load(address, offset)
        assert operation in {"add", "and"}
        self.store(address, offset, old + value if operation == "add" else old & value)
        self.events.append((operation, address, offset))
        return old

    def lock(self):
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.depth -= 1

    @contextmanager
    def locked(self):
        self.lock()
        try:
            yield
        finally:
            self.unlock()

    @property
    def gray(self):
        return self.load("pcc_gc_gray_count", 0)

    def object(self, color=GRAY, freeing=0, size=64, extra=0):
        obj, node = self.allocate(64), self.allocate(88)
        old_head = self.load("pcc_gc_object_head", 0)
        for address, offset, value in [(obj, 0, 1), (obj, 8, 5), (obj, 12, color | OLD | extra),
                                       (node, 0, obj), (node, 8, size), (node, 16, old_head),
                                       (node, 32, freeing)]:
            self.store(address, offset, value)
        if old_head:
            self.store(old_head, 40, node)
        self.store("pcc_gc_object_head", 0, node)
        self.index[obj] = node
        if not freeing:
            self.store("pcc_gc_live_bytes", 0, self.load("pcc_gc_live_bytes", 0) + max(0, size))
            if color & GRAY:
                self.store("pcc_gc_gray_count", 0, self.gray + 1)
        return obj, node

    def remove_index(self, obj):
        assert self.depth > 0
        self.events.append(("unlink", obj))
        return self.index.pop(obj, 0)

    def install_forwarding(self, source, target, plan=0):
        assert self.depth > 0
        if not self.forwarding_ok:
            return 1
        edge = self.allocate(64)
        self.store(edge, 0, source)
        self.store(edge, 8, target)
        self.store(edge, 16, self.load("pcc_gc_forwarding_head", 0))
        self.store("pcc_gc_forwarding_head", 0, edge)
        self.store("pcc_gc_forwarding_population", 0, self.load("pcc_gc_forwarding_population", 0) + 1)
        self.store(source, 12, self.load(source, 12) | 2048)
        self.store(target, 0, self.load(target, 0) + 1)
        self.forwardings[source] = edge
        self.reverse[target] = edge
        return 0

    def unlink_target(self, edge):
        self.reverse.pop(self.load(edge, 8), None)

    def unlink_forwarding(self, edge):
        assert self.load("pcc_gc_forwarding_head", 0) == edge
        self.store("pcc_gc_forwarding_head", 0, self.load(edge, 16))

    def decref(self, obj):
        if obj:
            self.store(obj, 0, self.load(obj, 0) - 1)

    def select(self, source):
        entry = self.allocate(16)
        self.store(entry, 0, source)
        self.store("pcc_gc_relocation_set_head", 0, entry)

    def copy(self, source, size):
        with self.locked():
            return self.ns["pcc_gc_backend4_relocate_copy_preallocated_unlocked"](
                source, size, self.target, 1, 1, self.allocate(24)
            )

    def retire(self, source, finish=None):
        with self.locked():
            self.ns["_retire_forwarded_source_into_finish"](
                source, self.allocate(48) if finish is None else finish
            )

    def moving_pair(self, color=GRAY):
        source, source_node = self.object(color)
        self.target, target_node = self.object(BLACK, extra=64)
        self.select(source)
        return source, source_node, self.target, target_node


@pytest.mark.parametrize("color", [WHITE, GRAY, BLACK])
@pytest.mark.parametrize("extra", [0, DYING])
@pytest.mark.parametrize("size", [0, 64])
def test_active_source_retires_only_its_own_gray_work(color, extra, size):
    state = RetirementState()
    source, node = state.object(color, size=size, extra=extra)
    target, _ = state.object(GRAY)
    original = state.load(source, 12)
    state.retire(source)
    assert state.gray == 1
    assert state.load(source, 12) == original & ~GRAY
    assert source not in state.index and target in state.index
    state.retire(source)
    assert state.gray == 1
    assert state.depth == 0


@pytest.mark.parametrize("backend", [3, 4])
def test_inactive_source_does_not_steal_replacement_gray_work(backend):
    state = RetirementState(backend)
    source, _ = state.object(GRAY, freeing=1)
    state.object(GRAY)
    flags = state.load(source, 12)
    state.retire(source)
    assert state.gray == 1 and state.load(source, 12) == flags


def test_missing_index_falls_back_to_list_and_repairs_trace_cursor():
    state = RetirementState()
    target, target_node = state.object(GRAY)
    source, source_node = state.object(GRAY)
    state.index.pop(source)
    state.store("pcc_gc_trace_cursor", 0, source_node)
    state.retire(source)
    assert state.gray == 1
    assert state.load("pcc_gc_trace_cursor", 0) == target_node
    assert state.load("pcc_gc_object_head", 0) == target_node
    assert state.load(target_node, 40) == 0 and target in state.index


@pytest.mark.parametrize("reason", ["null_source", "null_finish", "lease", "granule_failure", "nonlive_granule"])
def test_refused_retirement_keeps_gray_ownership(reason):
    state = RetirementState()
    source, node = state.object(GRAY)
    finish = state.allocate(48)
    if reason == "lease":
        state.store(node, 80, 1)
    elif reason == "granule_failure":
        state.granule_result = -1
    elif reason == "nonlive_granule":
        state.granule_live, state.granule_result = 0, 1
    state.retire(0 if reason == "null_source" else source,
                 0 if reason == "null_finish" else finish)
    assert state.gray == 1 and state.load(source, 12) & GRAY
    assert source in state.index and state.depth == 0


def test_empty_count_saturates_and_atomic_clear_preserves_late_flags():
    state = RetirementState()
    source, _ = state.object(GRAY)
    state.store("pcc_gc_gray_count", 0, 0)
    original = state.load(source, 12)
    state.before_atomic_and = lambda: state.store(source, 12, state.load(source, 12) | DYING | 64)
    state.retire(source)
    assert state.gray == 0
    assert state.load(source, 12) == (original | DYING | 64) & ~GRAY
    assert state.before_atomic_and is None


@pytest.mark.parametrize("color", [WHITE, GRAY, BLACK])
def test_two_public_drains_retire_shell_then_allow_scheduler_finish(color):
    state = RetirementState()
    source, _, target, _ = state.moving_pair(color)
    assert state.ns["pcc_gc_backend4_evacuation_drain"](1) == 1
    assert state.gray == 1 + int(color == GRAY)
    assert source in state.index and state.load(source, 12) & 131072
    assert state.ns["pcc_gc_backend4_evacuation_drain"](1) == 0
    assert source not in state.index and state.gray == 1
    assert state.load("pcc_gc_mark_active", 0) == 1
    assert state.ns["pcc_gc_tracing_step_cycle"](4) == 1
    assert state.gray == 0 and state.claims == [(1, 4)]
    assert state.events.count(("scan", target)) == 1 and state.depth == 0


def test_target_death_retires_source_and_target_work_once():
    state = RetirementState()
    source, _, target, _ = state.moving_pair()
    assert state.copy(source, 64) == target and state.gray == 2
    state.store(target, 12, state.load(target, 12) | DYING)
    state.ns["pcc_gc_note_object_freeing"](target)
    assert state.gray == 0 and not state.index and not state.forwardings
    state.ns["pcc_gc_note_object_freeing"](target)
    assert state.gray == 0 and state.depth == 0


@pytest.mark.parametrize("failure", ["payload", "forwarding"])
def test_failed_copy_leaves_source_work_and_target_cleanup_balanced(failure):
    state = RetirementState()
    source, _, target, _ = state.moving_pair()
    if failure == "payload":
        state.payload_ok = 0
    else:
        state.forwarding_ok = 0
    assert state.copy(source, 64) == 0
    assert state.gray == 1 and state.load(source, 12) & GRAY
    assert state.load(target, 12) & BLACK and not state.forwardings
    state.ns["pcc_gc_note_object_freeing"](target)
    assert state.gray == 1 and source in state.index and target not in state.index


@pytest.mark.parametrize("path", ["normal", "target_death"])
def test_payload_retirement_refusal_does_not_consume_source_work(path):
    state = RetirementState()
    source, _, target, _ = state.moving_pair()
    assert state.copy(source, 64) == target
    state.payload_ok = 0
    with state.locked():
        if path == "normal":
            state.store(source, 12, state.load(source, 12) | 131072)
            state.ns["pcc_gc_backend4_remap_and_retire_unlocked"](state.allocate(48))
        else:
            state.ns["pcc_gc_forwarding_remove_target"](target, state.allocate(48))
    assert state.gray == 2 and source in state.index
    assert state.load(source, 12) & GRAY and state.trips
