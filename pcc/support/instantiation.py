"""Session-owned instance registration and deterministic frontend work scheduling.

This module deliberately knows no language types, layouts, constraints, symbol
mangling, or diagnostics. Callers supply complete semantic keys and placeholders
only after language-specific selection/checking. Requesting an existing running
instance returns its placeholder: recursive functions are not layout cycles.
Language-specific layout checking must not be replaced by this worklist.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class InstanceState(Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in-progress"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(eq=False)
class Instance:
    key: object
    value: object
    context: object = None
    state: InstanceState = field(default=InstanceState.QUEUED, init=False)
    result: object = field(default=None, init=False)
    failure: BaseException | None = field(default=None, init=False)


class _ValueView(Mapping):
    """Read-only language compatibility view, never another instance cache."""
    def __init__(self, registry, owner):
        self._registry, self._owner = registry, owner

    def __getitem__(self, arguments):
        entry = self._registry.get((self._owner, arguments))
        if entry is None:
            raise KeyError(arguments)
        return entry.value

    def __iter__(self):
        for key in self._registry.entries:
            if isinstance(key, tuple) and len(key) == 2 and key[0] == self._owner:
                yield key[1]

    def __len__(self):
        return sum(1 for _ in self)


class InstanceRegistry:
    """One compilation session's memoization, state and FIFO worklist.

    Keys must be hashable, stable for this session and include declaration
    identity plus all semantic arguments. They are opaque to this class and
    are never serialized. Resource policy belongs to each frontend.

    Failed work is sticky: the original exception is re-raised on lookup of
    that entry, new requests, or another drain. ``entries`` remains inspectable
    for diagnostics. A callback may request more work, but must not recursively
    drain; existing IN_PROGRESS placeholders remain available without doing so.
    """
    def __init__(self):
        self._entries = {}
        self._queue = []
        self._cursor = 0
        self._active = False
        self._failure = None

    def __len__(self):
        return len(self._entries)

    @property
    def entries(self):
        return MappingProxyType(self._entries)

    @property
    def pending(self):
        # Registration history, including completed entries, for legacy
        # frontend introspection. Only drain() owns the scheduling cursor.
        return tuple(self._queue)

    def value_view(self, owner):
        """Map argument keys to placeholders for entries keyed (owner, args)."""
        return _ValueView(self, owner)

    def get(self, key):
        entry = self._entries.get(key)
        if entry is not None and entry.state is InstanceState.FAILED:
            raise entry.failure
        return entry

    def request(self, key, placeholder, context=None):
        if self._failure is not None:
            raise self._failure
        entry = self.get(key)
        if entry is None:
            entry = Instance(key, placeholder, context)
            self._entries[key] = entry
            self._queue.append(entry)
        return entry

    def drain(self, callback):
        if self._failure is not None:
            raise self._failure
        if self._active:
            raise RuntimeError("instance worklist cannot be drained recursively")
        self._active = True
        results = []
        try:
            while self._cursor < len(self._queue):
                entry = self._queue[self._cursor]
                entry.state = InstanceState.IN_PROGRESS
                try:
                    entry.result = callback(entry)
                except BaseException as error:
                    entry.failure = self._failure = error
                    entry.state = InstanceState.FAILED
                    raise
                entry.state = InstanceState.COMPLETE
                self._cursor += 1
                results.append(entry.result)
        finally:
            self._active = False
        return results
