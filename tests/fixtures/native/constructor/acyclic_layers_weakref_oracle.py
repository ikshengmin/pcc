"""Reference order for chain disposal and revival of a same-layer sibling."""
import sys
import weakref

events = []
held = None
holder = []
indirect = sys.argv[1] == 'holder'
rescue = sys.argv[1] in ("rescue", "caller", "holder")
withdrawn = sys.argv[1] == "withdrawn"


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


b = B()
a = A()
if not rescue:
    a.child = b
weak_a = weakref.ref(a, lambda _: events.append("a-weak"))
weak_b = weakref.ref(b, lambda _: events.append("b-weak"))
if rescue:
    del a
    del b
else:
    del b
    del a
events.append("b-dead" if weak_b() is None else "b-live")
print(" ".join(events))
