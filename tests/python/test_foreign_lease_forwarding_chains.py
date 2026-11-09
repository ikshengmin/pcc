"""MODEL: installed forwarding chains, root transfer and lease balance.

The shared loader runs production root/lease bodies and MovingRoots runs the
production forwarding installer and py_incref/py_decref mode gates. Memory,
indexes, graph locks and checked RC tails are finite doubles. This is not
native ABI, concurrent scheduling, allocator, or collector-cycle evidence.
"""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc4_conservative_forwarding_chain import MovingRoots


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class LeaseRoots(MovingRoots):
    def __init__(self, backend=4, *, counted=False):
        super().__init__(backend, counted=counted)
        self.ns.update({
            "MODE_LEASE_SHARD_COUNT": 32,
            "MODE_LEASE_SHARD_STRIDE": 128,
            "pcc_gc_stack_slot_root_is_conservative": lambda slot: 0,
        })
        # Do not inherit the ordinary lease Model's root-resolver double.
        load_functions(RUNTIME / "freestanding_gc_root_operations.py", {
            "_resolve_root_slot_mode", "pcc_gc_resolve_root_slot_unlocked",
            "pcc_gc_resolve_borrowed_root_slot_unlocked",
            "_foreign_lease_acquire", "pcc_gc_foreign_lease_acquire",
            "pcc_gc_borrowed_lease_acquire", "pcc_gc_foreign_lease_release",
            "_mode_lease_shard_index", "_mode_lease_reserve_shard",
            "_mode_lease_release_shard", "pcc_gc_foreign_lease_count_quiescent",
        }, self.ns)

    def slot(self, value):
        slot = self.allocate(8)
        self.store(slot, 0, value)
        return slot

    def acquire(self, slot, borrowed):
        name = "pcc_gc_borrowed_lease_acquire" if borrowed else "pcc_gc_foreign_lease_acquire"
        return self.ns[name](slot)

    def rc_events(self):
        return [event for event in self.events if event[0] in ("incref", "decref")]


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("counted", [False, True])
@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("edges", [0, 1, 2, 4])
def test_installed_chain_leases_only_terminal_and_releases_cleanly(backend, counted, borrowed, edges):
    state = LeaseRoots(backend, counted=counted)
    objects = [state.value()[:2] for _ in range(edges + 1)]
    with state.locked():
        for (source, _), (target, _) in zip(objects, objects[1:]):
            assert state.ns["pcc_gc_install_forwarding_unlocked"](source, target) == 0
    source, terminal = objects[0][0], objects[-1][0]
    slot = state.slot(source)
    state.events.clear()
    before_rc = [state.load(obj, 0) for obj, _ in objects]
    before_edges = {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()}
    def record_store(address, offset, value):
        if address == slot and offset == 0:
            state.events.append(("slot", value))
        state.store(address, offset, value)
    state.ns["store_ptr"] = record_store

    token = state.acquire(slot, borrowed)

    assert token == 1 and state.load(slot, 0) == terminal
    assert [state.load(node, 80) for _, node in objects] == [0] * edges + [1]
    assert state.ns["pcc_gc_foreign_lease_count_quiescent"]() == 1
    transfer = bool(edges and backend == 3 and counted and not borrowed)
    assert state.rc_events() == ([("incref", terminal), ("decref", source)] if transfer else [])
    publication = [event for event in state.events if event[0] in ("incref", "slot", "decref")]
    assert publication == (
        [("incref", terminal), ("slot", terminal), ("decref", source)]
        if transfer else ([("slot", terminal)] if edges else [])
    )
    expected_rc = list(before_rc)
    if transfer:
        expected_rc[0] -= 1
        expected_rc[-1] += 1
    assert [state.load(obj, 0) for obj, _ in objects] == expected_rc
    assert {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()} == before_edges
    assert state.ns["pcc_gc_foreign_lease_release"](slot, token) == 0
    assert [state.load(node, 80) for _, node in objects] == [0] * len(objects)
    assert state.ns["pcc_gc_foreign_lease_count_quiescent"]() == 0
    assert state.rc_events() == ([("incref", terminal), ("decref", source)] if transfer else [])
    assert state.depth == 0


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("counted", [False, True])
@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("invalid", [
    "null", "self", "tagged", "cycle", "missing-terminal", "dead-terminal",
    "retiring-terminal", "exhausted", "tagged-first",
])
def test_invalid_chain_aborts_before_slot_rc_or_lease_publication(backend, counted, borrowed, invalid):
    state = LeaseRoots(backend, counted=counted)
    objects = state.chain()
    source, middle, terminal = [obj for obj, _ in objects]
    edge = state.forwardings[middle]
    if invalid == "cycle":
        # Even a cycle can be assembled through the public installer.
        with state.locked():
            assert state.ns["pcc_gc_install_forwarding_unlocked"](terminal, source) == 0
    elif invalid in ("null", "self", "tagged"):
        state.store(edge, 8, {"null": 0, "self": middle, "tagged": 3}[invalid])
    elif invalid == "tagged-first":
        state.store(state.forwardings[source], 8, 3)
    elif invalid == "missing-terminal":
        del state.index[terminal]
    elif invalid == "dead-terminal":
        state.store(terminal, 12, state.load(terminal, 12) | 524288)
    elif invalid == "retiring-terminal":
        state.store(objects[-1][1], 32, 1)
    else:
        state.store("pcc_gc_forwarding_population", 0, 1)
    slot = state.slot(source)
    state.events.clear()
    before = dict(state.memory)

    with pytest.raises(AssertionError, match="runtime invariant abort"):
        state.acquire(slot, borrowed)

    assert state.memory == before
    assert state.load(slot, 0) == source and state.rc_events() == []
    assert [state.load(node, 80) for _, node in objects] == [0, 0, 0]
    assert state.ns["pcc_gc_foreign_lease_count_quiescent"]() == 0


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("edge_kind", ["missing", "null", "self"])
def test_zero_hop_stale_hint_and_unknown_original_keep_historical_identity(borrowed, known, edge_kind):
    state = LeaseRoots(3, counted=True)
    source, node, _ = state.value()
    if edge_kind != "missing":
        edge = state.allocate(56)
        state.store(edge, 8, source if edge_kind == "self" else 0)
        state.forwardings[source] = edge
        state.store("pcc_gc_forwarding_population", 0, 1)
    state.store(source, 12, state.load(source, 12) | 2048)
    if not known:
        del state.index[source]
    slot = state.slot(source)
    state.events.clear()
    flags = state.load(source, 12)
    name = "pcc_gc_resolve_borrowed_root_slot_unlocked" if borrowed else "pcc_gc_resolve_root_slot_unlocked"
    with state.locked():
        assert state.ns[name](slot, 0) == source
    assert state.load(slot, 0) == source
    assert state.load(source, 12) == (flags & ~2048 if known else flags)
    assert state.rc_events() == [] and state.load(node, 80) == 0


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("borrowed", [False, True])
def test_unknown_original_follows_installed_chain_to_known_terminal(backend, borrowed):
    state = LeaseRoots(backend, counted=True)
    objects = state.chain()
    source, terminal = objects[0][0], objects[-1][0]
    del state.index[source]
    slot = state.slot(source)
    state.events.clear()
    token = state.acquire(slot, borrowed)
    assert token == 1 and state.load(slot, 0) == terminal
    assert [state.load(node, 80) for _, node in objects] == [0, 0, 1]
    assert state.rc_events() == (
        [("incref", terminal), ("decref", source)] if backend == 3 and not borrowed else []
    )
    assert state.ns["pcc_gc_foreign_lease_release"](slot, token) == 0
    assert state.ns["pcc_gc_foreign_lease_count_quiescent"]() == 0


@pytest.mark.parametrize("borrowed", [False, True])
def test_terminal_stale_hint_is_not_mutated_during_chain_publication(borrowed):
    state = LeaseRoots()
    objects = state.chain()
    source, terminal = objects[0][0], objects[-1][0]
    flags = state.load(terminal, 12) | 2048
    state.store(terminal, 12, flags)
    slot = state.slot(source)
    token = state.acquire(slot, borrowed)
    assert token == 1 and state.load(slot, 0) == terminal
    assert state.load(terminal, 12) == flags
    assert state.ns["pcc_gc_foreign_lease_release"](slot, token) == 0
    assert state.load(terminal, 12) == flags & ~2048
    assert state.ns["pcc_gc_foreign_lease_count_quiescent"]() == 0


@pytest.mark.parametrize("backend", [0, 1, 2])
@pytest.mark.parametrize("borrowed", [False, True])
def test_nonmoving_production_lease_still_skips_resolution(backend, borrowed):
    state = LeaseRoots(backend)
    source, node, _ = state.value()
    slot = state.slot(source)
    before = dict(state.memory)
    def unexpected(*args):
        raise AssertionError("nonmoving fast exit must not resolve or lock")
    state.ns["pcc_gc_forwarding_index_find"] = unexpected
    state.ns["pcc_gc_object_index_find"] = unexpected
    state.ns["pcc_py_gc_minor_graph_lock"] = unexpected
    assert state.acquire(slot, borrowed) == 0
    assert state.ns["pcc_gc_foreign_lease_release"](slot, 0) == 0
    assert state.memory == before and state.load(node, 80) == 0
