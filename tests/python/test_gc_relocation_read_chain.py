"""Real installer/read/caller bodies over finite memory and index doubles.

Uses the shared production-body loader and production provenance predicate.
No native ABI, concurrency, performance or bootstrap acceptance is claimed.
"""
import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import PY
from tests.python.test_gc_remap_forwarding_chain import _chain, _observe

COUNT = "pcc_gc_relocation_barrier_forwards"


def _read_chain(backend, counted, edges):
    state, objects, slot = _chain(backend, counted, edges)
    load_functions(PY / "freestanding_gc_root_operations.py",
                   {"pcc_gc_object_is_known_no_lock"}, state.ns)
    load_functions(PY / "freestanding_gc_forwarding_identity.py",
                   {"pcc_gc_note_relocation_read"}, state.ns)
    state.store(COUNT, 0, 7)
    state.store("pcc_gc_config_initialized", 0, 1)
    state.store("pcc_gc_read_barrier_enabled", 0, 1)
    return state, objects, slot


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("counted", [False, True])
@pytest.mark.parametrize("edges", [1, 2, 3, 16])
@pytest.mark.parametrize("entry", ["_note_relocation_read_unlocked", "pcc_gc_note_relocation_read"])
def test_one_read_returns_validated_terminal_once(backend, counted, edges, entry):
    state, objects, _ = _read_chain(backend, counted, edges)
    counts = [state.load(obj, 0) for obj, _ in objects]
    links = {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()}
    writes, _ = _observe(state)
    with state.locked():
        assert state.ns[entry](objects[0][0]) == objects[-1][0]
    assert state.load(COUNT) == 8
    assert writes == []
    assert [state.load(obj, 0) for obj, _ in objects] == counts
    assert {obj: state.load(edge, 8) for obj, edge in state.forwardings.items()} == links
    assert not [event for event in state.events if event[0] in ("incref", "decref")]


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("kind", ["inactive", "index-only"])
def test_intermediate_identities_need_no_header_or_object_index(backend, kind):
    state, objects, _ = _read_chain(backend, False, 3)
    intermediates = {obj for obj, _ in objects[1:-1]}
    for obj, node in objects[1:-1]:
        if kind == "inactive":
            state.store(node, 32, 1)
        else:
            del state.index[obj]
    def header(address, offset):
        assert address not in intermediates, "intermediate header read"
        return state.load(address, offset)
    state.ns.update(load_i32=header, load_i64=header, load_ptr=header)
    _, probes = _observe(state)
    assert state.ns["pcc_gc_note_relocation_read"](objects[0][0]) == objects[-1][0]
    assert not [(name, obj) for name, obj in probes
                if name == "pcc_gc_object_index_find" and obj in intermediates]
    assert state.load(COUNT) == 8


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("invalid", ["null", "tagged", "unknown", "inactive", "dying",
                                    "missing-edge", "self-cycle", "cycle", "short-population"])
def test_invalid_chain_aborts_without_returning_prefix_or_counting(backend, invalid):
    state, objects, _ = _read_chain(backend, False, 3)
    source, middle, last_source, terminal = [obj for obj, _ in objects]
    forbidden = set()
    if invalid == "unknown":
        del state.index[terminal]
        forbidden.add(terminal)
    elif invalid == "inactive":
        state.store(objects[-1][1], 32, 1)
        forbidden.add(terminal)
    elif invalid == "dying":
        state.store(terminal, 12, state.load(terminal, 12) | 524288)
    elif invalid == "missing-edge":
        del state.forwardings[last_source]
        del state.index[last_source]
        forbidden.add(last_source)
    elif invalid == "short-population":
        state.store("pcc_gc_forwarding_population", 0, 2)
    elif invalid == "cycle":
        assert state.ns["pcc_gc_install_forwarding"](last_source, source) == 0
    else:
        target = {"null": 0, "tagged": 7, "self-cycle": last_source}[invalid]
        state.store(state.forwardings[last_source], 8, target)
        forbidden.add(target)
    counts = [state.load(obj, 0) for obj, _ in objects]
    def header(address, offset):
        assert address not in forbidden, "unproven header read"
        return state.load(address, offset)
    state.ns.update(load_i32=header, load_i64=header, load_ptr=header)
    writes, probes = _observe(state)
    with pytest.raises(AssertionError, match="runtime invariant abort"):
        state.ns["pcc_gc_note_relocation_read"](source)
    assert state.load(COUNT) == 7 and writes == []
    assert [state.load(obj, 0) for obj, _ in objects] == counts
    assert sum(name == "pcc_gc_forwarding_index_find" for name, _ in probes) <= (
        state.load("pcc_gc_forwarding_population") + 1)


@pytest.mark.parametrize("backend", [3, 4])
def test_removed_edge_leaves_live_terminal_hint_untouched(backend):
    state, objects, _ = _read_chain(backend, False, 2)
    terminal = objects[1][0]
    with state.locked():
        state.ns["pcc_gc_forwarding_remove"](terminal)
    flags = state.load(terminal, 12)
    assert flags & 2048 and terminal not in state.forwardings
    writes, _ = _observe(state)
    assert state.ns["pcc_gc_note_relocation_read"](objects[0][0]) == terminal
    assert state.load(terminal, 12) == flags and writes == []
    assert state.load(COUNT) == 8


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("entry", ["pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr",
                                  "pcc_gc_resolve_owned_ptr", "py_weakref_call"])
def test_actual_callers_consume_terminal_in_one_barrier(backend, entry):
    state, objects, slot = _read_chain(backend, True, 3)
    source, terminal = objects[0][0], objects[-1][0]
    if entry == "py_weakref_call":
        # A finite weakref-layout witness tests the consumer, not constructor
        # admission or native weakref lifetime. Native coverage creates one.
        from pcc.runtime.py.py_abi_constants import PY_TYPE_WEAKREF
        ref = state.allocate(48)
        state.store(ref, 8, PY_TYPE_WEAKREF)
        state.store(ref, 16, source)
        load_functions(PY / "py_weakref.py", {entry}, state.ns)
        args = (ref,)
    else:
        load_functions(PY / "py_obj.py", {entry}, state.ns)
        args = (source,) if entry == "pcc_gc_resolve_owned_ptr" else (0, slot + 16)
    counts = [state.load(obj, 0) for obj, _ in objects]
    assert state.ns[entry](*args) == terminal
    assert state.load(COUNT) == 8
    if entry in ("pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr"):
        assert state.load(slot, 16) == terminal
    if entry == "py_weakref_call":
        assert state.load(ref, 16) == terminal
        counts[-1] += 1  # The caller returns one owned reference.
    assert [state.load(obj, 0) for obj, _ in objects] == counts


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("kind", ["inactive", "index-only", "dying"])
@pytest.mark.parametrize("entry", ["_note_relocation_read_unlocked", "pcc_gc_note_relocation_read"])
def test_retired_original_source_still_resolves_by_identity(backend, kind, entry):
    state, objects, _ = _read_chain(backend, False, 3)
    source, node = objects[0]
    if kind == "inactive":
        state.store(node, 32, 1)
    elif kind == "index-only":
        del state.index[source]
    else:
        state.store(source, 12, state.load(source, 12) | 524288)
    def header(address, offset):
        # The public fast check may inspect a known dying source's flag.
        # Inactive/unmapped sources have no readable headers at all.
        assert address != source or kind == "dying", "retired source header read"
        return state.load(address, offset)
    state.ns.update(load_i32=header, load_i64=header, load_ptr=header)
    writes, _ = _observe(state)
    with state.locked():
        assert state.ns[entry](source) == objects[-1][0]
    assert state.load(COUNT) == 8 and writes == []
