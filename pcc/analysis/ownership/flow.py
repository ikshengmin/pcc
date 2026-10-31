"""Forward may-path facts and structured-loop fixed-point evaluation.

Extracted from the Rust move adapter. Facts are opaque projection paths grouped
by root; adapters define what a fact means and when to generate or remove it.
Union means a fact may hold on an incoming edge. No incoming edges is unreachable
(None), distinct from a reachable empty state ({}).

This is not a general CFG worklist, backward liveness/last-use analysis, alias
analysis or release insertion. Loop transfer must be monotone over a finite
fact domain; the adapter owns control flow and its transfer/diagnostic policy.
"""
from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from .places import Path

State = dict[Hashable, set[Path]]


def clone(state: State) -> State:
    return {root: set(paths) for root, paths in state.items()}


def join(states: list[State]) -> State | None:
    if not states:
        return None
    result = clone(states[0])
    for state in states[1:]:
        for root, paths in state.items():
            result.setdefault(root, set()).update(paths)
    return result


def restrict(state: State, visible: set[Hashable]) -> State:
    return {root: set(paths) for root, paths in state.items() if root in visible}


@dataclass
class LoopEdges:
    visible: set[Hashable]
    breaks: list[State] = field(default_factory=list)
    continues: list[State] = field(default_factory=list)

    def outer(self, state: State) -> State:
        return restrict(state, self.visible)


def solve_loop(entry: State,
               transfer: Callable[[State], tuple[list[State], list[State]]]) -> State | None:
    """Join entry/backedges until stable, then join that iteration's exits.

The callback receives a fresh header state and returns (backedges, exits).
Entry is always reachable; an empty exits list represents a non-returning loop.
Facts from transient iterations must not be accumulated into the final exits.
"""
    entry = clone(entry)
    header = clone(entry)
    while True:
        backs, exits = transfer(clone(header))
        next_header = join([entry] + backs)
        if next_header == header:
            return join(exits)
        header = next_header
