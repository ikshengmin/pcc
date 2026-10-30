"""Draft native Python regression; compile/run with the candidate threaded archive."""
import gc
import weakref
from pcc.extern import extern, c_int64

graph_depth = extern("pcc_py_gc_minor_graph_lock_depth", (), c_int64)
events = []
rescued = []
holder = []

def weak_dead(reference):
    events.append("weak")

class Item:
    def __init__(self, name, rescue):
        self.name = name
        self.rescue = rescue
    def __del__(self):
        assert graph_depth() == 0
        events.append("del:" + self.name)
        # Reentrant mutation must run after publication and outer unlock.
        holder.append("callback:" + self.name)
        if self.rescue:
            rescued.append(self)
        gc.collect()

def main():
    global holder
    value = Item("plain", False)
    reference = weakref.ref(value, weak_dead)
    holder = [value]
    value = None
    holder[-1] = "replacement"
    gc.collect()
    assert reference() is None and events == ["del:plain", "weak"]
    assert holder == ["replacement", "callback:plain"]
    events.clear()

    value = Item("rescue", True)
    reference = weakref.ref(value, weak_dead)
    holder = [value]
    value = None
    holder[0] = "replacement"
    gc.collect()
    assert events == ["del:rescue"] and reference() is rescued[0]
    assert holder == ["replacement", "callback:rescue"]
    rescued.clear()
    gc.collect()
    assert reference() is None and events == ["del:rescue", "weak"]

    for index in (2, -3):
        try:
            holder[index] = "must not store"
        except IndexError:
            pass
        else:
            raise AssertionError("missing IndexError")
    assert holder == ["replacement", "callback:rescue"]
    print("GC0_LIST_STORE_FINALIZER_OK")

main()
