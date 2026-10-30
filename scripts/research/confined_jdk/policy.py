"""Bounded retained-event-buffer policy for the host experiment only.

This is an explicit alternative to the unbounded aggregate EventPool cache.
It bounds idle bytes, not active bytes, and never recycles an active lease.
There is no production import, native-heap claim, or latency claim here.
"""

from .waitset import EventPool


class BoundedEventPool(EventPool):
    def __init__(self, memory, max_idle_bytes):
        if not isinstance(max_idle_bytes, int) or max_idle_bytes < 0:
            raise ValueError("max_idle_bytes must be a nonnegative integer")
        super().__init__(memory)
        self.max_idle_bytes = max_idle_bytes
        self.zeroed_bytes = 0
        self.dropped_returns = 0

    @property
    def retained_bytes(self):
        with self.lock:
            return sum(capacity for _, capacity in self.idle.values())

    def release(self, pointer):
        if not pointer:
            return
        with self.lock:
            waitset, capacity = self.active.pop(pointer)
            prior = self.idle.get(waitset)
            # Preserve the original preference for the larger same-owner buffer,
            # except that one oversized return must not pin unbounded idle bytes.
            if capacity > self.max_idle_bytes or (prior is not None and prior[1] >= capacity):
                self.memory.free(pointer)
                self.dropped_returns += 1
                return
            retained = sum(size for _, size in self.idle.values())
            projected = retained - (prior[1] if prior is not None else 0) + capacity
            if projected > self.max_idle_bytes:
                self.memory.free(pointer)
                self.dropped_returns += 1
                return
            # Wipe before publication; active ownership ended only at release.
            self.memory.clear(pointer, 0, capacity)
            self.zeroed_bytes += capacity
            if prior is not None:
                self.memory.free(prior[0])
            self.idle[waitset] = (pointer, capacity)

    def trim_idle(self):
        """Explicit pressure signal. Does not release or invalidate active leases."""
        with self.lock:
            released = sum(capacity for _, capacity in self.idle.values())
            for pointer, _ in self.idle.values():
                self.memory.free(pointer)
            self.idle.clear()
            return released
