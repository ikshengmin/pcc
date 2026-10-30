"""Record representation and explicit borrow/escape boundary, host-only.

This is not a Mojo compiler, a Python user-scope replacement, or a GC protocol.
It implements the small internal API of type_infer._Scope. Distinct records
shadow by parent linkage; mutable payload identity and late-bound parents stay
intact. No production module imports this experiment.
"""

from __future__ import annotations

from threading import get_ident
from types import MappingProxyType


class RecordScope:
    """Inline the first environment field; spill only on a second distinct one.

    `.bindings` returns this mapping facade, not a second owner/container.
    PCC's current direct accesses use only get and membership. Class-object
    marks are still separate semantic state and allocate a set on first use.
    All `_Scope`-compatible methods deliberately omit dynamic lifetime checks:
    the separate Borrow API measures those checks rather than hiding their cost.
    """

    __slots__ = ("parent", "_name", "_value", "_spill", "_classes")

    def __init__(self, parent=None):
        self.parent = parent
        self._name = None
        self._value = None
        self._spill = None
        self._classes = None

    @property
    def bindings(self):
        return self

    @property
    def class_objects(self):
        if self._classes is None:
            self._classes = set()
        return self._classes

    def get(self, name, default=None):
        if self._spill is not None:
            return self._spill.get(name, default)
        if name == self._name:
            return self._value
        return default

    def __contains__(self, name):
        if self._spill is not None:
            return name in self._spill
        return self._name is not None and name == self._name

    def __len__(self):
        if self._spill is not None:
            return len(self._spill)
        return int(self._name is not None)

    def __bool__(self):
        # A Scope is an owner, not the truth value of its field collection.
        # type_infer also walks it directly with `while scope`.
        return True

    def items(self):
        if self._spill is not None:
            return self._spill.items()
        if self._name is None:
            return ()
        return ((self._name, self._value),)

    def _find_local(self, name):
        return 0 if self.get(name) is not None else -1

    def define(self, name, ty):
        if self._spill is not None:
            self._spill[name] = ty
        elif self._name is None or name == self._name:
            self._name = name
            self._value = ty
        else:
            self._spill = {self._name: self._value, name: ty}
            self._name = None
            self._value = None
        if self._classes is not None:
            self._classes.discard(name)

    def update(self, name, ty):
        self.define(name, ty)

    def lookup_local(self, name):
        return self.get(name)

    def lookup(self, name):
        scope = self
        while scope is not None:
            found = scope.bindings.get(name)
            if found is not None:
                return found
            scope = scope.parent
        return None

    def is_class_object(self, name):
        scope = self
        while scope is not None:
            if name in scope.bindings:
                if isinstance(scope, RecordScope):
                    marks = scope._classes
                    return marks is not None and name in marks
                return name in scope.class_objects
            scope = scope.parent
        return False


class LifetimeError(RuntimeError):
    """An experimental internal borrow escaped its permitted owner interval."""


class Region:
    """Origin token: borrows are thread-confined and invalidated on every exit.

    Region does not keep a list of records: that would artificially extend all
    payload lifetimes and defeat the measurement. A Borrow retains the origin
    and record in CPython, but its validity still requires an open generation.
    Reset increments the generation before admitting new records (ABA check).
    """

    __slots__ = ("generation", "closed", "thread")

    def __init__(self):
        self.generation = 0
        self.closed = False
        self.thread = get_ident()

    def _check(self, generation):
        if get_ident() != self.thread:
            raise LifetimeError("borrow crossed its thread boundary")
        if self.closed or generation != self.generation:
            raise LifetimeError("borrow origin is closed or stale")

    def new_scope(self, parent=None):
        self._check(self.generation)
        if parent is not None:
            if not isinstance(parent, Borrow):
                raise LifetimeError("parent must be a current borrow")
            parent._check()
            if parent._region is not self:
                raise LifetimeError("parent belongs to another origin")
            parent = parent._scope
        return Borrow(self, RecordScope(parent))

    def reset(self):
        self._check(self.generation)
        self.generation += 1

    def close(self):
        if get_ident() != self.thread:
            raise LifetimeError("origin closed on another thread")
        if not self.closed:
            self.generation += 1
            self.closed = True

    def __enter__(self):
        self._check(self.generation)
        return self

    def __exit__(self, *_):
        self.close()


class Borrow:
    __slots__ = ("_region", "_generation", "_scope", "_moved")

    def __init__(self, region, scope):
        self._region = region
        self._generation = region.generation
        self._scope = scope
        self._moved = False

    def _check(self):
        self._region._check(self._generation)
        if self._moved:
            raise LifetimeError("borrow was moved")

    def lookup(self, name):
        self._check()
        return self._scope.lookup(name)

    def define(self, name, value):
        self._check()
        self._scope.define(name, value)

    def capture(self):
        """Retain a live record graph, not a snapshot of its current values."""
        self._check()
        return Capture(self._scope)

    def move(self):
        self._check()
        successor = Borrow(self._region, self._scope)
        self._moved = True
        self._scope = None
        return successor

    def snapshot(self):
        """Explicit copied snapshot; never substitute this for a live capture."""
        self._check()
        fields = {}
        scope = self._scope
        while scope is not None:
            for name, value in scope.items():
                if value is not None:
                    fields.setdefault(name, value)
            scope = scope.parent
        return MappingProxyType(fields)


class Capture:
    """Owned escape of an internal graph, retaining aliases and late binding.

    Thread confinement remains explicit; capture does not prove exclusivity or
    make the referenced values immutable. Release is idempotent and terminal.
    """

    __slots__ = ("_scope", "_thread")

    def __init__(self, scope):
        self._scope = scope
        self._thread = get_ident()

    def _check(self):
        if get_ident() != self._thread:
            raise LifetimeError("capture crossed its thread boundary")
        if self._scope is None:
            raise LifetimeError("capture was released")

    def lookup(self, name):
        self._check()
        return self._scope.lookup(name)

    def define(self, name, value):
        self._check()
        self._scope.define(name, value)

    def release(self):
        if get_ident() != self._thread:
            raise LifetimeError("capture released on another thread")
        self._scope = None
