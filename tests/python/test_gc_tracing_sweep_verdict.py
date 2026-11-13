"""Execute the production sweep bodies against a controlled raw-memory boundary.

These host tests isolate ordering; the native pending-verdict fixture exercises
the complete runtime's actual root scan, finalizers, weakrefs and reclamation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from tests.python.runtime_body_model import load_functions


SOURCE = Path(__file__).resolve().parents[2] / (
    "pcc/runtime/py/freestanding_gc_tracing_sweep_collector.py"
)
WHITE, GRAY, BLACK, FINALIZED, CANDIDATE = 8, 16, 32, 4, 1024


class _SweepBoundary:
    def __init__(self, *, backend=2, held_epoch=-1, finish_epoch=-1, count_free=False):
        self.nodes = []
        self.roots = []
        self.events = []
        self.phase_observations = []
        self.callbacks = {}
        self.held_epoch = held_epoch
        self.global_cells = {
            "pcc_gc_backend_selected": {0: backend},
            "pcc_gc_tracing_finish_stop_epoch": {0: finish_epoch},
            "pcc_gc_user_finalizer_runs": {0: 0},
            "pcc_gc_backend4_no_refcounts": {0: int(count_free)},
            # No finish filled a candidate list: the passes walk the nodes.
            "pcc_gc_tracing_candidate_count": {0: 0},
            "pcc_gc_tracing_candidate_commit": {0: -1},
            "pcc_gc_tracing_finish_commits": {0: 0},
        }
        self.forwardings = {}
        namespace = {
            "i64": int,
            "pcc_gc_scalar_sweep_try_enqueue": lambda obj, node: 0,
            "abi_constant": ABI_CONSTANTS.__getitem__,
            "global_addr": self.global_cells.__getitem__,
            "global_load_ptr": lambda name: self.nodes[0] if self.nodes else None,
            "ptr_is_null": lambda ptr: int(ptr is None),
            "load_ptr": self.load_pointer,
            "load_i32": lambda ptr, offset: ptr[offset],
            "load_i64": lambda ptr, offset: ptr[offset],
            "store_i32": lambda ptr, offset, value: ptr.__setitem__(offset, value),
            "store_i64": lambda ptr, offset, value: ptr.__setitem__(offset, value),
            "store_ptr": self.store_pointer,
            "stack_alloc": lambda size: {},
            "null": lambda: None,
            "pcc_gc_object_node_is_active": lambda node: int(node["active"]),
            "pcc_gc_forwarding_index_find": lambda obj: self.forwardings.get(id(obj)),
            "pcc_gc_object_node_is_address_pinned": self.node_is_address_pinned,
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == 10001),
            "pcc_gc_seed_roots": self.seed_roots,
            "pcc_gc_mark_root_gray_if_known": self.mark_root,
            "pcc_gc_drain_all_gray_unlocked": self.drain_roots,
            "py_user_del_dispatch": self.dispatch,
            "pcc_thread_world_stop_epoch_held": lambda: self.held_epoch,
            # The sweep's walk already read the candidate's own pin.
            "pcc_gc_tracing_clear_unpinned": self.clear,
            # With no user code since the weak-ref batches settled every
            # candidate's refs, PASS1 clears without its own weak-ref step.
            "pcc_gc_tracing_clear_referents": self.clear_settled,
            "pcc_gc_tracing_finalize_unpinned": self.reclaim_unpinned,
            "pcc_gc_visit_object_slots": self.visit_slots,
            "py_weakref_invalidate_tracing_candidates": self.invalidate_candidates,
            # This verdict model's weakref boundary has no user callbacks.
            "py_weakref_clear_tracing_candidates": self.invalidate_candidates,
            # Capture the production phase boundaries independently of the
            # collector event oracle. Atomic storage has its own native test.
            "pcc_platform_monotonic_us": lambda: 100,
            "_record_sweep_phase": lambda *args: self.phase_observations.append(args),
        }
        load_functions(SOURCE, {
            "pcc_gc_tracing_recheck_reachability_after_finalizers",
            "pcc_gc_tracing_sweep_unreachable",
        }, namespace)
        self.sweep = namespace["pcc_gc_tracing_sweep_unreachable"]

    def object(self, name, *, candidate=True, pinned=False, cext=False, is_class=False):
        tag = ABI_CONSTANTS["object.type.class"] if is_class else (10001 if cext else 100)
        obj = {8: tag, 12: WHITE | (CANDIDATE if candidate else 0),
               "name": name, "edges": [], "layout": [], "derived": [],
               "pinned": pinned, "weakref_live": True}
        node = {0: obj, 16: None, "active": True}
        if self.nodes:
            self.nodes[-1][16] = node
        self.nodes.append(node)
        return obj

    def load_pointer(self, pointer, offset):
        if isinstance(pointer, tuple):
            assert offset == 0
            obj, group, index = pointer
            return obj[group][index]
        return pointer[offset]

    def store_pointer(self, pointer, offset, value):
        if isinstance(pointer, tuple):
            assert offset == 0
            obj, group, index = pointer
            obj[group][index] = value
        else:
            pointer[offset] = value

    def visit_slots(self, obj, visitor, context):
        for group, role in (("edges", 1), ("layout", 2), ("derived", 3)):
            for index in range(len(obj[group])):
                visitor((obj, group, index), role, context)
        return 1

    def invalidate_candidates(self):
        for node in self.nodes:
            if node["active"] and node[0][12] & CANDIDATE:
                node[0]["weakref_live"] = False
        return 0

    def forward(self, source, target):
        self.forwardings[id(source)] = {0: source, 8: target}
        # As pcc_gc_install_forwarding_unlocked: an edge's source carries 2048.
        source[12] |= 2048

    def seed_roots(self):
        self.events.append("seed")
        for node in self.nodes:
            if node["active"]:
                node[0][12] = (node[0][12] & ~56) | WHITE

    def drain_roots(self):
        self.events.append("drain")
        pending = list(self.roots)
        pending += [node[0] for node in self.nodes
                    if node["active"] and (node[0]["pinned"] or node[0][12] & GRAY)]
        seen = set()
        while pending:
            obj = pending.pop()
            # Production root marking follows the source-to-target edge.
            forwarding = self.forwardings.get(id(obj))
            if forwarding is not None:
                obj = forwarding[8]
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            obj[12] = (obj[12] & ~56) | BLACK
            pending.extend(value for value in obj["edges"] + obj["layout"] if value is not None)
        return len(seen)

    def mark_root(self, obj):
        forwarding = self.forwardings.get(id(obj))
        if forwarding is not None:
            obj = forwarding[8]
        obj[12] = (obj[12] & ~56) | GRAY

    def dispatch(self, obj):
        if obj[8] == ABI_CONSTANTS["object.type.class"]:
            return
        if obj[12] & FINALIZED:
            return
        obj[12] |= FINALIZED
        # Every dispatched model finalizer represents user code, even when
        # its body is empty. Already-finalized objects do not run it again.
        runs = self.global_cells["pcc_gc_user_finalizer_runs"]
        runs[0] += 1
        self.events.append("del:" + obj["name"])
        callback = self.callbacks.get(obj["name"])
        if callback is not None:
            callback(obj)

    def clear(self, obj):
        self.events.append("weakref:" + obj["name"])
        obj["weakref_live"] = False
        self.events.append("clear:" + obj["name"])
        obj["edges"].clear()

    def clear_settled(self, obj):
        # The batch already invalidated this candidate's weak refs.
        assert obj["weakref_live"] is False
        self.events.append("clear:" + obj["name"])
        obj["edges"].clear()

    def reclaim(self, obj):
        if self.global_cells["pcc_gc_backend4_no_refcounts"][0]:
            if obj[8] == ABI_CONSTANTS["object.type.class"]:
                assert not obj["edges"] and not any(value is not None for value in obj["layout"])
                assert not any(node["active"] and node[0][8] != ABI_CONSTANTS["object.type.class"]
                               and node[0][12] & CANDIDATE for node in self.nodes)
                assert not any(node["active"] and any(owner is obj for owner in node[0]["layout"])
                               for node in self.nodes)
            else:
                for owner in obj["layout"]:
                    if owner is not None:
                        assert any(node["active"] and node[0] is owner for node in self.nodes)
        self.events.append("free:" + obj["name"])
        for node in self.nodes:
            if node[0] is obj:
                node["active"] = False

    def node_is_address_pinned(self, node, obj):
        # The sweep already has this object's registration node. Preserve
        # that contract rather than silently accepting an unrelated node.
        assert node["active"] and node[0] is obj
        return int(obj["pinned"])

    def reclaim_unpinned(self, obj):
        # Production now calls its unpinned entry after checking this node.
        # The boundary must fail if a future caller skips that prerequisite.
        assert not obj["pinned"]
        assert any(node["active"] and node[0] is obj for node in self.nodes)
        self.reclaim(obj)


def test_live_pending_candidate_is_rechecked_before_finalizer_or_weakref():
    # The native counterpart must prove these actual edges, not infer them
    # from refcounts: the selected tracing backends use count-free ownership.
    fixture = (SOURCE.parents[3] / "tests/fixtures/native/gc_tracing_pending_verdict.c").read_text()
    graph = fixture.split("static int graph_matches(int count) {", 1)[1].split(
        "static int inject_candidates(int count) {", 1)[0]
    assert "py_instance_get_field(watched[index], 0)" in graph
    assert "peer == watched[mode == 4 ? 1 - index : index]" in graph
    assert "py_list_len(roots[KEEPER]) != (mode == 1 ? 1 : 0)" in graph
    assert "py_list_get(roots[KEEPER], 0)" in graph
    assert "py_list_len(interior) == 1" in graph
    assert "py_list_get(interior, 0)" in graph
    assert "target == watched[0]" in graph
    for owned in ("peer", "target", "interior"):
        assert f"py_decref({owned})" in graph
    admission = fixture.split("static int inject_candidates(int count) {", 1)[1].split(
        "static int weak_matches", 1)[0]
    assert "if (pcc_gc_backend4_no_refcounts != 1) return 159;" in admission
    assert "pcc_gc_object_is_known_no_lock(obj) != 1" in admission
    assert "(header->flags & (PINNED | DEAD)) != 0" in admission
    assert "if (!graph_matches(count)) return 158;" in admission
    assert admission.index("return 130;") < admission.index("graph_matches(count)")
    assert admission.index("graph_matches(count)") < admission.index("| CANDIDATE;")
    assert "header->refcount !=" not in admission
    assert "flags=0x%x refcount=%lld" in admission
    # Keep compound-failure operands visible without changing the predicates
    # or inspecting potentially reclaimed objects. This is source wiring,
    # not execution coverage of the native diagnostic paths.
    diagnostic = fixture.split("static void report_live_verdict_failure(", 1)[1].split(
        "static int run_mode(void) {", 1)[0]
    for field in (
        "status=%d phase=%d mode=%d effective_backend=%lld",
        "finalizers=%d expected_finalizers=%d",
        "weak_callbacks=%d expected_weak_callbacks=0",
        "collected=%lld expected_collected=0",
        "known_first=%d expected_known_first=1",
        "known_second=%d gc_callbacks=%d callback_error=%d",
    ):
        assert field in diagnostic
    assert (
        "status, phase, mode, (long long)pcc_gc_backend(), "
        "finalizers, expected_finalizers, weak_callbacks, (long long)collected, "
        "known_first, known_second, gc_callbacks, callback_error);"
    ) in " ".join(diagnostic.split())
    assert "watched[" not in diagnostic
    assert "py_" not in diagnostic
    assert "pcc_gc_collect" not in diagnostic
    run = fixture.split("static int run_mode(void) {", 1)[1].split("int main(", 1)[0]
    for status, expected in ((143, "0"), (150, "count")):
        assert (
            f"if (finalizers != {expected} || weak_callbacks != 0 || collected != 0 "
            "|| known_first != 1) { "
            f"report_live_verdict_failure({status}, {expected}, collected, known_first, known_second); "
            f"return {status}; "
            "}"
        ) in " ".join(run.split())
    assert fixture.count("report_live_verdict_failure(") == 3
    boundary = _SweepBoundary()
    root = boundary.object("root", candidate=False)
    middle = boundary.object("middle", candidate=False)
    child = boundary.object("child")
    root["edges"] = [middle]
    middle["edges"] = [child]
    child["edges"] = [child]
    boundary.roots = [root]
    assert boundary.sweep(1) == 0
    assert boundary.events == ["seed", "drain"]
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


@pytest.mark.parametrize("budget", (1, 2, 3, 8))
def test_count_free_classes_retire_after_instances_with_bounded_progress(budget):
    boundary = _SweepBoundary(backend=4, count_free=True)
    base = boundary.object("base", is_class=True)
    child_class = boundary.object("class", is_class=True)
    first = boundary.object("first")
    second = boundary.object("second")
    base["edges"] = [first]
    child_class["edges"] = [second]
    child_class["layout"] = [base]
    child_class["derived"] = [child_class, base]
    first["layout"] = [child_class]
    second["layout"] = [child_class]
    first["edges"] = [second]
    second["edges"] = [first]
    counts = []
    for _ in range(10):
        counts.append(boundary.sweep(budget))
        assert 0 <= counts[-1] <= budget
        if not any(node["active"] for node in boundary.nodes):
            break
    assert sum(counts) == 4
    assert not any(node["active"] for node in boundary.nodes)
    for name in ("base", "class"):
        assert boundary.events.count("clear:" + name) == 1
        assert boundary.events.index("free:" + name) > boundary.events.index("free:first")
        assert boundary.events.index("free:" + name) > boundary.events.index("free:second")
    assert boundary.events.count("del:first") == boundary.events.count("del:second") == 1
    # Borrowed MRO data is neither an owning edge nor a reclamation dependency.
    assert child_class["derived"] == [child_class, base]


def test_count_free_empty_class_does_not_wait_for_a_reference_count():
    boundary = _SweepBoundary(backend=4, count_free=True)
    cls = boundary.object("class", is_class=True)
    assert boundary.sweep(1) == 1
    assert not boundary.nodes[0]["active"]
    assert not cls["weakref_live"]


def test_count_free_resurrection_keeps_instance_and_class_graph_intact():
    boundary = _SweepBoundary(backend=4, count_free=True)
    cls = boundary.object("class", is_class=True)
    child = boundary.object("child")
    child["layout"] = [cls]
    cls["edges"] = [child]
    boundary.callbacks["child"] = lambda obj: boundary.roots.append(obj)
    assert boundary.sweep(2) == 0
    assert cls["edges"] == [child] and child["layout"] == [cls]
    assert all(node["active"] for node in boundary.nodes)
    assert not any(event.startswith("clear:") or event.startswith("free:")
                   for event in boundary.events)


def test_finalizer_new_unreachable_instance_preserves_its_class_until_next_disposal_set():
    boundary = _SweepBoundary(backend=4, count_free=True)
    cls = boundary.object("class", is_class=True)
    namespace = boundary.object("namespace")
    old = boundary.object("old")
    cls["edges"] = [namespace]
    old["layout"] = [cls]
    born = []

    def allocate_after_mark(_old):
        new = boundary.object("new", candidate=False)
        new["layout"] = [cls]
        new["edges"] = [new]
        born.append(new)

    boundary.callbacks["old"] = allocate_after_mark
    assert boundary.sweep(10) == 1
    assert boundary.events.count("free:old") == 1
    assert "clear:class" not in boundary.events
    assert "free:namespace" not in boundary.events
    assert born[0]["layout"] == [cls]
    assert cls["edges"] == [namespace]
    assert all(node["active"] for node in boundary.nodes if node[0] is not old)
    # A later completed mark may select the entire remaining component.
    for node in boundary.nodes:
        if node["active"]:
            node[0][12] = (node[0][12] & ~56) | WHITE | CANDIDATE
    assert boundary.sweep(10) == 3
    assert not any(node["active"] for node in boundary.nodes)


def test_counted_class_lifetime_policy_is_preserved():
    boundary = _SweepBoundary(backend=4)
    cls = boundary.object("class", is_class=True)
    assert boundary.sweep(1) == 0
    assert boundary.nodes[0]["active"]
    assert cls[12] & CANDIDATE == 0


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
    assert boundary.events == ["seed", "drain", "weakref:cext",
                               "clear:cext", "free:cext"]
    assert pinned["weakref_live"] and pinned[12] & CANDIDATE == 0


def test_same_stop_epoch_pinned_candidate_skips_every_sweep_pass():
    # Without a reachability refresh, this synthetic candidate reaches the
    # node-aware pin check directly instead of being rescued by root seeding.
    boundary = _SweepBoundary(held_epoch=7, finish_epoch=7)
    pinned = boundary.object("pinned", pinned=True)
    pinned["edges"] = [pinned]
    assert boundary.sweep(1) == 0
    assert boundary.events == []
    assert boundary.nodes[0]["active"] and pinned["weakref_live"]
    assert pinned["edges"] == [pinned]
    assert pinned[12] & (CANDIDATE | FINALIZED) == 0


def test_node_pin_boundary_rejects_a_different_objects_node():
    boundary = _SweepBoundary()
    first = boundary.object("first")
    second = boundary.object("second", pinned=True)
    assert boundary.node_is_address_pinned(boundary.nodes[0], first) == 0
    assert boundary.node_is_address_pinned(boundary.nodes[1], second) == 1
    with pytest.raises(AssertionError):
        boundary.node_is_address_pinned(boundary.nodes[0], second)


def test_unpinned_reclamation_boundary_rejects_a_pinned_object():
    boundary = _SweepBoundary()
    pinned = boundary.object("pinned", pinned=True)
    with pytest.raises(AssertionError):
        boundary.reclaim_unpinned(pinned)
    assert boundary.events == [] and boundary.nodes[0]["active"]


def test_same_stopped_world_without_user_finalizer_needs_no_recheck():
    boundary = _SweepBoundary(held_epoch=7, finish_epoch=7)
    boundary.object("cext", cext=True)
    assert boundary.sweep(1) == 1
    assert boundary.events == ["weakref:cext", "clear:cext", "free:cext"]
    assert boundary.global_cells["pcc_gc_user_finalizer_runs"][0] == 0


def test_changed_stop_epoch_rechecks_live_pending_candidate():
    boundary = _SweepBoundary(held_epoch=8, finish_epoch=7)
    child = boundary.object("child")
    child["edges"] = [child]
    boundary.roots = [child]
    assert boundary.sweep(1) == 0
    assert boundary.events == ["seed", "drain"]
    assert child[12] & (CANDIDATE | FINALIZED) == 0
    assert child["weakref_live"]
    assert child["edges"] == [child]


def test_same_stopped_world_finalizer_still_rechecks_resurrection():
    boundary = _SweepBoundary(held_epoch=7, finish_epoch=7)
    child = boundary.object("child")
    child["edges"] = [child]
    boundary.callbacks["child"] = lambda obj: boundary.roots.append(obj)
    assert boundary.sweep(1) == 0
    assert boundary.events == ["del:child", "seed", "drain"]
    assert boundary.global_cells["pcc_gc_user_finalizer_runs"][0] == 1
    assert child[12] & CANDIDATE == 0
    assert child[12] & FINALIZED
    assert child["weakref_live"]
    assert child["edges"] == [child]


def test_gc4_forwarding_shell_is_excluded_while_unrelated_dead_object_is_swept():
    boundary = _SweepBoundary(backend=4)
    source = boundary.object("source")
    copy = boundary.object("copy", candidate=False)
    dead = boundary.object("dead")
    dead["edges"] = [dead]
    boundary.forward(source, copy)
    # The real owner still stores the old address. Root marking keeps the
    # replacement alive while the physical source remains white.
    boundary.roots = [source]

    assert boundary.sweep(3) == 1
    assert boundary.events == ["seed", "drain", "del:dead", "seed", "drain",
                               "weakref:dead", "clear:dead", "free:dead"]
    assert source[12] & (CANDIDATE | FINALIZED) == 0
    assert source[12] & WHITE
    assert copy[12] & (CANDIDATE | FINALIZED) == 0
    assert copy[12] & BLACK
    assert source["weakref_live"] and copy["weakref_live"]
    assert boundary.forwardings[id(source)][8] is copy
    assert all(node["active"] for node in boundary.nodes if node[0] is not dead)
    assert boundary.sweep(3) == 0
    assert boundary.events.count("del:source") == 0
    assert boundary.events.count("del:copy") == 0
    assert boundary.events.count("del:dead") == 1


def test_gc4_selected_but_unforwarded_candidate_still_finalizes_and_reclaims():
    boundary = _SweepBoundary(backend=4)
    selected = boundary.object("selected")
    selected[12] |= 2048  # Selection alone does not establish a forwarding edge.
    selected["edges"] = [selected]

    assert boundary.sweep(1) == 1
    assert boundary.events == ["seed", "drain", "del:selected", "seed", "drain",
                               "weakref:selected", "clear:selected", "free:selected"]
    assert selected[12] & FINALIZED
    assert not selected["weakref_live"]
    assert selected["edges"] == []
    assert not boundary.nodes[0]["active"]
    assert boundary.sweep(1) == 0
    assert boundary.events.count("del:selected") == 1
    assert boundary.events.count("free:selected") == 1


def _listed_boundary(*, commit=1):
    """A GC1 count-free sweep in its finish's own stopped world, its passes
    driven by the candidate list that finish sealed (commit 1)."""
    boundary = _SweepBoundary(backend=1, held_epoch=7, finish_epoch=7, count_free=True)
    boundary.global_cells.update({
        "pcc_gc_concurrent_sweep_allowed": {0: 0},
        "pcc_gc_tracing_finish_commits": {0: 1},
        # The model's list is never larger than its candidates.
        "pcc_gc_tracing_candidate_capacity": {0: 0},
    })
    boundary.visited = []
    namespace = boundary.sweep.__globals__
    is_active = namespace["pcc_gc_object_node_is_active"]

    def node_is_active(node):
        boundary.visited.append(node[0]["name"])
        return is_active(node)

    namespace["pcc_gc_object_node_is_active"] = node_is_active
    boundary.listed_nodes = {}
    object_head = namespace["global_load_ptr"]
    namespace["global_load_ptr"] = lambda name: (
        boundary.listed_nodes if name == "pcc_gc_tracing_candidate_nodes" else object_head(name)
    )

    def seal(*objects):
        nodes = [node for node in boundary.nodes if any(node[0] is obj for obj in objects)]
        boundary.listed_nodes.clear()
        boundary.listed_nodes.update({index * 8: node for index, node in enumerate(nodes)})
        boundary.global_cells["pcc_gc_tracing_candidate_count"][0] = len(nodes)
        boundary.global_cells["pcc_gc_tracing_candidate_commit"][0] = commit

    boundary.seal = seal
    return boundary


def test_listed_sweep_visits_only_its_finish_candidates_with_the_walks_verdicts():
    """The passes of a sweep whose finish listed its candidates visit that
    list, not every registered object, and reach exactly the walk's
    finalizations, clears and frees."""
    outcomes = []
    live_visits = []
    for listed in (False, True):
        boundary = _listed_boundary()
        live = boundary.object("live", candidate=False)
        first = boundary.object("first")
        second = boundary.object("second")
        first["edges"] = [second]
        second["edges"] = [first]
        boundary.roots = [live]
        if listed:
            boundary.seal(first, second)
        assert boundary.sweep(9223372036854775807) == 2
        outcomes.append([event for event in boundary.events
                         if event.split(":")[0] in ("del", "clear", "free")])
        live_visits.append(boundary.visited.count("live"))
        if listed:
            # The list is retired once its sweep consumed it.
            assert boundary.global_cells["pcc_gc_tracing_candidate_commit"][0] == -1
    # PASS0, PASS1 and PASS2 each walked past the live object; listed, only
    # the post-finalizer reachability recheck (a whole-heap mark) visits it.
    assert live_visits[0] - live_visits[1] == 3
    assert outcomes[0] == outcomes[1]
    assert outcomes[1] == ["del:first", "del:second", "clear:first", "clear:second",
                           "free:first", "free:second"]


def test_listed_sweep_stops_when_a_finalizer_commits_another_finish():
    """A collection nested in a finalizer finishes and sweeps on its own;
    the outer listed sweep must not keep visiting its stale list."""
    boundary = _listed_boundary()
    first = boundary.object("first")
    second = boundary.object("second")
    boundary.seal(first, second)
    commits = boundary.global_cells["pcc_gc_tracing_finish_commits"]
    boundary.callbacks["first"] = lambda obj: commits.__setitem__(0, commits[0] + 1)
    assert boundary.sweep(9223372036854775807) == 0
    assert "del:first" in boundary.events and "del:second" not in boundary.events
    assert not any(event.startswith(("clear:", "free:")) for event in boundary.events)


def test_unsealed_or_stale_list_leaves_the_sweep_to_its_walk():
    for commit in (0, 2):
        boundary = _listed_boundary(commit=commit)
        live = boundary.object("live", candidate=False)
        dead = boundary.object("dead")
        boundary.roots = [live]
        boundary.seal(dead)
        assert boundary.sweep(9223372036854775807) == 1
        assert "live" in boundary.visited and "free:dead" in boundary.events
