"""Deferred GC1/GC2 sweep over production bodies and raw cells.

Specification: docs/project-intent.md (five-GC lifetime equality; ordinary
finalizer and weakref semantics). An automatic GC1/GC2 sweep runs PASS0
finalizers, weakref invalidation and PASS1 clearing in its stopped world,
then records plain dead objects for the collector thread
(pcc_gc_concurrent_sweep_slice) instead of destroying them while every
mutator waits. These models execute the production admission, eligibility,
PASS2's recorded array, the batched slice, the stopped finish, the
scheduler's mark-start guard and the worker's loop and exit drain. They are
not native threading or throughput evidence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.runtime_body_model import gc_state_cells, load_functions
from tests.python.test_gc_deferred_free import Runtime, _install_worker_loop
from tests.python.test_gc_gray_worklist_scheduler import _SchedulerMemory
from tests.python.test_gc_tracing_sweep_verdict import CANDIDATE, FINALIZED, _SweepBoundary


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
SWEEP = RUNTIME / "freestanding_gc_tracing_sweep_collector.py"
NODES = RUNTIME / "freestanding_gc_object_nodes.py"
STATE = "pcc_gc_concurrent_sweep_state"
OBJECTS = "pcc_gc_concurrent_sweep_objects"
COUNT = "pcc_gc_concurrent_sweep_count"
NEXT = "pcc_gc_concurrent_sweep_next"
REMAINING = "pcc_gc_concurrent_sweep_remaining"
TAG = ABI_CONSTANTS.__getitem__
USER_CLASS = TAG("object.type.user_class_start")
CEXT = USER_CLASS + 900
ELIGIBLE = [TAG("object.type." + name) for name in (
    "int", "float", "str", "bytes", "list", "tuple", "dict", "set", "func",
    "iter", "instance", "valuebox",
)] + [USER_CLASS, USER_CLASS + 7]
KEPT = [TAG("object.type." + name) for name in (
    "class", "weakref", "gen", "coroutine", "task", "thread", "file", "exc",
    "cpy_handle", "virtual_thread",
)] + [CEXT]
GC_STATE = gc_state_cells()


class _Heap:
    """Integer addresses over exact cells; unknown globals fail closed."""

    def __init__(self, backend=1):
        self.memory = {}
        self.globals = {}
        self.next_address = 0x1000
        self.graph = 0
        self.finalized = []
        self.cleared = []
        self.freed_arrays = []
        self.on_handoff = None
        # Another thread queued for the graph lock (the kernel's queue tail).
        self.waiting = False
        self.yields = []
        self.ns = {
            "i64": int,
            "global_addr": self.global_addr,
            "global_load_ptr": lambda name: self.load(self.global_addr(name), 0),
            "global_store_ptr": lambda name, value: self.store(self.global_addr(name), 0, value),
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": lambda pointer, offset, order: self.load(pointer, offset),
            "atomic_store_i32": lambda pointer, offset, value, order: self.store(pointer, offset, value),
            "atomic_rmw_i64": self.rmw,
            "null": lambda: 0,
            "ptr_is_null": lambda pointer: int(pointer == 0),
            "ptr_eq": lambda first, second: int(first == second),
            "is_tagged_int": lambda pointer: int(pointer & 1),
            "abi_constant": TAG,
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == CEXT),
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_py_gc_minor_graph_lock_contended": self.contended,
            "pcc_py_gc_minor_graph_lock_yield": self.yield_lock,
            "pcc_gc_object_node_is_active": lambda node: int(self.load(node, 32) == 0),
            "pcc_gc_tracing_finalize_unpinned": self.finalize,
            "pcc_gc_tracing_clear_referents": self.clear,
            "free": self.free,
        }
        self.set("pcc_gc_backend_selected", backend)
        load_functions(SWEEP, {
            "_concurrent_sweep_admitted",
            "_concurrent_sweep_eligible",
            "_concurrent_sweep_eligible_object",
            "pcc_gc_concurrent_sweep_slice",
            "pcc_gc_concurrent_sweep_finish_stopped",
        }, self.ns)
        load_functions(NODES, {"pcc_gc_object_node_unlink"}, self.ns)
        self.nodes = {}

    # Raw cells -----------------------------------------------------------
    def allocate(self, size=96):
        address = self.next_address
        self.next_address += max(128, size)
        self.memory[address] = {offset: 0 for offset in range(0, size, 4)}
        return address

    def global_addr(self, name):
        if name not in self.globals:
            assert name in GC_STATE, ("unknown global", name)
            self.globals[name] = self.allocate(8)
            self.memory[self.globals[name]][0] = GC_STATE[name][1]
        return self.globals[name]

    def load(self, pointer, offset):
        assert pointer in self.memory, ("read of unmapped or freed address", pointer)
        return self.memory[pointer][offset]

    def store(self, pointer, offset, value):
        assert pointer in self.memory, ("write of unmapped or freed address", pointer)
        assert offset in self.memory[pointer], (pointer, offset)
        self.memory[pointer][offset] = value

    def rmw(self, operation, pointer, offset, value, order):
        assert operation == "add"
        before = self.load(pointer, offset)
        self.store(pointer, offset, before + value)
        return before

    def free(self, pointer):
        if pointer:
            self.freed_arrays.append(pointer)
            del self.memory[pointer]

    def get(self, name):
        return self.load(self.global_addr(name), 0)

    def set(self, name, value):
        self.store(self.global_addr(name), 0, value)

    # The recursive graph lock ----------------------------------------------
    def lock(self):
        self.graph += 1

    def unlock(self):
        assert self.graph > 0
        self.graph -= 1
        if self.graph == 0 and self.on_handoff is not None:
            self.on_handoff()

    def contended(self):
        # Only a holder asks whether to hand the lock over.
        assert self.graph > 0
        return int(self.waiting)

    def yield_lock(self):
        # The kernel's yield: release, let the queued thread in, take it back.
        assert self.graph == 1
        self.yields.append(len(self.finalized))
        self.unlock()
        self.lock()

    # Objects ---------------------------------------------------------------
    def object(self, tag, *, candidate=True):
        """Prepend a listed object, as registration links new nodes."""
        obj = self.allocate(32)
        self.memory[obj][8] = tag
        self.memory[obj][12] = CANDIDATE if candidate else 0
        node = self.allocate(96)
        self.memory[node][0] = obj
        head = self.get("pcc_gc_object_head")
        self.memory[node][16] = head
        if head:
            self.memory[head][40] = node
        self.set("pcc_gc_object_head", node)
        self.nodes[obj] = node
        return obj

    def listed(self):
        found = []
        node = self.get("pcc_gc_object_head")
        while node:
            found.append(self.load(node, 0))
            node = self.load(node, 16)
        return found

    def publish(self, objects, kept_classes=0):
        """What a deferring PASS2 publishes, under the graph lock."""
        array = self.allocate(8 * max(1, len(objects)))
        for index, obj in enumerate(objects):
            self.memory[array][index * 8] = obj
        self.set(OBJECTS, array)
        self.set(COUNT, len(objects))
        self.set(NEXT, 0)
        self.set(REMAINING, kept_classes)
        self.set(STATE, 1)
        return array

    def finalize(self, obj):
        # Destruction runs under the graph lock: its own transactions nest,
        # and nothing parks half destroyed for a stopped collection to see.
        assert self.graph >= 1
        assert self.load(obj, 12) & CANDIDATE
        self.finalized.append(obj)
        node = self.nodes.pop(obj)
        self.lock()
        self.ns["pcc_gc_object_node_unlink"](node)
        self.unlock()
        del self.memory[node]
        del self.memory[obj]

    def clear(self, obj):
        # The stopped sweep may leave a deferred object uncleared: the slice
        # clears it under the graph lock right before its teardown.
        assert self.graph >= 1
        assert obj not in self.finalized
        self.cleared.append(obj)

    def slice(self, budget):
        freed = self.ns["pcc_gc_concurrent_sweep_slice"](budget)
        assert self.graph == 0
        return freed


@pytest.mark.parametrize("tag", ELIGIBLE)
def test_eligible_kinds_free_only_their_own_storage(tag):
    assert _Heap().ns["_concurrent_sweep_eligible"](tag) == 1


@pytest.mark.parametrize("tag", KEPT)
def test_shared_or_foreign_teardown_stays_in_the_stopped_sweep(tag):
    assert _Heap().ns["_concurrent_sweep_eligible"](tag) == 0


def _thread_object(heap, tag, waiter):
    obj = heap.allocate(64)
    heap.memory[obj][8] = tag
    # The parked virtual-thread FIFO head of a Lock or an Event.
    heap.memory[obj][40] = waiter
    return obj


@pytest.mark.parametrize("name", ("thread_lock", "thread_event"))
def test_a_lock_or_event_without_parked_threads_frees_only_its_own_storage(name):
    heap = _Heap()
    eligible = heap.ns["_concurrent_sweep_eligible_object"]
    assert eligible(_thread_object(heap, TAG("object.type." + name), 0), TAG("object.type." + name)) == 1
    # A parked waiter would be resumed or dropped by the teardown.
    waiting = _thread_object(heap, TAG("object.type." + name), heap.allocate(16))
    assert eligible(waiting, TAG("object.type." + name)) == 0


def test_bytearray_is_freed_like_bytes_and_the_listed_kinds_keep_their_answer():
    heap = _Heap()
    eligible = heap.ns["_concurrent_sweep_eligible_object"]
    assert eligible(heap.allocate(32), TAG("object.type.bytearray")) == 1
    for tag in ELIGIBLE:
        assert eligible(heap.allocate(32), tag) == 1
    for tag in KEPT:
        assert eligible(heap.allocate(64), tag) == 0
    for name in ("memoryview", "thread_rlock", "thread_condition", "thread_semaphore"):
        assert eligible(heap.allocate(64), TAG("object.type." + name)) == 0


def test_slice_clears_each_object_right_before_its_teardown():
    heap = _Heap()
    dead = [heap.object(TAG("object.type.list")) for _ in range(40)]
    heap.publish(dead)
    assert heap.slice(256) == 40
    assert heap.cleared == dead and heap.finalized == dead


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("count_free", (0, 1))
@pytest.mark.parametrize("allowed", (0, 1))
@pytest.mark.parametrize("worker", (0, 1, 3))
def test_admission_needs_count_free_gc1_gc2_automatic_collection_and_running_worker(
        backend, count_free, allowed, worker):
    heap = _Heap(backend)
    heap.set("pcc_gc_concurrent_sweep_allowed", allowed)
    heap.set("pcc_gc_cms_worker_started", worker)
    # GC3 sweeps on the collector thread like GC1/GC2 (03cbd1849); only GC4,
    # which relocates, sweeps in its stopped world.
    expected = int(count_free == 1 and backend in (1, 2, 3) and allowed == 1 and worker == 1)
    assert heap.ns["_concurrent_sweep_admitted"](count_free) == expected


def test_slice_frees_the_recorded_objects_and_requests_the_stop_for_kept_classes():
    heap = _Heap()
    live = heap.object(TAG("object.type.list"), candidate=False)
    cls = heap.object(TAG("object.type.class"))
    dead = [heap.object(TAG("object.type.instance")) for _ in range(3)]
    array = heap.publish(dead, kept_classes=1)
    assert heap.slice(256) == 3
    # Only the recorded objects: no walk over the live heap or the class.
    assert heap.finalized == dead
    assert heap.listed() == [cls, live]
    assert heap.get(STATE) == 0 and heap.get(OBJECTS) == 0
    assert heap.get(COUNT) == 0 and heap.get(NEXT) == 0
    assert heap.freed_arrays == [array]
    # The kept class retires in a stopped sweep.
    assert heap.get("pcc_gc_cms_sweep_pending") == 1
    assert heap.get("pcc_gc_backend4_auto_step_pending") == 1
    assert heap.get("pcc_gc_concurrent_sweep_freed") == 3


def test_a_finished_sweep_without_kept_classes_commits_no_candidate():
    heap = _Heap()
    heap.object(TAG("object.type.str"), candidate=False)
    dead = heap.object(TAG("object.type.int"))
    heap.set("pcc_gc_tracing_finish_commits", 41)
    heap.publish([dead])
    assert heap.slice(256) == 1
    assert heap.get(STATE) == 0
    assert heap.get("pcc_gc_cms_sweep_pending") == 0
    assert heap.get("pcc_gc_tracing_no_candidate_commit") == 41


def test_budgeted_slices_resume_at_the_next_recorded_entry():
    heap = _Heap()
    dead = [heap.object(TAG("object.type.float")) for _ in range(5)]
    heap.publish(dead)
    assert heap.slice(2) == 2 and heap.get(STATE) == 1 and heap.get(NEXT) == 2
    assert heap.slice(2) == 2 and heap.get(STATE) == 1 and heap.get(NEXT) == 4
    assert heap.slice(2) == 1 and heap.get(STATE) == 0
    assert heap.finalized == dead and heap.listed() == []


def test_batches_hand_the_graph_lock_back_to_mutators():
    heap = _Heap()
    dead = [heap.object(TAG("object.type.tuple")) for _ in range(100)]
    heap.publish(dead)
    released = []
    heap.on_handoff = lambda: released.append(len(heap.finalized))
    assert heap.slice(256) == 100
    # Every 32 destructions, then once at the end of the slice.
    assert released == [32, 64, 96, 100]


def test_a_queued_mutator_gets_the_graph_lock_after_one_destruction():
    # A mutator that queues for the graph lock mid-batch used to wait for the
    # rest of the batch (up to 31 destructions); measured on the GC3 HTTP
    # gateway, the carrier spent 12% of its time waiting so. The slice hands
    # the lock over after the destruction in progress.
    heap = _Heap()
    dead = [heap.object(TAG("object.type.tuple")) for _ in range(100)]
    heap.publish(dead)
    released = []
    finalize = heap.finalize

    def finalize_while_a_mutator_queues(obj):
        finalize(obj)
        if len(heap.finalized) in (5, 40):
            heap.waiting = True

    def handoff():
        released.append(len(heap.finalized))
        # The queued mutator takes the lock, runs and leaves.
        heap.waiting = False

    heap.ns["pcc_gc_tracing_finalize_unpinned"] = finalize_while_a_mutator_queues
    heap.on_handoff = handoff
    assert heap.slice(256) == 100
    assert released == [5, 32, 40, 64, 96, 100]
    # Only the two queued mutators were yielded to; the other releases are
    # the batch boundaries and the end of the slice.
    assert heap.yields == [5, 40]
    assert heap.finalized == dead and heap.get(STATE) == 0


def test_slice_stops_when_a_stopped_collection_finished_the_sweep():
    heap = _Heap()
    dead = [heap.object(TAG("object.type.set")) for _ in range(40)]
    array = heap.publish(dead, kept_classes=1)

    def stopped_finish():
        # Between batches this thread may park; a stopped collection then
        # frees the rest. It runs on another thread with its own lock state.
        if len(heap.finalized) == 32:
            heap.on_handoff = None
            saved = heap.graph
            heap.graph = 0
            assert heap.ns["pcc_gc_concurrent_sweep_finish_stopped"]() == 8
            heap.graph = saved

    heap.on_handoff = stopped_finish
    assert heap.slice(256) == 32
    assert heap.finalized == dead and heap.get(STATE) == 0
    assert heap.freed_arrays == [array]
    assert heap.get("pcc_gc_cms_sweep_pending") == 1


def test_finish_stopped_frees_everything_and_is_a_no_op_without_a_sweep():
    heap = _Heap()
    assert heap.ns["pcc_gc_concurrent_sweep_finish_stopped"]() == 0
    dead = [heap.object(TAG("object.type.tuple")) for _ in range(5000)]
    heap.publish(dead)
    assert heap.ns["pcc_gc_concurrent_sweep_finish_stopped"]() == 5000
    assert heap.get(STATE) == 0 and heap.finalized == dead


class _DeferringBoundary(_SweepBoundary):
    """The stopped sweep with GC1/GC2 deferral admitted."""

    def __init__(self, *, backend=1, allowed=1, worker=1, memory=True):
        super().__init__(backend=backend, count_free=True)
        self.published = {}
        self.arrays = []
        self.freed_arrays = []
        self.memory = memory
        self.graph = 0
        self.global_cells.update({
            "pcc_gc_concurrent_sweep_allowed": {0: allowed},
            "pcc_gc_cms_worker_started": {0: worker},
            COUNT: {0: -1},
            NEXT: {0: -1},
            REMAINING: {0: -1},
            STATE: {0: 0},
        })
        namespace = self.sweep.__globals__
        namespace.update({
            "abi_constant": TAG,
            "atomic_load_i32": lambda pointer, offset, order: pointer[offset],
            "atomic_store_i32": self.atomic_store,
            "global_store_ptr": self.global_store,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "malloc": self.malloc,
            "free": self.free_array,
            "_finalizer_targets_append": self.append_target,
        })

    @staticmethod
    def append_target(targets, obj):
        # PASS0's target array is the sweep's own scratch, not a deferral
        # array: keep it out of the arrays these tests account for.
        data = targets.get(0)
        if data is None:
            data = {"targets": True}
            targets[0] = data
        count = targets[8]
        data[count * 8] = obj
        targets[8] = count + 1

    def free_array(self, array):
        if not array.get("targets"):
            self.freed_arrays.append(array)

    def malloc(self, size):
        if not self.memory:
            return None
        array = {"size": size}
        self.arrays.append(array)
        return array

    def recorded(self):
        array = self.published[OBJECTS]
        return [array[index * 8] for index in range(self.global_cells[COUNT][0])]

    def lock(self):
        assert self.graph == 0
        self.graph = 1

    def unlock(self):
        assert self.graph == 1
        self.graph = 0

    def atomic_store(self, pointer, offset, value, order):
        if pointer is self.global_cells[STATE]:
            assert self.graph == 1 and order == "release"
            # The array, its count and next index precede the state.
            assert self.published[OBJECTS] is self.arrays[-1]
            assert self.global_cells[NEXT][0] == 0
        pointer[offset] = value

    def global_store(self, name, value):
        assert self.graph == 1
        self.published[name] = value


def _instances(boundary, count):
    objects = []
    for index in range(count):
        obj = boundary.object(f"dead{index}")
        obj[8] = TAG("object.type.instance")
        objects.append(obj)
    return objects


def _freed(boundary):
    return [event for event in boundary.events if event.startswith("free:")]


@pytest.mark.parametrize("backend", (1, 2))
def test_pass2_records_eligible_candidates_finalized_and_cleared(backend):
    boundary = _DeferringBoundary(backend=backend)
    dead = _instances(boundary, 3)
    boundary.object("cext", cext=True)
    cls = boundary.object("cls", is_class=True)
    assert boundary.sweep(9223372036854775807) == 4
    # PASS0 finalizers and PASS1 clearing ran in the stopped world; only
    # the C-extension object was destroyed there. The class waits for its
    # instances.
    assert _freed(boundary) == ["free:cext"]
    for obj in dead:
        assert "del:" + obj["name"] in boundary.events
        assert "clear:" + obj["name"] in boundary.events
        assert obj[12] & CANDIDATE
    assert cls[12] & CANDIDATE
    assert boundary.recorded() == dead
    assert boundary.global_cells[STATE][0] == 1
    assert boundary.global_cells[REMAINING][0] == 1
    # PASS1 cleared four non-classes: the array's capacity bound.
    assert boundary.arrays[-1]["size"] == 4 * 8
    assert boundary.graph == 0 and not boundary.freed_arrays


def _without_finalizers(objects):
    # FINALIZED: no user code is pending (py_user_del_pending answers 0), so
    # no finalizer or weak callback runs and the weak refs stay settled.
    for obj in objects:
        obj[12] |= FINALIZED
    return objects


@pytest.mark.parametrize("backend", (1, 2, 3))
def test_a_settled_sweep_leaves_eligible_candidates_uncleared_to_the_collector_thread(backend):
    boundary = _DeferringBoundary(backend=backend)
    dead = _without_finalizers(_instances(boundary, 3))
    cext = boundary.object("cext", cext=True)
    _without_finalizers([cext])
    cls = boundary.object("cls", is_class=True)
    assert boundary.sweep(9223372036854775807) == 4
    # No finalizer ran, so PASS1 clears only what the stopped world itself
    # destroys: the C-extension object. The instances go to the collector
    # thread uncleared; it clears each before its teardown.
    assert not any(event.startswith("del:") for event in boundary.events)
    for obj in dead:
        assert "clear:" + obj["name"] not in boundary.events
        assert obj[12] & CANDIDATE
    assert boundary.events.count("clear:cext") == 1
    assert _freed(boundary) == ["free:cext"]
    assert boundary.recorded() == dead
    assert cls[12] & CANDIDATE and boundary.global_cells[REMAINING][0] == 1


def test_a_settled_sweep_without_an_array_clears_before_destroying():
    boundary = _DeferringBoundary(memory=False)
    dead = _without_finalizers(_instances(boundary, 2))
    assert boundary.sweep(9223372036854775807) == 2
    # PASS1 left them to a collector thread that cannot take them: each is
    # cleared right before its teardown in the stopped world.
    for obj in dead:
        name = obj["name"]
        assert boundary.events.index("clear:" + name) < boundary.events.index("free:" + name)
    assert boundary.global_cells[STATE][0] == 0 and not boundary.published


def test_a_budgeted_sweep_clears_in_the_stopped_world():
    boundary = _DeferringBoundary()
    dead = _without_finalizers(_instances(boundary, 2))
    boundary.sweep(1 << 40)
    # Only an unbounded sweep leaves candidates uncleared.
    for obj in dead:
        assert "clear:" + obj["name"] in boundary.events


@pytest.mark.parametrize("allowed, worker", ((0, 1), (1, 0), (1, 3)))
def test_pass2_without_admission_destroys_in_the_stopped_world(allowed, worker):
    boundary = _DeferringBoundary(allowed=allowed, worker=worker)
    dead = _instances(boundary, 2)
    assert boundary.sweep(9223372036854775807) == 2
    assert _freed(boundary) == ["free:" + obj["name"] for obj in dead]
    assert boundary.global_cells[STATE][0] == 0 and not boundary.published
    assert not boundary.arrays


def test_pass2_without_an_array_destroys_in_the_stopped_world():
    boundary = _DeferringBoundary(memory=False)
    dead = _instances(boundary, 2)
    assert boundary.sweep(9223372036854775807) == 2
    assert _freed(boundary) == ["free:" + obj["name"] for obj in dead]
    assert boundary.global_cells[STATE][0] == 0 and not boundary.published


def test_pass2_with_an_incomplete_clear_defers_nothing():
    # A budget-limited PASS1 leaves uncleared candidates that still own
    # their referents: none may reach the collector thread.
    boundary = _DeferringBoundary()
    dead = _instances(boundary, 3)
    assert boundary.sweep(2) == 2
    assert _freed(boundary) == ["free:" + obj["name"] for obj in dead[:2]]
    assert boundary.global_cells[STATE][0] == 0 and not boundary.published
    assert not boundary.arrays


class _Begin(Exception):
    pass


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("sweeping", (0, 1))
def test_no_mark_begins_while_the_collector_thread_still_sweeps(backend, sweeping):
    memory = _SchedulerMemory(backend)
    memory.set_scalar("pcc_gc_mark_active", 0)
    memory.set_scalar("pcc_gc_cycle_requested", 1)
    memory.global_addr(STATE)
    memory.set_scalar(STATE, sweeping)

    def begin():
        raise _Begin

    memory.namespace["pcc_gc_begin_mark_cycle"] = begin
    if sweeping:
        assert memory.step(4) == 0
        assert memory.scalar("pcc_gc_cycle_requested") == 1
        assert memory.scalar("pcc_gc_mark_active") == 0
    else:
        with pytest.raises(_Begin):
            memory.namespace["pcc_gc_tracing_step_cycle"](4)


def test_worker_exit_finishes_the_deferred_sweep_before_its_final_drains():
    rt = Runtime()
    _install_worker_loop(rt)
    rt.define_global(STATE, 1)
    calls = []

    def slice_(budget):
        calls.append(("slice", budget))
        if len(calls) == 3:
            rt.write(rt.global_address(STATE), 0, 0)
        return 1

    def scalar_drain(worker, closing, token):
        calls.append(("scalar", closing))
        return 0

    rt.ns["pcc_gc_concurrent_sweep_slice"] = slice_
    rt.ns["pcc_gc_scalar_sweep_drain"] = scalar_drain
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 3)
    rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, 1)
    rt.thread = 2
    assert rt.ns["pcc_gc_cms_worker_main_py"](0) == 0
    assert calls == [("slice", 4096)] * 3 + [("scalar", 1)]
    assert rt.scalar(STATE) == 0


def test_worker_slices_the_deferred_sweep_before_any_mark_step():
    rt = Runtime()
    _install_worker_loop(rt)
    rt.define_global(STATE, 1)
    rt.write(rt.global_address("pcc_gc_mark_active"), 0, 1)
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 1)
    calls = []

    def slice_(budget):
        calls.append(("slice", budget))
        if len(calls) == 2:
            rt.write(rt.global_address(STATE), 0, 0)
        return 0

    def step(budget):
        assert rt.scalar(STATE) == 0, "a mark step ran while the sweep was pending"
        calls.append(("step", budget))
        rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, 1)
        return 0

    rt.ns.update({
        "pcc_gc_concurrent_sweep_slice": slice_,
        "pcc_gc_tracing_step_cycle": step,
        # Gray work remains: the worker keeps tracing rather than handing off.
        "pcc_gc_gray_count_load_acquire": lambda: 1,
        "pcc_platform_sleep_ns": lambda ns: calls.append(("sleep", ns)),
        "pcc_thread_safepoint_gate": lambda: None,
    })
    rt.thread = 2
    assert rt.ns["pcc_gc_cms_worker_main_py"](0) == 0
    # A pending sweep is sliced every loop without sleeping; marking resumes
    # only once it is done.
    assert calls == [("slice", 256), ("slice", 256), ("step", 512)]
