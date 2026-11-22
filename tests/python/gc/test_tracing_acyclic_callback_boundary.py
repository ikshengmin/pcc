"""Reference model of a resume/stop boundary; no changes to GC dispatch."""

from dataclasses import dataclass

import pytest

from tests.python.gc.test_tracing_weakref_candidate_peel import classify_before_callbacks


MODEL_WORK_BUDGET = 100


@dataclass(frozen=True)
class StoppedView:
    epoch: int
    strong_slots: dict
    roots: frozenset
    pending_finalizers: frozenset

    def white(self):
        # Independently model the mark, not a flag carried over from preflight.
        live = set()
        pending = list(self.roots)
        while pending:
            node = pending.pop()
            if node not in live:
                live.add(node)
                pending.extend(self.strong_slots[node])
        return self.strong_slots.keys() - live


class AcyclicCallbackOracle:
    def __init__(self, view):
        self.view = view
        self.finalized = set()
        self.window_open = False

    def ready(self):
        if self.window_open:
            return ()
        white = self.view.white()
        pending = (self.view.pending_finalizers & white) - self.finalized
        cyclic = classify_before_callbacks(
            white, self.view.strong_slots, pending, budget=MODEL_WORK_BUDGET,
            epoch=self.view.epoch, sealed_epoch=self.view.epoch,
        )
        if cyclic is None:
            return ()
        # Only zero-incoming candidates can start the acyclic chain. A
        # non-finalizable owner still owns its slots until separately retired.
        owned = {
            child for owner in white for child in self.view.strong_slots[owner]
            if child in white
        }
        return tuple(sorted(node for node, in_cycle_region in cyclic.items()
                            if not in_cycle_region and node not in owned))

    def start_one(self, target):
        if target not in self.ready():
            raise ValueError("no stopped verdict authorizes this finalizer")
        self.finalized.add(target)
        self.window_open = True

    def seal_after_callback(self, view):
        if not self.window_open or view.epoch <= self.view.epoch:
            raise ValueError("expected a new stopped-world mark")
        self.view = view
        self.window_open = False

    def observe_retirement(self, owner, view):
        if (self.window_open or view.epoch <= self.view.epoch
                or owner not in self.view.white() or owner in view.strong_slots):
            raise ValueError("unsafe edge retirement")
        self.view = view


def view(epoch, edges, *, roots=(), pending=()):
    return StoppedView(epoch, edges, frozenset(roots), frozenset(pending))


@pytest.mark.parametrize("parent,child", ((1, 2), (2, 1)))
def test_fresh_mark_protects_the_child_rescued_by_its_parent(parent, child):
    links = {parent: (child,), child: ()}
    first = view(3, links, pending=(parent, child))
    oracle = AcyclicCallbackOracle(first)
    assert oracle.ready() == (parent,)
    oracle.start_one(parent)
    assert not oracle.ready()
    with pytest.raises(ValueError, match="new stopped-world mark"):
        oracle.seal_after_callback(first)
    # The callback preserves the parent, and thus implicitly preserves
    # the child even though it was never added to a root by itself.
    oracle.seal_after_callback(view(4, links, roots=(parent,), pending=(parent, child)))
    assert not oracle.ready()


def test_non_resurrection_retires_the_owner_before_its_child_becomes_ready():
    oracle = AcyclicCallbackOracle(view(1, {1: (2,), 2: ()}, pending=(1, 2)))
    oracle.start_one(1)
    oracle.seal_after_callback(view(2, {1: (2,), 2: ()}, pending=(1, 2)))
    # A new mark alone is insufficient: until the original strong edge is
    # detached, finalizing the child could violate the parent's graph lifetime.
    assert not oracle.ready()
    oracle.observe_retirement(1, view(3, {2: ()}, pending=(2,)))
    assert oracle.ready() == (2,)


def test_independent_frontier_is_not_an_unconditional_callback_batch():
    links = {1: (), 2: ()}
    oracle = AcyclicCallbackOracle(view(5, links, pending=(1, 2)))
    assert oracle.ready() == (1, 2)
    oracle.start_one(1)
    with pytest.raises(ValueError, match="no stopped verdict"):
        oracle.start_one(2)
    # An external weak observation can acquire a root during user code;
    # there was no owning edge connecting these two in the stopped view.
    oracle.seal_after_callback(view(6, links, roots=(2,), pending=(1, 2)))
    assert not oracle.ready()


def test_cycle_created_while_resumed_needs_the_cyclic_ownership_path():
    oracle = AcyclicCallbackOracle(view(5, {1: (), 2: ()}, pending=(1, 2)))
    oracle.start_one(1)
    oracle.seal_after_callback(view(6, {2: (2,)}, pending=(2,)))
    assert not oracle.ready()


def test_address_reuse_cannot_inherit_an_old_finalizer_decision():
    first_cell = (12, "old incarnation")
    replacement = (12, "new incarnation")
    oracle = AcyclicCallbackOracle(view(1, {first_cell: ()}, pending=(first_cell,)))
    oracle.start_one(first_cell)
    oracle.seal_after_callback(view(2, {replacement: ()}, pending=(replacement,)))
    assert oracle.ready() == (replacement,)
