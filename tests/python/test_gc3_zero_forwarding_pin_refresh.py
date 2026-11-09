"""Actual scan differential against its pre-specialization two-pass control.

Finite words/index memory prove reset ordering, not native capture or speed.
The oracle restores the 9a4f0aeb scan's two policy statements while retaining
all actual capture, extent, pin-query and terminal-mark implementation bodies.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.python.test_gc4_conservative_forwarding_chain import MovingRoots

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_conservative_roots.py"


def _install_original_scan(state):
    tree = ast.parse(SOURCE.read_text())
    scan = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_scan_thread_roots")
    scan.decorator_list = []
    # Restore the original unconditional GC3 separate pass. The additional
    # specialization is the sole top-level if whose body assigns zero here.
    specializations = [node for node in scan.body if isinstance(node, ast.If)
                       and len(node.body) == 1
                       and isinstance(node.body[0], ast.Assign)
                       and ast.unparse(node.body[0]) == "chain_pin_cut = 0"]
    assert len(specializations) == 1
    scan.body.remove(specializations[0])
    predicates = [node for node in scan.body if isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name) and node.target.id == "refresh_inline"]
    assert len(predicates) == 1
    scan.body.remove(predicates[0])
    guards = [node for node in ast.walk(scan) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "refresh_inline != 0"]
    assert len(guards) == 1
    guards[0].test = ast.parse("mark == 0 and chain_pin_cut == 0", mode="eval").body
    module = ast.fix_missing_locations(ast.Module(body=[scan], type_ignores=[]))
    exec(compile(module, "9a4f0aeb-two-pass-control", "exec"), state.ns)


def _comparison_memory(state, saved_callback, keys):
    # This fixture installs an instance-bound list.append in one global slot.
    # Check its exact identity and owner before normalizing only that slot;
    # independent fixture instances cannot compare their bound owners equal.
    slot = ("py_cpy_handle_release_fn", 0)
    assert state.memory[slot] is saved_callback
    assert saved_callback.__self__ is state.foreign_releases
    assert saved_callback.__name__ == "append"
    memory = {key: state.memory[key] for key in keys}
    memory[slot] = "unchanged fixture foreign_releases.append"
    return memory


def _run_case(*, original, mark, kind, reverse, backend=3, counted=False, population=0):
    state = MovingRoots(backend, counted=counted)
    if original:
        _install_original_scan(state)
    objects = [state.value()[:2] for _ in range(5)]
    for _, node in objects:
        state.store(node, 56, 5)  # stale conservative pin plus independent bit
    # Detached physical source and dying object must remain excluded. Their
    # bytes and unrelated provenance remain untouched, not implicitly freed.
    state.store(objects[3][1], 32, 1)
    state.store(objects[3][0], 12, state.load(objects[3][0], 12) | 2048)
    state.store(objects[4][0], 12, state.load(objects[4][0], 12) | 524288)
    nodes = [node for _, node in objects]
    if reverse:
        nodes.reverse()
    state.store("pcc_gc_object_head", 0, nodes[0])
    for index, node in enumerate(nodes):
        state.store(node, 16, nodes[index + 1] if index + 1 < len(nodes) else 0)
        state.store(node, 40, nodes[index - 1] if index else 0)
    state.words = [0x70000000]  # nonempty frozen set, no managed owner
    rooted = set()
    if kind == "raw":
        state.words += [objects[0][0] + 9] * 3
        rooted.add(0)
    elif kind == "lease":
        state.store(objects[0][1], 80, 2)
        rooted.add(0)
    elif kind == "header":
        state.store(objects[0][0], 12, state.load(objects[0][0], 12) | 64)
        rooted.add(0)
    elif kind == "static":
        state.ns["pcc_gc_static_literal_contains"] = lambda obj: int(obj == objects[0][0])
        rooted.add(0)
    elif kind in ("payload", "alias"):
        payload = state.allocate(128)
        for index in (0, 1):
            owner, span = state.allocate(72), state.allocate(48)
            state.store(objects[index][1], 48, owner)
            state.store(owner, 64, span)
            state.store(span, 8, payload + (16 * index if kind == "payload" else 0))
            state.store(span, 16, 64)
        state.words += [payload + 25] * 3
        rooted.update((0, 1))
    state.store("pcc_gc_forwarding_population", 0, population)
    calls = []
    live_extent = state.ns["_node_has_live_extent"]

    def checked_extent(node):
        assert state.stopped and state.depth > 0
        calls.append(node)
        return live_extent(node)

    state.ns["_node_has_live_extent"] = checked_extent
    before = dict(state.memory)
    saved_callback = before[("py_cpy_handle_release_fn", 0)]
    with state.locked():
        state.ns["_scan_thread_roots"](mark)
    assert state.depth == 0 and state.stopped
    assert not state.forwardings and len(state.index) == 5
    assert state.load("pcc_gc_forwarding_population", 0) == population
    assert state.words[0] == 0x70000000
    assert state.owner_snapshot_refreshes == 1
    # Compare the complete pre-existing memory, not only observed roots:
    # payload provenance, leases, flags and index-node links must agree too.
    outcome = (_comparison_memory(state, saved_callback, before), state.events,
               state.gray, dict(state.index), state.words, list(state.foreign_releases))
    if backend == 3 and not counted and population == 0:
        assert [state.load(node, 56) for _, node in objects] == [
            4 | int(index in rooted) if index < 3 else 5 for index in range(5)
        ]
        assert {obj for event, obj in state.events if event == "root"} == (
            {objects[index][0] for index in rooted} if mark else set()
        )
        assert state.gray == (len(rooted) if mark else 0)
    return outcome, len(calls)


@pytest.mark.parametrize("mark", [0, 1])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind", ["none", "raw", "lease", "header", "static", "payload", "alias"])
def test_zero_forwarding_scan_matches_two_pass_cut_with_one_heap_walk(mark, reverse, kind):
    control, before_calls = _run_case(original=True, mark=mark, reverse=reverse, kind=kind)
    actual, after_calls = _run_case(original=False, mark=mark, reverse=reverse, kind=kind)
    assert actual == control
    assert (before_calls, after_calls) == (10, 5)


@pytest.mark.parametrize("backend,counted,population", [
    (0, False, 0), (1, False, 0), (2, False, 0), (4, False, 0),
    (0, True, 0), (1, True, 0), (2, True, 0), (3, True, 0), (4, True, 0),
    (3, False, -1),
])
@pytest.mark.parametrize("mark", [0, 1])
def test_other_modes_and_invalid_negative_population_keep_original_policy(backend, counted, population, mark):
    args = dict(mark=mark, reverse=False, kind="none", backend=backend,
                counted=counted, population=population)
    assert _run_case(original=False, **args) == _run_case(original=True, **args)


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("mark", [0, 1])
@pytest.mark.parametrize("order", [(0, 1, 2), (0, 2, 1), (1, 0, 2),
                                  (1, 2, 0), (2, 0, 1), (2, 1, 0)])
def test_nonzero_forwarding_scan_keeps_two_pass_order_and_expiration(backend, mark, order):
    outcomes = []
    for original in (True, False):
        state = MovingRoots(backend)
        if original:
            _install_original_scan(state)
        objects = state.chain(order)
        saved_callback = state.memory[("py_cpy_handle_release_fn", 0)]
        for _, node in objects:
            state.store(node, 56, 5)
        state.words = [objects[0][0] + 9]
        calls = []
        live_extent = state.ns["_node_has_live_extent"]

        def checked_extent(node):
            assert state.stopped and state.depth > 0
            calls.append(node)
            return live_extent(node)

        state.ns["_node_has_live_extent"] = checked_extent
        with state.locked():
            state.ns["_scan_thread_roots"](mark)
        assert [state.load(node, 56) for _, node in objects] == [5, 5, 5]
        outcomes.append((_comparison_memory(state, saved_callback, state.memory),
                         list(state.events), list(state.foreign_releases)))
        # Current cut disappears. Expiration must not depend on source/target
        # order, even for mark=1's GC3 address-pinned query.
        state.words = [0x70000000]
        state.events.clear()
        with state.locked():
            state.ns["_scan_thread_roots"](mark)
        assert [state.load(node, 56) for _, node in objects] == [4, 4, 4]
        assert not [event for event in state.events if event[0] == "root"]
        assert len(calls) == 12
        outcomes.append((_comparison_memory(state, saved_callback, state.memory),
                         list(state.events), list(state.foreign_releases)))
    assert outcomes[:2] == outcomes[2:]
