"""Diagnostic probe: direct death, self-cycle, and a cycle-owned target."""
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

gc.disable()
mode = sys.argv[1]
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
