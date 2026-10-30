#!/usr/bin/env python3
"""Python transition mirror for FlowGCTransfer.tla, NOT a TLA+ interpreter.

Explore distinct Python states and compare every complete schedule with the
pre-existing flow_gc_protocol_model.py experiment. This checks the handwritten
mirror and finite protocol interpretation, not TLA+ parsing or refinement to PCC.
No PCC module, native compiler, collector, or third-party package is imported.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
from itertools import product
import json
from typing import Iterator

from flow_gc_protocol_model import mark_schedule, mark_schedules


@dataclass(frozen=True)
class State:
    identities: tuple[int, ...]
    roots: tuple[int, ...]
    fields: tuple[int, ...]
    length: int = 0
    output_scanned: bool = False
    fields_scanned: bool = False
    scanned_roots: frozenset[int] = frozenset()
    marked: frozenset[int] = frozenset()
    marking: bool = True
    missed: frozenset[int] = frozenset()
    published: bool = False

    @classmethod
    def initial(cls, identities: tuple[int, ...]) -> State:
        if not identities:
            raise ValueError("the marking experiment needs at least one source")
        return cls(identities, tuple(range(1, len(identities) + 1)), (0,) * len(identities))

    def visible_tokens(self) -> frozenset[int]:
        return frozenset(self.roots + self.fields[:self.length]) - {0}

    def visible_identities(self) -> frozenset[int]:
        return frozenset(self.identities[token - 1] for token in self.visible_tokens())

    def invariant_errors(self, *, max_sources: int = 3) -> tuple[str, ...]:
        """Mirror the named invariants; this does not evaluate TLA+ expressions."""
        count = len(self.identities)
        slots = frozenset(range(1, count + 1))
        errors = []
        if not (
            1 <= count <= max_sources
            and set(self.identities) <= {1, 2}
            and len(self.roots) == len(self.fields) == count
            and set(self.roots + self.fields) <= slots | {0}
            and 0 <= self.length <= count
            and all(type(value) is bool for value in (
                self.output_scanned, self.fields_scanned, self.marking, self.published
            ))
            and self.scanned_roots <= slots
            and self.marked <= {1, 2}
            and self.missed <= {1, 2}
        ):
            errors.append("TypeOK")
        owners = Counter(token for token in self.roots + self.fields if token)
        if owners != Counter(slots):
            errors.append("OwnershipConserved")
        if any(
            (field, root) != ((index, 0) if index <= self.length else (0, index))
            for index, (field, root) in enumerate(zip(self.fields, self.roots), 1)
        ):
            errors.append("PrefixVisible")
        if self.visible_tokens() != slots:
            errors.append("AllOwnedVisible")
        if (
            self.fields_scanned and not self.output_scanned
            or not self.marking and not (
                self.fields_scanned and self.scanned_roots == slots
            )
        ):
            errors.append("ScanOrder")
        if self.published and (self.length != count or self.marking):
            errors.append("PublishedComplete")
        if self.missed:
            errors.append("NoLostLiveObjects")
        return tuple(errors)


def enabled_events(state: State) -> Iterator[str]:
    """Nonstuttering Next actions; omit the explicit terminal Done self-loop."""
    count = len(state.identities)
    if state.marking:
        if not state.output_scanned:
            yield "output"
        if state.output_scanned and not state.fields_scanned:
            yield "fields"
        for index in range(count):
            if index + 1 not in state.scanned_roots:
                yield f"root:{index}"
    if state.length < count and not state.published:
        yield f"move:{state.length}"
    if (
        state.marking and state.output_scanned and state.fields_scanned
        and len(state.scanned_roots) == count
    ):
        yield "sweep"
    if not state.marking and state.length == count and not state.published:
        yield "publish"


def step(state: State, event: str, *, barrier: bool) -> State:
    """Execute one enabled action. Event indices are Python's zero-based slots."""
    if event not in enabled_events(state):
        raise ValueError(f"disabled event: {event}")
    if event == "output":
        return replace(state, output_scanned=True)
    if event == "fields":
        children = frozenset(state.identities[token - 1]
                             for token in state.fields[:state.length])
        return replace(state, fields_scanned=True, marked=state.marked | children)
    if event.startswith("root:"):
        index = int(event.split(":")[1])
        token = state.roots[index]
        shade = frozenset((state.identities[token - 1],)) if token else frozenset()
        return replace(state, scanned_roots=state.scanned_roots | {index + 1},
                       marked=state.marked | shade)
    if event.startswith("move:"):
        index = state.length
        token = state.roots[index]
        if token != index + 1:
            raise ValueError("transfer requires the next owning root")
        roots = state.roots[:index] + (0,) + state.roots[index + 1:]
        fields = state.fields[:index] + (token,) + state.fields[index + 1:]
        marked = state.marked
        if barrier and state.marking and state.fields_scanned:
            marked |= {state.identities[index]}
        return replace(state, roots=roots, fields=fields, length=index + 1,
                       marked=marked)
    if event == "sweep":
        return replace(state, marking=False,
                       missed=state.visible_identities() - state.marked)
    if event == "publish":
        return replace(state, published=True)
    raise AssertionError(event)


def explore(*, max_sources: int, barrier: bool) -> dict[str, object]:
    """Breadth-first explicit-state exploration in Python, not TLC."""
    if not 1 <= max_sources <= 3:
        raise ValueError("the cross-check supports one to three sources")
    initial = [State.initial(ids) for count in range(1, max_sources + 1)
               for ids in product((1, 2), repeat=count)]
    pending = deque((state, ()) for state in initial)
    seen = set(initial)
    transitions = 0
    terminals = 0
    violations = 0
    first = None
    while pending:
        state, trace = pending.popleft()
        errors = state.invariant_errors(max_sources=max_sources)
        structural_errors = set(errors) - {"NoLostLiveObjects"}
        if structural_errors:
            raise AssertionError((trace, sorted(structural_errors)))
        if "NoLostLiveObjects" in errors:
            violations += 1
            if first is None:
                first = {"identities": list(state.identities), "events": list(trace),
                         "missed": sorted(state.missed)}
        events = tuple(enabled_events(state))
        if not events:
            if not state.published:
                raise AssertionError((trace, "unexpected nonterminal deadlock"))
            terminals += 1
        for event in events:
            successor = step(state, event, barrier=barrier)
            transitions += 1
            if successor not in seen:
                seen.add(successor)
                pending.append((successor, trace + (event,)))
    return {"unique_python_states": len(seen), "initial_states": len(initial),
            "nonstuttering_transitions": transitions, "terminal_states": terminals,
            "safety_violating_states": violations, "first_counterexample": first}


def compare_schedules(max_sources: int = 3) -> dict[str, int]:
    """Replay every original schedule against the independently written mirror."""
    if not 1 <= max_sources <= 3:
        raise ValueError("the cross-check supports one to three sources")
    checked = 0
    safe_failures = 0
    unsafe_failures = 0
    for count in range(1, max_sources + 1):
        for identities in product((1, 2), repeat=count):
            for schedule in mark_schedules(count):
                checked += 1
                for barrier in (True, False):
                    state = State.initial(identities)
                    for event in schedule:
                        state = step(state, event, barrier=barrier)
                    state = step(state, "publish", barrier=barrier)
                    expected = mark_schedule(identities, schedule, barrier=barrier)
                    if state.missed != expected:
                        raise AssertionError((identities, schedule, barrier,
                                              state.missed, expected))
                    if state.missed:
                        if barrier:
                            safe_failures += 1
                        else:
                            unsafe_failures += 1
    return {"complete_schedules_per_variant": checked,
            "safe_failing_schedules": safe_failures,
            "barrier_removed_failing_schedules": unsafe_failures,
            "mirror_disagreements": 0}


def experiment(max_sources: int = 3) -> dict[str, object]:
    positive = explore(max_sources=max_sources, barrier=True)
    negative = explore(max_sources=max_sources, barrier=False)
    schedules = compare_schedules(max_sources)
    if positive["safety_violating_states"] or schedules["safe_failing_schedules"]:
        raise AssertionError("positive Python model violated safety")
    if not negative["safety_violating_states"]:
        raise AssertionError("negative control did not expose a missed referent")
    return {"mode": "Python transition mirror; NOT SANY/TLC or PCC/native execution",
            "max_sources": max_sources, "object_ids": [1, 2],
            "positive": positive, "barrier_removed": negative,
            "schedule_comparison": schedules}


if __name__ == "__main__":
    print(json.dumps(experiment(), indent=2))
