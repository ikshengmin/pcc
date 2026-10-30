"""A finished virtual thread's result, exception and outcome need no lock.

Completion stores the result, exception and outcome before the terminal
state, and nothing writes them afterwards (start, readiness and cancel all
refuse a finished thread). Every completion path therefore publishes state 4
with a release store, and the readers take the scheduler lock only while the
thread is unfinished; each request of the handler benchmark read three
results under it.
"""

import ast
import os
from pathlib import Path
import re
import subprocess
import types

import pytest

from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py/py_virtual_thread_runtime.py"
READERS = ("py_virtual_thread_result", "py_virtual_thread_exception", "py_virtual_thread_outcome")


class Thread:
    def __init__(self, state):
        self.fields = {32: state, 24: "value", 72: "error", 80: 1}


def _readers(events, thread):
    def load(pointer, offset):
        return pointer.fields[offset]

    def atomic_load(pointer, offset, order):
        events.append(("state", order))
        return pointer.fields[offset]

    def locked():
        events.append(("lock",))
        return 0

    namespace = {
        "_checked": lambda pointer: pointer,
        "ptr_is_null": lambda pointer: pointer is None,
        "atomic_load_i64": atomic_load,
        "load_i64": load,
        "pcc_gc_load_ptr": lambda owner, address: address,
        "ptr_add": lambda pointer, offset: pointer.fields[offset],
        "global_load_ptr": lambda name: name,
        "py_incref": lambda value: events.append(("incref", value)),
        "_scheduler_lock": locked,
        "_scheduler_unlock": lambda: events.append(("unlock",)),
        "null": lambda: None,
    }
    nodes = [node for node in ast.parse(RUNTIME.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in READERS]
    assert len(nodes) == len(READERS)
    for node in nodes:
        node.decorator_list = []
    code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType) and const.co_name in READERS:
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace


def test_finished_thread_reads_take_no_scheduler_lock():
    events = []
    thread = Thread(4)
    readers = _readers(events, thread)
    assert readers["py_virtual_thread_result"](thread) == "value"
    assert readers["py_virtual_thread_exception"](thread) == "error"
    assert readers["py_virtual_thread_outcome"](thread) == 1
    assert ("lock",) not in events
    assert events.count(("state", "acquire")) == 3


def test_unfinished_thread_reads_keep_the_scheduler_lock():
    events = []
    thread = Thread(2)
    readers = _readers(events, thread)
    readers["py_virtual_thread_result"](thread)
    readers["py_virtual_thread_outcome"](thread)
    assert events.count(("lock",)) == 2 and events.count(("unlock",)) == 2


def test_every_terminal_state_store_releases():
    source = RUNTIME.read_text()
    assert not re.search(r"(?<!atomic_)store_i64\(\w+, 32, 4\)", source)
    published = re.findall(r'atomic_store_i64\((\w+), 32, 4, "release"\)', source)
    assert len(published) == 3


PROGRAM = r'''
import pcc.virtual_thread as vt


class Box:
    def __init__(self, value):
        self.value = value


def child(n):
    vt.yield_now()
    if n % 5 == 0:
        raise ValueError(n)
    return Box(n)


def watcher(threads):
    total = 0
    for thread in threads:
        while vt.outcome(thread) == vt.OUTCOME_PENDING:
            vt.yield_now()
        if vt.outcome(thread) == vt.OUTCOME_RETURNED:
            total += vt.result(thread).value
        else:
            total += 1000 * len(vt.exception(thread).args)
    return total


def main():
    threads = [vt.spawn(child, n) for n in range(200)]
    watchers = [vt.spawn(watcher, threads) for _ in range(4)]
    vt.run(4, 1000000)
    print([vt.result(w) for w in watchers], vt.outcome(threads[0]), vt.outcome(threads[1]))


main()
'''

# Returned values 0..199 without multiples of five (16000), plus 1000 for
# each of the forty raised ones, seen by every watcher.
EXPECTED = "[56000, 56000, 56000, 56000] 2 1\n"


@pytest.mark.integration
def test_watchers_read_racing_completions_on_four_carriers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "finished_reads.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "finished_reads"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, attempt)
