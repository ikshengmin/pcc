"""Execute producer admission and real tracking removal over raw model cells.

Specification: docs/project-intent.md, five-GC lifetime equality. These boundary
tests complement the native PASS0/PASS1/PASS2 fixtures; empty slots alone are not
a certificate that arbitrary native callers have run generator finalizers.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_deferred_free import RUNTIME, _load
from tests.python.test_gc_scalar_sweep import FLAGS, ScalarRuntime

GEN, TRACKED, INLINE = 15, 2, 4194304


class GeneratorRuntime(ScalarRuntime):
    def __init__(self, backend=1, *, merged=False):
        super().__init__(backend)
        self.tracked_nodes = {}
        self.tracking_events = []
        self.tracked_lock = 0
        self.merged = merged
        self.define_global("pcc_gc_deferred_node_free_head", 0)
        self.ns.update({
            "pcc_threads_enabled": lambda: 1,
            "pcc_current_thread_id": lambda: 1,
            "gc_backend_current": lambda: self.backend,
            "_flag_only_tracking": lambda: 0,
            "_tracked_table_owned_by_current": lambda: int(self.merged),
            "_tracked_shard_index": lambda obj: 3,
            "_tracked_shard_lock": self.tracking_lock,
            "_tracked_shard_unlock": self.tracking_unlock,
            "py_gc_index_remove": self.tracking_remove,
            "pcc_gc_default_unlink_tracked_node": self.tracking_unlink,
            "_tracked_node_recycle": self.tracking_recycle,
            "atomic_rmw_i32": self.tracking_bits,
            "_TRACKED": TRACKED,
            "_NODE_OBJECT": 0, "_NODE_PREV": 24, "_NODE_NEXT": 32,
        })
        _load(RUNTIME / "freestanding_gc_tracking.py", self.ns, {"py_gc_untrack"})

    def generator(self, *, tracked=True, usable=64):
        obj, node = self.object(usable, GEN, FLAGS | (TRACKED if tracked else 0))
        self.write(obj, 24, 0)
        self.write(obj, 48, 0)
        if tracked:
            gc_node = self.next_address
            self.next_address += 128
            self.memory[gc_node] = {0: obj, 24: 0, 32: 0}
            self.tracked_nodes[obj] = gc_node
        return obj, node

    def tracking_lock(self, index):
        assert index == 3 and self.graph == self.tracked_lock == 0
        self.tracked_lock = 1
        self.tracking_events.append(("lock",))

    def tracking_unlock(self, index):
        assert index == 3 and self.graph == 0 and self.tracked_lock == 1
        self.tracked_lock = 0
        self.tracking_events.append(("unlock",))

    def tracking_remove(self, obj):
        assert self.graph == 0 and (self.merged or self.tracked_lock)
        assert obj in self.noted and self.read(self.nodes[obj], 32) == -self.sc(11)
        assert self.sc(0) == 1  # BUILDING is retained until sweep returns.
        old_thread = self.thread
        self.thread = 2
        assert self.scalar_call("drain", 1, 0, 0) == 0
        self.thread = old_thread
        self.tracking_events.append(("remove", obj))
        return self.tracked_nodes.pop(obj, 0)

    def tracking_unlink(self, node):
        assert self.graph == 0 and (self.merged or self.tracked_lock)
        self.tracking_events.append(("unlink", node))

    def tracking_recycle(self, node):
        assert self.graph == 0 and self.tracked_lock and not self.merged
        self.tracking_events.append(("recycle", node))

    def tracking_bits(self, operation, obj, offset, value, order):
        assert operation == "and" and offset == 12 and value == ~TRACKED
        before = self.read(obj, offset)
        self.write(obj, offset, before & value)
        return before


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("tracked", (False, True))
@pytest.mark.parametrize("merged", (False, True))
def test_complete_generator_claim_untracks_before_seal_without_graph_shard_nesting(backend, tracked, merged):
    rt = GeneratorRuntime(backend, merged=merged)
    obj, node = rt.generator(tracked=tracked)
    gc_node = rt.tracked_nodes.get(obj)
    rt.begin()
    admitted = int(backend in (1, 2))
    assert rt.enqueue((obj, node)) == admitted
    if admitted:
        assert obj not in rt.tracked_nodes and not rt.read(obj, 12) & TRACKED
        assert rt.read(node, 32) == -rt.sc(11) and rt.read(obj, 0) == 731
        assert rt.live_bytes == 0 and rt.noted == [obj]
        assert rt.sc(4) == rt.sc(5) == 0
        assert rt.tracking_events.count(("remove", obj)) == int(tracked)
        if tracked and merged:
            assert rt.scalar("pcc_gc_deferred_node_free_head") == gc_node
            assert rt.read(gc_node, 0) == rt.read(gc_node, 24) == 0
        elif tracked:
            assert ("recycle", gc_node) in rt.tracking_events
    else:
        assert not rt.tracking_events and rt.live_bytes == 64 and not rt.noted
        assert bool(rt.read(obj, 12) & TRACKED) == tracked
    rt.seal()
    assert rt.drain() == admitted
    assert rt.sc(9) == rt.sc(1) == rt.sc(2) == 0


@pytest.mark.parametrize("reason", (
    "frame", "send", "inline", "other-tag", "pin", "lease", "static",
    "gray", "fresh", "identity", "provenance", "raw", "over-size",
    "full-count", "full-bytes", "wrong-node", "already-owned",
))
def test_rejected_generator_preserves_tracking_and_logical_death(reason):
    rt = GeneratorRuntime()
    obj, node = rt.generator(usable=65537 if reason == "over-size" else 64)
    if reason in ("frame", "send"):
        rt.write(obj, 24 if reason == "frame" else 48, 9876)
    if reason in ("inline", "pin", "gray", "fresh"):
        rt.write(obj, 12, rt.read(obj, 12) | {"inline": INLINE, "pin": 64, "gray": 16, "fresh": 16384}[reason])
    if reason == "other-tag": rt.write(obj, 8, 65536)
    if reason == "lease": rt.write(node, 80, 1)
    if reason == "static": rt.static.add(obj)
    if reason == "identity": rt.identities.add(obj)
    if reason == "provenance": rt.provenance.add(obj)
    if reason == "raw": rt.granules.remove(obj)
    if reason == "already-owned": rt.write(node, 32, 1)
    rt.begin()
    if reason == "full-count": rt.write(rt.global_address("pcc_gc_scalar_sweep_count"), 0, 65536)
    if reason == "full-bytes": rt.write(rt.global_address("pcc_gc_scalar_sweep_bytes"), 0, 4194304)
    before = dict(rt.tracked_nodes), rt.live_bytes, dict(rt.memory[obj]), dict(rt.memory[node])
    assert rt.enqueue((obj, node if reason != "wrong-node" else 123456)) == 0
    assert (dict(rt.tracked_nodes), rt.live_bytes, dict(rt.memory[obj]), dict(rt.memory[node])) == before
    assert not rt.tracking_events and not rt.noted and not rt.freed


@pytest.mark.parametrize("change", ("frame", "send", "inline", "tracked", "granule", "tag"))
def test_consumer_rechecks_generator_payload_and_tracking_before_retirement(change):
    rt = GeneratorRuntime()
    obj, node = rt.generator()
    rt.begin()
    assert rt.enqueue((obj, node)) == 1
    rt.seal()
    if change in ("frame", "send"):
        rt.write(obj, 24 if change == "frame" else 48, 9876)
    if change in ("inline", "tracked"):
        rt.write(obj, 12, rt.read(obj, 12) | (INLINE if change == "inline" else TRACKED))
    if change == "granule": rt.retirement_failures.add(obj)
    if change == "tag":
        rt.write(obj, 8, 65536)
        def checked_load(base, offset):
            assert base != obj or offset not in (24, 48), "retagged payload read"
            return rt.read(base, offset)
        rt.ns["load_ptr"] = checked_load
    assert rt.drain() == 0
    assert rt.sc(0) == 4 and rt.sc(1) == rt.sc(9) == 1 and rt.sc(2) == 64
    assert rt.nodes[obj] == node and rt.read(node, 32) == -rt.sc(11)
    assert rt.read(obj, 0) == 731 and not rt.freed


def test_generator_cannot_be_consumed_between_claim_unlock_and_tracking_removal():
    rt = GeneratorRuntime()
    obj, node = rt.generator()
    rt.begin()
    observed = []
    def hook():
        if rt.sc(3) == 1 and rt.read(obj, 12) & TRACKED:
            observed.append(obj)
            assert rt.sc(0) == 1 and obj in rt.tracked_nodes
            rt.thread = 2
            assert rt.scalar_call("drain", 1, 0, 0) == 0
            rt.thread = 1
    rt.unlock_hook = hook
    assert rt.enqueue((obj, node)) == 1
    rt.unlock_hook = None
    assert observed == [obj] and obj not in rt.tracked_nodes
    rt.seal()
    assert rt.drain() == 1 and rt.sc(5) == 1
