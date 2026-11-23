"""Exact finalizer/weak callback order for an independently observed chain."""

import gc
import sys
import weakref


def trace_chain(n):
    events = []

    class Node:
        def __init__(self, index):
            self.index = index
            self.child = None

        def __del__(self):
            events.append(("D", self.index))

    def make_chain():
        refs = []
        previous = None
        for index in range(n):
            node = Node(index)
            if previous is not None:
                previous.child = node
            else:
                head = node
            refs.append(weakref.ref(node, lambda _, i=index: events.append(("W", i))))
            previous = node
        return head, refs

    head, refs = make_chain()
    del head
    gc.collect()
    assert all(ref() is None for ref in refs)
    expected = [(kind, i) for i in range(n) for kind in ("D", "W")]
    assert events == expected, events
    return "".join(kind for kind, _ in events)


if __name__ == "__main__":
    print(trace_chain(int(sys.argv[1])))
