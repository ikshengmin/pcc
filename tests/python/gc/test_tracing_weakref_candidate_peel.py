"""Reference model only: classify expired owning edges, not weakref callbacks."""

from collections import deque
from itertools import product

import pytest


def classify_before_callbacks(candidates, strong_slots, targets, *, budget, epoch, sealed_epoch):
    """Map callback targets to cyclic-isolate order, or decline the whole verdict.

    Each row belongs to the stopped snapshot. An unknown strong edge poisons
    the verdict; an edge to a known non-candidate does not keep its owner
    alive. Count each physical owning edge, including parallel slots.
    """
    if epoch != sealed_epoch:
        return None
    if not targets:
        return {}
    if not targets <= candidates:
        return None

    incoming = dict.fromkeys(candidates, 0)
    edges = {}
    for owner in candidates:
        if budget <= 0:
            return None
        budget -= 1
        slots = strong_slots.get(owner)
        if slots is None:
            return None
        children = []
        for child in slots:
            if budget <= 0 or child is None:
                return None
            budget -= 1
            if child in incoming:
                incoming[child] += 1
                children.append(child)
        edges[owner] = children

    zeros = deque(owner for owner, count in incoming.items() if count == 0)
    while zeros:
        if budget <= 0:
            return None
        budget -= 1
        owner = zeros.popleft()
        for child in edges[owner]:
            if budget <= 0:
                return None
            budget -= 1
            incoming[child] -= 1
            if incoming[child] == 0:
                zeros.append(child)

    return {target: incoming[target] > 0 for target in targets}


def cycle_descendant_oracle(nodes, rows):
    """Independent transitive reachability; no incoming-edge counters."""
    reachable = {}
    for node in nodes:
        found = set()
        work = list(rows[node])
        while work:
            child = work.pop()
            if child in nodes and child not in found:
                found.add(child)
                work.extend(rows[child])
        reachable[node] = found
    return {child for node, found in reachable.items() if node in found for child in found}


def _classify(candidates, slots, targets=None, *, budget=100, epoch=7, sealed_epoch=7):
    return classify_before_callbacks(
        candidates, slots, candidates if targets is None else targets,
        budget=budget, epoch=epoch, sealed_epoch=sealed_epoch,
    )


@pytest.mark.parametrize("count", (1, 2, 3, 4))
def test_candidate_peel_matches_all_small_directed_graphs(count):
    nodes = set(range(count))
    arcs = list(product(range(count), repeat=2))
    for mask in range(1 << len(arcs)):
        rows = {node: [] for node in nodes}
        for position, (source, target) in enumerate(arcs):
            if mask & (1 << position):
                rows[source].append(target)
        cyclic_order = cycle_descendant_oracle(nodes, rows)
        assert _classify(nodes, rows) == {node: node in cyclic_order for node in nodes}


def test_acyclic_target_can_still_need_early_clear():
    # owner 2 retains target 3, even though target 3 isn't in the cycle.
    # Candidate 0 is acyclic and must not make sibling 1 a cycle descendant.
    candidates = set(range(4))
    rows = {0: (1, 1), 1: (), 2: (2, 3, 3), 3: ()}
    assert _classify(candidates, rows, {1, 3}) == {1: False, 3: True}


def test_live_owner_rows_and_live_descendants_are_not_in_the_candidate_peel():
    # The stop/mark phase establishes candidates. A real live-to-candidate
    # edge would mean the snapshot was invalid, not permission to relabel
    # its target here. Live owner 9 need not even provide a row.
    candidates = {0, 1}
    rows = {0: (1, 9), 1: (), 9: None}
    assert _classify(candidates, rows) == {0: False, 1: False}
    assert _classify(candidates, rows, {9}) is None
    assert _classify(candidates, rows, set(), budget=0) == {}


@pytest.mark.parametrize("rows", ({0: ()}, {0: (1,), 1: (None,)}))
def test_unknown_row_or_unresolved_strong_slot_blocks_all_actions(rows):
    assert _classify({0, 1}, rows, {0}) is None


def test_expired_snapshot_or_insufficient_budget_never_publishes_partial_result():
    rows = {0: (1,), 1: (), 2: (2,)}
    expected = {0: False, 1: False, 2: True}
    # Three rows, two input arcs, two zero-incoming vertices, one peeled arc.
    assert _classify({0, 1, 2}, rows, budget=8) == expected
    assert _classify({0, 1, 2}, rows, budget=7) is None
    assert _classify({0, 1, 2}, rows, epoch=8) is None
