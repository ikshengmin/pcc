"""Conservative roots must pin and shade the canonical moving owner.

Run the actual snapshot, word scan, forwarding installation, pin and root-mark
bodies over the existing finite memory/index fixture. This is host semantic
coverage, not native stack capture, page retirement, or throughput evidence.
"""
from __future__ import annotations

from itertools import permutations

import pytest

from tests.python.test_gc3_norc_source_retirement import GC3
from tests.python.runtime_body_model import RUNTIME, load_functions


class MovingRoots(GC3):
    def __init__(self, backend=4, *, counted=False):
        super().__init__(counted=counted)
        self.store("pcc_gc_backend_selected", 0, backend)
        self.ns["pcc_gc_backend"] = lambda: backend
        self.ns["pcc_gc_config_ensure"] = lambda: backend
        self.load_bodies("freestanding_gc_root_operations.py", {
            "pcc_gc_mark_root_gray_if_known", "pcc_gc_object_is_known_no_lock",
        })
        self.load_bodies("freestanding_gc_conservative_roots.py", {
            "pcc_gc_mark_canonical_root_gray_with_node",
        })
        self.ns["pcc_gc_trace_stack_push"] = lambda obj: self.events.append(("root", obj))
        self.ns["pcc_gc_forwarding_target_index_upsert"] = self.upsert_reverse

    # Keep this model's explicit 1=new / 0=existing leaf contract, including
    # no replacement on duplicate insert, aligned with the base fixture.
    # The real forwarding installer still owns publication,
    # reverse links, header flags, population and rollback.
    def _insert_entry(self, table, obj, node, *, fail=False, require_node=False):
        if not obj or obj & 1 or (require_node and not node):
            return -1
        if obj in table:
            return 0
        if fail:
            return -1
        table[obj] = node
        return 1

    def insert_index(self, obj, node):
        return self._insert_entry(self.index, obj, node,
                                  fail=self.fail_index, require_node=True)

    def insert_forward(self, obj, edge):
        return self._insert_entry(self.forwardings, obj, edge, fail=self.fail_forward)

    def insert_reverse(self, obj, edge):
        return self._insert_entry(self.reverse, obj, edge)

    def insert_identity(self, obj, node):
        return self._insert_entry(self.identities, obj, node)

    def upsert_reverse(self, obj, edge):
        if not obj or obj & 1 or not edge:
            return -1
        inserted = int(obj not in self.reverse)
        self.reverse[obj] = edge
        return inserted

    def chain(self, order=(0, 1, 2)):
        objects = [self.value()[:2] for _ in order]
        with self.locked():
            for (source, _), (target, _) in zip(objects, objects[1:]):
                assert self.ns["pcc_gc_install_forwarding_unlocked"](source, target) == 0
        # A source may precede or follow any target in the allocation list.
        nodes = [objects[index][1] for index in order]
        self.store("pcc_gc_object_head", 0, nodes[0])
        for index, node in enumerate(nodes):
            self.store(node, 16, nodes[index + 1] if index + 1 < len(nodes) else 0)
            self.store(node, 40, nodes[index - 1] if index else 0)
        self.events.clear()
        return objects


def test_snapshot_chain_helper_compiles_as_part_of_strict_runtime(tmp_path, monkeypatch):
    """Compile the whole production module, including its real helper call.

    IR/object emission is not native execution or a full-runtime link test.
    """
    import re
    import subprocess
    from unittest.mock import patch

    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    target = "x86_64-unknown-linux-gnu"
    output = tmp_path / "conservative_roots.ll"
    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        compile_python(str(RUNTIME / "freestanding_gc_conservative_roots.py"),
                       str(output), emit_llvm_only=True, python_library=True,
                       backend="self", target_triple=target)
        ir = output.read_text()
        symbol = "pcc_gc_conservative_pin_snapshot_owner_chain"
        assert re.search(r"(?m)^define (?:external )?void @" + symbol
                         + r"\(ptr [^,\n]+, i64 [^)\n]+\)", ir)
        snapshot = re.search(
            r"(?ms)^define [^\n]*@pcc_gc_conservative_pin_snapshot_owners\(.*?^\}", ir
        )
        assert snapshot is not None
        assert re.search(r"call void @" + symbol + r"\(ptr [^,\n]+, i64 ", snapshot.group(0))
        assert emit_owned_object(ir, target)[:4] == b"\x7fELF"


@pytest.mark.parametrize("name, table_name", [
    ("pcc_gc_object_index_insert", "index"),
    ("pcc_gc_forwarding_index_insert", "forwardings"),
    ("pcc_gc_forwarding_target_index_insert", "reverse"),
    ("pcc_gc_identity_index_insert", "identities"),
])
def test_finite_insert_boundary_matches_new_and_duplicate_results(name, table_name):
    state = MovingRoots()
    obj, first, replacement = [state.allocate(32) for _ in range(3)]
    insert = state.ns[name]
    table = getattr(state, table_name)
    assert insert(obj, first) == 1
    assert insert(obj, replacement) == 0
    assert table[obj] == first
    assert insert(0, first) == insert(obj | 1, first) == -1
    assert set(table) == {obj}


def test_finite_reverse_upsert_replaces_an_existing_head():
    state = MovingRoots()
    obj, first, replacement = [state.allocate(32) for _ in range(3)]
    upsert = state.ns["pcc_gc_forwarding_target_index_upsert"]
    assert upsert(obj, first) == 1
    assert upsert(obj, replacement) == 0
    assert state.reverse[obj] == replacement
    assert upsert(obj, 0) == -1
    assert state.reverse[obj] == replacement


@pytest.mark.parametrize("name, table_name, failure_flag", [
    ("pcc_gc_object_index_insert", "index", "fail_index"),
    ("pcc_gc_forwarding_index_insert", "forwardings", "fail_forward"),
])
def test_finite_insert_failure_keeps_the_existing_table(name, table_name, failure_flag):
    state = MovingRoots()
    obj, node = [state.allocate(32) for _ in range(2)]
    insert = state.ns[name]
    table = getattr(state, table_name)
    setattr(state, failure_flag, True)
    assert insert(obj, node) == -1 and not table
    setattr(state, failure_flag, False)
    assert insert(obj, node) == 1
    setattr(state, failure_flag, True)
    assert insert(obj, node + 8) == 0 and table[obj] == node


@pytest.mark.parametrize("fail", [False, True])
def test_actual_forwarding_install_observes_finite_insert_failure(fail):
    state = MovingRoots()
    source, source_node, _ = state.value()
    target, _, _ = state.value()
    state.fail_forward = fail
    before = set(state.raw)
    with state.locked():
        result = state.ns["pcc_gc_install_forwarding_unlocked"](source, target)
    assert result == (-1 if fail else 0)
    assert state.load("pcc_gc_forwarding_population", 0) == int(not fail)
    if fail:
        assert state.forwardings == state.reverse == {}
        assert state.load("pcc_gc_forwarding_head", 0) == 0
        assert not state.load(source, 12) & 2048
        allocated_edges = {ptr for ptr in set(state.raw) - before if state.raw[ptr] == 56}
        assert len(allocated_edges) == 1 and allocated_edges <= state.freed
    else:
        edge = state.forwardings[source]
        assert state.reverse[target] == edge
        assert state.load(edge, 0) == source and state.load(edge, 8) == target
        assert state.load(source, 12) & 2048 and state.load(target, 12) & 8192
    assert state.index[source] == source_node


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("mark", [0, 1])
@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_raw_source_pins_full_chain_independently_of_object_list_order(backend, mark, order):
    state = MovingRoots(backend)
    objects = state.chain(order)
    source, canonical = objects[0][0], objects[-1][0]
    for _, node in objects:
        state.store(node, 56, 4)  # An independent metadata bit must survive.
    state.words = [source + 9]  # Unaligned interior raw pointer, not an exact root.

    state.ns["_scan_thread_roots"](mark)

    assert [state.load(node, 56) for _, node in objects] == [5, 5, 5]
    assert {event for event in state.events if event[0] == "root"} == (
        {("root", canonical)} if mark else set()
    )
    assert bool(state.load(canonical, 12) & 16) == bool(mark)
    # The real forwarding installer must now reject a second move of the
    # canonical owner, even though the raw word only mentioned the old shell.
    replacement, _, _ = state.value()
    with state.locked():
        assert state.ns["pcc_gc_install_forwarding_unlocked"](canonical, replacement) == -2


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0)])
def test_chain_pins_expire_with_the_frozen_raw_root_cut(backend, order):
    state = MovingRoots(backend)
    objects = state.chain(order)
    state.words = [objects[0][0] + 8]
    state.ns["pcc_gc_pin_conservative_thread_roots"]()
    assert [state.load(node, 56) & 1 for _, node in objects] == [1, 1, 1]

    state.words.clear()
    state.ns["pcc_gc_pin_conservative_thread_roots"]()

    assert [state.load(node, 56) & 1 for _, node in objects] == [0, 0, 0]
    assert not [event for event in state.events if event[0] == "root"]
    assert state.depth == 0


@pytest.mark.parametrize("invalid", ["null-target", "self-edge", "cycle", "missing-target"])
def test_invalid_gc4_chain_fails_closed_without_shading_a_partial_target(invalid):
    state = MovingRoots()
    objects = state.chain()
    source, middle, canonical = [obj for obj, _ in objects]
    edge = state.forwardings[middle]
    if invalid == "missing-target":
        del state.index[canonical]
    else:
        target = {"null-target": 0, "self-edge": middle, "cycle": source}[invalid]
        state.store(edge, 8, target)
    state.words = [source + 8]

    with pytest.raises(AssertionError, match="runtime invariant abort"):
        state.ns["_scan_thread_roots"](1)

    assert not [event for event in state.events if event[0] == "root"]


def test_counted_gc4_keeps_its_existing_single_owner_pin_protocol():
    state = MovingRoots(counted=True)
    objects = state.chain()
    state.words = [objects[0][0] + 8]

    state.ns["pcc_gc_pin_conservative_thread_roots"]()

    assert [state.load(node, 56) for _, node in objects] == [1, 0, 0]


@pytest.mark.parametrize("backend", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("counted", [False, True])
def test_no_forwardings_keep_cycle_pins_separate_from_other_node_metadata(backend, counted):
    state = MovingRoots(backend, counted=counted)
    source, node, _ = state.value()
    state.store(node, 56, 4)
    state.words = [source + 8]

    state.ns["pcc_gc_pin_conservative_thread_roots"]()
    assert state.load(node, 56) == (1 if counted else 5)
    state.words.clear()
    state.ns["pcc_gc_pin_conservative_thread_roots"]()

    assert state.load(node, 56) == (0 if counted else 4)
    assert not [event for event in state.events if event[0] == "root"]


def _record_index_probes(state):
    probes = []
    for name, table in (("pcc_gc_object_index_find", state.index),
                        ("pcc_gc_forwarding_index_find", state.forwardings)):
        def find(obj, name=name, table=table):
            assert state.stopped and state.depth > 0
            probes.append((name, obj))
            return table.get(obj, 0)
        state.ns[name] = find
    return probes


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("edges", [0, 1, 3])
@pytest.mark.parametrize("mark", [0, 1])
def test_terminal_owner_reuses_exact_probes_and_matches_generic_mark(backend, edges, mark):
    outcomes = []
    for reuse in (False, True):
        state = MovingRoots(backend)
        objects = [state.value()[:2] for _ in range(edges + 1)]
        with state.locked():
            for (source, _), (target, _) in zip(objects, objects[1:]):
                assert state.ns["pcc_gc_install_forwarding_unlocked"](source, target) == 0
        for _, node in objects:
            state.store(node, 56, 4)
        state.events.clear()
        state.gray_worklist_events.clear()
        # The generic entry is unchanged production source, and supplies the
        # pre-change terminal behavior rather than a reimplemented oracle.
        if not reuse:
            state.ns["pcc_gc_mark_canonical_root_gray_with_node"] = (
                lambda obj, node: state.ns["pcc_gc_mark_root_gray_if_known"](obj)
            )
        probes = _record_index_probes(state)
        with state.locked():
            state.ns["_pin_owner_chain"](objects[0][1], mark)
        expected = len(objects) + (1 if mark and not reuse else 0)
        for name in ("pcc_gc_object_index_find", "pcc_gc_forwarding_index_find"):
            assert sum(kind == name for kind, _ in probes) == expected
        assert [state.load(node, 56) for _, node in objects] == [5] * len(objects)
        assert [event for event in state.events if event[0] == "root"] == (
            [("root", objects[-1][0])] if mark else []
        )
        outcomes.append((
            [state.load(obj, 12) for obj, _ in objects], state.gray,
            list(state.events), list(state.gray_worklist_events),
        ))
    assert outcomes[0] == outcomes[1]


class ExplicitPins(MovingRoots):
    def __init__(self, backend=4, *, counted=False):
        super().__init__(backend, counted=counted)
        self.ns["frame_address"] = lambda: 0x100000
        load_functions(RUNTIME / "freestanding_gc_root_operations.py", {
            "pcc_gc_pin", "pcc_gc_unpin",
        }, self.ns)


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("mark", [0, 1])
@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("pinned", [0, 1, 2])
def test_explicit_pin_retains_only_its_forward_suffix(backend, mark, order, pinned):
    state = ExplicitPins(backend)
    objects = state.chain(order)
    source, canonical = objects[pinned][0], objects[-1][0]
    for _, node in objects:
        state.store(node, 56, 4)
    assert state.words == state.root_cells == state.module_roots == []
    state.ns["pcc_gc_pin"](source)

    with state.locked():
        state.ns["_scan_thread_roots"](mark)

    assert [state.load(node, 56) for _, node in objects] == (
        [4] * pinned + [5] * (3 - pinned)
    )
    assert bool(state.load(canonical, 12) & 16) == bool(mark)
    assert {event for event in state.events if event[0] == "root"} == (
        {("root", canonical)} if mark else set()
    )
    pin_flag = state.ns["PY_FLAG_GC_PINNED"]
    assert [bool(state.load(obj, 12) & pin_flag) for obj, _ in objects] == [
        index == pinned for index in range(3)
    ]

    state.ns["pcc_gc_unpin"](source)
    state.ns["pcc_gc_pin_conservative_thread_roots"]()

    assert [state.load(node, 56) for _, node in objects] == [4, 4, 4]
    assert all(state.load(obj, 12) & pin_flag == 0 for obj, _ in objects)
    assert state.depth == 0


@pytest.mark.parametrize("size", [8, 32, 128])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("all_pinned", [False, True])
@pytest.mark.parametrize("mark", [0, 1])
def test_gc4_explicit_pin_cut_has_linear_index_work(size, reverse, all_pinned, mark):
    state = ExplicitPins()
    order = tuple(reversed(range(size))) if reverse else tuple(range(size))
    objects = state.chain(order)
    roots = objects if all_pinned else objects[:1]
    for obj, _ in roots:
        state.ns["pcc_gc_pin"](obj)
    probes = _record_index_probes(state)

    with state.locked():
        state.ns["_scan_thread_roots"](mark)

    assert [state.load(node, 56) & 1 for _, node in objects] == [1] * size
    assert [event for event in state.events if event[0] == "root"] == (
        [("root", objects[-1][0])] if mark else []
    )
    # Two passes over fresh owners, plus at most two completed-suffix
    # checks per explicit root. This is an operation bound, not timing.
    assert sum(kind == "pcc_gc_object_index_find" for kind, _ in probes) <= (
        2 * size + 2 * len(roots)
    )
    assert sum(kind == "pcc_gc_forwarding_index_find" for kind, _ in probes) == 2 * size


@pytest.mark.parametrize("invalid", ["null-target", "self-edge", "cycle", "missing-target"])
def test_gc4_explicit_pin_validates_before_publishing_cut_bits(invalid):
    state = ExplicitPins()
    objects = state.chain()
    source, middle, canonical = [obj for obj, _ in objects]
    edge = state.forwardings[middle]
    if invalid == "missing-target":
        del state.index[canonical]
    else:
        state.store(edge, 8, {"null-target": 0, "self-edge": middle, "cycle": source}[invalid])
    state.ns["pcc_gc_pin"](source)

    with state.locked(), pytest.raises(AssertionError, match="runtime invariant abort"):
        state.ns["_scan_thread_roots"](1)

    assert [state.load(node, 56) & 1 for _, node in objects] == [0, 0, 0]
    assert not [event for event in state.events if event[0] == "root"]


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0)])
def test_explicit_pin_mark_refreshes_a_prior_pin_only_snapshot(backend, order):
    state = ExplicitPins(backend)
    objects = state.chain(order)
    state.ns["pcc_gc_pin"](objects[0][0])
    state.ns["pcc_gc_pin_conservative_thread_roots"]()
    assert state.load(objects[-1][0], 12) & 16 == 0

    with state.locked():
        state.ns["_scan_thread_roots"](1)

    assert state.load(objects[-1][0], 12) & 16
    assert {event for event in state.events if event[0] == "root"} == {
        ("root", objects[-1][0]),
    }


@pytest.mark.parametrize("backend", [4])
def test_independent_chain_pin_then_mark_still_shades_terminal(backend):
    state = ExplicitPins(backend)
    objects = state.chain()
    with state.locked():
        state.ns["_pin_owner_chain"](objects[0][1], 0)
        assert state.load(objects[-1][0], 12) & 16 == 0
        state.ns["_pin_owner_chain"](objects[0][1], 1)
    assert state.load(objects[-1][0], 12) & 16
    assert [event for event in state.events if event[0] == "root"] == [
        ("root", objects[-1][0]),
    ]


@pytest.mark.parametrize("mark", [0, 1])
def test_gc4_snapshot_chain_does_not_trust_a_listed_node_as_indexed(mark):
    state = ExplicitPins()
    objects = state.chain()
    listed = state.allocate(88)
    state.store(listed, 0, objects[0][0])
    state.store(listed, 56, 5)
    # The listed hint is not an exact index answer. Its set bit must not
    # certify a completed suffix on the real, still-unpinned owner nodes.
    assert all(state.load(node, 56) == 0 for _, node in objects)
    with state.locked():
        state.ns["_pin_snapshot_owner_chain"](listed, mark)
    assert [state.load(node, 56) for _, node in objects] == [1, 1, 1]
    assert state.load(listed, 56) == 5
    assert bool(state.load(objects[-1][0], 12) & 16) == bool(mark)


@pytest.mark.parametrize("backend,counted,edges", [
    (0, False, False), (1, False, False), (2, False, False),
    (4, False, False), (0, True, False), (1, True, False),
    (2, True, False), (3, True, False), (4, True, False),
    # Only the relocating collector can hold a forwarding edge.
    (4, True, True),
])
def test_explicit_pin_does_not_expand_unaffected_scan_paths(backend, counted, edges):
    state = ExplicitPins(backend, counted=counted)
    objects = state.chain() if edges else [state.value()[:2]]
    state.ns["pcc_gc_pin"](objects[0][0])

    def unexpected(*args):
        raise AssertionError("new explicit-pin work on an unaffected scan path")

    state.ns["pcc_gc_object_node_is_address_pinned"] = unexpected
    state.ns["_pin_snapshot_owner_chain"] = unexpected
    with state.locked():
        state.ns["_scan_thread_roots"](0)

    assert [state.load(node, 56) for _, node in objects] == [0] * len(objects)


class ExplicitPinCycle(ExplicitPins):
    """Actual seed/trace/final-remark/verdict/sweep selection on finite memory.

    Scalar finalization and weakref boundaries below do not add roots or
    alter verdicts. Destruction records the selected scalar; this is not
    native allocation reclamation, stack capture, or concurrent execution.
    """
    def __init__(self, backend):
        super().__init__(backend)
        self.cleared = []
        self.destroyed = []
        self.ns.update({
            "pcc_gc_tracing_clear_unpinned": self.cleared.append,
            "pcc_gc_tracing_finalize_unpinned": self.destroy_scalar,
            "py_user_del_dispatch": self.scalar_finalizer,
            "py_weakref_invalidate_tracing_candidates": lambda: 0,
            "pcc_platform_monotonic_us": lambda: 100,
            "pcc_gc_tracing_recheck_reachability_after_finalizers": self.unexpected_recheck,
        })
        for filename, names in (
            ("freestanding_gc_root_operations.py", {
                "pcc_gc_gray_count_store_release",
            }),
            ("freestanding_gc_object_root_seeding.py", {
                "pcc_gc_prepare_object_list_mark", "pcc_gc_gray_current_roots",
                "pcc_gc_gray_registered_roots",
            }),
            ("freestanding_gc_common_mark_cycle.py", {
                "pcc_gc_seed_roots", "pcc_gc_drain_all_gray_unlocked",
                "pcc_gc_drain_all_gray_locked_slice", "pcc_gc_trace_referents",
                "pcc_gc_trace_stack_begin", "pcc_gc_trace_stack_end",
                "pcc_gc_trace_stack_push", "pcc_gc_trace_stack_drain",
                "pcc_gc_finish_tracing_cycle",
            }),
            ("freestanding_gc_incremental_concurrent_scheduler.py", {
                "pcc_gc_complete_claimed_tracing_cycle",
            }),
            ("freestanding_gc_tracing_sweep_collector.py", {
                "pcc_gc_tracing_sweep_unreachable",
            }),
        ):
            load_functions(RUNTIME / filename, names, self.ns)

    def scalar_finalizer(self, obj):
        assert self.load(obj, 8) == self.ns["PY_TYPE_FLOAT"]

    def destroy_scalar(self, obj):
        self.scalar_finalizer(obj)
        self.destroyed.append(obj)
        self.store(obj, 12, (self.load(obj, 12) & ~1024) | 524288)

    def unexpected_recheck(self):
        raise AssertionError("same-stop scalar sweep must use the completed verdict")


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_explicit_source_pin_survives_actual_same_stop_mark_and_sweep(backend, order):
    state = ExplicitPinCycle(backend)
    objects = state.chain(order)
    garbage, _, _ = state.value()
    source, canonical = objects[0][0], objects[-1][0]
    state.ns["pcc_gc_pin"](source)
    assert state.words == state.root_cells == state.module_roots == []
    state.store("pcc_gc_mark_active", 0, 1)
    state.store("pcc_gc_explicit_collect_active", 0, 1)
    state.store("pcc_gc_tracing_cycle_epoch", 0, 1)
    state.store("pcc_gc_tracing_finish_claim_epoch", 0, 1)
    state.store("pcc_gc_tracing_finish_claim_backend", 0, backend)

    with state.locked():
        state.ns["pcc_gc_seed_roots"]()
        state.ns["pcc_gc_drain_all_gray_unlocked"]()
    assert state.ns["pcc_gc_complete_claimed_tracing_cycle"](1, backend) == 1
    assert state.load("pcc_gc_tracing_finish_stop_epoch", 0) == 1
    assert state.load("pcc_gc_mark_active", 0) == 0
    assert state.gray == 0
    assert state.load(canonical, 12) & 1024 == 0
    assert state.load(garbage, 12) & 1024

    assert state.ns["pcc_gc_tracing_sweep_unreachable"](100) == 1

    assert state.cleared == state.destroyed == [garbage]
    assert all(state.known(obj) for obj, _ in objects)
    assert [state.load(node, 56) & 1 for _, node in objects] == [1, 1, 1]
    state.ns["pcc_gc_unpin"](source)
    state.ns["pcc_gc_pin_conservative_thread_roots"]()
    assert [state.load(node, 56) & 1 for _, node in objects] == [0, 0, 0]
    assert state.depth == 0


@pytest.mark.parametrize("case", ["white", "gray", "black", "retired", "dead",
                                   "null-object", "tagged-object", "null-node"])
def test_trusted_terminal_marker_preserves_generic_validity_and_trace_push(case):
    outcomes = []
    for reuse in (False, True):
        state = MovingRoots()
        obj, node, _ = state.value()
        state.store(obj, 12, 8 | 1024)
        state.store("pcc_gc_gray_count", 0, 0)
        if case == "gray":
            state.store(obj, 12, 16 | 1024)
            state.store("pcc_gc_gray_count", 0, 1)
        elif case == "black":
            state.store(obj, 12, 32 | 1024)
        elif case == "retired":
            state.store(node, 32, 1)
        elif case == "dead":
            state.store(obj, 12, 8 | 1024 | 524288)
        elif case == "null-object":
            obj = 0
        elif case == "tagged-object":
            obj = 3
        elif case == "null-node":
            del state.index[obj]
            node = 0
        state.events.clear()
        state.gray_worklist_events.clear()
        probes = _record_index_probes(state)
        with state.locked():
            if reuse:
                state.ns["pcc_gc_mark_canonical_root_gray_with_node"](obj, node)
                assert probes == []
            else:
                state.ns["pcc_gc_mark_root_gray_if_known"](obj)
        accepted = case in ("white", "gray", "black")
        assert [event for event in state.events if event[0] == "root"] == (
            [("root", obj)] if accepted else []
        )
        if accepted:
            assert state.load(obj, 12) == 16 | 1024
        outcomes.append((state.load(obj, 12), state.gray,
                         list(state.events), list(state.gray_worklist_events)))
    assert outcomes[0] == outcomes[1]
