"""Execute the production sweep bodies against a controlled raw-memory boundary.

These host tests isolate ordering; the native pending-verdict fixture exercises
the complete runtime's actual root scan, finalizers, weakrefs and reclamation.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / (
    "pcc/runtime/py/freestanding_gc_tracing_sweep_collector.py"
)
WHITE, BLACK, FINALIZED, CANDIDATE = 8, 32, 4, 1024


class _SweepBoundary:
    def __init__(self):
        self.nodes = []
        self.roots = []
        self.events = []
        self.callbacks = {}
        namespace = {
            "i64": int,
            "global_load_ptr": lambda name: self.nodes[0] if self.nodes else None,
            "ptr_is_null": lambda ptr: int(ptr is None),
            "load_ptr": lambda ptr, offset: ptr[offset],
            "load_i32": lambda ptr, offset: ptr[offset],
            "store_i32": lambda ptr, offset, value: ptr.__setitem__(offset, value),
            "pcc_gc_object_node_is_active": lambda node: int(node["active"]),
            "pcc_gc_object_is_address_pinned": lambda obj: int(obj["pinned"]),
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == 10001),
            "pcc_gc_seed_roots": self.seed_roots,
            "pcc_gc_drain_all_gray_unlocked": self.drain_roots,
            "py_user_del_dispatch": self.dispatch,
            "pcc_gc_tracing_clear_unreachable": self.clear,
            "pcc_gc_tracing_finalize_unreachable": self.reclaim,
        }
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        names = {
            "pcc_gc_tracing_recheck_reachability_after_finalizers",
            "pcc_gc_tracing_sweep_unreachable",
        }
        tree.body = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in tree.body} == names
        for node in tree.body:
            node.decorator_list = []
        exec(compile(tree, str(SOURCE), "exec"), namespace)
        self.sweep = namespace["pcc_gc_tracing_sweep_unreachable"]

    def object(self, name, *, candidate=True, pinned=False, cext=False):
        obj = {8: 10001 if cext else 100, 12: WHITE | (CANDIDATE if candidate else 0),
               "name": name, "edges": [], "pinned": pinned, "weakref_live": True}
        node = {0: obj, 16: None, "active": True}
        if self.nodes:
            self.nodes[-1][16] = node
        self.nodes.append(node)
        return obj

    def seed_roots(self):
        self.events.append("seed")
        for node in self.nodes:
            if node["active"]:
                node[0][12] = (node[0][12] & ~56) | WHITE

    def drain_roots(self):
        self.events.append("drain")
        pending = list(self.roots)
        pending += [node[0] for node in self.nodes
                    if node["active"] and node[0]["pinned"]]
        seen = set()
        while pending:
            obj = pending.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            obj[12] = (obj[12] & ~56) | BLACK
            pending.extend(obj["edges"])
        return len(seen)

    def dispatch(self, obj):
        if obj[12] & FINALIZED:
            return
        obj[12] |= FINALIZED
        self.events.append("del:" + obj["name"])
        callback = self.callbacks.get(obj["name"])
        if callback is not None:
            callback(obj)

    def clear(self, obj):
        self.events.append("weakref:" + obj["name"])
        obj["weakref_live"] = False
        self.events.append("clear:" + obj["name"])
        obj["edges"].clear()

    def reclaim(self, obj):
        self.events.append("free:" + obj["name"])
        for node in self.nodes:
            if node[0] is obj:
                node["active"] = False


def test_live_pending_candidate_is_rechecked_before_finalizer_or_weakref():
    boundary = _SweepBoundary()
    root = boundary.object("root", candidate=False)
    middle = boundary.object("middle", candidate=False)
    child = boundary.object("child")
    root["edges"] = [middle]
    middle["edges"] = [child]
    child["edges"] = [child]
    boundary.roots = [root]
    assert boundary.sweep(1) == 0
    assert boundary.events == ["seed", "drain", "seed", "drain"]
    assert child[12] & (CANDIDATE | FINALIZED) == 0
    assert child["weakref_live"]
    assert child["edges"] == [child]


def test_dead_cycle_finalizes_before_clear_and_reclaims_once():
    boundary = _SweepBoundary()
    child = boundary.object("child")
    child["edges"] = [child]
    assert boundary.sweep(1) == 1
    assert boundary.events == ["seed", "drain", "del:child", "seed", "drain",
                               "weakref:child", "clear:child", "free:child"]
    assert boundary.sweep(1) == 0
    assert boundary.events.count("del:child") == 1
    assert boundary.events.count("free:child") == 1


def test_post_finalizer_recheck_preserves_resurrected_graph_and_weakref():
    boundary = _SweepBoundary()
    child = boundary.object("child")
    child["edges"] = [child]
    boundary.callbacks["child"] = lambda obj: boundary.roots.append(obj)
    assert boundary.sweep(1) == 0
    assert boundary.events == ["seed", "drain", "del:child", "seed", "drain"]
    assert child[12] & CANDIDATE == 0
    assert child[12] & FINALIZED
    assert child["weakref_live"]
    assert child["edges"] == [child]


def test_finalizers_keep_isolated_batch_semantics_when_one_resurrects_peer():
    boundary = _SweepBoundary()
    first = boundary.object("first")
    second = boundary.object("second")
    first["edges"] = [second]
    second["edges"] = [first]
    boundary.callbacks["first"] = lambda obj: boundary.roots.append(second)
    assert boundary.sweep(2) == 0
    assert boundary.events == ["seed", "drain", "del:first", "del:second",
                               "seed", "drain"]
    assert first["weakref_live"] and second["weakref_live"]
    assert first[12] & CANDIDATE == second[12] & CANDIDATE == 0


@pytest.mark.parametrize("budget", [0, -1])
def test_nonpositive_budget_does_not_mark_finalize_or_clear(budget):
    boundary = _SweepBoundary()
    child = boundary.object("child")
    original_flags = child[12]
    assert boundary.sweep(budget) == 0
    assert boundary.events == []
    assert child[12] == original_flags


def test_budget_limits_reclamation_without_repeating_finalizers():
    boundary = _SweepBoundary()
    first = boundary.object("first")
    second = boundary.object("second")
    first["edges"] = [first]
    second["edges"] = [second]
    assert boundary.sweep(1) == 1
    assert boundary.events == ["seed", "drain", "del:first", "del:second",
                               "seed", "drain", "weakref:first", "clear:first",
                               "free:first"]
    assert boundary.sweep(1) == 1
    assert boundary.events.count("del:first") == boundary.events.count("del:second") == 1
    assert boundary.events.count("free:first") == boundary.events.count("free:second") == 1


def test_pinned_and_cext_finalizer_exclusions_are_preserved():
    boundary = _SweepBoundary()
    pinned = boundary.object("pinned", pinned=True)
    boundary.object("cext", cext=True)
    assert boundary.sweep(2) == 1
    assert boundary.events == ["seed", "drain", "seed", "drain", "weakref:cext",
                               "clear:cext", "free:cext"]
    assert pinned["weakref_live"] and pinned[12] & CANDIDATE == 0
