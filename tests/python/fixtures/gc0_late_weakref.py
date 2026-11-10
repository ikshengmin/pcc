"""Ordinary cyclic-finalizer weakrefs, with and without resurrection.

CPython 3.15.0's cyclic collector silently clears weakrefs made by finalizers
on objects that remain unreachable. A resurrected object keeps its new refs;
their callbacks run on its later genuine death. No traceback retains a target.
"""

import gc
import weakref


dead_finalizers = 0
dead_early_callbacks = 0
dead_late_callbacks = 0
dead_late_refs = []
dead_events = []

saved_finalizers = 0
saved_early_callbacks = 0
saved_late_callbacks = 0
saved_late_refs = []
saved_events = []
saved = []


def dead_early_callback(ref):
    global dead_early_callbacks
    dead_early_callbacks += 1
    dead_events.append(("early", ref() is None))


def dead_late_callback(ref):
    global dead_late_callbacks
    dead_late_callbacks += 1
    dead_events.append(("late", ref() is None))


class DeadCycle:
    def __del__(self):
        global dead_finalizers
        dead_finalizers += 1
        dead_events.append(("finalizer", True))
        dead_late_refs.append(weakref.ref(self))
        dead_late_refs.append(weakref.ref(self, dead_late_callback))


def make_dead_cycle():
    target = DeadCycle()
    target.cycle = target
    return weakref.ref(target, dead_early_callback)


def saved_early_callback(ref):
    global saved_early_callbacks
    saved_early_callbacks += 1
    saved_events.append(("early", ref() is None))


def saved_late_callback(ref):
    global saved_late_callbacks
    saved_late_callbacks += 1
    saved_events.append(("late", ref() is None))


class SavedCycle:
    def __del__(self):
        global saved_finalizers
        saved_finalizers += 1
        saved_events.append(("finalizer", True))
        saved_late_refs.append(weakref.ref(self))
        saved_late_refs.append(weakref.ref(self, saved_late_callback))
        saved.append(self)


def make_saved_cycle():
    target = SavedCycle()
    target.cycle = target
    return weakref.ref(target, saved_early_callback)


def main():
    gc.disable()
    gc.collect()

    dead_early = make_dead_cycle()
    gc.collect()
    assert dead_early() is None
    assert len(dead_late_refs) == 2
    assert dead_late_refs[0]() is None
    assert dead_late_refs[1]() is None
    assert (dead_finalizers, dead_early_callbacks, dead_late_callbacks) == (1, 1, 0)
    assert dead_events == [("early", True), ("finalizer", True)]
    gc.collect()
    assert (dead_finalizers, dead_early_callbacks, dead_late_callbacks) == (1, 1, 0)
    print("late-cleared finalizers=1 early=1 late=0")

    saved_early = make_saved_cycle()
    gc.collect()
    assert saved_early() is None
    assert len(saved) == 1
    assert len(saved_late_refs) == 2
    assert saved_late_refs[0]() is saved[0]
    assert saved_late_refs[1]() is saved[0]
    assert saved[0].cycle is saved[0]
    assert (saved_finalizers, saved_early_callbacks, saved_late_callbacks) == (1, 1, 0)
    assert saved_events == [("early", True), ("finalizer", True)]
    gc.collect()
    assert saved_late_refs[0]() is saved[0]
    assert saved_late_refs[1]() is saved[0]
    assert (saved_finalizers, saved_early_callbacks, saved_late_callbacks) == (1, 1, 0)
    print("resurrected finalizers=1 early=1 late=0")

    # Keep the saved owner until after the cycle is explicitly broken. Do not
    # bind either weakref's result to a local that could delay genuine death.
    saved[0].cycle = None
    assert saved_late_refs[0]() is saved[0]
    assert saved_late_refs[1]() is saved[0]
    assert saved_late_callbacks == 0
    saved.clear()
    gc.collect()
    assert saved_early() is None
    assert saved_late_refs[0]() is None
    assert saved_late_refs[1]() is None
    assert (saved_finalizers, saved_early_callbacks, saved_late_callbacks) == (1, 1, 1)
    assert saved_events == [("early", True), ("finalizer", True), ("late", True)]
    gc.collect()
    assert (saved_finalizers, saved_early_callbacks, saved_late_callbacks) == (1, 1, 1)
    assert (dead_finalizers, dead_early_callbacks, dead_late_callbacks) == (1, 1, 0)
    print("released finalizers=1 early=1 late=1")


main()
