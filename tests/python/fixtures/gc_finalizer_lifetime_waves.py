# Ordinary, single-application-thread Python. Fixed small label domains.
import gc

births = []
callbacks = []
overflow = [0]


def scalar(value):
    if type(value) is int:
        if 100 <= value < 116 or 200 <= value < 216:
            return value
    return -1


class Item:
    def __init__(self, label, cyclic):
        self.label = label
        if cyclic:
            self.link = self
        else:
            self.link = None
        if len(births) >= 32:
            overflow[0] = 1
        else:
            births.append(scalar(label))

    def __del__(self):
        # Keep no object/address. No output, added user lock or private API.
        if len(callbacks) >= 128:
            overflow[0] = 1
        else:
            callbacks.append(scalar(getattr(self, 'label', -1)))


def make_wave(base):
    held = []
    for i in range(16):
        item = Item(base + i, i % 4 < 2)
        if i % 2 == 0:
            held.append(item)
    return held


def held_labels(held):
    labels = []
    for item in held:
        labels.append(scalar(getattr(item, 'label', -1)))
    return labels


def scenario():
    # A-even still has ordinary strong owners across this completed collection.
    a = make_wave(100)
    gc.collect()
    born1 = len(births)
    end1 = len(callbacks)

    # Both held cohorts remain strongly referenced through this checkpoint.
    b = make_wave(200)
    born2 = len(births)
    end2 = len(callbacks)
    proof_a = held_labels(a)
    a = []

    # B-even stays owned through a *second* ordinary completed collection.
    gc.collect()
    end3 = len(callbacks)
    proof_b = held_labels(b)
    b = []

    # Both ordinary owner lists have been released. Missing is checkpoint-only.
    gc.collect()
    end4 = len(callbacks)
    # After the boundary, print only bounded scalar labels/prefixes.
    print('GC4WAVE1', born1, born2, end1, end2, end3, end4, overflow[0])
    print('BIRTH', births)
    print('HOLD-A', proof_a)
    print('HOLD-B', proof_b)
    print('CALL', callbacks[:end4])
    print('END_GC4WAVE1')


scenario()
