"""Candidate-only recheck under a complete callback-window write journal.

This is a protocol model. The runtime does not provide the journal yet.
"""

from collections import deque

import pytest


def full_rescue(candidates, slots, roots):
    live = set()
    work = list((slots.keys() - candidates) | roots)
    while work:
        node = work.pop()
        if node not in live:
            live.add(node)
            work.extend(slots[node])
    return live & candidates


def journal_rescue(candidates, slots, roots, external_dirty, new, *, complete):
    """After a full seed, recheck newer published roots and outside owners."""
    if not complete or (external_dirty | new) & candidates:
        return None
    if not (roots | external_dirty | new) <= slots.keys():
        return None
    pending = deque(roots & candidates)
    visits = 0
    for owner in external_dirty | new:
        visits += 1
        pending.extend(child for child in slots[owner] if child in candidates)
    rescued = set()
    while pending:
        node = pending.popleft()
        if node in rescued:
            continue
        rescued.add(node)
        visits += 1
        pending.extend(child for child in slots[node] if child in candidates)
    return rescued, visits


@pytest.mark.parametrize("old_count", (16, 64, 256))
def test_clean_external_owners_are_not_revisited(old_count):
    # None of these old owners points into the candidates at the first
    # whole-heap settlement. One mutates while callbacks run. A new private
    # owner also survives this sweep even though it has no visible root.
    slots = {node: () for node in range(old_count)}
    candidates = {old_count + index for index in range(5)}
    survivor, child, private_target, explicit, untouched = sorted(candidates)
    new_owner = max(candidates) + 1
    slots[survivor] = (child,)
    slots[child] = (survivor,)
    slots[private_target] = ()
    slots[explicit] = ()
    slots[untouched] = ()
    assert full_rescue(candidates, slots, set()) == set()

    slots[0] = (survivor,)
    slots[new_owner] = (private_target,)
    roots = {explicit}
    expected = {survivor, child, private_target, explicit}
    assert full_rescue(candidates, slots, roots) == expected
    assert journal_rescue(candidates, slots, roots, {0}, {new_owner}, complete=True) == (
        expected, 6,
    )


def test_missing_writer_is_detected_by_oracle_not_assumed_safe():
    candidates = {1, 2}
    slots = {0: (1,), 1: (2,), 2: ()}
    assert full_rescue(candidates, slots, set()) == {1, 2}
    assert journal_rescue(candidates, slots, set(), set(), set(), complete=True) == (set(), 0)
    assert journal_rescue(candidates, slots, set(), set(), set(), complete=False) is None


def test_reused_owner_identity_without_current_record_refuses():
    old = (1, "old")
    new = (1, "replacement")
    target = (2, "target")
    slots = {new: (target,), target: ()}
    assert journal_rescue({target}, slots, set(), {old}, {new}, complete=True) is None
    assert journal_rescue({target}, slots, set(), set(), {new}, complete=True) == ({target}, 2)


def test_replaced_edge_does_not_rescue_the_old_target():
    slots = {0: (2,), 1: (), 2: ()}
    candidates = {1, 2}
    assert journal_rescue(candidates, slots, set(), {0}, set(), complete=True) == ({2}, 2)
    assert full_rescue(candidates, slots, set()) == {2}
