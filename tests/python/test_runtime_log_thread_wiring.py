"""Execute actual runtime bodies with checked allocation/lock/TLS models.

These models check lifecycle instrumentation, not native concurrency or GC.
"""

from __future__ import annotations

import __future__
import ast
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"


def _namespace(filename):
    tree = ast.parse((RUNTIME / filename).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    for function in functions:
        function.decorator_list = []
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {"null": lambda: None, "ptr_is_null": lambda value: value is None}
    exec(compile(module, filename, "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def _logger(tokens):
    namespace = _namespace("py_runtime_log.py")
    state = {"pcc_log_init_state": 2, "pcc_diagnostics_runtime_log_fast_state": 1}
    registrations = []
    namespace.update(
        cstr=lambda text: text.encode() + b"\0",
        strlen=lambda text: len(text.split(b"\0", 1)[0]),
        load_i8=lambda pointer, offset: pointer[offset],
        ptr_add=lambda pointer, offset: pointer[offset:],
        global_addr=lambda name: name,
        load_i32=lambda name, offset: state[name],
        atomic_load_i32=lambda name, offset, order: state[name],
        pcc_current_thread_id=lambda: registrations.append("registered"),
    )
    state["pcc_log_mask"] = namespace["_parse_tokens"](tokens)
    return namespace, state, registrations


@pytest.mark.parametrize(
    "tokens,enabled",
    [(None, False), (b"\0", False), (b"gc\0", False), (b"threaded\0", False),
     (b"thread\0", True), (b"gc, thread\0", True), (b"thread, gc\0", True),
     (b"all\0", True), (b"gc,all\0", True), (b"1\0", True)],
)
def test_thread_category_filter_uses_existing_token_rules(tokens, enabled):
    namespace, _, registrations = _logger(tokens)
    assert bool(namespace["_code_enabled"](9)) is enabled
    assert bool(namespace["pcc_diagnostics_runtime_log_enabled"](b"thread\0")) is enabled
    assert namespace["_category_mask"](b"thread\0") == 256
    assert namespace["_category_from_code"](9) == b"thread\0"
    assert len(registrations) == 2


def test_disabled_coded_event_neither_initializes_nor_registers_or_writes():
    namespace, state, registrations = _logger(b"thread\0")
    state["pcc_diagnostics_runtime_log_fast_state"] = 0
    state["pcc_log_init_state"] = 1
    namespace["_init_once"] = lambda: pytest.fail("disabled event initialized logger")
    namespace["pcc_diagnostics_runtime_log_event"] = lambda *args: pytest.fail("disabled event wrote")
    namespace["pcc_diagnostics_runtime_log_event_code"](9, 1, 0, 0, None)
    assert registrations == []


def test_thread_events_have_stable_named_local_integer_codes():
    namespace, _, _ = _logger(None)
    names = ["start", "enter", "exit", "join", "joined", "start_failed", "join_failed", "detach"]
    for code, name in enumerate(names, 1):
        assert namespace["_event_from_code"](9, code) == name.encode() + b"\0"
    assert namespace["_event_from_code"](9, 999) == b"thread_event\0"
    tree = ast.parse((RUNTIME / "freestanding_thread_kernel_pthread.py").read_text())
    local_codes = {}
    for function in tree.body:
        if isinstance(function, ast.FunctionDef):
            for node in function.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id.startswith("thread_") and node.target.id.endswith("_event"):
                        local_codes[node.target.id] = ast.literal_eval(node.value)
    assert local_codes == {"thread_" + name + "_event": code for code, name in enumerate(names, 1)}


class KernelModel:
    def __init__(self, allocation_failure=0, create_status=0, join_status=0):
        self.namespace = _namespace("freestanding_thread_kernel_pthread.py")
        self.memory = {}
        self.allocated = set()
        self.freed = set()
        self.allocation_count = 0
        self.allocation_failure = allocation_failure
        self.create_status = create_status
        self.join_status = join_status
        self.locked = False
        self.registered = True
        self.events = []
        self.actions = []
        self.start_record = None
        self.namespace.update(
            malloc=self.allocate, free=self.free, pcc_mutex_new=lambda: self.allocate(1),
            pcc_mutex_free=self.free, load_ptr=self.load, load_i32=self.load,
            store_ptr=self.store, store_i32=self.store,
            pcc_mutex_lock=self.lock, pcc_mutex_unlock=self.unlock,
            function_addr=lambda name: name, pthread_create=self.create,
            pthread_join=self.join, pthread_detach=lambda handle: 0,
            stack_alloc=lambda size: "stack", call_ptr1=self.callback,
            pcc_current_thread_id=self.register,
            pcc_thread_unregister_current=self.unregister,
            pcc_thread_safepoint=self.safepoint,
            pcc_diagnostics_runtime_log_event_code=self.log,
            pcc_platform_abort=lambda: pytest.fail("unexpected abort"),
            sched_yield=lambda: pytest.fail("unexpected wait"),
        )

    def allocate(self, size):
        self.allocation_count += 1
        if self.allocation_count == self.allocation_failure:
            return None
        pointer = self.allocation_count
        self.allocated.add(pointer)
        return pointer

    def free(self, pointer):
        assert pointer in self.allocated and pointer not in self.freed
        self.freed.add(pointer)
        self.actions.append(("free", pointer))

    def load(self, pointer, offset):
        assert pointer not in self.freed
        return self.memory[pointer, offset]

    def store(self, pointer, offset, value):
        assert pointer not in self.freed
        self.memory[pointer, offset] = value

    def lock(self, pointer):
        assert pointer not in self.freed and not self.locked
        self.locked = True
        return 0

    def unlock(self, pointer):
        assert pointer not in self.freed and self.locked
        self.locked = False
        return 0

    def register(self):
        self.registered = True
        self.actions.append(("register",))
        return 1

    def unregister(self):
        assert self.registered and not self.locked
        self.registered = False
        self.actions.append(("unregister",))

    def safepoint(self):
        assert self.registered and not self.locked
        self.actions.append(("safepoint",))

    def log(self, category, event, status, reserved, pointer):
        assert category == 9 and reserved == 0
        assert self.registered and not self.locked and pointer not in self.freed
        self.events.append((event, status, pointer))
        self.actions.append(("log", event))

    def create(self, handle, attributes, entry, start):
        self.actions.append(("create",))
        self.start_record = start
        self.store(handle, 0, 1000)
        return self.create_status

    def callback(self, entry, arg):
        assert entry == "callback" and arg == "argument"
        self.actions.append(("callback",))
        return 77

    def join(self, native_handle, out):
        assert native_handle == 1000
        self.store(out, 0, 77)
        return self.join_status

    def start(self):
        return self.namespace["pcc_thread_start"]("out", "callback", "argument")

    def run_child(self):
        self.registered = False
        result = self.namespace["_thread_trampoline"](self.start_record)
        assert self.actions[-1] == ("unregister",)
        assert not self.registered
        self.registered = True  # Restore the separately registered caller.
        return result


@pytest.mark.parametrize("failed_allocation,status", [(1, -2), (2, -3), (3, -4)])
def test_start_allocation_failure_logs_after_cleanup(failed_allocation, status):
    model = KernelModel(allocation_failure=failed_allocation)
    assert model.start() == -1
    assert model.events == [(6, status, None)]
    assert model.freed == model.allocated
    assert ("create",) not in model.actions
    assert model.actions[-1] == ("log", 6)


def test_platform_create_failure_preserves_status_and_disposes_resources():
    model = KernelModel(create_status=11)
    assert model.start() == -1
    assert model.events == [(1, 0, 1), (6, 11, 1)]
    assert model.actions[:3] == [("log", 1), ("create",), ("log", 6)]
    assert model.freed == model.allocated


def test_invalid_start_join_and_null_detach_have_no_allocations():
    model = KernelModel()
    assert model.namespace["pcc_thread_start"](None, "callback", "argument") == -1
    assert model.namespace["pcc_thread_start"]("out", None, "argument") == -1
    assert model.namespace["pcc_thread_join"](None, "result") == -1
    model.namespace["pcc_thread_detach"](None)
    assert model.events == [(6, -1, None), (6, -1, None), (7, -1, None)]
    assert model.allocated == set()


@pytest.mark.parametrize("join_status,last_event", [(0, 5), (22, 7)])
def test_join_logs_outside_locks_before_disposal_and_never_after_unregister(join_status, last_event):
    model = KernelModel(join_status=join_status)
    assert model.start() == 0
    handle = model.load("out", 0)
    assert model.run_child() == 77
    assert model.namespace["pcc_thread_join"](handle, "result") == (0 if join_status == 0 else -1)
    assert model.load("result", 0) == 77
    assert model.events == [(1, 0, handle), (2, 0, handle), (3, 0, handle),
                            (4, 0, handle), (last_event, join_status, handle)]
    assert model.freed == model.allocated


@pytest.mark.parametrize("detach_before_return", [True, False])
def test_detach_logs_before_either_disposal_order(detach_before_return):
    model = KernelModel()
    assert model.start() == 0
    handle = model.load("out", 0)
    if detach_before_return:
        model.namespace["pcc_thread_detach"](handle)
    assert model.run_child() == 77
    if not detach_before_return:
        model.namespace["pcc_thread_detach"](handle)
    assert [event for event, _, _ in model.events] == ([1, 8, 2, 3] if detach_before_return else [1, 2, 3, 8])
    assert model.freed == model.allocated


def test_instrumentation_is_confined_to_lock_free_lifecycle_boundaries():
    tree = ast.parse((RUNTIME / "freestanding_thread_kernel_pthread.py").read_text())
    instrumented = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        if any(isinstance(node.func, ast.Name) and node.func.id == "_thread_log" for node in calls):
            instrumented.add(function.name)
    assert instrumented == {"_thread_trampoline", "pcc_thread_start", "pcc_thread_join", "pcc_thread_detach"}
