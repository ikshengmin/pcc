"""Conservative roots must pin and shade the canonical moving owner.

Run the actual snapshot, word scan, forwarding installation, pin and root-mark
bodies over the existing finite memory/index fixture. This is host semantic
coverage, not native stack capture, page retirement, or throughput evidence.
"""
from __future__ import annotations

from itertools import permutations

import pytest

from tests.python.test_gc3_norc_source_retirement import GC3


class MovingRoots(GC3):
    def __init__(self, backend=4, *, counted=False):
        super().__init__(counted=counted)
        self.store("pcc_gc_backend_selected", 0, backend)
        self.ns["pcc_gc_backend"] = lambda: backend
        self.ns["pcc_gc_config_ensure"] = lambda: backend
        self.load_bodies("freestanding_gc_root_operations.py", {
            "pcc_gc_mark_root_gray_if_known", "pcc_gc_object_is_known_no_lock",
        })
        self.ns["pcc_gc_trace_stack_push"] = lambda obj: self.events.append(("root", obj))

    def chain(self, order=(0, 1, 2)):
        objects = [self.value()[:2] for _ in range(3)]
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


@pytest.mark.parametrize("backend", [3, 4])
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


@pytest.mark.parametrize("backend", [3, 4])
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
