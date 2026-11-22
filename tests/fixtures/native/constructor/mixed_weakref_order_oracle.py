"""CPython reference for live and discarded weakrefs to a cycle-owned leaf."""
import gc
import weakref


gc.disable()
events = []
finalized = []


class Leaf:
    def __del__(self):
        assert events == [2, 1]
        finalized.append(self)


def arrange():
    owner = []
    leaf = Leaf()
    owner.extend((owner, leaf))
    earlier = weakref.ref(leaf, lambda ref: observe(ref, 1))
    later = weakref.ref(leaf, lambda ref: observe(ref, 2))
    weakref.ref(leaf, lambda ref: events.append(3))
    return earlier, later


def observe(ref, number):
    assert ref() is None
    assert earlier() is None and later() is None
    events.append(number)


earlier, later = arrange()
gc.collect()
print(f"mixed {''.join(str(x) for x in events if x != 3)} {len(finalized)} {events.count(3)}")
