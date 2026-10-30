"""A frame's slots are linked as roots before its entry can service a stop.

Until the registry links the frame node, nothing tells a collector about the
values its slots hold; a moving collector run then could relocate or free
them (the encoder's borrowed entry frame was a recorded instance). The graph
lock's contended path polls for a stop before it queues, so frame entry must
stay unparkable until the link. The registry source runs under CPython with a
graph lock that services a pending stop whenever the caller may park.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "pcc/runtime/py/freestanding_gc_frame_registry.py"
NAMES = ("pcc_gc_note_frame_enter", "pcc_gc_note_frame_enter_lifo")


def _code_objects(source: str) -> dict:
    tree = ast.parse(source)
    tree.body = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    module = compile(tree, str(REGISTRY), "exec")
    return {const.co_name: const for const in module.co_consts
            if isinstance(const, types.CodeType)}


class _Registry:
    def __init__(self, source: str):
        self.events = []
        self.no_park = 0
        self.stop_pending = True
        self.globals = {"pcc_gc_frame_head": None}
        names = {
            "ptr_is_null": lambda value: int(value is None),
            "ptr_eq": lambda left, right: int(left is right),
            "null": lambda: None,
            "pcc_gc_frame_roots_disabled_fast": lambda: 0,
            "gc_backend_current": lambda: 3,
            "pcc_gc_should_track_frame_roots": lambda: 1,
            "pcc_gc_root_slot_count_from_map": lambda frame_map: 2,
            "pcc_gc_frame_node_create": lambda frame_map, slots, count, flags: {"slots": slots},
            "pcc_thread_no_park_enter": self.no_park_enter,
            "pcc_thread_no_park_exit": self.no_park_exit,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": lambda: self.events.append("unlock"),
            "pcc_gc_frame_index_plan_capacity": lambda count: 0,
            "stack_alloc": lambda size: {},
            "global_load_ptr": lambda name: self.globals[name],
            "global_store_ptr": self.store_global,
            "global_addr": lambda name: name,
            "store_ptr": lambda base, offset, value: (
                base.__setitem__(offset, value) if isinstance(base, dict) else None),
            "load_i32": lambda base, offset: 0,
            "pcc_gc_root_registry_note_mutation_locked": lambda: None,
            "pcc_gc_frame_index_replace_preallocated": lambda slots, node: None,
            "pcc_gc_cycle_requested_store_release": lambda value: None,
            "free": lambda pointer: None,
        }
        codes = _code_objects(source)
        for name, code in codes.items():
            names[name] = types.FunctionType(code, names)
        self.functions = names

    def no_park_enter(self):
        self.no_park += 1

    def no_park_exit(self):
        self.no_park -= 1
        if self.no_park == 0:
            self.poll()

    def poll(self):
        if self.stop_pending and self.no_park == 0:
            self.events.append("park")
            self.stop_pending = False

    def lock(self):
        # Contended acquisition: service a pending stop, then queue and own.
        self.poll()
        self.events.append("lock")

    def store_global(self, name, value):
        self.globals[name] = value
        if name == "pcc_gc_frame_head":
            self.events.append("link")


@pytest.mark.parametrize("entry", ["pcc_gc_note_frame_enter", "pcc_gc_note_frame_enter_lifo"])
def test_frame_entry_links_before_servicing_a_stop(entry):
    registry = _Registry(REGISTRY.read_text())
    registry.functions[entry]("frame map", "slots")
    assert registry.events.index("link") < registry.events.index("park"), registry.events
    assert registry.no_park == 0
    assert registry.globals["pcc_gc_frame_head"]["slots"] == "slots"


@pytest.mark.parametrize("entry", ["pcc_gc_note_frame_enter", "pcc_gc_note_frame_enter_lifo"])
def test_untracked_frames_skip_the_unparkable_scope(entry):
    registry = _Registry(REGISTRY.read_text())
    registry.functions["pcc_gc_should_track_frame_roots"] = lambda: 0
    registry.functions[entry]("frame map", "slots")
    assert registry.events == []
    assert registry.globals["pcc_gc_frame_head"] is None
