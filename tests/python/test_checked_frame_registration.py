"""Checked root-registration host fault model; not native collector execution."""
import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_frame_registry.py"


class RegistryModel:
    def __init__(self, *, depth=1, disabled=False, tracking=True, failure=None, previous=0):
        self.depth = depth
        self.disabled = disabled
        self.tracking = tracking
        self.failure = failure
        self.current = previous
        self.locks = 0
        self.entered = 0
        self.events = []
        self.node = {0: 100, 8: 200, 40: 3}
        namespace = {
            "i64": int,
            "pcc_thread_no_park_depth": lambda: self.depth,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_eq": lambda left, right: int(left == right),
            "pcc_gc_root_slot_count_from_map": lambda pointer: 0 if pointer == 99 else 3,
            "pcc_gc_frame_roots_disabled_fast": lambda: self.disabled,
            "gc_backend_current": lambda: 4,
            "pcc_gc_should_track_frame_roots": lambda: self.tracking,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_gc_frame_index_find": self.find,
            "pcc_gc_note_frame_enter": self.enter,
            "load_ptr": self.load,
            "load_i64": self.load,
            "pcc_platform_abort": lambda: (_ for _ in ()).throw(RuntimeError("corrupt registration")),
        }
        node = next(node for node in ast.parse(SOURCE.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_note_frame_enter_checked")
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
        self.run = namespace[node.name]
        self.namespace = namespace

    def lock(self):
        assert self.depth > 0
        assert self.locks == 0
        self.locks += 1
        self.events.append("lock")

    def unlock(self):
        assert self.locks == 1
        self.locks -= 1
        self.events.append("unlock")

    def find(self, slots):
        assert slots == 200 and self.locks == 1
        return self.current

    def load(self, node, offset):
        assert node == 300 and self.locks == 1
        return self.node[offset]

    def enter(self, frame_map, slots):
        assert (frame_map, slots) == (100, 200)
        assert self.depth > 0 and self.locks == 0
        self.entered += 1
        # The real entry's failures have the same externally visible shape:
        # node allocation, plan capacity, plan allocation, insertion failure.
        if self.failure is None:
            self.current = 300
        self.events.append("enter")


def test_checked_registration_preserves_outer_guard_and_verifies_node():
    model = RegistryModel()
    assert model.run(100, 200) == 1
    assert model.depth == 1 and model.locks == 0 and model.entered == 1
    assert model.events == ["lock", "unlock", "enter", "lock", "unlock"]


def test_registration_failure_is_observable_before_outer_guard_release():
    model = RegistryModel(failure="registration")
    assert model.run(100, 200) == 0
    assert model.depth == 1 and model.locks == 0 and model.current == 0


def test_old_indexed_frame_cannot_mask_failed_new_registration():
    model = RegistryModel(previous=300)
    assert model.run(100, 200) == 0
    assert model.entered == 0 and model.current == 300 and model.locks == 0


@pytest.mark.parametrize("depth", [0, -1])
def test_no_unprotected_registration_or_parking(depth):
    model = RegistryModel(depth=depth)
    assert model.run(100, 200) == -1
    assert not model.events and model.entered == 0


@pytest.mark.parametrize("frame_map,slots", [(0, 200), (100, 0), (99, 200)])
def test_invalid_slots_or_map_do_not_register(frame_map, slots):
    model = RegistryModel()
    assert model.run(frame_map, slots) == 0
    assert not model.events and model.entered == 0


@pytest.mark.parametrize("disabled,tracking", [(True, True), (False, False)])
def test_deliberately_disabled_tracking_is_not_allocation_failure(disabled, tracking):
    model = RegistryModel(disabled=disabled, tracking=tracking)
    assert model.run(100, 200) == 1
    assert not model.events and model.entered == 0


@pytest.mark.parametrize("offset,value", [(0, 999), (8, 999), (40, 999)])
def test_corrupt_index_is_not_reported_as_success_or_ordinary_failure(offset, value):
    model = RegistryModel()
    model.node[offset] = value
    with pytest.raises(RuntimeError, match="corrupt registration"):
        model.run(100, 200)
    assert model.locks == 0 and model.depth == 1


def test_checked_entry_emits_raw_status_abi_without_managed_prologue(tmp_path):
    import re
    from pcc.frontends.python.pipeline import compile_python

    output = tmp_path / "checked_frame.ll"
    compile_python(str(SOURCE), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode="off", backend="self", target_triple="x86_64-unknown-linux-gnu")
    text = output.read_text()
    entry = re.search(
        r"^define (?:external )?i64 @pcc_gc_note_frame_enter_checked\(ptr [^,]+, ptr [^)]+\).*?^}",
        text, re.M | re.S,
    )
    assert entry is not None
    body = entry.group()
    assert "@pcc_thread_no_park_depth" in body
    assert "@pcc_gc_frame_index_find" in body
    assert "@pcc_gc_note_frame_enter(" in body
    assert "@pcc_gc_frame_enter(" not in body
    assert "@py_" not in body
    assert "@pcc_thread_stop_requested" not in body


class ActualEntryModel(RegistryModel):
    """Execute both production entry bodies, injecting raw allocator failures."""
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.globals = {"pcc_gc_frame_head": 0}
        self.memory = {300: self.node, 400: {}, 500: {}}
        self.released = []
        self.freed = []
        self.parked = False
        self.namespace.update({
            "null": lambda: 0,
            "pcc_thread_no_park_enter": self.enter_guard,
            "pcc_thread_no_park_exit": self.exit_guard,
            "pcc_gc_frame_node_create": self.create,
            "pcc_gc_frame_node_release": self.released.append,
            "pcc_gc_frame_node_unlink": lambda node: self.globals.update(pcc_gc_frame_head=0),
            "pcc_gc_frame_index_plan_capacity": lambda count: -1 if failure == "capacity" else (1 if failure == "allocation" else 0),
            "pcc_gc_index_slot_size": lambda: 24,
            "malloc": lambda size: 0 if failure == "allocation" else 500,
            "free": self.freed.append,
            "memset": lambda base, value, size: None,
            "stack_alloc": lambda size: 400,
            "global_load_ptr": lambda name: self.globals[name],
            "global_store_ptr": lambda name, value: self.globals.__setitem__(name, value),
            "global_addr": lambda name: name,
            "load_i32": lambda base, offset: 0,
            "store_ptr": lambda base, offset, value: self.memory[base].__setitem__(offset, value),
            "pcc_gc_frame_index_replace_preallocated": self.replace,
            "pcc_gc_root_registry_note_mutation_locked": lambda: None,
            "pcc_gc_cycle_requested_store_release": lambda value: None,
        })
        node = next(node for node in ast.parse(SOURCE.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_note_frame_enter")
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def enter_guard(self):
        self.depth += 1

    def exit_guard(self):
        self.depth -= 1
        if self.depth == 0:
            self.parked = True

    def create(self, frame_map, slots, count, flags):
        assert self.depth == 2
        self.entered += 1
        return 0 if self.failure == "node" else 300

    def replace(self, slots, node):
        assert self.depth == 2 and self.locks == 1
        if self.failure == "insertion":
            return node
        self.current = node
        return 0


@pytest.mark.parametrize("failure", [None, "node", "capacity", "allocation", "insertion"])
def test_checked_entry_runs_real_registration_failure_paths_without_parking(failure):
    model = ActualEntryModel(failure)
    assert model.run(100, 200) == (1 if failure is None else 0)
    assert model.depth == 1 and model.locks == 0 and not model.parked
    assert model.current == (300 if failure is None else 0)
    assert model.globals["pcc_gc_frame_head"] == (300 if failure is None else 0)
    assert model.released == ([300] if failure in {"capacity", "allocation", "insertion"} else [])
