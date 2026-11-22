"""Reference order for chain disposal and revival of a same-layer sibling."""
import gc
import sys
import weakref

events = []
held = None
holder = []
indirect = sys.argv[1] == 'holder'
rescue = sys.argv[1] in ("rescue", "caller", "holder", "mixed-rescue")
withdrawn = sys.argv[1] == "withdrawn"
mixed = sys.argv[1] in ("mixed", "mixed-tail", "mixed-rescue")
gc.disable()


class A:
    def __del__(self):
        global held
        events.append("a-del")
        if indirect:
            holder.append(weak_b())
        elif rescue or withdrawn:
            held = weak_b()
            if withdrawn:
                held = None


class B:
    def __del__(self):
        events.append("b-del")


class Tail:
    def __del__(self):
        events.append("tail-del")


b = B()
a = A()
if sys.argv[1] == "mixed-tail":
    b.tail = Tail()
if mixed:
    b.child = b
elif not rescue:
    a.child = b
weak_a = weakref.ref(a, lambda _: events.append("a-weak"))
weak_b = weakref.ref(b, lambda _: events.append("b-weak"))
if rescue:
    del a
    del b
else:
    del b
    del a
if mixed:
    gc.collect()
events.append("b-dead" if weak_b() is None else "b-live")
print(" ".join(events))
