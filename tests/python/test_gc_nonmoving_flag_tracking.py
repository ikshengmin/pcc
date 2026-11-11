"""Actual GC1/2 flag-tracking bodies over bounded raw-memory seams.

Specification: docs/project-intent.md, five-GC lifetime/identity contract.
This checks representation, admission ordering and failure atomicity, not native
thread scheduling. The companion owned-native matrix covers those boundaries.
"""
from __future__ import annotations

import pytest

from pcc.runtime.py.py_abi_constants import (
    PY_FLAG_FINALIZED,
    PY_FLAG_GC_DEALLOCATING,
)
from tests.python.test_gc_backend_configuration_race import NO_RC, Setter
from tests.python.test_gc_mark_color_atomic import _load
from tests.python.test_gc_tracking_snapshot_ownership import RUNTIME, SnapshotRuntime


pytestmark = pytest.mark.xdist_group("gc_nonmoving_flag_tracking")


class NonmovingRuntime(SnapshotRuntime):
    """Execute the real active-ObjectNode enumerator, not a third-list stub."""

    def __init__(self, backend=1):
        super().__init__(backend=backend)
        self.global_set("pcc_gc_nonmoving_count_free", 1)
        self.object_nodes = {}
        self.ns["pcc_gc_granule_is_object_start"] = lambda obj: 1 if obj in self.small else -1
        for name in ("pcc_gc_object_list_head", "pcc_gc_object_list_non_slab_enumerate"):
            _load(RUNTIME / "freestanding_gc_object_nodes.py", name, self.ns)

    def link(self, obj, *, freeing=0, detached=False):
        node = self.reserve(88)
        self.store(node, 0, obj)
        self.store(node, 32, freeing)
        self.object_nodes[obj] = node
        if not detached:
            self.store(node, 16, self.global_get("pcc_gc_object_head"))
            self.global_set("pcc_gc_object_head", node)
        return node

    def graph_object(self, *, tracked=True, flags=64):
        obj = self.object(large=True, flags=flags)
        self.large.remove(obj)
        self.link(obj)
        if not tracked:
            self.ns["py_gc_untrack"](obj)
        return obj


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("no_refcounts", (0, 1))
@pytest.mark.parametrize("probe", (0, 1))
def test_backend_freeze_selects_only_gc0_or_count_free_nonmoving_tracking(
    backend, no_refcounts, probe,
):
    model = Setter(initial=backend, published=backend)
    model.injected = True
    model.words[NO_RC] = no_refcounts
    model.words["pcc_gc_refcount_provenance_probe"] = probe
    expected = int(backend == 0 or (backend in (1, 2) and no_refcounts != 0))

    def enable():
        assert model.depth == 1 and model.words["pcc_gc_backend_frozen"] == 0
        model.events.append("tracking-flag-mode")

    def stage(enabled):
        # Allocation staging is fixed with the collector, under the same
        # lock and before the freeze is published.
        assert model.depth == 1 and model.words["pcc_gc_backend_frozen"] == 0
        model.events.append(("alloc-stage", enabled))

    model.ns["pcc_gc_tracking_enter_flag_mode"] = enable
    model.ns["pcc_gc_alloc_stage_enable"] = stage
    model.ns["pcc_gc_fix_backend"]()
    assert model.events.count("tracking-flag-mode") == expected
    count_free_nonmoving = int(backend in (1, 2) and no_refcounts != 0)
    assert model.words.get("pcc_gc_nonmoving_count_free", 0) == count_free_nonmoving
    # Only the count-free nonmoving tracers stage their registrations.
    assert model.events.count(("alloc-stage", 1)) == count_free_nonmoving
    assert model.words["pcc_gc_backend_frozen"] == 1 and model.depth == 0
    model.ns["pcc_gc_fix_backend"]()
    assert model.events.count("tracking-flag-mode") == expected
    assert model.events.count(("alloc-stage", 1)) == count_free_nonmoving


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("operation", ("py_gc_track", "py_gc_untrack"))
@pytest.mark.parametrize("transition", ("view_ends", "mode_freezes"))
@pytest.mark.parametrize("threads", (0, 1))
def test_raw_admission_precedes_header_access_and_representation_choice(
    backend, operation, transition, threads,
):
    rt = SnapshotRuntime(flag_mode=int(transition == "view_ends"), backend=backend)
    obj = rt.object()
    if operation == "py_gc_track":
        rt.ns["py_gc_untrack"](obj)
    if transition == "view_ends":
        rt.enter()
        assert rt.ns["pcc_gc_tracked_materialize"]() >= 0
    old_flags = rt.load(obj, 12)
    old_count = rt.global_get("py_gc_tracked_count")
    admissions = []

    def admit():
        admissions.append(1)
        # Model the first ID lookup waiting for the stopped owner to release
        # its view, or for the first-allocation freeze to discard old nodes.
        assert rt.load(obj, 12) == old_flags
        assert rt.global_get("py_gc_tracked_count") == old_count
        if transition == "view_ends":
            rt.ns["pcc_gc_tracked_dematerialize"](1)
            rt.leave()
        else:
            rt.ns["pcc_gc_tracking_enter_flag_mode"]()
        return 77

    rt.ns.update(pcc_current_thread_id=admit, pcc_threads_enabled=lambda: threads)
    rt.ns[operation](obj)
    assert admissions == [1]
    assert bool(rt.load(obj, 12) & 2) == (operation == "py_gc_track")
    assert rt.global_get("py_gc_tracked_count") == old_count + (
        1 if operation == "py_gc_track" else -1
    )
    assert rt.global_get("pcc_gc_tracking_materialized") == 0
    assert not rt.index and not rt.world and rt.global_get("pcc_gc_table_lock_depth") == 0
    rt.drain_pool()


@pytest.mark.parametrize("operation", ("py_gc_track", "py_gc_untrack"))
def test_failed_raw_admission_cannot_mutate_tracking(operation):
    rt = SnapshotRuntime(backend=1)
    obj = rt.object()
    before = dict(rt.words)
    rt.ns["pcc_current_thread_id"] = lambda: 0

    def abort():
        raise RuntimeError("unadmitted")

    rt.ns["pcc_platform_abort"] = abort
    with pytest.raises(RuntimeError, match="unadmitted"):
        rt.ns[operation](obj)
    assert rt.words == before


@pytest.mark.parametrize("operation", ("py_gc_track", "py_gc_untrack"))
@pytest.mark.parametrize("case", ("null", "tagged", "gc4"))
def test_unchanged_noop_entries_do_not_register_or_touch_headers(operation, case):
    rt = SnapshotRuntime(backend=4 if case == "gc4" else 1)
    rt.ns["pcc_current_thread_id"] = lambda: pytest.fail("no-op registered")
    rt.ns["is_tagged_int"] = lambda obj: int(obj == 1)
    pointer = {"null": 0, "tagged": 1, "gc4": 123456}[case]
    before = dict(rt.words)
    rt.ns[operation](pointer)
    assert rt.words == before


@pytest.mark.parametrize("backend", (0, 1, 2))
@pytest.mark.parametrize("operation", ("py_gc_track", "py_gc_untrack"))
@pytest.mark.parametrize("already_tracked", (False, True))
def test_first_freeze_while_waiting_for_shard_rechecks_representation(
    backend, operation, already_tracked,
):
    rt = SnapshotRuntime(flag_mode=0, backend=backend)
    obj = rt.object()
    if not already_tracked:
        rt.ns["py_gc_untrack"](obj)
    acquire = rt.ns["_tracked_shard_lock"]
    transitions = []

    def acquire_after_freeze(shard):
        # The caller selected the node path, but the freezer acquired all
        # shards first. Execute the real transition before granting its shard.
        assert rt.global_get("pcc_gc_tracking_flag_mode") == 0
        rt.ns["pcc_gc_tracking_enter_flag_mode"]()
        transitions.append(1)
        acquire(shard)

    rt.ns["_tracked_shard_lock"] = acquire_after_freeze
    rt.ns[operation](obj)
    expected = int(operation == "py_gc_track")
    assert transitions == [1]
    assert bool(rt.load(obj, 12) & 2) == bool(expected)
    assert rt.global_get("py_gc_tracked_count") == expected
    assert not rt.index and rt.global_get("pcc_gc_tracking_materialized") == 0
    rt.drain_pool()


def test_first_freeze_publishes_flag_mode_only_after_old_nodes_are_retired():
    rt = SnapshotRuntime(flag_mode=0)
    objects = rt.seed()
    remove = rt.ns["py_gc_index_remove"]
    publish = rt.ns["atomic_store_i32"]
    observations = []

    def retiring(obj):
        assert rt.ns["_flag_only_tracking"]() == 0
        assert rt.global_get("pcc_gc_tracking_flag_mode") == 0
        assert rt.global_get("pcc_gc_tracking_materialized") == 1
        observations.append(("retire", obj))
        return remove(obj)

    def publishing(pointer, offset, value, order):
        if pointer == rt.address("pcc_gc_tracking_flag_mode") and value == 1:
            assert order == "release"
            assert not rt.index and rt.global_get("py_gc_head") == 0
            assert rt.global_get("pcc_gc_tracking_materialized") == 0
            observations.append(("publish", value))
        publish(pointer, offset, value, order)

    rt.ns.update(py_gc_index_remove=retiring, atomic_store_i32=publishing)
    rt.ns["pcc_gc_tracking_enter_flag_mode"]()
    assert observations[-1] == ("publish", 1)
    assert {obj for event, obj in observations if event == "retire"} == set(objects)
    assert rt.ns["_flag_only_tracking"]() == 1
    assert rt.global_get("py_gc_tracked_count") == 3
    assert all(rt.load(obj, 12) & 2 for obj in objects)
    rt.drain_pool()


@pytest.mark.parametrize("backend", (1, 2))
def test_three_origins_are_complete_deduplicated_and_leave_objectnodes_intact(backend):
    rt = NonmovingRuntime(backend)
    slab = rt.object()
    rt.link(slab)
    raw = rt.graph_object()
    overlap = rt.object(large=True)
    rt.link(overlap)
    static = rt.object(large=True, flags=1)
    partial = rt.graph_object(tracked=False)
    objects = {slab, raw, overlap, static}
    before_nodes = dict(rt.object_nodes)
    before_flags = {obj: rt.load(obj, 12) for obj in (*objects, partial)}
    rt.enter()
    assert rt.ns["pcc_gc_tracked_materialize"]() == 4
    outer_nodes = rt.members()
    assert set(outer_nodes) == objects
    out = rt.ns["py_gc_get_objects"]()
    assert set(rt.lists[out]) == objects and len(rt.lists[out]) == 4
    assert out not in rt.lists[out] and partial not in rt.lists[out]
    assert all(rt.index[obj] == node for obj, node in outer_nodes.items())
    rt.decref(out)
    rt.ns["pcc_gc_tracked_dematerialize"](1)
    rt.leave()
    assert not rt.index and rt.global_get("py_gc_tracked_count") == 4
    assert rt.object_nodes == before_nodes
    assert {obj: rt.load(obj, 12) for obj in before_flags} == before_flags
    rt.drain_pool()


@pytest.mark.parametrize("backend", (1, 2))
def test_non_slab_snapshot_keeps_live_finalized_and_rejects_terminal_owners(backend):
    rt = NonmovingRuntime(backend)
    live = rt.graph_object(flags=PY_FLAG_FINALIZED)
    terminal = rt.graph_object(flags=PY_FLAG_GC_DEALLOCATING)
    zero = rt.graph_object()
    rt.store(zero, 0, 0)
    # Detached pending storage may stay indexed but is never an active-list
    # source. A nonactive list record must not even inspect its header.
    detached = rt.reserve(64)
    inactive = rt.reserve(64)
    rt.link(detached, freeing=-7, detached=True)
    rt.link(inactive, freeing=1)
    rt.freed.update((detached, inactive))
    out = rt.ns["py_gc_get_objects"]()
    assert rt.lists[out] == [live]
    assert rt.load(live, 12) & PY_FLAG_FINALIZED
    assert rt.load(terminal, 12) & PY_FLAG_GC_DEALLOCATING
    assert rt.load(zero, 0) == 0 and not rt.index
    rt.decref(out)
    rt.freed.difference_update((detached, inactive))
    rt.drain_pool()


def test_objectnode_enumerator_requires_stopped_world_and_respects_capacity():
    rt = NonmovingRuntime()
    first = rt.graph_object()
    second = rt.graph_object()
    enumerate_nodes = rt.ns["pcc_gc_object_list_non_slab_enumerate"]
    assert enumerate_nodes(0, 0, 2) == -1
    rt.enter()
    out = rt.reserve(16)
    rt.store(out, 8, 12345)
    assert enumerate_nodes(0, 0, 2) == 2
    assert enumerate_nodes(out, 1, 2) == 2
    assert rt.load(out, 0) in (first, second) and rt.load(out, 8) == 12345
    rt.leave()


@pytest.mark.parametrize("fault", ("negative", "overflow", "fill", "node3", "node4", "index3", "index4"))
def test_added_origin_failures_never_publish_partial_view(fault):
    rt = NonmovingRuntime()
    objects = {rt.object(), rt.object(large=True), rt.graph_object(), rt.graph_object()}
    flags = {obj: rt.load(obj, 12) for obj in objects}
    enumerate_nodes = rt.ns["pcc_gc_object_list_non_slab_enumerate"]

    def faulted(out, capacity, required):
        if fault == "negative":
            return -1
        if fault == "overflow":
            return 1152921504606846975
        return enumerate_nodes(out, capacity, required) + int(fault == "fill" and bool(out))

    rt.ns["pcc_gc_object_list_non_slab_enumerate"] = faulted
    if fault.startswith("node"):
        rt.fail_malloc = 1 + int(fault[-1])
    if fault.startswith("index"):
        rt.fail_insert = int(fault[-1])
    rt.enter()
    assert rt.ns["pcc_gc_tracked_materialize"]() == -1
    assert rt.members() == {} and rt.global_get("pcc_gc_tracking_materialized") == 0
    assert rt.global_get("py_gc_tracked_count") == 4
    assert {obj: rt.load(obj, 12) for obj in objects} == flags
    rt.fail_malloc = rt.fail_insert = 0
    rt.ns["pcc_gc_object_list_non_slab_enumerate"] = enumerate_nodes
    assert rt.ns["pcc_gc_tracked_materialize"]() == 4
    assert set(rt.members()) == objects
    rt.ns["pcc_gc_tracked_dematerialize"](1)
    rt.leave()
    rt.drain_pool()


@pytest.mark.parametrize("backend", (0, 3, 4))
def test_unselected_backends_never_walk_the_added_objectnode_source(backend):
    rt = SnapshotRuntime(flag_mode=int(backend == 0), backend=backend)
    rt.seed()
    rt.ns["pcc_gc_object_list_non_slab_enumerate"] = lambda *args: pytest.fail("wrong backend")
    rt.enter()
    assert rt.ns["pcc_gc_tracked_materialize"]() == (3 if backend == 0 else 0)
    rt.ns["pcc_gc_tracked_dematerialize"](int(backend == 0))
    rt.leave()


def test_materialized_node_pool_still_has_one_bounded_reusable_budget():
    # Flag mode legitimately needs nodes only inside a view. This exercises
    # the old pool invariant through that real path rather than expecting
    # ordinary flag track/untrack to manufacture 4096 permanent nodes.
    rt = SnapshotRuntime(backend=1)
    rt.enter()
    assert rt.ns["pcc_gc_tracked_materialize"]() == 0
    objects = [rt.object() for _ in range(4100)]
    assert len(rt.members()) == 4100
    rt.ns["pcc_gc_tracked_dematerialize"](1)
    assert rt.global_get("pcc_gc_tracked_node_pool_count") == 4096
    assert len(rt.freed) == 4 and not rt.index
    assert rt.global_get("py_gc_tracked_count") == 4100
    for obj in objects:
        rt.ns["py_gc_untrack"](obj)
    before_malloc = rt.malloc_calls
    assert rt.ns["pcc_gc_tracked_materialize"]() == 0
    reused = rt.object()
    assert rt.malloc_calls == before_malloc
    assert rt.global_get("pcc_gc_tracked_node_pool_count") == 4095
    rt.ns["pcc_gc_tracked_dematerialize"](1)
    rt.ns["py_gc_untrack"](reused)
    assert rt.global_get("py_gc_tracked_count") == 0
    assert rt.global_get("pcc_gc_tracked_node_pool_count") == 4096
    rt.leave()
    rt.drain_pool()
