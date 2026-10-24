#!/usr/bin/env python3
"""Executable design models, NOT a PCC collector or a flow-typing compiler.

The models separate linear ownership credits from Python object identities,
collector visibility, relocation, and marking barriers. All executions are
sequentially consistent abstractions; no native runtime is imported or built.
Run this file to exhaust finite concurrent schedules and display a counterexample.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import permutations, product
from typing import Callable, Iterator


class ProtocolError(RuntimeError):
    """A modeled ownership/effect obligation was violated."""


@dataclass(frozen=True)
class Credit:
    number: int
    identity: int
    kind: str = "Object"


@dataclass
class Loan:
    context: FlowContext
    slot: str
    credit: Credit
    active: bool = True

    def read(self) -> int:
        if not self.active:
            raise ProtocolError("use after returning a focused capability")
        return self.credit.identity

    def finish(self, identity: int | None = None, *, preserve_identity: bool = False) -> None:
        if not self.active or self.context.pending.get(self.slot) is not self:
            raise ProtocolError("residual must be invoked exactly once")
        replacement = self.credit.identity if identity is None else identity
        if preserve_identity and replacement != self.credit.identity:
            raise ProtocolError("same type is not the identity-preservation contract")
        self.context.slots[self.slot] = Credit(self.credit.number, replacement, self.credit.kind)
        del self.context.pending[self.slot]
        self.active = False
        self.context.check()


class FlowContext:
    """Runtime witness for linear capabilities; not static enforcement in Python.

    A/A may replace a value by another A. Object identity is a separate contract.
    Focus removes the capability from the usable context, not the registered root
    from a collector. Multiple credits may refer to the very same Python object.
    """

    def __init__(self, identities: tuple[int, ...]):
        self.slots: dict[str, Credit | None] = {
            f"r{i}": Credit(i, identity) for i, identity in enumerate(identities)
        }
        self.pending: dict[str, Loan] = {}
        self.retired: set[int] = set()
        self.credit_count = len(identities)
        self.registered = set(self.slots)

    def check(self) -> None:
        numbers = [credit.number for credit in self.slots.values() if credit is not None]
        numbers += [loan.credit.number for loan in self.pending.values()]
        numbers += list(self.retired)
        if Counter(numbers) != Counter(range(self.credit_count)):
            raise ProtocolError("ownership credit duplicated or lost")
        if any(self.slots[name] is not None for name in self.pending):
            raise ProtocolError("focused slot is also available in the context")

    def focus(self, slot: str) -> Loan:
        credit = self.slots[slot]
        if credit is None:
            raise ProtocolError("dead or already focused slot")
        loan = Loan(self, slot, credit)
        self.slots[slot] = None
        self.pending[slot] = loan
        self.check()
        return loan

    @contextmanager
    def focused(self, slot: str) -> Iterator[Loan]:
        loan = self.focus(slot)
        try:
            yield loan
        finally:
            if loan.active:
                loan.finish()

    def release(self, slot: str) -> None:
        if slot in self.pending or self.slots[slot] is None:
            raise ProtocolError("cannot release a borrowed or dead capability")
        self.retired.add(self.slots[slot].number)
        self.slots[slot] = None
        self.check()

    def effect(self, name: str) -> None:
        if name in {"return", "suspend", "escape", "unknown_callback"} and self.pending:
            raise ProtocolError("ephemeral capabilities cannot cross this effect")
        if name == "safepoint" and not set(self.pending) <= self.registered:
            raise ProtocolError("linear ownership did not keep the GC root registered")

    def signature(self) -> tuple[tuple[str, str], ...]:
        return tuple((name, "focused" if name in self.pending else
                      "live" if value is not None else "dead")
                     for name, value in self.slots.items())


def join_branches(left: FlowContext, right: FlowContext) -> None:
    if left.signature() != right.signature():
        raise ProtocolError("branches do not return the same capability context")


@dataclass(frozen=True)
class Pointer:
    identity: int
    epoch: int


@dataclass
class Node:
    strong: set[int] = field(default_factory=set)
    finalizer: Callable[[TraceHeap, int], None] | None = None
    finalized: bool = False


class TraceHeap:
    """Small abstract tracing/relocation oracle, not Python finalizer semantics.

    Object identities never change. Handles are updated when addresses move.
    Finalizers are one-shot and roots are re-traced after callbacks. Ordering
    between Python weakref callbacks, __del__, exceptions and cycles is NOT
    modeled, so these tests cannot certify those PCC behaviors.
    """

    def __init__(self) -> None:
        self.nodes: dict[int, Node] = {}
        self.roots: dict[str, Pointer] = {}
        self.epoch = 0

    def add(self, identity: int, *edges: int, finalizer=None) -> None:
        self.nodes[identity] = Node(set(edges), finalizer)

    def root(self, name: str, identity: int) -> None:
        if identity not in self.nodes:
            raise ProtocolError("rooting a reclaimed object")
        self.roots[name] = Pointer(identity, self.epoch)

    def resolve(self, pointer: Pointer) -> int:
        if pointer.epoch != self.epoch or pointer.identity not in self.nodes:
            raise ProtocolError("stale raw pointer after relocation or reclamation")
        return pointer.identity

    def move(self) -> None:
        self.epoch += 1
        self.roots = {name: Pointer(p.identity, self.epoch)
                      for name, p in self.roots.items()}

    def reachable(self) -> set[int]:
        work = [pointer.identity for pointer in self.roots.values()]
        seen: set[int] = set()
        while work:
            identity = work.pop()
            if identity not in seen and identity in self.nodes:
                seen.add(identity)
                work.extend(self.nodes[identity].strong)
        return seen

    def collect(self) -> set[int]:
        for identity in sorted(set(self.nodes) - self.reachable()):
            node = self.nodes[identity]
            if node.finalizer and not node.finalized:
                node.finalized = True
                node.finalizer(self, identity)
        dead = set(self.nodes) - self.reachable()
        for identity in dead:
            del self.nodes[identity]
        return dead

    def weak_get(self, identity: int) -> int | None:
        return identity if identity in self.nodes else None


class Construction:
    """Fresh-container ownership model with observable initialized prefix."""

    def __init__(self, identities: tuple[int, ...]):
        self.identities = identities
        self.roots: list[int | None] = list(range(len(identities)))
        self.fields: list[int | None] = [None] * len(identities)
        self.length = 0
        self.published = False
        self.released: set[int] = set()
        self.finalized: list[int] = []
        self.callback_errors: list[Exception] = []
        self.locked = False

    def check(self) -> None:
        tokens = [x for x in self.roots + self.fields if x is not None]
        tokens += list(self.released)
        if Counter(tokens) != Counter(range(len(self.identities))):
            raise ProtocolError("construction duplicated or lost an owner")
        if any(x is None for x in self.fields[:self.length]):
            raise ProtocolError("collector-visible prefix includes an uninitialized field")
        if any(x is not None for x in self.fields[self.length:]):
            raise ProtocolError("an owned field is invisible to tracing")

    def visible_identities(self) -> set[int]:
        tokens = self.roots + self.fields[:self.length]
        return {self.identities[x] for x in tokens if x is not None}

    def transfer(self, index: int) -> None:
        if self.published or index != self.length or self.roots[index] is None:
            raise ProtocolError("transfer requires the next distinct owning root and fresh output")
        self.locked = True
        self.fields[index], self.roots[index] = self.roots[index], None
        self.length += 1
        self.locked = False
        self.check()

    def publish(self) -> None:
        self.check()
        if self.length != len(self.roots) or self.published:
            raise ProtocolError("publish requires complete initialization exactly once")
        self.published = True

    def _release(self, token: int, callback: Callable[[int], None]) -> None:
        if self.locked or token in self.released:
            raise ProtocolError("double release or callback inside graph lock")
        self.released.add(token)
        identity = self.identities[token]
        if all(i in self.released for i, value in enumerate(self.identities) if value == identity):
            self.finalized.append(identity)
            callback(identity)

    def cleanup(self, original_error: str, callback: Callable[[int], None]) -> str:
        """Retire partial fields and untouched operands; preserve caller error."""
        self.check()
        if self.locked:
            raise ProtocolError("cleanup cannot execute under graph lock")

        def invoke_finalizer(identity: int) -> None:
            # Model invariants are checked outside the user-callback catch.
            self.check()
            try:
                callback(identity)
            except Exception as error:
                # Abstract unraisable-error channel, not a Python TLS model.
                self.callback_errors.append(error)

        # Remove the initialized tail and update its visible bound before
        # releasing an owner. A callback may inspect or recursively drain us.
        while self.length:
            index = self.length - 1
            token = self.fields[index]
            assert token is not None  # Established by check() and valid reentry.
            self.fields[index] = None
            self.length = index
            self._release(token, invoke_finalizer)
        for index, token in enumerate(self.roots):
            if token is not None:
                self.roots[index] = None
                self._release(token, invoke_finalizer)
        self.check()
        return original_error


@dataclass
class MutableEntry:
    """One dictionary-like location; version tracks structural invalidation."""

    key: str
    value: int
    version: int = 0
    locked: bool = False

    def reentrant_replace(self, key: str, value: int) -> None:
        if self.locked:
            raise ProtocolError("callback cannot execute under graph lock")
        self.key, self.value = key, value
        self.version += 1


class StorePlan:
    """Minimal prepare/commit/finish witness; not the PCC plan ABI."""

    def __init__(self, entry: MutableEntry):
        self.entry = entry
        self.version = entry.version
        self.state = "prepared"
        self.displaced: int | None = None

    def commit(self, value: int) -> bool:
        if not self.entry.locked or self.state != "prepared":
            raise ProtocolError("commit needs a fresh plan and graph lock")
        if self.entry.version != self.version:
            self.state = "retry"
            return False
        self.displaced = self.entry.value
        self.entry.value = value
        self.state = "committed"
        return True

    def finish(self, release: Callable[[int], None]) -> None:
        if self.entry.locked or self.state == "finished":
            raise ProtocolError("finish must be once, outside graph lock")
        self.state = "finished"  # Mark consumed before a reentrant callback.
        if self.displaced is not None:
            release(self.displaced)


def interleave(left: tuple[str, ...], right: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    if not left:
        yield right
    elif not right:
        yield left
    else:
        for tail in interleave(left[1:], right):
            yield (left[0],) + tail
        for tail in interleave(left, right[1:]):
            yield (right[0],) + tail


def mark_schedules(size: int) -> Iterator[tuple[str, ...]]:
    """All root-scan orders and atomic-transfer positions in one finite cycle.

    The output must be discovered before its fields are scanned. Sweep follows
    every marker scan. Mutator commits retain their source-program order.
    """
    scans = ("output", "fields") + tuple(f"root:{i}" for i in range(size))
    commits = tuple(f"move:{i}" for i in range(size))
    for order in permutations(scans):
        if order.index("output") < order.index("fields"):
            yield from interleave(order + ("sweep",), commits)


def mark_schedule(
    identities: tuple[int, ...], schedule: tuple[str, ...], *, barrier: bool
) -> set[int]:
    """Return reachable leaf identities wrongly swept by this abstract marker.

    Linear transfer is atomic and conserves ownership even with barrier=False.
    Thus a failure here specifically demonstrates the missing temporal marking
    obligation, not missing ownership. The barrier is a toy insertion barrier,
    not any claim about the exact algorithm chosen by a PCC backend.
    """
    construction = Construction(identities)
    marked: set[int] = set()
    owner_black = False
    marking = True
    missed: set[int] = set()
    for event in schedule:
        if event == "fields":
            marked.update(identities[x] for x in construction.fields[:construction.length]
                          if x is not None)
            owner_black = True
        elif event.startswith("root:"):
            token = construction.roots[int(event.split(":")[1])]
            if token is not None:
                marked.add(identities[token])
        elif event.startswith("move:"):
            index = int(event.split(":")[1])
            construction.transfer(index)
            if barrier and marking and owner_black:
                marked.add(identities[index])
        elif event == "sweep":
            missed = construction.visible_identities() - marked
            marking = False
    construction.publish()
    return missed


def minor_collection(*, remembered: bool) -> set[int]:
    """Old output -> young child after transfer: old roots are not scanned."""
    construction = Construction((7,))
    construction.transfer(0)
    young_marked = construction.visible_identities() if remembered else set()
    return {7} - young_marked


def finite_mark_experiment(max_sources: int = 3) -> dict[str, object]:
    if not 1 <= max_sources <= 3:
        raise ValueError("bounded experiment supports one to three sources")
    checked = 0
    unsafe_failures = 0
    witness: dict[str, object] | None = None
    for size in range(1, max_sources + 1):
        for identities in product((1, 2), repeat=size):
            for schedule in mark_schedules(size):
                checked += 1
                if mark_schedule(identities, schedule, barrier=True):
                    raise AssertionError(
                        (identities, schedule, "barrier model lost a live object")
                    )
                missed = mark_schedule(identities, schedule, barrier=False)
                if missed:
                    unsafe_failures += 1
                    if witness is None:
                        witness = {
                            "identities": identities,
                            "events": schedule,
                            "missed": sorted(missed),
                        }
    return {"mode": "abstract CPython model, not PCC/native GC qualification",
            "checked_schedules": checked, "safe_failures": 0,
            "barrier_removed_failures": unsafe_failures, "first_counterexample": witness}


if __name__ == "__main__":
    import json

    print(json.dumps(finite_mark_experiment(), indent=2))
