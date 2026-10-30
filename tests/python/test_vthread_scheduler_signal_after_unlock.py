"""A wakeup requested in a scheduler critical section is sent after unlock.

Enqueueing a ready thread signals the scheduler condition for a sleeping
carrier. Signalling while holding the mutex kept the system call inside the
critical section, and the woken carrier then contended with the signaller
for the lock. The request is recorded under the mutex and the holder signals
once it has released it; the carriers' timed idle wait bounds any delay.
"""

import ast
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[2]
PENDING = "pcc_vthread_scheduler_signal_pending_py"


class Model:
    def __init__(self, *, unlock_status=0):
        self.memory = {PENDING: 0}
        self.events = []
        self.unlock_status = unlock_status
        self.namespace = {
            "global_addr": lambda name: name,
            "load_i64": lambda slot, offset: self.memory[slot],
            "store_i64": self.store,
            "ptr_is_null": lambda pointer: pointer == 0,
            "_scheduler_mutex": lambda: "mutex",
            "_scheduler_cond": lambda: "cond",
            "pcc_mutex_unlock": self.unlock,
            "pcc_cond_signal": lambda cond: self.events.append(("signal", cond)),
            "pcc_platform_abort": lambda: self.events.append(("abort",)),
            "pcc_thread_scheduler_lock_released": lambda: self.events.append(("released",)),
            "pcc_thread_no_park_exit": lambda: self.events.append(("no_park_exit",)),
            "pcc_diagnostics_runtime_log_event_code": lambda *args: None,
        }
        path = ROOT / "pcc/runtime/py/py_virtual_thread_runtime.py"
        names = ("_scheduler_signal", "_scheduler_unlock")
        nodes = [node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert len(nodes) == len(names)
        code = compile(ast.Module(nodes, type_ignores=[]), str(path), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name in names:
                self.namespace[const.co_name] = types.FunctionType(const, self.namespace)

    def store(self, slot, offset, value):
        assert offset == 0
        self.memory[slot] = value

    def unlock(self, mutex):
        # The request is consumed while the mutex is still held.
        self.events.append(("unlock", mutex, self.memory[PENDING]))
        return self.unlock_status

    def run(self, *names):
        for name in names:
            self.namespace[name]()
        return self.events


def test_requested_wakeup_is_signalled_after_the_mutex_is_released():
    model = Model()
    assert model.run("_scheduler_signal") == []
    assert model.memory[PENDING] == 1
    assert model.run("_scheduler_unlock") == [
        ("unlock", "mutex", 0), ("signal", "cond"), ("released",), ("no_park_exit",),
    ]
    assert model.memory[PENDING] == 0


def test_repeated_requests_in_one_critical_section_signal_once():
    events = Model().run("_scheduler_signal", "_scheduler_signal", "_scheduler_unlock")
    assert [event for event in events if event[0] == "signal"] == [("signal", "cond")]


def test_unlock_without_a_request_does_not_signal():
    assert Model().run("_scheduler_unlock") == [
        ("unlock", "mutex", 0), ("released",), ("no_park_exit",),
    ]


def test_failed_unlock_aborts_without_signalling():
    events = Model(unlock_status=-1).run("_scheduler_signal", "_scheduler_unlock")
    assert events == [("unlock", "mutex", 0), ("abort",)]
