"""Allocation staging rings over unchanged production bodies.

The host seam replaces raw memory, thread-local storage, atomic instructions,
the allocator, the graph lock and the registration the rings feed
(pcc_gc_note_object_allocated_sized); the ring protocol, its drains and the
object-index miss hook are the production bodies. Reading memory nothing
wrote fails. Host threads exercise interleavings but are not a weak-memory
proof, and none of this is native threading or throughput evidence.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
import threading

import pytest

from tests.python.runtime_body_model import (
    declare_module_globals,
    gc_state_cells,
    load_functions,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
SOURCE = RUNTIME / "freestanding_gc_alloc_stage.py"
INDEX_SOURCE = RUNTIME / "freestanding_gc_index_table.py"
PREFIX = "pcc_gc_alloc_stage_"
LOCAL = "pcc_gc_alloc_stage_local_ring"
RINGS = "pcc_gc_alloc_stage_rings"
ENABLED = "pcc_gc_alloc_stage_enabled"
DRAINING = "pcc_gc_alloc_stage_draining"
PUBLISHED = "pcc_gc_alloc_stage_published"
FULL_DRAINS = "pcc_gc_alloc_stage_full_drains"
MARK_ACTIVE = "pcc_gc_mark_active"
EPOCH = "pcc_gc_tracing_cycle_epoch"
CYCLE_REQUESTED = "pcc_gc_cycle_requested"
CAPACITY = 256
GC_STATE_CELLS = gc_state_cells()


class _Stage:
    """Word memory keyed by address, per-thread TLS and a counted graph lock."""

    def __init__(self, *, enabled=1):
        self.thread = threading.local()
        self.mutex = threading.RLock()
        self.graph = threading.RLock()
        self.depth = {}
        self.memory = {}
        self.globals = {}
        self.thread_local = set()
        self.tls = {}
        self.next_address = 0x7000_0000
        self.allocations = []
        self.fail_allocations = 0
        self.events = []
        self.registered = []
        self.index = {}
        self.unregistrable = set()
        self.shaded = []
        self.world_owner = None
        self.world_epoch = 7
        self.nested_lookup = None
        self.namespace = {
            "i64": int,
            "global_addr": self.global_addr,
            "global_load_ptr": self.global_load,
            "global_store_ptr": self.global_store,
            "load_i32": self.load,
            "load_i64": self.load,
            "load_ptr": self.load,
            "store_i32": self.store,
            "store_i64": self.store,
            "store_ptr": self.store,
            "atomic_load_i32": lambda pointer, offset, order: self.load(pointer, offset),
            "atomic_load_i64": lambda pointer, offset, order: self.load(pointer, offset),
            "atomic_store_i64": lambda pointer, offset, value, order: self.store(
                pointer, offset, value
            ),
            "atomic_rmw_i64": self.rmw,
            "malloc": self.malloc,
            "null": lambda: 0,
            "ptr_add": lambda pointer, offset: pointer + offset,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_to_int": lambda value: value,
            "int_to_ptr": lambda value: value,
            "is_tagged_int": lambda value: value & 1,
            "define_global_i32": self.define,
            "define_global_i64": self.define,
            "define_global_ptr_null": lambda name: self.define(name, 0),
            "define_thread_local_ptr_null": self.define_thread_local,
            "pcc_py_gc_minor_graph_lock": lambda: self.lock(0),
            "pcc_py_gc_minor_graph_lock_for": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_thread_graph_lock_depth": lambda: self.depth.get(self.tid, 0),
            "pcc_thread_world_stop_epoch_held": self.world_stop_epoch_held,
            "pcc_gc_note_object_allocated_sized": self.register,
            "pcc_gc_shade_gray_atomic": lambda obj, allow_black: self.shaded.append(
                (obj, allow_black)
            ),
            "pcc_gc_index_py_find_published": lambda cell, obj: self.index.get(obj, 0),
        }
        declare_module_globals(SOURCE, self.namespace)
        for name in (MARK_ACTIVE, EPOCH, CYCLE_REQUESTED):
            self.define(name, GC_STATE_CELLS[name][1])
        self.define("pcc_py_gc_object_published", 0)
        load_functions(SOURCE, None, self.namespace)
        # The production lookup, so its miss hook is what the tests run.
        load_functions(INDEX_SOURCE, {"pcc_gc_object_index_find"}, self.namespace)
        self.set(ENABLED, enabled)

    # -- threads -----------------------------------------------------------
    @property
    def tid(self):
        return getattr(self.thread, "name", "main")

    @contextmanager
    def as_thread(self, name):
        previous = self.tid
        self.thread.name = name
        try:
            yield
        finally:
            self.thread.name = previous

    # -- memory ------------------------------------------------------------
    def define(self, name, initial):
        address = self.next_address
        self.next_address += 64
        self.globals[name] = address
        self.memory[address] = initial

    def define_thread_local(self, name):
        self.thread_local.add(name)

    def global_addr(self, name):
        assert name not in self.thread_local, name
        return self.globals[name]

    def global_load(self, name):
        if name in self.thread_local:
            return self.tls.get((self.tid, name), 0)
        return self.memory[self.globals[name]]

    def global_store(self, name, value):
        if name in self.thread_local:
            self.tls[(self.tid, name)] = value
        else:
            self.memory[self.globals[name]] = value

    def load(self, pointer, offset):
        return self.memory[pointer + offset]

    def store(self, pointer, offset, value):
        self.memory[pointer + offset] = value

    def rmw(self, operation, pointer, offset, value, order):
        assert operation == "add"
        with self.mutex:
            previous = self.memory[pointer + offset]
            self.memory[pointer + offset] = previous + value
        return previous

    def malloc(self, size):
        if self.fail_allocations:
            self.fail_allocations -= 1
            return 0
        with self.mutex:
            address = self.next_address
            self.next_address += (size + 127) & ~63
        self.allocations.append((address, size))
        return address

    def get(self, name):
        return self.memory[self.globals[name]]

    def set(self, name, value):
        self.memory[self.globals[name]] = value

    # -- runtime doubles ----------------------------------------------------
    def lock(self, site):
        self.graph.acquire()
        self.depth[self.tid] = self.depth.get(self.tid, 0) + 1
        self.events.append(("lock", self.tid, site))

    def unlock(self):
        assert self.depth.get(self.tid, 0) > 0, "unlock without the graph lock"
        self.depth[self.tid] -= 1
        self.events.append(("unlock", self.tid))
        self.graph.release()

    def world_stop_epoch_held(self):
        return self.world_epoch if self.world_owner == self.tid else -1

    def register(self, obj, size):
        """The registration the rings feed: it runs under the graph lock."""
        assert self.depth.get(self.tid, 0) > 0, "registration without the graph lock"
        assert obj not in self.index, "registered twice"
        if self.nested_lookup is not None:
            # Registration looks up the index itself (plan, zpage owner).
            self.find(self.nested_lookup)
        self.registered.append((obj, size, self.tid))
        if obj not in self.unregistrable:
            self.index[obj] = obj + 0x1000_0000

    # -- production entries --------------------------------------------------
    def call(self, name, *arguments):
        return self.namespace[PREFIX + name](*arguments)

    def note(self, obj, size=48):
        return self.call("note", obj, size)

    def find(self, obj):
        return self.namespace["pcc_gc_object_index_find"](obj)

    @contextmanager
    def locked(self):
        self.lock(9)
        try:
            yield
        finally:
            self.unlock()

    def ring(self):
        return self.tls.get((self.tid, LOCAL), 0)

    def rings(self):
        result = []
        ring = self.get(RINGS)
        while ring:
            result.append(ring)
            ring = self.memory[ring]
        return result

    def staged(self, ring):
        return self.memory[ring + 128] - self.memory[ring + 64]

    def objects(self):
        return [entry[0] for entry in self.registered]


def _objects(count, base=0x10_0000):
    return [base + index * 64 for index in range(count)]


def test_a_collector_without_staging_registers_every_allocation_itself():
    stage = _Stage(enabled=0)
    assert stage.note(0x10_0000) == 0
    assert stage.rings() == [] and stage.allocations == []
    assert stage.call("pending") == 0
    assert stage.call("drain_stopped") == 0 and stage.call("miss_drain") == 0
    assert stage.events == []


@pytest.mark.parametrize("obj", [0, 0x10_0001], ids=["null", "tagged-int"])
def test_values_without_a_header_are_never_staged(obj):
    stage = _Stage()
    assert stage.note(obj) == 0
    assert stage.rings() == []


def test_staging_takes_no_lock_after_the_ring_exists_and_registers_nothing():
    stage = _Stage()
    stage.set(CYCLE_REQUESTED, 0)
    first, second = _objects(2)
    assert stage.note(first, 40) == 1
    # The first allocation of a thread links its ring under the graph lock.
    assert [event[0] for event in stage.events] == ["lock", "unlock", "lock", "unlock"]
    assert stage.rings() == [stage.ring()] and len(stage.allocations) == 1
    stage.events.clear()
    assert stage.note(second, 56) == 1
    assert stage.events == []
    assert stage.registered == [] and stage.index == {}
    assert stage.staged(stage.ring()) == 2 and stage.call("pending") == 1
    # Staging requests a cycle exactly as registration did.
    assert stage.get(CYCLE_REQUESTED) == 1


def test_a_requested_cycle_is_not_rewritten_by_every_allocation():
    stage = _Stage()
    stage.set(CYCLE_REQUESTED, 1)
    writes = []
    store = stage.namespace["store_i32"]
    address = stage.globals[CYCLE_REQUESTED]

    def recording(pointer, offset, value):
        if pointer + offset == address:
            writes.append(value)
        store(pointer, offset, value)

    stage.namespace["store_i32"] = recording
    for obj in _objects(3):
        assert stage.note(obj) == 1
    assert writes == []


def test_a_drain_registers_in_allocation_order_and_empties_the_ring():
    stage = _Stage()
    objects = _objects(5)
    for index, obj in enumerate(objects):
        assert stage.note(obj, 32 + index) == 1
    with stage.locked():
        assert stage.call("drain_locked") == 5
    assert stage.registered == [(obj, 32 + index, "main") for index, obj in enumerate(objects)]
    assert stage.staged(stage.ring()) == 0 and stage.call("pending") == 0
    assert stage.get(PUBLISHED) == 5 and stage.get(DRAINING) == 0
    with stage.locked():
        assert stage.call("drain_locked") == 0
    assert stage.get(PUBLISHED) == 5


def test_a_locked_index_miss_registers_the_staged_object_and_finds_it():
    stage = _Stage()
    older, wanted, newer = _objects(3)
    for obj in (older, wanted, newer):
        stage.note(obj)
    with stage.locked():
        assert stage.find(wanted) == stage.index[wanted]
    # The whole ring went with it: order is preserved.
    assert stage.objects() == [older, wanted, newer]


def test_another_threads_lock_holder_finds_an_object_staged_before_it_escaped():
    stage = _Stage()
    obj = 0x20_0000
    with stage.as_thread("producer"):
        assert stage.note(obj) == 1
    with stage.as_thread("consumer"), stage.locked():
        assert stage.find(obj) != 0
    assert stage.registered == [(obj, 48, "consumer")]


def test_an_unlocked_miss_stands_and_leaves_the_object_staged():
    stage = _Stage()
    obj = 0x20_0000
    stage.note(obj)
    stage.events.clear()
    assert stage.find(obj) == 0
    assert stage.registered == [] and stage.events == []
    assert stage.call("pending") == 1


def test_a_miss_for_an_address_no_ring_holds_registers_the_ring_and_stays_a_miss():
    stage = _Stage()
    stage.note(0x20_0000)
    with stage.locked():
        assert stage.find(0x90_0000) == 0
        # Nothing is staged any more: the next miss costs no drain.
        registered = len(stage.registered)
        assert stage.find(0x90_0040) == 0
    assert registered == 1 == len(stage.registered)


def test_the_stopped_worlds_owner_registers_its_own_allocations_directly():
    stage = _Stage()
    stage.world_owner = "main"
    assert stage.note(0x20_0000) == 0
    assert stage.rings() == []
    with stage.as_thread("other"):
        # Only the holder: another thread still stages.
        assert stage.note(0x20_0040) == 1


def test_a_stopped_world_begins_with_every_threads_allocations_registered():
    stage = _Stage()
    first, second = _objects(2), _objects(3, base=0x30_0000)
    with stage.as_thread("a"):
        for obj in first:
            stage.note(obj)
    with stage.as_thread("b"):
        for obj in second:
            stage.note(obj)
    stage.events.clear()
    with stage.as_thread("collector"):
        stage.world_owner = "collector"
        assert stage.call("drain_stopped") == 5
    assert stage.events == [("lock", "collector", 0), ("unlock", "collector")]
    assert sorted(stage.objects()) == sorted(first + second)
    assert [obj for obj in stage.objects() if obj in first] == first
    assert [obj for obj in stage.objects() if obj in second] == second
    stage.events.clear()
    # Nothing staged: the next stop takes no lock at all.
    assert stage.call("drain_stopped") == 0 and stage.events == []


def test_the_owner_of_a_stopped_world_may_drain_from_a_miss_without_the_lock():
    stage = _Stage()
    obj = 0x20_0000
    with stage.as_thread("producer"):
        stage.note(obj)
    with stage.as_thread("collector"):
        stage.world_owner = "collector"
        assert stage.find(obj) != 0
    assert stage.registered == [(obj, 48, "collector")]


def test_a_full_ring_registers_its_batch_under_the_allocation_site_and_stages_on():
    stage = _Stage()
    objects = _objects(CAPACITY + 1)
    for obj in objects[:CAPACITY]:
        assert stage.note(obj) == 1
    assert stage.registered == [] and stage.get(FULL_DRAINS) == 0
    stage.events.clear()
    assert stage.note(objects[CAPACITY]) == 1
    assert stage.events == [("lock", "main", 2), ("unlock", "main")]
    assert stage.objects() == objects[:CAPACITY]
    assert stage.staged(stage.ring()) == 1 and stage.get(FULL_DRAINS) == 1
    with stage.locked():
        stage.call("drain_locked")
    assert stage.objects() == objects


def test_ring_cursors_wrap_without_losing_or_repeating_an_entry():
    stage = _Stage()
    objects = _objects(3 * CAPACITY + 17)
    for start in range(0, len(objects), 100):
        for obj in objects[start:start + 100]:
            assert stage.note(obj) == 1
        with stage.locked():
            stage.call("drain_locked")
    assert stage.objects() == objects
    assert len(stage.allocations) == 1


@pytest.mark.parametrize(
    "staged_active, staged_epoch, now_active, now_epoch, shaded",
    [
        (0, 3, 0, 3, False),   # no mark at either end
        (1, 4, 1, 4, False),   # allocated in the active mark: registered black
        (0, 3, 1, 4, True),    # predates the mark that registers it
        (1, 4, 1, 5, True),    # allocated in an earlier mark
        (1, 4, 0, 4, False),   # the mark is over: nothing owes marking work
    ],
    ids=["idle", "same-mark", "before-mark", "earlier-mark", "mark-over"],
)
def test_registration_shades_an_object_that_predates_the_active_mark(
    staged_active, staged_epoch, now_active, now_epoch, shaded
):
    stage = _Stage()
    obj = 0x20_0000
    stage.set(MARK_ACTIVE, staged_active)
    stage.set(EPOCH, staged_epoch)
    stage.note(obj)
    stage.set(MARK_ACTIVE, now_active)
    stage.set(EPOCH, now_epoch)
    with stage.locked():
        stage.call("drain_locked")
    assert stage.objects() == [obj]
    # allow_black: the registration itself colored the object black.
    assert stage.shaded == ([(obj, 1)] if shaded else [])


def test_an_object_registration_could_not_index_gets_no_marking_work():
    stage = _Stage()
    obj = 0x20_0000
    stage.unregistrable.add(obj)
    stage.note(obj)
    stage.set(MARK_ACTIVE, 1)
    stage.set(EPOCH, 9)
    with stage.locked():
        assert stage.call("drain_locked") == 1
    assert stage.shaded == [] and stage.index == {}


def test_registrations_own_index_lookup_does_not_start_a_second_drain():
    stage = _Stage()
    objects = _objects(4)
    for obj in objects:
        stage.note(obj)
    # While the first entry registers, it looks up the last staged object.
    stage.nested_lookup = objects[3]
    with stage.locked():
        assert stage.call("drain_locked") == 4
    assert stage.objects() == objects
    assert stage.get(DRAINING) == 0


def test_a_leaving_thread_registers_its_objects_and_releases_its_ring():
    stage = _Stage()
    obj = 0x20_0000
    with stage.as_thread("worker"):
        stage.note(obj)
        ring = stage.ring()
        stage.call("thread_exit")
        assert stage.ring() == 0
    assert stage.objects() == [obj] and stage.memory[ring + 8] == 0
    with stage.as_thread("next"):
        stage.note(0x20_0040)
        # The released ring serves the next thread; none is ever freed.
        assert stage.ring() == ring
    assert len(stage.allocations) == 1 and stage.rings() == [ring]
    assert stage.memory[ring + 8] == 1


def test_a_thread_that_never_allocated_leaves_without_the_lock():
    stage = _Stage()
    stage.call("thread_exit")
    assert stage.events == []


def test_two_live_threads_never_share_a_ring():
    stage = _Stage()
    with stage.as_thread("a"):
        stage.note(0x20_0000)
        first = stage.ring()
    with stage.as_thread("b"):
        stage.note(0x20_0040)
        second = stage.ring()
    assert first != second and stage.rings() == [second, first]


def test_without_memory_for_a_ring_the_caller_registers_directly():
    stage = _Stage()
    stage.fail_allocations = 1
    assert stage.note(0x20_0000) == 0
    assert stage.rings() == [] and stage.ring() == 0
    # The allocator recovered: staging starts with the next allocation.
    assert stage.note(0x20_0040) == 1


def test_producers_and_a_draining_consumer_register_every_object_once_in_order():
    stage = _Stage()
    per_thread = 4000
    names = ["p0", "p1", "p2"]
    batches = {
        name: _objects(per_thread, base=0x100_0000 * (index + 1))
        for index, name in enumerate(names)
    }
    done = threading.Event()

    def produce(name):
        with stage.as_thread(name):
            for obj in batches[name]:
                assert stage.note(obj) == 1
            stage.call("thread_exit")

    def consume():
        with stage.as_thread("collector"):
            while not done.is_set():
                with stage.locked():
                    stage.call("drain_locked")

    consumer = threading.Thread(target=consume)
    producers = [threading.Thread(target=produce, args=(name,)) for name in names]
    consumer.start()
    for thread in producers:
        thread.start()
    for thread in producers:
        thread.join()
    done.set()
    consumer.join()
    with stage.locked():
        stage.call("drain_locked")
    registered = stage.objects()
    assert len(registered) == per_thread * len(names) == len(set(registered))
    for name in names:
        wanted = set(batches[name])
        assert [obj for obj in registered if obj in wanted] == batches[name]
    assert stage.call("pending") == 0


# -- the four callers ---------------------------------------------------------


def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calls(node, name):
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name) and call.func.id == name
    ]


def test_allocation_stages_after_provenance_and_registers_only_when_refused():
    alloc = _function(RUNTIME / "py_obj.py", "pcc_gc_alloc")
    provenance, = _calls(alloc, "pcc_gc_pointer_register")
    staging, = _calls(alloc, "pcc_gc_alloc_stage_note")
    registration, = _calls(alloc, "pcc_gc_note_object_allocated_sized")
    assert provenance.lineno < staging.lineno < registration.lineno
    guard = next(
        node for node in ast.walk(alloc)
        if isinstance(node, ast.If) and staging in ast.walk(node.test)
    )
    assert ast.unparse(guard.test) == "pcc_gc_alloc_stage_note(obj, size) == 0"
    assert registration in ast.walk(guard) and not guard.orelse


def test_a_stopped_world_drains_once_after_every_other_thread_parked():
    stop = _function(
        RUNTIME / "freestanding_thread_kernel_pthread.py", "pcc_stop_the_world"
    )
    drain, = _calls(stop, "pcc_gc_alloc_stage_drain_stopped")
    statements = stop.body
    position = next(
        index for index, statement in enumerate(statements)
        if drain in ast.walk(statement)
    )
    # The last statement before the outermost stop's return, after the wait
    # for the other threads and the world lock's release.
    assert isinstance(statements[position + 1], ast.Return)
    assert position + 2 == len(statements)
    waits = [call.lineno for call in _calls(stop, "pthread_cond_wait")]
    unlocks = [call.lineno for call in _calls(stop, "pthread_mutex_unlock")]
    assert max(waits) < max(unlocks) < drain.lineno


def test_only_the_count_free_nonmoving_tracers_enable_staging():
    fix = _function(RUNTIME / "py_gc_backend.py", "pcc_gc_fix_backend")
    enable, = _calls(fix, "pcc_gc_alloc_stage_enable")
    assert ast.unparse(enable) == "pcc_gc_alloc_stage_enable(1)"
    guard = next(
        node for node in ast.walk(fix)
        if isinstance(node, ast.If) and enable in ast.walk(node)
        and "pcc_gc_nonmoving_count_free" in ast.unparse(node.body)
    )
    condition = ast.unparse(guard.test)
    # Every tracing collector runs the count-free nonmoving protocol
    # (03cbd1849, 4e2feca72): the guard is that protocol's predicate, which
    # admits GC1-GC4 only when counts are off.
    assert condition == "nonmoving != 0"
    assign = next(
        node for node in ast.walk(fix)
        if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "nonmoving"
    )
    assert ast.unparse(assign.value) == "_nonmoving_count_free_backend(container_backend)"
    predicate = ast.unparse(
        _function(RUNTIME / "py_gc_backend.py", "_nonmoving_count_free_backend")
    )
    assert "pcc_gc_backend4_no_refcounts" in predicate
    assert "backend >= 1 and backend <= 4" in predicate


def test_a_leaving_thread_releases_its_ring_with_its_other_gc_buffers():
    unregister = _function(
        RUNTIME / "py_gc_backend.py", "pcc_gc_thread_unregister_buffers"
    )
    assert len(_calls(unregister, "pcc_gc_alloc_stage_thread_exit")) == 1
