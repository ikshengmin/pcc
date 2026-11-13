"""Persistent production gray work through concurrent scheduler slices.

Contract: docs/project-intent.md (object lifetime and the five collectors).
Queue, shade, exact membership, scheduler, whole-owner visitor, workstack and
C-extension transaction bodies are unchanged production code. Only raw memory,
atomics, TLS, registration, locks and the external extension slot provider are
host boundaries. This proves neither native concurrency/weak memory nor final
remark, sweeping, full-runtime qualification or a latency/throughput bound.
"""
from __future__ import annotations

from collections import Counter

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.test_gc_gray_worklist import (
    BACKEND, BLACK, COUNT, EPOCH, FALLBACK, GRAY, MODE, NONMOVING, PENDING, PUBLISHED, READ,
    REQUEST, SOURCE, WHITE, _RawCollectorMemory, _install,
)


CURSOR = "pcc_gc_trace_cursor"
HEAD = "pcc_gc_object_head"
CEXT_OBJECT = "pcc_gc_trace_cext_pending_obj"
CEXT_EPOCH = "pcc_gc_trace_cext_pending_epoch"
CEXT_BACKEND = "pcc_gc_trace_cext_pending_backend"
ENABLED = "py_gc_enabled"
THRESHOLD0 = "py_gc_threshold0"
DEBT = "pcc_gc_debt_bytes"


class _SchedulerMemory(_RawCollectorMemory):
    """Join the existing strict raw queue boundary to production scheduling."""

    def __init__(self, backend, *, cms_worker=False):
        super().__init__()
        self.graph_depth = 0
        self.next_aux = 0x900000000
        self.node_addresses = set()
        self.slot_reads = []
        self.extension_calls = []
        self.decrefs = Counter()
        self.completions = []
        self.on_extension = None
        self.cells.update({
            CURSOR: bytearray(8),
            "pcc_gc_mark_active": bytearray((1).to_bytes(4, "little")),
            "pcc_gc_cycle_requested": bytearray(4),
            "pcc_gc_trace_extension_roots_pending": bytearray(4),
            "pcc_gc_tracing_finish_claim_epoch": bytearray(8),
            "pcc_gc_tracing_finish_claim_backend": bytearray(8),
            "pcc_gc_cms_worker_current": bytearray(4),
            ENABLED: bytearray(4),
            THRESHOLD0: bytearray(4),
        })
        self.set_scalar(BACKEND, backend)
        # Queue-only slices are the count-free nonmoving collectors' (GC1/GC2).
        self.set_scalar(NONMOVING, int(backend in (1, 2)))
        self.set_scalar(ENABLED, 1)
        self.set_scalar(THRESHOLD0, 700)
        self.set_scalar("pcc_gc_cms_worker_current", int(cms_worker))
        self.set_scalar("pcc_gc_tracing_finish_claim_backend", -1)
        self.namespace.update({
            "abi_constant": ABI_CONSTANTS.__getitem__,
            "stack_alloc": self.allocate_aux,
            "ptr_add": lambda base, offset: (base, offset),
            "call_void_ptr_i64_ptr": lambda visitor, slot, role, ctx: visitor(slot, role, ctx),
            "pcc_gc_load_ptr": self.read_slot,
            "pcc_py_gc_minor_graph_lock": self.graph_lock,
            "pcc_py_gc_minor_graph_unlock": self.graph_unlock,
            "pcc_thread_owns_stopped_world": lambda: 0,
            "pcc_threads_enabled": lambda: 1,
            "pcc_gc_complete_claimed_tracing_cycle": self.complete,
            "pcc_capi_visit_cext_object_slots_i64": self.visit_extension,
            "py_decref": self.decref,
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_object_slots.py"), {
            "_visit_slot", "_has_no_pointer_slots",
            "pcc_gc_visit_object_slots_slice", "pcc_gc_visit_object_slots",
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_common_mark_cycle.py"), {
            "pcc_gc_trace_referents_work", "pcc_gc_trace_stack_drain_work",
            "pcc_gc_trace_cext_slot_transaction", "pcc_gc_trace_cext_referents_unlocked",
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_public_collection.py"), {
            "pcc_gc_is_cms_worker",
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_incremental_concurrent_scheduler.py"), {
            "pcc_gc_tracing_step_cycle", "pcc_gc_tracing_finish_left_to_mutator",
            "_pcc_gc_trace_cext_complete_context",
        })

    def install_pacing(self):
        # Extend only raw counters/atomics for the actual allocation caller
        # and worker loop. Final remark/sweep remain outside this boundary.
        for name in (DEBT, "pcc_gc_debt_threshold_override",
                     "pcc_gc_tracing_finish_commits"):
            self.cells[name] = bytearray(8)
        for name in ("pcc_gc_stepmul", "pcc_gc_cms_queue_pushes",
                     "pcc_gc_cms_mutator_assists", "pcc_gc_cms_sweep_pending",
                     "pcc_gc_backend4_auto_step_pending", "pcc_gc_cms_worker_drains",
                     "pcc_gc_cms_worker_stop_requested", "pcc_gc_cms_worker_stops"):
            self.cells[name] = bytearray(4)
        self.set_scalar("pcc_gc_stepmul", 100)
        self.set_scalar("pcc_gc_debt_threshold_override", 64)
        self.namespace.update({
            "atomic_rmw_i32": self.metric_add32,
            "unsigned_div_i64": lambda left, right: left // right,
            # The worker retires scalar blocks and deferred frees each round;
            # this model retires none.
            "pcc_gc_scalar_sweep_drain": lambda worker, closing, token: 0,
            "pcc_gc_deferred_free_worker_drain": lambda closing: 0,
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_incremental_concurrent_scheduler.py"), {
            "pcc_gc_cms_note_alloc", "pcc_gc_tracing_debt_threshold",
            "pcc_gc_tracing_budget_from_debt", "pcc_gc_tracing_discharge_debt",
        })
        _install(self.namespace, SOURCE.with_name("freestanding_gc_public_collection.py"), {
            "pcc_gc_cms_worker_main_py",
        })

    def metric_add32(self, operation, pointer, offset, value, order):
        assert operation == "add" and offset == 0 and value == 1
        assert (pointer, order) in {
            ("pcc_gc_cms_queue_pushes", "release"),
            ("pcc_gc_cms_mutator_assists", "relaxed"),
            ("pcc_gc_cms_worker_stops", "relaxed"),
        }
        observed = self.load(pointer, offset, 4)
        self.store(pointer, offset, observed + value, 4)
        return observed

    def atomic_load(self, pointer, offset, order):
        if pointer == DEBT:
            assert (offset, order) == (0, "acquire")
            return self.load(pointer, offset)
        return super().atomic_load(pointer, offset, order)

    def cas(self, pointer, offset, expected, desired, success, failure):
        if pointer == DEBT:
            assert (offset, success, failure) == (0, "acq_rel", "acquire")
            observed = self.load(pointer, offset)
            if observed == expected:
                self.store(pointer, offset, desired)
            return observed
        return super().cas(pointer, offset, expected, desired, success, failure)

    def rmw(self, operation, pointer, offset, value, order):
        if pointer == DEBT:
            assert (operation, offset, order) == ("add", 0, "acq_rel")
            observed = self.load(pointer, offset)
            self.store(pointer, offset, observed + value)
            return observed
        return super().rmw(operation, pointer, offset, value, order)

    def global_load(self, name):
        if name == CURSOR:
            return self.load(name, 0)
        return super().global_load(name)

    def global_store(self, name, value):
        if name == CURSOR:
            self.store(name, 0, value)
        else:
            super().global_store(name, value)

    def allocate_aux(self, size):
        address = self.next_aux
        self.next_aux += max(0x1000, size + 16)
        self.heap[address] = bytearray(size)
        self.initialized[address] = set(range(size))
        return address

    def graph_lock(self):
        self.graph.acquire()
        self.graph_depth += 1
        self.thread.graph_owned = True

    def graph_unlock(self):
        assert self.graph_depth > 0
        self.graph_depth -= 1
        self.thread.graph_owned = bool(self.graph_depth)
        self.graph.release()

    def find(self, obj):
        assert self.graph_depth > 0, "object-index lookup escaped the graph lock"
        return super().find(obj)

    def read_slot(self, owner, slot):
        assert self.graph_depth > 0, "tracing slot load escaped the graph lock"
        base, offset = slot
        self.slot_reads.append((base, offset))
        return self.load(base, offset)

    def leaf(self, name, *, black=False):
        obj = self.object(name)
        self.store(obj, 8, ABI_CONSTANTS["object.type.float"], 4)
        if black:
            self.namespace["pcc_gc_set_mark_color"](obj, BLACK)
        return obj

    def object(self, name, *, address=None, cext=False):
        obj = super().object(name, address=address, cext=cext)
        self.node_addresses.add(self.index[obj])
        return obj

    def list_owner(self, name, children):
        obj = self.object(name)
        self.heap[obj].extend(bytearray(48))
        self.initialized[obj].update(range(16, 64))
        self.store(obj, 8, ABI_CONSTANTS["object.type.list"], 4)
        self.store(obj, ABI_CONSTANTS["object.list.length_offset"], len(children))
        items = self.allocate_aux(max(8, len(children) * 8))
        for index, child in enumerate(children):
            self.store(items, index * 8, child)
        self.store(obj, ABI_CONSTANTS["object.list.items_offset"], items)
        return obj

    def visit_extension(self, obj, visitor, context):
        # This is the real external C-extension enumeration boundary. Both
        # the transaction callback and its completion run production bodies.
        assert self.graph_depth == 0
        assert self.scalar("pcc_gc_trace_stack_owner") == 0
        assert self.scalar("pcc_gc_trace_stack_count") == 0
        assert self.load(obj, 8, 4) == 10001
        assert self.increfs[obj] == 1 and self.decrefs[obj] == 0
        snapshot = (obj, self.scalar(CEXT_OBJECT), self.scalar(CEXT_EPOCH),
                    self.scalar(CEXT_BACKEND), self.graph_depth)
        assert snapshot[0] == snapshot[1] == self.load(context, 0)
        assert snapshot[2:4] == (self.load(context, 8), self.load(context, 16))
        self.extension_calls.append(snapshot)
        if self.on_extension is not None:
            self.on_extension(obj)
        for child in self.object_data[obj]["edges"]:
            slot = self.allocate_aux(8)
            self.store(slot, 0, child)
            visitor((slot, 0), 1, context)
            assert self.graph_depth == 0
        return 1

    def decref(self, obj):
        assert self.graph_depth == 0
        self.decrefs.update([obj])

    def complete(self, epoch, backend):
        # Final remark/sweep are explicitly outside this scheduler boundary.
        assert self.graph_depth == 0 and self.gray == 0
        assert self.scalar("pcc_gc_trace_stack_owner") == 0
        assert self.scalar("pcc_gc_trace_stack_count") == 0
        self.completions.append((epoch, backend))

    def step(self, budget):
        result = self.namespace["pcc_gc_tracing_step_cycle"](budget)
        assert self.graph_depth == 0
        assert not getattr(self.thread, "graph_owned", False)
        assert self.scalar("pcc_gc_trace_stack_owner") == 0
        assert self.scalar("pcc_gc_trace_stack_count") == 0
        return result

    def node_next_reads(self, since):
        return [row for row in self.accesses[since:]
                if row[1] == "read" and row[2] in self.node_addresses and row[3] == 16]


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("published", [False, True])
def test_queued_gray_precedes_long_black_prefix_without_walking_it(backend, published):
    memory = _SchedulerMemory(backend)
    for index in range(97):
        memory.leaf(f"black-{index}", black=True)
    target = memory.leaf("target")
    memory.set_scalar(CURSOR, memory.scalar(HEAD))
    if published:
        with memory.as_thread("producer"):
            memory.shade(target)
            memory.call("flush")
    else:
        memory.shade(target)
    before = len(memory.accesses)
    assert memory.step(1) == 1
    assert memory.index_lookups == [target]
    assert not memory.node_next_reads(before)
    assert memory.load(target, 12, 4) & 56 == BLACK
    assert memory.gray_changes == [1, -1] and memory.gray == 0
    assert memory.completions == [(17, backend)]
    assert bool(memory.exchanges) is published


@pytest.mark.parametrize("backend", [1, 2])
def test_queued_wide_owner_overshoots_once_without_consuming_next_owner(backend):
    memory = _SchedulerMemory(backend)
    for index in range(17):
        memory.leaf(f"black-{index}", black=True)
    wide = memory.list_owner("wide", [0] * 9)
    next_owner = memory.list_owner("next", [0] * 9)
    cursor = memory.scalar(HEAD)
    memory.set_scalar(CURSOR, cursor)
    with memory.as_thread("producer"):
        memory.shade(wide)
        memory.shade(next_owner)
        batch = memory.local()
        memory.call("flush")
    before = len(memory.accesses)
    assert memory.step(4) == 1
    assert len(memory.slot_reads) == 9
    assert memory.load(wide, 12, 4) & 56 == BLACK
    assert memory.load(next_owner, 12, 4) & 56 == GRAY
    assert memory.gray == 1 and not memory.completions
    assert memory.scalar(CURSOR) == cursor
    assert memory.index_lookups == [wide]
    assert not memory.node_next_reads(before)
    assert memory.scalar(PENDING) == batch and batch not in memory.freed
    with memory.graph:
        memory.thread.graph_owned = True
        try:
            assert memory.load(batch, READ) == 1
            assert memory.load(batch, COUNT) == 2
        finally:
            memory.thread.graph_owned = False
    assert memory.step(4) == 1
    assert len(memory.slot_reads) == 18 and memory.gray == 0
    assert memory.load(next_owner, 12, 4) & 56 == BLACK
    assert memory.completions == [(17, backend)]


@pytest.mark.parametrize("backend", [1, 2])
def test_direct_and_stack_share_work_and_queue_recovers_discarded_child(backend):
    memory = _SchedulerMemory(backend)
    left = memory.list_owner("left", [0] * 7)
    right = memory.list_owner("right", [0] * 7)
    root = memory.list_owner("root", [left, right])
    with memory.as_thread("producer"):
        memory.shade(root)
        memory.call("flush")
    before = len(memory.accesses)
    assert memory.step(4) == 2
    assert len(memory.slot_reads) == 9
    assert memory.load(root, 12, 4) & 56 == BLACK
    assert memory.load(right, 12, 4) & 56 == BLACK
    assert memory.load(left, 12, 4) & 56 == GRAY
    assert memory.gray == 1 and memory.local() == 0
    assert memory.scalar(PUBLISHED) != 0
    # The short-lived stack was discarded by step(), but its left child is
    # still in a real published batch. The later BLACK right token is benign.
    assert memory.step(1) == 1
    assert len(memory.slot_reads) == 16 and memory.gray == 0
    assert memory.load(left, 12, 4) & 56 == BLACK
    assert not memory.node_next_reads(before)
    assert memory.completions == [(17, backend)]


@pytest.mark.parametrize("backend", [1, 2])
def test_partial_step_flushes_new_child_tail_for_another_consumer(backend):
    memory = _SchedulerMemory(backend)
    left, right = memory.leaf("left"), memory.leaf("right")
    root = memory.list_owner("root", [left, right, *([0] * 7)])
    with memory.as_thread("producer"):
        memory.shade(root)
        original_batch = memory.local()
        memory.call("flush")
    with memory.as_thread("cms"):
        assert memory.step(4) == 1
        assert len(memory.slot_reads) == 9
        assert memory.local() == 0
        child_batch = memory.scalar(PUBLISHED)
        assert child_batch and child_batch != original_batch
        assert memory.scalar(PENDING) == original_batch
        assert memory.gray == 2 and not memory.completions
        assert memory.load(left, 12, 4) & 56 == GRAY
        assert memory.load(right, 12, 4) & 56 == GRAY
        memory.call("unregister")
        assert child_batch not in memory.freed
    before = len(memory.accesses)
    with memory.as_thread("mutator-assist"):
        assert memory.local() == 0 and memory.step(1) == 1
        assert memory.scalar(PENDING) == child_batch
        assert original_batch in memory.freed and child_batch not in memory.freed
        assert memory.load(left, 12, 4) & 56 == BLACK
        assert memory.load(right, 12, 4) & 56 == GRAY
        assert memory.gray == 1
    with memory.as_thread("next-consumer"):
        assert memory.local() == 0 and memory.step(1) == 1
    assert not memory.node_next_reads(before)
    assert memory.gray == 0 and memory.load(right, 12, 4) & 56 == BLACK
    assert memory.gray_changes == [1, 1, 1, -1, -1, -1]
    assert memory.completions == [(17, backend)]


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("stale_kind", ["retired", "already-black"])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_stale_queue_token_spends_scan_budget_without_retiring_gray(backend, stale_kind, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    stale, live = memory.leaf("stale"), memory.leaf("live")
    with memory.as_thread("producer"):
        memory.shade(stale)
        if stale_kind == "retired":
            memory.retire(stale)
        else:
            # A real stack drain can finish an object while its address token
            # remains queued; do not fabricate a BLACK queue entry.
            memory.graph_lock()
            try:
                memory.namespace["pcc_gc_trace_stack_begin"]()
                memory.namespace["pcc_gc_trace_stack_push"](stale)
                assert memory.namespace["pcc_gc_trace_stack_drain"](1) == 1
                memory.namespace["pcc_gc_trace_stack_end"]()
            finally:
                memory.graph_unlock()
        memory.shade(live)
        memory.call("flush")
    # Nothing in the fallback list can rescue the live entry in this test.
    memory.set_scalar(HEAD, 0)
    memory.set_scalar(CURSOR, 0)
    memory.gray_changes.clear()
    before = len(memory.accesses)
    assert memory.step(1) == 0
    assert memory.index_lookups == [stale]
    assert memory.gray == 1 and memory.gray_changes == []
    assert not memory.node_next_reads(before)
    if stale_kind == "retired":
        assert not any(row[2] == stale for row in memory.accesses[before:])
    assert not any(row[2] == live for row in memory.accesses[before:])
    assert memory.step(1) == 1
    assert memory.gray == 0 and memory.gray_changes == [-1]
    assert memory.load(live, 12, 4) & 56 == BLACK
    assert memory.completions == ([] if cms_worker else [(17, backend)])


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("invalidate", [None, "epoch", "backend"])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_queued_cext_handoff_keeps_pin_identity_and_unlocked_callback(backend, invalidate, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    memory.set_scalar(REQUEST, 1)
    extension = memory.object("extension", cext=True)
    child = memory.leaf("child")
    other = memory.leaf("other")
    memory.object_data[extension]["edges"] = [child]
    with memory.as_thread("producer"):
        memory.shade(extension)
        memory.shade(other)
        memory.call("flush")

    def while_unlocked(obj):
        assert obj == extension and memory.gray == 2
        assert memory.load(extension, 12, 4) & 56 == GRAY
        # A reentrant scheduler must observe the pending owner and leave it
        # alone, without consuming or double-retiring its queued obligation.
        assert memory.step(1) == 0
        # The eligible consumer ended its request before the unlocked callback;
        # the reentrant pending-owner return must not arm another request.
        assert memory.scalar(REQUEST) == 0
        if invalidate == "epoch":
            memory.set_scalar(EPOCH, 18)
        elif invalidate == "backend":
            memory.set_scalar(BACKEND, 3 - backend)

    memory.on_extension = while_unlocked
    memory.gray_changes.clear()
    before = len(memory.accesses)
    assert memory.step(1) == 1  # Existing C-extension dispatch credit is retained.
    assert memory.extension_calls == [(extension, extension, 17, backend, 0)]
    assert memory.increfs == memory.decrefs == {extension: 1}
    assert not memory.node_next_reads(before)
    assert memory.scalar(CEXT_OBJECT) == memory.scalar(CEXT_EPOCH) == 0
    assert memory.scalar(CEXT_BACKEND) == -1
    assert memory.load(other, 12, 4) & 56 == GRAY
    assert not memory.completions
    if invalidate is None:
        assert memory.load(extension, 12, 4) & 56 == BLACK
        assert memory.load(child, 12, 4) & 56 == GRAY
        assert memory.gray == 2 and memory.gray_changes == [1, -1]
        assert memory.step(1) == 1
        assert memory.gray == 1 and memory.load(child, 12, 4) & 56 == BLACK
        assert memory.load(other, 12, 4) & 56 == GRAY
        assert memory.step(1) == 1
        assert memory.gray == 0 and memory.load(other, 12, 4) & 56 == BLACK
        assert memory.completions == ([] if cms_worker else [(17, backend)])
        assert not memory.node_next_reads(before)
    else:
        assert memory.load(extension, 12, 4) & 56 == GRAY
        assert memory.load(child, 12, 4) & 56 == WHITE
        assert memory.gray == 2 and memory.gray_changes == []


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("initial_cursor", [False, True])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumers_request_two_unpublished_tails_without_inspecting_cursor(backend, initial_cursor, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    for index in range(97):
        memory.leaf(f"prefix-{index}", black=True)
    first, second = memory.leaf("first"), memory.leaf("second")
    for producer, obj in (("producer-a", first), ("producer-b", second)):
        with memory.as_thread(producer):
            memory.shade(obj)
            assert memory.local_count() == 1
    cursor = memory.scalar(HEAD) if initial_cursor else 0
    memory.set_scalar(CURSOR, cursor)
    before = len(memory.accesses)
    assert memory.step(8) == 0
    assert memory.gray == 2 and memory.scalar(REQUEST) == 1
    assert memory.scalar(CURSOR) == cursor and not memory.node_next_reads(before)
    assert not memory.completions
    with memory.as_thread("producer-a"):
        memory.call("flush")
        assert memory.scalar(REQUEST) == 1  # Cannot acknowledge producer-b.
    # Only eligible consumption may end the request. B is still unpublished, so
    # clearing on A's token is progress, never a claim that all TLS is empty.
    assert memory.step(1) == 1
    assert memory.gray == 1 and memory.scalar(REQUEST) == 0
    assert memory.scalar(CURSOR) == cursor
    assert not memory.node_next_reads(before) and not memory.completions
    assert memory.step(8) == 0
    assert memory.gray == 1 and memory.scalar(REQUEST) == 1
    with memory.as_thread("producer-b"):
        memory.call("flush")
        assert memory.scalar(REQUEST) == 1
    # No new allocation ticket or cycle request accompanies publication.
    assert memory.step(8) == 1
    assert memory.gray == 0 and memory.scalar(REQUEST) == 0
    assert not memory.node_next_reads(before)
    assert memory.completions == ([] if cms_worker else [(17, backend)])
    assert memory.gray_changes == [1, 1, -1, -1]
    assert memory.scalar("pcc_gc_tracing_finish_claim_epoch") == (0 if cms_worker else 17)
    assert memory.scalar("pcc_gc_tracing_finish_claim_backend") == (-1 if cms_worker else backend)


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumers_do_not_finish_a_shader_reserved_before_cas(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    target = memory.leaf("reserved")
    original = memory.namespace["atomic_cas_i32"]
    observations = []

    def interleave(pointer, offset, expected, desired, success, failure):
        if pointer == target and not observations:
            assert memory.gray == 1 and memory.load(target, 12, 4) & 56 == WHITE
            with memory.as_thread("consumer"):
                before = len(memory.accesses)
                observations.append(memory.step(8))
                assert not memory.node_next_reads(before)
                assert memory.scalar(REQUEST) == 1 and not memory.completions
        return original(pointer, offset, expected, desired, success, failure)

    memory.namespace["atomic_cas_i32"] = interleave
    with memory.as_thread("producer"):
        memory.shade(target)
        memory.call("flush")
    assert observations == [0] and memory.gray == 1
    assert memory.step(8) == 1
    assert memory.gray == 0 and memory.scalar(REQUEST) == 0
    assert memory.completions == ([] if cms_worker else [(17, backend)])


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumer_oom_keeps_bounded_cursor_fallback_after_later_allocation(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    for index in range(9):
        memory.leaf(f"prefix-{index}", black=True)
    lost, queued = memory.leaf("lost"), memory.leaf("queued")
    with memory.as_thread("producer"):
        memory.fail_allocations = 1
        memory.shade(lost)
        memory.shade(queued)
        memory.call("flush")
    assert memory.scalar(FALLBACK) == 1
    memory.set_scalar(REQUEST, 1)
    before = len(memory.accesses)
    assert memory.step(2) == 1
    assert memory.gray == 1 and memory.scalar(REQUEST) == int(not cms_worker)
    assert len(memory.node_next_reads(before)) == 1
    for _ in range(8):
        if memory.gray == 0:
            break
        before = len(memory.accesses)
        memory.step(2)
        assert len(memory.node_next_reads(before)) <= 2
    assert memory.gray == 0 and memory.scalar(FALLBACK) == 1
    assert memory.completions == ([] if cms_worker else [(17, backend)])
    memory.locked_call("clear_stopped")
    assert memory.scalar(FALLBACK) == memory.scalar(REQUEST) == 0


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumer_observes_oom_that_arrives_at_queue_exhaustion(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    target = memory.leaf("lost")
    original = memory.namespace["pcc_gc_gray_worklist_take_locked"]
    injected = []

    def exhaust():
        result = original()
        if not result and not injected:
            injected.append(True)
            with memory.as_thread("producer"):
                memory.fail_allocations = 1
                memory.shade(target)
        return result

    memory.namespace["pcc_gc_gray_worklist_take_locked"] = exhaust
    assert memory.step(2) == 1
    assert memory.gray == 0 and memory.scalar(FALLBACK) == 1
    assert memory.scalar(REQUEST) == 0
    assert memory.completions == ([] if cms_worker else [(17, backend)])


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("mode", ["disabled-auto", "zero-threshold", "stopped", "disabled-queue"])
def test_unpublished_tail_retains_original_fallback_outside_eligible_consumer(backend, mode):
    memory = _SchedulerMemory(backend, cms_worker=mode in ("stopped", "disabled-queue"))
    if mode == "disabled-auto":
        memory.set_scalar(ENABLED, 0)
    elif mode == "zero-threshold":
        memory.set_scalar(THRESHOLD0, 0)
    if mode == "stopped":
        memory.namespace["pcc_thread_owns_stopped_world"] = lambda: 1
    target = memory.leaf("tail")
    with memory.as_thread("producer"):
        memory.shade(target)
    if mode == "disabled-queue":
        # A counted collector neither queues nor runs queue-only slices.
        memory.set_scalar(MODE, 0)
        memory.set_scalar(NONMOVING, 0)
    before = len(memory.accesses)
    assert memory.step(2) == 1
    assert memory.node_next_reads(before) and memory.gray == 0
    assert memory.scalar(REQUEST) == 0


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("enabled, threshold0", [(1, 700), (0, 700), (1, 0)])
def test_mutator_acknowledges_request_only_with_automatic_collection_enabled(backend, enabled, threshold0):
    memory = _SchedulerMemory(backend)
    memory.set_scalar(ENABLED, enabled)
    memory.set_scalar(THRESHOLD0, threshold0)
    target = memory.leaf("queued")
    with memory.as_thread("producer"):
        memory.shade(target)
        memory.call("flush")
    memory.set_scalar(REQUEST, 1)
    assert memory.step(1) == 1
    assert memory.gray == 0
    assert memory.scalar(REQUEST) == int(not (enabled and threshold0 > 0))


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumer_stale_tokens_clear_request_once_per_slice_then_rearm(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    stale = [memory.leaf(f"black-{index}", black=True) for index in range(3)]
    hidden = memory.leaf("hidden")
    with memory.as_thread("stale-publisher"):
        for obj in stale:
            memory.call("note", obj)
        memory.call("flush")
    with memory.as_thread("hidden-producer"):
        memory.shade(hidden)
    memory.set_scalar(REQUEST, 1)
    before = len(memory.accesses)
    assert memory.step(2) == 0
    writes = [row for row in memory.accesses[before:]
              if row[1] == "write" and row[2] == REQUEST]
    assert len(writes) == 1 and writes[0][-1] == 0
    assert memory.gray == 1 and not memory.completions
    assert not memory.node_next_reads(before)
    assert memory.step(2) == 0
    assert memory.gray == 1 and memory.scalar(REQUEST) == 1


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_consumer_reused_address_retires_current_member_only_once(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    old, hidden = memory.leaf("old"), memory.leaf("hidden")
    with memory.as_thread("producer"):
        memory.shade(old)
        memory.retire(old)
        current = memory.object("current", address=old)
        memory.store(current, 8, ABI_CONSTANTS["object.type.float"], 4)
        memory.shade(current)
        memory.call("flush")
    with memory.as_thread("hidden-producer"):
        memory.shade(hidden)
    memory.set_scalar(CURSOR, memory.scalar(HEAD))
    memory.set_scalar(REQUEST, 1)
    memory.gray_changes.clear()
    before = len(memory.accesses)
    assert memory.step(1) == 1
    assert memory.gray == 1 and memory.gray_changes == [-1]
    assert memory.load(current, 12, 4) & 56 == BLACK
    assert memory.scalar(REQUEST) == 0
    assert memory.step(1) == 0  # The duplicate token spends scan budget only.
    assert memory.gray == 1 and memory.gray_changes == [-1]
    assert memory.index_lookups == [current, current]
    assert not memory.completions and not memory.node_next_reads(before)
    assert memory.step(1) == 0
    assert memory.scalar(REQUEST) == 1
    with memory.as_thread("hidden-producer"):
        memory.call("flush")
    assert memory.step(1) == 1
    assert memory.gray == 0 and memory.gray_changes == [-1, -1]
    assert memory.index_lookups == [current, current, hidden]
    assert memory.scalar(REQUEST) == 0 and not memory.node_next_reads(before)
    assert memory.completions == ([] if cms_worker else [(17, backend)])


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("cms_worker", [False, True])
def test_eligible_consumer_clears_obsolete_request_without_gray_credit(backend, cms_worker):
    memory = _SchedulerMemory(backend, cms_worker=cms_worker)
    memory.set_scalar(REQUEST, 1)
    before = len(memory.accesses)
    assert memory.step(1) == 0
    assert memory.scalar(REQUEST) == 0 and not memory.node_next_reads(before)
    assert memory.completions == ([] if cms_worker else [(17, backend)])


@pytest.mark.parametrize("backend, count_free", [(0, 1), (1, 0), (2, 0), (3, 1), (4, 1)])
def test_other_mutator_modes_keep_cursor_and_do_not_store_publication_request(backend, count_free):
    memory = _SchedulerMemory(backend)
    memory.set_scalar(MODE, count_free)
    memory.set_scalar(NONMOVING, int(count_free and backend in (1, 2)))
    target = memory.leaf("tail")
    with memory.as_thread("producer"):
        memory.shade(target)
    memory.set_scalar(REQUEST, 1)
    before = len(memory.accesses)
    assert memory.step(2) == 1
    assert memory.gray == 0 and memory.node_next_reads(before)
    assert memory.load(target, 12, 4) & 56 == BLACK
    assert memory.scalar(REQUEST) == 1
    assert not [row for row in memory.accesses[before:]
                if row[1] == "write" and row[2] == REQUEST]


@pytest.mark.parametrize("backend", [1, 2])
def test_allocation_assist_waits_for_hidden_tails_without_paying_debt_or_sweeping(backend):
    memory = _SchedulerMemory(backend)
    memory.install_pacing()
    for index in range(97):
        memory.leaf(f"prefix-{index}", black=True)
    first, second = memory.leaf("first"), memory.leaf("second")
    for producer, obj in (("producer-a", first), ("producer-b", second)):
        with memory.as_thread(producer):
            memory.shade(obj)
    cursor = memory.scalar(HEAD)
    memory.set_scalar(CURSOR, cursor)
    memory.set_scalar(DEBT, 256)
    production_step = memory.namespace["pcc_gc_tracing_step_cycle"]
    results = []

    def observe_step(budget):
        result = production_step(budget)
        results.append((budget, result))
        assert memory.graph_depth == 0
        assert memory.scalar("pcc_gc_trace_stack_owner") == 0
        assert memory.scalar("pcc_gc_trace_stack_count") == 0
        return result

    memory.namespace["pcc_gc_tracing_step_cycle"] = observe_step
    before = len(memory.accesses)
    # The assist acts when a thread publishes its allocation batch
    # (pcc_gc_cms_note_alloc batches per thread): publish one byte at a time.
    publish = memory.namespace["pcc_gc_cms_publish_alloc"]
    note_alloc = lambda size: publish(size, 1)
    note_alloc(1)
    note_alloc(1)
    assert results == [(4, 0), (4, 0)]
    assert memory.scalar(DEBT) == 258  # Only actual completions may pay debt.
    assert memory.gray == 2 and memory.scalar(REQUEST) == 1
    assert memory.scalar(CURSOR) == cursor and not memory.node_next_reads(before)
    assert not memory.completions
    assert memory.scalar("pcc_gc_tracing_finish_claim_epoch") == 0
    assert memory.scalar("pcc_gc_tracing_finish_commits") == 0
    assert memory.scalar("pcc_gc_cms_sweep_pending") == 0
    assert memory.scalar("pcc_gc_backend4_auto_step_pending") == 0
    with memory.as_thread("producer-a"):
        memory.call("flush")
        assert memory.scalar(REQUEST) == 1
    note_alloc(1)
    assert results[-1] == (4, 1) and memory.scalar(DEBT) == 195
    assert memory.gray == 1 and memory.scalar(REQUEST) == 1
    assert not memory.completions and not memory.node_next_reads(before)
    assert memory.scalar("pcc_gc_cms_sweep_pending") == 0
    with memory.as_thread("producer-b"):
        memory.call("flush")
    note_alloc(100)
    assert results[-1] == (4, 1) and memory.scalar(DEBT) == 231
    assert memory.scalar("pcc_gc_cms_queue_pushes") == 4
    assert memory.scalar("pcc_gc_cms_mutator_assists") == 4
    assert memory.gray == 0 and memory.scalar(REQUEST) == 0
    assert memory.completions == [(17, backend)]
    assert not memory.node_next_reads(before)


@pytest.mark.parametrize("backend", [1, 2])
@pytest.mark.parametrize("pause", [ENABLED, THRESHOLD0])
def test_paused_cms_clears_request_but_explicit_mutator_walks_until_reenabled(backend, pause):
    memory = _SchedulerMemory(backend)
    memory.install_pacing()
    memory.leaf("prefix", black=True)
    first, second = memory.leaf("first"), memory.leaf("second")
    with memory.as_thread("producer"):
        memory.shade(first)
        memory.shade(second)
    memory.set_scalar(pause, 0)
    memory.set_scalar(CURSOR, memory.scalar(HEAD))
    memory.set_scalar(REQUEST, 1)
    sleeps = []

    def sleep(nanoseconds):
        assert memory.scalar(REQUEST) == 0 and memory.gray == 2
        assert not memory.completions
        sleeps.append(nanoseconds)
        return 0

    memory.namespace["pcc_platform_sleep_ns"] = sleep
    # A raw safepoint boundary ends the worker after one actual paused loop.
    memory.namespace["pcc_thread_safepoint_gate"] = lambda: memory.set_scalar(
        "pcc_gc_cms_worker_stop_requested", 1
    )
    with memory.as_thread("cms"):
        assert memory.namespace["pcc_gc_cms_worker_main_py"](0) == 0
    assert sleeps == [1000000]
    assert memory.scalar("pcc_gc_cms_worker_current") == 0
    before = len(memory.accesses)
    assert memory.step(2) == 1
    assert len(memory.node_next_reads(before)) == 2
    assert memory.gray == 1 and memory.load(first, 12, 4) & 56 == BLACK
    cursor = memory.scalar(CURSOR)
    assert cursor == memory.index[second] and memory.scalar(REQUEST) == 0
    memory.set_scalar(ENABLED, 1)
    memory.set_scalar(THRESHOLD0, 700)
    before = len(memory.accesses)
    assert memory.step(2) == 0
    assert memory.gray == 1 and memory.scalar(REQUEST) == 1
    assert memory.scalar(CURSOR) == cursor and not memory.node_next_reads(before)
    assert not memory.completions
    with memory.as_thread("producer"):
        memory.call("flush")
    assert memory.step(2) == 1
    assert memory.gray == 0 and not memory.node_next_reads(before)
    assert memory.completions == [(17, backend)]
