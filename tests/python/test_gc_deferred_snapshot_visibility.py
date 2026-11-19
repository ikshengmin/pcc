"""Committed GC1-GC3 deaths are not application-visible tracked objects.

Specification: docs/project-intent.md (identity, finalizers and weak references)
and Python Library Reference, gc.get_objects and gc.get_referrers. The host
models run both actual queue producers at a deterministic publication boundary;
the ordinary native program checks the public lifecycle independently. Explicit
gc.collect() drains any earlier automatic queue: that program is a semantic
control, not proof of an in-flight background batch.
"""
from __future__ import annotations

import os
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py.py_abi_constants import PY_FLAG_GC_TRACKED
from tests.python.process_timeout import run_process_group_timeout
from tests.python.runtime_body_model import load_functions
from tests.python.test_gc_concurrent_sweep import (
    CANDIDATE, FINALIZED, RUNTIME, STATE, _DeferringBoundary, _instances, _listed_deferring_boundary,
    _without_finalizers,
)
from tests.python.test_gc_tracking_snapshot_ownership import SnapshotRuntime


@pytest.mark.parametrize("handoff", ["listed-pass1", "after-finalizer-pass2"])
@pytest.mark.parametrize("outer_materialized", [False, True])
def test_deferred_handoff_excludes_only_committed_objects_from_snapshots(handoff, outer_materialized):
    # Observe the production release-publication boundary, not a racy window
    # in a native worker. Both modeled objects still have positive owners in
    # the snapshot fixture. Only one has actually been handed to the worker.
    if handoff == "listed-pass1":
        boundary = _listed_deferring_boundary()
        dead, = _without_finalizers(_instances(boundary, 1))
        survivor = boundary.object("live", candidate=False)
        _without_finalizers([survivor])
    else:
        boundary = _DeferringBoundary()
        dead, = _without_finalizers(_instances(boundary, 1))
        survivor = boundary.object("resurrected")
        boundary.callbacks["resurrected"] = boundary.roots.append
    cls = boundary.object("layout", is_class=True)
    dead["layout"].append(cls)
    if handoff == "listed-pass1":
        boundary.seal(dead, cls)

    # Give the sweep's modeled objects normal, positively owned tracked
    # storage. Only the public tracking operation can remove membership.
    snapshot = SnapshotRuntime(backend=1)
    cells = {id(obj): snapshot.object(flags=obj[12]) for obj in (dead, survivor)}
    for obj in (dead, survivor):
        obj[12] = snapshot.load(cells[id(obj)], 12)
    if outer_materialized:
        snapshot.enter()
        assert snapshot.ns["pcc_gc_tracked_materialize"]() == 2

    def untrack(obj):
        assert boundary.graph == 0  # no graph -> shard lock nesting
        cell = cells[id(obj)]
        snapshot.store(cell, 12, obj[12])
        snapshot.ns["py_gc_untrack"](cell)
        obj[12] = snapshot.load(cell, 12)

    at_publication = []
    namespace = boundary.sweep.__globals__
    # Use the real tracing-record admission. Dropping a layout's instance
    # from this internal graph before the worker frees it is not a fix.
    for node in boundary.nodes:
        node[32] = 0
    namespace["is_tagged_int"] = lambda obj: 0
    load_functions(RUNTIME / "freestanding_gc_refcount_roots.py",
                   {"pcc_gc_object_node_is_active"}, namespace)
    namespace["py_gc_untrack"] = untrack
    release_store = namespace["atomic_store_i32"]

    def observe_release(cell, offset, value, order):
        if cell is boundary.global_cells[STATE] and value == 1:
            assert boundary.recorded() == [dead]
            at_publication.append((dead[12], survivor[12]))
        release_store(cell, offset, value, order)

    namespace["atomic_store_i32"] = observe_release
    assert boundary.sweep(9223372036854775807) == 1
    assert "free:layout" not in boundary.events and cls[12] & CANDIDATE
    assert namespace["pcc_gc_object_node_is_active"](boundary.nodes[0]) == 1
    (dead_flags, survivor_flags), = at_publication
    assert survivor_flags & FINALIZED
    # Materialize tracked objects with the actual observed flags and run
    # the real get_objects admission (also get_referrers' candidate source).
    dead_cell, survivor_cell = cells[id(dead)], cells[id(survivor)]
    snapshot.store(dead_cell, 12, dead_flags)
    snapshot.store(survivor_cell, 12, survivor_flags)
    assert snapshot.global_get("py_gc_tracked_count") == 1
    # The worker later untracks as part of typed teardown. Membership is
    # already gone; that ordinary operation cannot deduct the count twice.
    snapshot.ns["py_gc_untrack"](dead_cell)
    assert snapshot.global_get("py_gc_tracked_count") == 1
    objects = snapshot.ns["py_gc_get_objects"]()
    assert snapshot.lists[objects] == [survivor_cell]
    assert dead_cell not in snapshot.lists[objects]
    if outer_materialized:
        assert snapshot.world and snapshot.global_get("pcc_gc_table_lock_depth") == 1
        snapshot.ns["pcc_gc_tracked_dematerialize"](1)
        snapshot.leave()


def test_failed_queue_storage_keeps_ordinary_synchronous_retirement():
    boundary = _listed_deferring_boundary()
    boundary.memory = False
    dead, = _without_finalizers(_instances(boundary, 1))
    dead[12] |= PY_FLAG_GC_TRACKED
    boundary.seal(dead)
    boundary.sweep.__globals__["py_gc_untrack"] = lambda obj: pytest.fail("uncommitted handoff")
    # The synchronous reclaim fixture stands in for the ordinary final
    # destruction (including its own untracking), not a queue entry.
    assert boundary.sweep(9223372036854775807) == 1
    assert "free:dead0" in boundary.events
    assert boundary.global_cells[STATE][0] == 0 and not boundary.published


PROGRAM = '''import gc
import threading
import weakref

marker = []
live_holder = [marker]
saved = []
dead_refs = []


class Discarded:
    def __init__(self):
        self.loop = self
        self.marker = marker


class Revived:
    def __init__(self):
        self.loop = self

    def __del__(self):
        saved.append(self)


def create_cycles():
    gone = Discarded()
    dead_refs.append(weakref.ref(gone))
    revived = Revived()


def contains_identity(values, target):
    for value in values:
        if value is target:
            return True
    return False


def main():
    gc.disable()
    creator = threading.Thread(target=create_cycles)
    creator.start()
    creator.join()
    gc.collect()
    assert dead_refs[0]() is None
    assert len(saved) == 1
    objects = gc.get_objects()
    marker_refs = gc.get_referrers(marker)
    revived_refs = gc.get_referrers(saved[0])
    for obj in objects:
        assert not isinstance(obj, Discarded)
    for referrer in marker_refs:
        assert not isinstance(referrer, Discarded)
    if not objects:
        # Threaded GC4 currently has no public tracked-object table.
        assert not marker_refs and not revived_refs
        print("empty tracked table")
    else:
        assert contains_identity(objects, saved[0])
        assert contains_identity(objects, live_holder)
        assert contains_identity(marker_refs, live_holder)
        assert contains_identity(revived_refs, saved)
        print("dead hidden; live and resurrected visible")


main()
'''


@pytest.mark.integration
def test_public_snapshot_after_death_and_resurrection(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "lifecycle.py"
    source.write_text(PROGRAM)
    oracle = run_process_group_timeout([sys.executable, str(source)], timeout=30)
    assert oracle.returncode == 0, (oracle.stdout, oracle.stderr)
    assert oracle.stdout == "dead hidden; live and resurrected visible\n"
    binary = tmp_path / "lifecycle"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=30, env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                                               PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic"),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert not result.stderr, (backend, result.stderr)
        assert result.stdout == ("empty tracked table\n" if backend == 4 else oracle.stdout)
