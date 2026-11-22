"""Reference order for chain disposal and revival of a same-layer sibling."""
import gc
import sys
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
indirect = sys.argv[1] in ("holder", "capi-list")
rescue = sys.argv[1] in ("rescue", "caller", "holder", "mixed-rescue", "pin", "interior", "dict-holder", "field-holder", "dict-replace", "list-replace", "set-holder", "dict-update", "list-slice", "set-update", "capi-dict", "capi-list", "capi-attr")
withdrawn = sys.argv[1] == "withdrawn"
mixed = sys.argv[1] in ("mixed", "mixed-tail", "mixed-rescue")
gc.disable()


class A:
    def __del__(self):
        global held
        events.append("a-del")
        if late_new:
            late_refs.append(weakref.ref(weak_b(), lambda _: events.append("b-new")))
        if indirect:
            holder.append(weak_b())
        elif sys.argv[1] in ("dict-holder", "dict-replace", "capi-dict"):
            dict_holder[7] = weak_b()
        elif sys.argv[1] in ("field-holder", "capi-attr"):
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
if sys.argv[1] in ("mixed-tail", "pin"):
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
