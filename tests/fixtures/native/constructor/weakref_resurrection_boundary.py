"""Diagnostic probe for callback weakrefs and resurrection boundaries."""
import gc
import sys
import weakref

restored = []
events = []

def cleared(watch):
    events.append("weak")

class Target:
    def __init__(self, mode):
        self.mode = mode
        self.edge = None

    def __del__(self):
        events.append("finalize")
        restored.append(self)

class Carrier:
    def __init__(self, target):
        self.target = target
        self.loop = self

def arrange(mode):
    value = Target(mode)
    watch = weakref.ref(value, cleared)
    if mode == "self-cycle":
        value.edge = value
    if mode == "downstream":
        carrier = Carrier(value)
    return watch

def child_cleared(watch):
    events.append("child-weak")


class Child:
    def __del__(self):
        events.append("child-finalize")


class Parent:
    def __init__(self):
        self.child = None
        self.loop = None

    def __del__(self):
        events.append("parent-finalize")
        restored.append(self)


def arrange_child(mode):
    if mode == "child-first":
        child = Child()
        parent = Parent()
    else:
        parent = Parent()
        child = Child()
    parent.child = child
    if mode == "parent-cycle":
        parent.loop = parent
    return weakref.ref(child, child_cleared)


def observe_child(mode):
    watch = arrange_child(mode)
    gc.collect()
    gc.collect()
    print("first", mode, events, len(restored), watch() is restored[0].child)
    restored[0].loop = None
    restored.clear()
    gc.collect()
    gc.collect()
    print("last", mode, events, watch() is None)


gc.disable()
mode = sys.argv[1]
if mode in ("child-first", "parent-first", "parent-cycle"):
    observe_child(mode)
else:
    watch = arrange(mode)
    gc.collect()
    gc.collect()
    print("first", mode, events, len(restored), watch() is restored[0])
    if mode == "self-cycle":
        restored[0].edge = None
    restored.clear()
    gc.collect()
    gc.collect()
    print("last", mode, events, watch() is None)
