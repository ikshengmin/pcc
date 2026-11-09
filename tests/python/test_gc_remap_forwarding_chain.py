"""Real forwarding installs and remap bodies, over finite memory/index doubles.

This is host semantic coverage, not native ABI, concurrency or performance
evidence. The GC3 fixture loads production bodies with load_functions.
"""
from __future__ import annotations

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import GC3, PY


def _chain(backend, counted, edges, installer="public"):
    state = GC3(counted=counted)
    state.store("pcc_gc_backend_selected", 0, backend)
    state.ns["pcc_gc_config_ensure"] = lambda: backend
    state.ns["pcc_gc_backend"] = lambda: backend
    load_functions(PY / "freestanding_gc_relocation_remap.py",
                   {"pcc_gc_backend4_remap_heal_slot", "pcc_gc_backend4_remap_slot"},
                   state.ns)
    objects = [state.value()[:2] for _ in range(edges + 1)]
    with state.locked():
        for (source, _), (target, _) in zip(objects, objects[1:]):
            name = ("pcc_gc_install_forwarding" if installer == "public"
                    else "pcc_gc_install_forwarding_unlocked")
            assert state.ns[name](source, target) == 0
    slot = state.allocate(24)
    state.store(slot, 16, objects[0][0])
    state.events.clear()
    return state, objects, slot


def _observe(state):
    writes, probes = [], []
    def store(base, offset, value):
        writes.append((base, offset, value))
        state.store(base, offset, value)
    state.ns["store_ptr"] = store
    for name, table in (("pcc_gc_forwarding_index_find", state.forwardings),
                        ("pcc_gc_object_index_find", state.index)):
        def find(obj, name=name, table=table):
            probes.append((name, obj))
            return table.get(obj, 0)
        state.ns[name] = find
    return writes, probes


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("counted", [False, True])
@pytest.mark.parametrize("installer", ["public", "unlocked"])
@pytest.mark.parametrize("edges", [1, 2, 3, 16])
def test_remap_publishes_only_canonical_terminal(backend, counted, installer, edges):
    state, objects, slot = _chain(backend, counted, edges, installer)
    counts = [state.load(obj, 0) for obj, _ in objects]
    links = {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()}
    writes, probes = _observe(state)
    with state.locked():
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    terminal = objects[-1][0]
    assert state.load(slot, 16) == terminal
    assert writes == [(slot, 16, terminal)]
    assert [state.load(obj, 0) for obj, _ in objects] == counts
    assert {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()} == links
    assert not [event for event in state.events if event[0] in ("incref", "decref")]
    assert len(probes) == edges + 2


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("invalid", [
    "null", "tagged", "missing-target", "inactive-terminal", "dying-terminal",
    "missing-edge", "self-cycle", "long-cycle", "short-population",
])
def test_invalid_chain_never_publishes_a_prefix(backend, invalid):
    state, objects, slot = _chain(backend, False, 3)
    source, middle, last_source, terminal = [obj for obj, _ in objects]
    if invalid == "missing-target":
        del state.index[terminal]
    elif invalid == "inactive-terminal":
        state.store(objects[-1][1], 32, 1)
    elif invalid == "dying-terminal":
        state.store(terminal, 12, state.load(terminal, 12) | 524288)
    elif invalid == "missing-edge":
        del state.forwardings[last_source]
        state.store(objects[-2][1], 32, 1)
    elif invalid == "short-population":
        state.store("pcc_gc_forwarding_population", 0, 2)
    elif invalid in ("self-cycle", "long-cycle"):
        # The public installer can close a cycle by retargeting an edge.
        with state.locked():
            assert state.ns["pcc_gc_install_forwarding"](last_source, source) == 0
        if invalid == "self-cycle":
            state.store(state.forwardings[last_source], 8, last_source)
    else:
        state.store(state.forwardings[last_source], 8, 0 if invalid == "null" else 7)
    counts = [state.load(obj, 0) for obj, _ in objects]
    writes, probes = _observe(state)
    with state.locked(), pytest.raises(AssertionError, match="runtime invariant abort"):
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    assert state.load(slot, 16) == source and writes == []
    assert [state.load(obj, 0) for obj, _ in objects] == counts
    assert len(probes) <= state.load("pcc_gc_forwarding_population", 0) + 2


@pytest.mark.parametrize("value_kind", ["null", "tagged", "ordinary", "stale-flag"])
def test_fast_paths_do_not_add_index_calls_or_stores(value_kind):
    state, objects, slot = _chain(4, False, 0)
    value = {"null": 0, "tagged": 7}.get(value_kind, objects[0][0])
    state.store(slot, 16, value)
    if value_kind == "stale-flag":
        state.store(value, 12, state.load(value, 12) | 2048)
    writes, probes = _observe(state)
    with state.locked():
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    assert state.load(slot, 16) == value and writes == []
    assert probes == ([("pcc_gc_forwarding_index_find", value)]
                      if value_kind == "stale-flag" else [])


@pytest.mark.parametrize("backend", [3, 4])
def test_inactive_intermediate_shells_still_reach_active_terminal(backend):
    state, objects, slot = _chain(backend, False, 3)
    for _, node in objects[:-1]:
        state.store(node, 32, 1)
    with state.locked():
        state.ns["pcc_gc_backend4_remap_slot"](slot + 16, 1, 0)
    assert state.load(slot, 16) == objects[-1][0]


def test_index_only_intermediates_are_not_dereferenced_or_provenance_checked():
    state, objects, slot = _chain(4, False, 3)
    intermediate = {obj for obj, _ in objects[1:-1]}
    for obj in intermediate:
        del state.index[obj]
    def header(address, offset):
        assert address not in intermediate, "intermediate header dereferenced"
        return state.load(address, offset)
    state.ns["load_i32"] = header
    writes, probes = _observe(state)
    with state.locked():
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    assert writes == [(slot, 16, objects[-1][0])]
    assert [obj for name, obj in probes if name == "pcc_gc_object_index_find"] == [objects[-1][0]]


def test_live_terminal_with_stale_forwarded_hint_is_accepted_without_flag_write():
    state, objects, slot = _chain(4, False, 2)
    terminal = objects[1][0]
    # Real removal leaves this live former source with its old header hint.
    with state.locked():
        state.ns["pcc_gc_forwarding_remove"](terminal)
    flags = state.load(terminal, 12)
    assert flags & 2048 and terminal not in state.forwardings
    writes, _ = _observe(state)
    with state.locked():
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    assert writes == [(slot, 16, terminal)]
    assert state.load(terminal, 12) == flags


@pytest.mark.parametrize("invalid", ["null", "tagged", "unknown"])
def test_invalid_target_is_rejected_before_header_dereference(invalid):
    state, objects, slot = _chain(4, False, 1)
    source, terminal = [obj for obj, _ in objects]
    target = {"null": 0, "tagged": 7, "unknown": terminal}[invalid]
    state.store(state.forwardings[source], 8, target)
    del state.index[terminal]
    def header(address, offset):
        assert address != target, "unproven target header dereferenced"
        return state.load(address, offset)
    state.ns["load_i32"] = header
    with state.locked(), pytest.raises(AssertionError, match="runtime invariant abort"):
        state.ns["pcc_gc_backend4_remap_heal_slot"](slot, 16)
    assert state.load(slot, 16) == source


def test_gc3_one_remap_keeps_heap_field_valid_after_all_source_retirement():
    state, objects, slot = _chain(3, False, 2)
    # The fixture allocates the full 40-byte list; its data points at our
    # existing source slot, reached through the real descriptor visitor.
    from pcc.runtime.py.py_abi_constants import PY_TYPE_LIST
    owner, _, _ = state.value(PY_TYPE_LIST)
    state.store(owner, 16, 1)  # length
    state.store(owner, 24, 1)  # capacity
    state.store(owner, 32, slot + 16)  # items
    # Registered roots contain only nonforwarded owners; the forwarded
    # reference remains an ordinary heap data slot.
    for live in (owner, objects[-1][0]):
        root = state.allocate(8)
        state.store(root, 0, live)
        state.root_cells.append(root)
    assert state.remap() > 0
    assert state.load(slot, 16) == objects[-1][0]
    assert owner in state.index
    assert [state.load(root, 0) for root in state.root_cells] == [owner, objects[-1][0]]
    assert objects[-1][0] in state.index
    assert all(obj not in state.index for obj, _ in objects[:-1])
