"""Reference order for chain disposal and revival of a same-layer sibling."""
import gc
import sys
import threading
import weakref

events = []
held = None
holder = []
dict_holder = {7: None} if sys.argv[1] == 'dict-replace' else {}
list_holder = [None]
set_holder = set()


class FieldHolder:
    pass


field_holder = FieldHolder()
late_refs = []
late_new = sys.argv[1] == "late-new"
indirect = sys.argv[1] in ("holder", "capi-list", "caller-old-holder")
rescue = sys.argv[1] in ("rescue", "caller", "holder", "mixed-rescue", "pin", "pin-reassert", "pin-transfer", "interior", "dict-holder", "field-holder", "dict-replace", "list-replace", "set-holder", "dict-update", "list-slice", "set-update", "capi-dict", "capi-list", "capi-attr", "extension-field", "caller-old-holder")
withdrawn = sys.argv[1] in ("withdrawn", "pin-withdraw")
mixed = sys.argv[1] in ("mixed", "mixed-tail", "mixed-rescue")
gc.disable()
same_layer_peer = sys.argv[1] in ("peer-hold", "peer-depart")
peer_new = sys.argv[1] == "peer-new"
peer_mode = same_layer_peer or peer_new
peer_request = threading.Event()
peer_read = threading.Event()
peer_done = threading.Event()


def weakref_peer():
    peer_request.wait()
    borrowed = weak_b()
    assert borrowed is not None
    if peer_new:
        late_refs.append(weakref.ref(borrowed, lambda _: events.append("b-new")))
    peer_read.set()
    if sys.argv[1] == "peer-hold":
        peer_done.wait()
        assert weak_b() is borrowed


class A:
    def __del__(self):
        global held
        events.append("a-del")
        if peer_mode:
            peer_request.set()
            peer_read.wait()
            if sys.argv[1] != "peer-hold":
                peer.join()
        if late_new:
            late_refs.append(weakref.ref(weak_b(), lambda _: events.append("b-new")))
        if indirect:
            holder.append(weak_b())
        elif sys.argv[1] in ("dict-holder", "dict-replace", "capi-dict"):
            dict_holder[7] = weak_b()
        elif sys.argv[1] in ("field-holder", "capi-attr", "extension-field"):
            field_holder.child = weak_b()
        elif sys.argv[1] == "list-replace":
            list_holder[0] = weak_b()
        elif sys.argv[1] == "set-holder":
            set_holder.add(weak_b())
        elif sys.argv[1] == "dict-update":
            dict_holder.update(self.child)
        elif sys.argv[1] == "list-slice":
            list_holder[:] = self.child
        elif sys.argv[1] == "set-update":
            set_holder.update(self.child)
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
if sys.argv[1] == "dict-update":
    a.child = {7: b}
elif sys.argv[1] in ("list-slice", "set-update"):
    a.child = [b]
if sys.argv[1] in ("mixed-tail", "pin", "pin-reassert", "pin-transfer"):
    b.tail = Tail()
if mixed:
    b.child = b
elif not rescue and not same_layer_peer:
    a.child = b
weak_a = weakref.ref(a, lambda _: events.append("a-weak"))
weak_b = weakref.ref(b, lambda _: events.append("b-weak"))
if peer_mode:
    peer = threading.Thread(target=weakref_peer)
    peer.start()
if sys.argv[1] == "prior-live":
    held = weak_b()
    assert held is b
if rescue or same_layer_peer:
    del a
    del b
else:
    del b
    del a
if mixed:
    gc.collect()
events.append("b-dead" if weak_b() is None else "b-live")
print(" ".join(events))

if sys.argv[1] == "peer-hold":
    peer_done.set()
    peer.join()
