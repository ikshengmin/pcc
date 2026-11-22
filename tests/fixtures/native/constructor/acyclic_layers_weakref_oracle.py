"""Reference order for chain disposal and revival of a same-layer sibling."""
import sys
import weakref

events = []
held = None
rescue = sys.argv[1] != "chain"


class A:
    def __del__(self):
        global held
        events.append("a-del")
        if rescue:
            held = weak_b()


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
