"""Exercise the stdlib provider, not the frontend's intrinsic threading.Event."""

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


PROVIDER = Path(__file__).resolve().parents[2] / "pcc" / "stdlib" / "threading.py"
PROGRAM = '''from pcc.stdlib.threading import Event
from threading import Thread
import time


def set_later(event: Event):
    time.sleep(0.02)
    event.set()


def exercise():
    event = Event()
    print(event.wait(timeout=0), event.wait(-1))
    start = time.monotonic()
    expired = event.wait(0.02)
    print(expired, time.monotonic() - start >= 0.015)
    try:
        event.wait("invalid")
    except TypeError:
        print("bad timeout rejected")
    event.set()
    print(event.wait(), event.wait(None), event.wait(timeout=0), event.wait("ignored"))
    event.clear()
    print(event.is_set(), event.wait(0))
    worker = Thread(target=set_later, args=(event,))
    worker.start()
    print(event.wait(timeout=2.0))
    worker.join()


exercise()
'''


def _compile(tmp_path, out, *, ir_only=False):
    from pcc.frontends.python.pipeline import compile_python_multi

    entry = tmp_path / "event_probe.py"
    entry.write_text(PROGRAM, encoding="utf-8")
    compile_python_multi(
        [str(entry), str(PROVIDER)], str(out), backend="self", ir_scaffold_mode="on",
        module_names=["event_probe", "pcc.stdlib.threading"], entry_module="event_probe",
        emit_llvm_only=ir_only,
    )


def test_event_wait_provider_uses_deadline_only_for_timed_calls(tmp_path):
    ir = tmp_path / "event_probe.ll"
    _compile(tmp_path, ir, ir_only=True)
    text = ir.read_text(encoding="utf-8")
    symbol = "user_pcc_stdlib_threading_Event_wait"

    def calls(function):
        body = re.search(r"^define [^\n]+@" + function + r"\([^\n]*\n(.*?)^}",
                         text, re.MULTILINE | re.DOTALL)
        assert body is not None, function
        return set(re.findall(r"\bcall [^\n]*?@([\w.]+)\(", body[1]))

    # The test program actually calls the shim method, which must in turn
    # select the timed and indefinite runtime entries. Declarations don't count.
    entries = {"py_threading_event_wait", "py_threading_event_wait_args"}
    caller = calls("user_event_probe_exercise")
    assert symbol in caller and caller.isdisjoint(entries)
    assert entries <= calls(symbol)


@pytest.mark.integration
def test_event_wait_provider_matches_cpython(tmp_path):
    exe = tmp_path / "event_probe"
    reference = PROGRAM.replace("from pcc.stdlib.threading import Event", "from threading import Event")
    expected = subprocess.run([sys.executable, "-c", reference], capture_output=True, timeout=10, check=True)
    _compile(tmp_path, exe)
    for backend in range(5):
        run = subprocess.run([str(exe)], capture_output=True, timeout=10,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
