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
SLEEPERS = "pcc_vthread_sleepers_py"
READY = "pcc_vthread_ready_count_py"
STOP = "pcc_vthread_persistent_pool_stop_py"


class Model:
    def __init__(self, *, unlock_status=0, sleepers=1, ready=0, stop=0):
        self.memory = {PENDING: 0, SLEEPERS: sleepers, READY: ready, STOP: stop}
        self.events = []
        self.unlock_status = unlock_status
        self.namespace = {
            "global_addr": lambda name: name,
            "load_i64": lambda slot, offset: self.memory[slot],
            "store_i64": self.store,
            "_counter": lambda slot: self.memory[slot],
            "atomic_fence": lambda order: self.events.append(("fence", order)),
            "atomic_rmw_i64": self.atomic_add,
            "ptr_is_null": lambda pointer: pointer == 0,
            "_scheduler_mutex": lambda: "mutex",
            "_scheduler_cond": lambda: "cond",
            "_scheduler_lock": self.lock,
            "pcc_mutex_unlock": self.unlock,
            "pcc_cond_signal": lambda cond: self.events.append(("signal", cond)),
            "pcc_cond_timedwait_ms": self.wait,
            "pcc_platform_abort": lambda: self.events.append(("abort",)),
            "pcc_thread_scheduler_lock_released": lambda: self.events.append(("released",)),
            "pcc_thread_no_park_exit": lambda: self.events.append(("no_park_exit",)),
            "pcc_diagnostics_runtime_log_event_code": lambda *args: None,
        }
        path = ROOT / "pcc/runtime/py/py_virtual_thread_runtime.py"
        names = ("_scheduler_signal", "_scheduler_unlock", "_scheduler_signal_unlocked",
                 "_carrier_wait_locked")
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

    def atomic_add(self, operation, slot, offset, value, order):
        assert operation == "add" and slot == SLEEPERS and offset == 0
        assert order == "seq_cst"
        old = self.memory[slot]
        self.memory[slot] += value
        self.events.append(("sleepers", self.memory[slot]))
        return old

    def lock(self):
        self.events.append(("lock",))
        return 0

    def wait(self, cond, mutex, milliseconds):
        assert self.memory[SLEEPERS] > 0
        self.events.append(("wait", cond, mutex, milliseconds))

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


def test_unlock_with_no_sleepers_consumes_the_request_without_a_signal():
    model = Model(sleepers=0)
    assert model.run("_scheduler_signal", "_scheduler_unlock") == [
        ("unlock", "mutex", 0), ("released",), ("no_park_exit",),
    ]


def test_local_publication_needs_no_mutex_without_sleepers():
    assert Model(sleepers=0).run("_scheduler_signal_unlocked") == [("fence", "seq_cst")]


def test_local_publication_synchronizes_with_the_sleep_mutex_before_signalling():
    assert Model().run("_scheduler_signal_unlocked") == [
        ("fence", "seq_cst"), ("lock",), ("unlock", "mutex", 0),
        ("signal", "cond"), ("released",), ("no_park_exit",),
    ]


def test_idle_carrier_registers_before_rechecking_and_waiting():
    assert Model(sleepers=0).run("_carrier_wait_locked") == [
        ("sleepers", 1), ("fence", "seq_cst"), ("wait", "cond", "mutex", 1),
        ("sleepers", 0),
    ]


def test_work_or_stop_visible_after_registration_prevents_waiting():
    for ready, stop in ((1, 0), (0, 1), (1, 1)):
        model = Model(sleepers=0, ready=ready, stop=stop)
        assert model.run("_carrier_wait_locked") == [
            ("sleepers", 1), ("fence", "seq_cst"), ("sleepers", 0),
        ]
