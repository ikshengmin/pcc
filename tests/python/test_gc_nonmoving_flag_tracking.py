"""The exact view's temporary tracking-node pool keeps a bounded budget."""
from tests.python.test_gc_tracking_snapshot_ownership import SnapshotRuntime


def test_materialized_node_pool_still_has_one_bounded_reusable_budget():
    # Flag mode legitimately needs nodes only inside a view. This exercises
    # the old pool invariant through that real path rather than expecting
    # ordinary flag track/untrack to manufacture 4096 permanent nodes.
    rt = SnapshotRuntime()
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
