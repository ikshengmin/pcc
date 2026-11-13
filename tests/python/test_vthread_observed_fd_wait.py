"""The fd-wait contract in docs/virtual-thread-io.md.

The native/reference cases live in gc_production_contract's I/O waitset probe;
these checks exercise the actual import, effect and registration owners.
"""

import ast
from pathlib import Path

import pytest

from pcc.frontends.python.codegen.native_modules import _is_virtual_thread_export
from pcc.frontends.python.codegen.vthread_effect_analysis import (
    compute_vthread_may_park_functions,
)
from pcc.frontends.python.parser import parse


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_virtual_thread_runtime.py"


def test_wait_operation_is_native_and_effect_visible_through_aliases():
    module = parse('''import pcc.virtual_thread as vt
from pcc.virtual_thread import wait_current_on_fd as wait_after_read
def leaf(fd):
    vt.wait_current_on_fd(fd, 1, 10)
def alias(fd):
    wait_after_read(fd, 1, 10)
def caller(fd):
    leaf(fd)
''', "observed_wait.py")
    assert _is_virtual_thread_export("wait_current_on_fd")
    _ids, names = compute_vthread_may_park_functions(module)
    assert names == {"leaf", "alias", "caller"}


@pytest.mark.parametrize("probe_first", [False, True])
def test_wait_entries_share_registration_with_only_the_requested_probe(probe_first):
    thread = {32: 2, 104: None, 120: 0}
    events = []
    def poll(fd, interest, timeout):
        events.append(("poll", fd, interest, timeout))
        return 0
    def register(owner, fd, interest, deadline):
        assert owner is thread and owner[32] == 3
        events.append(("register", fd, interest, deadline))
        return 0
    namespace = {
        "_checked": lambda owner: owner,
        "ptr_is_null": lambda value: value is None,
        "atomic_load_i64": lambda obj, offset, order: obj[offset],
        "atomic_store_i64": lambda obj, offset, value, order: obj.__setitem__(offset, value),
        "load_i64": lambda obj, offset: obj[offset],
        "load_ptr": lambda obj, offset: obj[offset],
        "_scheduler_lock": lambda: 0,
        "_scheduler_unlock": lambda: events.append(("unlock",)),
        "_timer_cancel": lambda owner: events.append(("timer_cancel",)),
        "_io_cancel": lambda owner: events.append(("io_cancel",)),
        "_now_ms": lambda: 100,
        "_io_add": register,
        "poll_fd": poll,
        "c_abi_export": lambda name: lambda function: function,
    }
    selected = {"_block_on_fd_common", "py_virtual_thread_block_on_fd", "py_virtual_thread_wait_on_fd"}
    nodes = [node for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in selected]
    exec(compile(ast.Module(nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    entry = "py_virtual_thread_block_on_fd" if probe_first else "py_virtual_thread_wait_on_fd"
    assert namespace[entry](thread, 12, 1, 7) == 0
    polls = [event for event in events if event[0] == "poll"]
    assert polls == ([("poll", 12, 1, 0)] if probe_first else [])
    assert ("register", 12, 1, 107) in events
    assert events[-1] == ("unlock",)
