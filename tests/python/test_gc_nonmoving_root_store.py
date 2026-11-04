"""Execute GC1/2 root publication with the actual store/shader bodies.

Contract: docs/project-intent.md, shared root lifetime and five collectors.
Kernel stop admission, raw atomics and index membership are the primitive
boundary here; native tests separately exercise the compiled runtime.
"""
import ast
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WHITE, GRAY, BLACK, DYING = 8, 16, 32, 524288


def load_functions(namespace, filename, names):
    path = RUNTIME / filename
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(nodes, type_ignores=[]), str(path), "exec"), namespace)


class RootStore:
    def __init__(self, backend, active=1, flags=WHITE):
        self.slot, self.obj = 0x1000, 0x2000
        # The old root is intentionally not dereferenceable. A count-free
        # store must not read its header just to release no ownership.
        self.words = {(self.slot, 0): 0xBAD0, (self.obj, 12): flags}
        self.globals = {
            "pcc_gc_refcount_atomic": 0,
            "pcc_gc_backend4_no_refcounts": 1,
            "pcc_diagnostics_runtime_log_fast_state": 0,
            "pcc_gc_refcount_provenance_probe": 0,
            "pcc_gc_mark_active": active,
        }
        self.depth = self.gray = self.notes = self.lookups = 0
        self.events = []
        self.stopped_roots = []
        self.ns = {
            "i64": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "global_addr": lambda name: name,
            "load_i32": self.read, "load_i64": self.read,
            "load_ptr": self.read, "store_ptr": self.write,
            "store_i32": self.write, "store_i64": self.write,
            "atomic_load_i32": lambda base, offset, order: self.read(base, offset),
            "atomic_cas_i32": self.cas,
            "ptr_is_null": lambda value: int(value == 0),
            "is_tagged_int": lambda value: value & 1,
            "null": lambda: 0, "_gc_backend_fast": lambda: backend,
            "pcc_thread_no_park_enter": self.enter,
            "pcc_thread_no_park_exit": self.exit,
            "pcc_gc_note_store": self.note,
            "pcc_gc_object_index_seen_stable": self.member,
            "pcc_gc_gray_count_increment_acq_rel": self.increment,
            "pcc_gc_gray_count_decrement_acq_rel": self.decrement,
        }
        for name in ("pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_unlock",
                     "py_incref", "py_decref", "pcc_gc_load_ptr", "stack_alloc"):
            self.ns[name] = lambda *args, name=name: pytest.fail("unexpected " + name)
        load_functions(self.ns, "freestanding_gc_root_operations.py", {"pcc_gc_shade_gray_atomic"})
        load_functions(self.ns, "py_obj.py", {
            "_store_root_nonmoving_tracing", "pcc_gc_store_root", "pcc_gc_store_root_take",
        })

    def read(self, base, offset):
        if isinstance(base, str):
            return self.globals[base]
        assert (base, offset) in self.words, "invalid old/foreign header read"
        return self.words[base, offset]

    def write(self, base, offset, value):
        assert self.depth == 1
        self.words[base, offset] = value
        self.events.append("store")

    def cas(self, base, offset, expected, desired, success, failure):
        assert self.depth == 1
        old = self.read(base, offset)
        if old == expected:
            self.words[base, offset] = desired
        self.events.append("shade")
        return old

    def enter(self):
        assert self.depth == 0
        self.depth = 1
        self.events.append("enter")

    def exit(self):
        assert self.depth == 1
        # A pending stop is admitted only here, after slot publication and
        # all reserved gray credit has been committed or returned.
        self.depth = 0
        self.events.append("exit")
        self.stopped_roots.append((self.words[self.slot, 0], self.gray))

    def member(self, obj):
        assert self.depth == 1
        self.lookups += 1
        return int(obj == self.obj)

    def note(self):
        assert self.depth == 1
        self.notes += 1

    def increment(self):
        assert self.depth == 1
        self.gray += 1

    def decrement(self):
        assert self.depth == 1 and self.gray > 0
        self.gray -= 1


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
@pytest.mark.parametrize("value_kind", ("object", "null", "tagged", "foreign"))
@pytest.mark.parametrize("active", (0, 1))
def test_root_publication_has_no_graph_or_refcount_protocol(backend, entry, value_kind, active):
    model = RootStore(backend, active)
    value = {"object": model.obj, "null": 0, "tagged": 123, "foreign": 0xCAFE}[value_kind]
    model.ns[entry](model.slot, value)
    shaded = int(active and value_kind == "object")
    assert model.words[model.slot, 0] == value
    assert model.notes == 1 and model.depth == 0 and model.gray == shaded
    assert model.stopped_roots == [(value, shaded)]
    assert model.events[0] == "enter" and model.events[-1] == "exit"
    if shaded:
        assert model.events.index("store") < model.events.index("shade")
    if not active or value_kind in ("null", "tagged"):
        assert model.lookups == 0
    assert model.globals["pcc_gc_mark_active"] == active


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("color", (GRAY, BLACK, WHITE | DYING))
def test_root_barrier_preserves_collector_color_policy(backend, color):
    model = RootStore(backend, flags=color)
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    shaded = int(backend == 2 and color == BLACK)
    assert model.gray == shaded
    assert model.words[model.obj, 12] == (GRAY if shaded else color)


@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
def test_null_root_address_has_no_effect(entry):
    model = RootStore(1)
    model.ns[entry](0, model.obj)
    assert not model.events and model.notes == model.lookups == 0


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("probe,logging", ((1, 0), (0, 1)))
def test_checked_or_logged_roots_keep_the_original_plan(backend, probe, logging):
    model = RootStore(backend)
    model.globals.update(pcc_gc_refcount_provenance_probe=probe,
                         pcc_diagnostics_runtime_log_fast_state=logging)
    events = []
    model.ns.update(
        stack_alloc=lambda size: 0x3000,
        pcc_gc_store_root_plan_init=lambda plan, selected: events.append(("init", selected)),
        pcc_py_gc_minor_graph_lock=lambda: events.append("lock"),
        pcc_gc_store_root_plan_commit_locked=lambda *args: events.append("commit"),
        pcc_py_gc_minor_graph_unlock=lambda: events.append("unlock"),
        pcc_gc_store_root_plan_finish=lambda plan: events.append("finish"),
    )
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    assert events == [("init", backend), "lock", "commit", "unlock", "finish"]
    assert not model.events
