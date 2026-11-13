"""pcc_gc_note_root_slot_barrier for runtime-source models.

The _adopt helpers of the slot-frame runtime call this barrier once a
producer published its result in a root slot. Outside container mode it
rereads and barriers the slot under the graph lock, as those helpers did
inline; the models that execute those helpers keep exercising that locked
protocol through their own lock, barrier and load mocks.
"""
from __future__ import annotations


def locked_root_slot_barrier(namespace):
    def barrier(slot):
        namespace["pcc_py_gc_minor_graph_lock"]()
        namespace["pcc_gc_note_slot_write_barrier"](
            namespace["null"](), slot, namespace["load_ptr"](slot, 0)
        )
        namespace["pcc_py_gc_minor_graph_unlock"]()
    return barrier


def install_locked_root_slot_barrier(namespace):
    namespace["pcc_gc_note_root_slot_barrier"] = locked_root_slot_barrier(namespace)
    return namespace
