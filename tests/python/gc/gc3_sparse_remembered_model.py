"""Serial reference model for the proposed GC3 remembered set.

This lives under tests deliberately. No allocation, collector, or write
barrier in PCC imports it. Old owners are assumed live during a minor; their
liveness and address reuse belong to the major collector.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MinorWork:
    live_young: frozenset
    cards: tuple
    old_visits: int
    young_visits: int
    root_visits: int
    major_required: bool = False


class Model:
    def __init__(self, queue_capacity=None):
        self.young = {}
        self.cards = {}
        self.dirty = {}
        self.queue_capacity = queue_capacity
        self.major_required = False

    def add_young(self, owner, edges=()):
        self.young[owner] = tuple(edges)

    def add_old(self, owner, card, edges=()):
        self.cards.setdefault(card, {})[owner] = ()
        self.store_old(card, owner, edges)

    def store_old(self, card, owner, edges):
        # One logical transaction. Loss of its remembered entry prevents
        # nursery sweep rather than pretending the overwritten slot is clean.
        self.cards[card][owner] = slots = tuple(edges)
        if card in self.dirty or not any(child in self.young for child in slots):
            return
        if self.queue_capacity is not None and len(self.dirty) >= self.queue_capacity:
            self.major_required = True
        else:
            self.dirty[card] = None

    def promote(self, owner, card):
        # Apply the same remembered update to current edges, not just future
        # writes: promotion itself creates an old-to-young relationship.
        self.add_old(owner, card, self.young.pop(owner))

    def minor(self, roots):
        if self.major_required:
            return MinorWork(frozenset(), (), 0, 0, 0, major_required=True)
        worklist = list(roots)
        root_visits = len(worklist)
        scanned = tuple(self.dirty)
        persistent_cards = {}
        old_visits = 0
        for card in scanned:
            for slots in self.cards[card].values():
                old_visits += 1
                for child in slots:
                    if child in self.young:
                        worklist.append(child)
                        persistent_cards[card] = None

        marked = set()
        while worklist:
            current = worklist.pop()
            if current not in self.young or current in marked:
                continue
            marked.add(current)
            worklist.extend(self.young[current])

        self.dirty = persistent_cards
        self.young = {owner: self.young[owner] for owner in marked}
        return MinorWork(frozenset(marked), scanned, old_visits, len(marked), root_visits)

    def full_reachable_young(self, roots):
        """Independent full-graph oracle, not part of minor collection."""
        graph = dict(self.young)
        for owners in self.cards.values():
            graph.update(owners)
        visited = set()
        pending = list(roots)
        while pending:
            node = pending.pop()
            if node not in visited:
                visited.add(node)
                pending.extend(graph.get(node, ()))
        return visited.intersection(self.young)
