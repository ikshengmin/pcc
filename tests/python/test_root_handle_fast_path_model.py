"""Production-body checks for GC4 scheduler-handle admission and invalidation.

Contract: docs/project-intent.md, five collectors and shared root/slot lifetime.
Raw memory, atomic interleavings, and graph admission are finite doubles. This
does not prove real thread scheduling, native ABI, or stopped-world exclusion.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions


REPO = Path(os.environ.get("PCC_ROOT_HANDLE_REPO", Path(__file__).resolve().parents[2])).resolve()
RUNTIME = REPO / "pcc/runtime/py"
REGISTERED = 562949953421312
MAX_REVISION = 9223372036854775807
REVISION = "pcc_gc_root_registry_revision"
SPIN = "pcc_py_gc_scheduler_root_spin"
MODES = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)]


class RootState:
    def __init__(self, backend=4, tracing=1, initialized=1, frozen=1, running="running",
                 exit_unregisters=0):
        self.memory = {}
        self.address = 1024
        self.graph_depth = 0
        self.graph_entries = 0
        self.list_entries = 0
        self.allocations = []
        self.freed = []
        self.oom = False
        self.cas_interference = []
        self.revision_attempts = []
        self.atomic_revision_reads = []
        self.store("pcc_gc_config_initialized", 0, initialized)
        self.store("pcc_gc_backend_selected", 0, backend)
        self.store("pcc_gc_backend_frozen", 0, frozen)
        self.store("pcc_gc_backend4_no_refcounts", 0, tracing)
        self.store("pcc_tls_thread_state_py", 0, 0 if running == "cold" else REGISTERED)
        self.record = self.allocate_raw(96)
        self.store("pcc_tls_thread_record_py", 0, 0 if running == "cold" else self.record)
        self.store(self.record, 16, 2 if running == "exited" else 0)
        ns = {
            "i64": int, "c_ptr": int,
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.read(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_i32": self.read, "load_i64": self.plain_i64, "load_ptr": self.read,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": lambda base, offset, order: self.read(base, offset),
            "atomic_load_i64": self.atomic_i64,
            "atomic_store_i32": lambda base, offset, value, order: self.store(base, offset, value),
            "atomic_cas_i32": self.cas32, "atomic_cas_i64": self.cas64,
            "null": lambda: 0, "ptr_is_null": lambda ptr: int(ptr == 0),
            "ptr_eq": lambda left, right: int(left == right),
            "malloc": self.malloc, "free": self.free, "memset": self.memset,
            "gc_backend_current": lambda: backend,
            "pcc_py_gc_minor_graph_lock": self.graph_lock,
            "pcc_py_gc_minor_graph_unlock": self.graph_unlock,
            # Whether this thread exits through pcc_thread_unregister_current
            # (pcc_thread_start); only such a thread caches emptied nodes.
            "pcc_thread_exit_unregisters": lambda: exit_unregisters,
        }
        self.ns = ns
        load_functions(RUNTIME / "freestanding_thread_kernel_pthread.py",
                       {"pcc_thread_is_running"}, ns)
        # These finite root slots live outside a machine stack. The shared
        # loader supplies its documented conservative-stack boundary default.
        # OOM still executes the actual cross-object revocation release store.
        load_functions(RUNTIME / "freestanding_gc_generational_scheduler.py",
                       {"pcc_gc_backend3_revoke_closed_young_domain"}, ns)
        load_functions(RUNTIME / "freestanding_gc_root_registry.py", {
            "_scheduler_root_spin_acquire", "_scheduler_root_spin_release",
            "_scheduler_root_node_take", "_scheduler_root_node_give",
            "pcc_gc_cycle_requested_store_release", "_scheduler_handle_needs_graph",
            "pcc_gc_root_registry_note_mutation_locked",
            "pcc_gc_scheduler_root_link_locked", "_scheduler_root_link",
            "pcc_gc_scheduler_root_unlink_locked", "_scheduler_root_unlink",
            "pcc_gc_scheduler_root_register_handle", "pcc_gc_scheduler_root_unregister_handle",
            "pcc_gc_scheduler_root_tls_cache_drain",
        }, ns)

    def allocate_raw(self, size):
        result = self.address
        self.address += max(size, 128)
        return result

    def malloc(self, size):
        assert self.read(SPIN, 0) == 0, "allocation must not hold the list word"
        self.allocations.append(size)
        return 0 if self.oom else self.allocate_raw(size)

    def free(self, pointer):
        assert self.read(SPIN, 0) == 0, "deallocation must not hold the list word"
        self.freed.append(pointer)

    def memset(self, base, value, size):
        # [0] slot, [8] next, [16] previous, [24] this thread's cache link.
        assert value == 0 and size == 32
        for offset in range(0, size, 8):
            self.store(base, offset, 0)

    def read(self, base, offset):
        return self.memory.get((base, offset), 0)

    def plain_i64(self, base, offset):
        if base == REVISION:
            backend = self.read("pcc_gc_backend_selected", 0)
            tracing = self.read("pcc_gc_backend4_no_refcounts", 0)
            assert self.graph_depth > 0 and (backend in (1, 2, 3) or (backend == 4 and not tracing)), (
                "only graph-serialized writers may use the plain revision path"
            )
        return self.read(base, offset)

    def atomic_i64(self, base, offset, order):
        value = self.read(base, offset)
        if base == REVISION:
            self.atomic_revision_reads.append((value, order))
        return value

    def store(self, base, offset, value):
        self.memory[base, offset] = value

    def cas32(self, base, offset, expected, desired, success, failure):
        actual = self.read(base, offset)
        if actual == expected:
            if base == SPIN and desired == 1:
                self.list_entries += 1
            self.store(base, offset, desired)
        return actual

    def cas64(self, base, offset, expected, desired, success, failure):
        assert base == REVISION and offset == 0
        assert self.graph_depth or self.read(SPIN, 0) == 1
        assert (success, failure) == ("acq_rel", "acquire")
        if self.cas_interference:
            self.store(base, offset, self.cas_interference.pop(0))
        actual = self.read(base, offset)
        self.revision_attempts.append((expected, desired, actual))
        if actual == expected:
            self.store(base, offset, desired)
        return actual

    def graph_lock(self):
        # This double records protocol selection, not cold-thread admission.
        assert self.read(self.record, 16) == 0, "an exited caller is not authorized to access roots"
        self.graph_depth += 1
        self.graph_entries += 1

    def graph_unlock(self):
        assert self.graph_depth > 0
        self.graph_depth -= 1

    @property
    def revision(self):
        return self.read(REVISION, 0)

    def register(self, slot):
        return self.ns["pcc_gc_scheduler_root_register_handle"](slot)

    def unregister(self, handle):
        self.ns["pcc_gc_scheduler_root_unregister_handle"](handle)

    def linked(self):
        result = []
        previous = 0
        node = self.read("pcc_gc_scheduler_root_head", 0)
        while node:
            assert node not in result, "cycle or duplicate handle in root registry"
            assert self.read(node, 16) == previous
            result.append(node)
            previous, node = node, self.read(node, 8)
        return result


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("tracing", [0, 1])
@pytest.mark.parametrize("initialized,frozen", [(0, 0), (0, 1), (1, 0), (1, 1)])
@pytest.mark.parametrize("running", ["cold", "running", "exited"])
def test_graph_selection_requires_the_complete_gc4_running_contract(backend, tracing, initialized, frozen, running):
    state = RootState(backend, tracing, initialized, frozen, running)
    expected_fast = backend == 0 or (
        backend == 4 and tracing and initialized and frozen and running == "running"
    )
    before = dict(state.memory)
    assert state.ns["_scheduler_handle_needs_graph"](backend) == int(not expected_fast)
    assert state.memory == before and state.graph_entries == 0
    # Exited mode is a predicate test only: it never invokes a root operation.


@pytest.mark.parametrize("registered,record,state_value,expected", [
    (0, False, 0, 0), (0, True, 0, 0), (1, False, 0, 0),
    (1, True, 0, 1), (1, True, 1, 0), (1, True, 2, 0), (1, True, 3, 0),
])
def test_running_predicate_requires_bit_record_and_running_record_state(registered, record, state_value, expected):
    state = RootState()
    state.store("pcc_tls_thread_state_py", 0, REGISTERED if registered else 0)
    state.store("pcc_tls_thread_record_py", 0, state.record if record else 0)
    state.store(state.record, 16, state_value)
    before = dict(state.memory)
    assert state.ns["pcc_thread_is_running"]() == expected
    assert state.memory == before


@pytest.mark.parametrize("backend,tracing,initialized,frozen,graphs", [
    (0, 0, 1, 1, 0), (1, 0, 1, 1, 6), (2, 0, 1, 1, 6),
    (3, 0, 1, 1, 6), (4, 0, 1, 1, 6), (4, 1, 1, 1, 0),
    (4, 1, 0, 1, 6), (4, 1, 1, 0, 6),
])
def test_real_handle_bodies_keep_list_pool_and_revision_exact(backend, tracing, initialized, frozen, graphs):
    state = RootState(backend, tracing, initialized, frozen)
    first, second = state.register(0x10000), state.register(0x10008)
    assert first and second and first != second
    assert state.linked() == [second, first] and state.revision == 2
    state.unregister(first)
    assert state.linked() == [second] and state.revision == 3
    state.unregister(second)
    assert state.linked() == [] and state.revision == 4
    allocations = len(state.allocations)
    reused = state.register(0x10010)
    assert reused in {first, second} and state.linked() == [reused]
    assert len(state.allocations) == allocations
    state.unregister(reused)
    assert state.linked() == [] and state.revision == 6
    assert state.graph_entries == graphs and state.graph_depth == 0
    assert state.read(SPIN, 0) == 0
    assert state.read("pcc_gc_cycle_requested", 0) == 1
    assert state.read("pcc_py_gc_scheduler_root_free_count", 0) == 2


@pytest.mark.parametrize("backend,tracing", MODES)
def test_null_handles_do_not_mutate_or_request_a_cycle(backend, tracing):
    state = RootState(backend, tracing)
    assert state.register(0) == 0
    state.unregister(0)
    assert state.linked() == [] and state.revision == 0
    assert state.allocations == [] and state.graph_entries == 0
    assert state.read("pcc_gc_cycle_requested", 0) == 0


@pytest.mark.parametrize("backend,tracing", MODES)
def test_node_oom_does_not_link_but_recycled_node_does_not_need_malloc(backend, tracing):
    state = RootState(backend, tracing)
    state.oom = True
    assert state.register(0x10000) == 0
    assert state.read("pcc_gc_backend3_closed_young_domain", 0) == 2
    assert state.linked() == [] and state.revision == 0 and state.graph_entries == 0
    assert state.read(SPIN, 0) == 0 and state.read("pcc_gc_cycle_requested", 0) == 0
    state.oom = False
    handle = state.register(0x10000)
    state.unregister(handle)
    allocations = len(state.allocations)
    state.oom = True
    reused = state.register(0x10008)
    assert reused == handle and len(state.allocations) == allocations
    state.unregister(reused)
    assert state.revision == 4 and state.linked() == []


@pytest.mark.parametrize("backend,tracing", MODES)
def test_recycled_handle_pair_needs_only_one_list_acquisition_per_change(backend, tracing):
    state = RootState(backend, tracing)
    initial = state.register(0x10000)
    state.unregister(initial)
    entries = state.list_entries
    state.oom = True
    handle = state.register(0x10008)
    assert handle == initial and state.linked() == [handle]
    assert state.read(handle, 0) == 0x10008
    state.unregister(handle)
    assert state.linked() == [] and state.revision == 4
    assert state.list_entries - entries == (2 if backend == 0 or (backend == 4 and tracing) else 4)


@pytest.mark.parametrize("backend", range(5))
def test_counted_handle_operations_skip_tracing_admission_queries(backend):
    state = RootState(backend, 0)
    state.ns["_scheduler_handle_needs_graph"] = lambda unused: pytest.fail(
        "counted handle operations must keep their direct backend path"
    )
    first = state.register(0x10000)
    state.unregister(first)
    reused = state.register(0x10008)
    assert reused == first and state.linked() == [reused]
    state.unregister(reused)
    assert state.revision == 4 and state.linked() == []


@pytest.mark.parametrize("backend,tracing", MODES)
def test_full_free_pool_releases_detached_handle_outside_list_word(backend, tracing):
    state = RootState(backend, tracing)
    handle = state.register(0x10000)
    state.store("pcc_py_gc_scheduler_root_free_count", 0, 4096)
    state.unregister(handle)
    assert state.freed == [handle] and state.linked() == []
    assert state.read("pcc_py_gc_scheduler_root_free_count", 0) == 4096
    assert state.revision == 2 and state.read(SPIN, 0) == 0


@pytest.mark.parametrize("initial,interference,expected", [
    (0, [], 1), (MAX_REVISION, [], 1), (10, [11, 12], 13),
    (MAX_REVISION - 1, [MAX_REVISION, 1], 2),
])
def test_revision_cas_retries_competing_writer_and_wraps_to_one(initial, interference, expected):
    state = RootState()
    state.store(REVISION, 0, initial)
    state.cas_interference = list(interference)
    state.graph_lock()
    state.ns["pcc_gc_root_registry_note_mutation_locked"]()
    state.graph_unlock()
    assert state.revision == expected
    assert len(state.revision_attempts) == len(interference) + 1
    assert state.atomic_revision_reads == [(initial, "acquire")]


@pytest.mark.parametrize("backend,tracing", MODES)
@pytest.mark.parametrize("initial,expected", [(0, 1), (MAX_REVISION, 1)])
def test_revision_uses_atomics_only_when_writers_have_independent_locks(backend, tracing, initial, expected):
    state = RootState(backend, tracing)
    state.store(REVISION, 0, initial)
    state.graph_lock()
    state.ns["pcc_gc_root_registry_note_mutation_locked"]()
    state.graph_unlock()
    assert state.revision == expected
    independent_locks = backend == 0 or (backend == 4 and tracing)
    assert len(state.revision_attempts) == int(independent_locks)
    assert state.atomic_revision_reads == ([(initial, "acquire")] if independent_locks else [])


@pytest.mark.parametrize("backend,tracing", [(0, 0), (4, 1)])
def test_started_thread_reuses_emptied_nodes_without_the_list_word(backend, tracing):
    # A thread whose exit unregisters it keeps an unregistered node linked
    # with an empty slot (every walker skips it) and reuses it for its next
    # registration: neither change takes the list word or touches the list.
    state = RootState(backend, tracing, exit_unregisters=1)
    first = state.register(0x10000)
    linked = state.linked()
    entries, revision = state.list_entries, state.revision
    state.unregister(first)
    assert state.linked() == linked and state.read(first, 0) == 0
    assert state.read("pcc_gc_scheduler_root_tls_cache", 0) == first
    again = state.register(0x10008)
    assert again == first and state.read(first, 0) == 0x10008
    assert state.read("pcc_gc_scheduler_root_tls_cache", 0) == 0
    assert state.list_entries == entries and state.revision == revision
    assert state.graph_entries == 0 and state.read(SPIN, 0) == 0
    # At exit the cached nodes leave the list and return to the pool.
    state.unregister(again)
    state.ns["pcc_gc_scheduler_root_tls_cache_drain"]()
    assert state.linked() == [] and state.read("pcc_gc_scheduler_root_tls_cache", 0) == 0
    assert state.read("pcc_gc_scheduler_root_tls_cache_count", 0) == 0
    assert state.read("pcc_py_gc_scheduler_root_free_count", 0) == 1


def test_started_thread_cache_holds_at_most_64_nodes():
    state = RootState(0, 0, exit_unregisters=1)
    handles = [state.register(0x10000 + 8 * index) for index in range(65)]
    for handle in handles:
        state.unregister(handle)
    assert state.read("pcc_gc_scheduler_root_tls_cache_count", 0) == 64
    # The 65th was detached and pooled as before.
    assert len(state.linked()) == 64
    assert state.read("pcc_py_gc_scheduler_root_free_count", 0) == 1
