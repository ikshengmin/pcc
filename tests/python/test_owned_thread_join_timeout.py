"""Check the owned stdlib Thread provider, not the frontend intrinsic."""

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


PROVIDER = Path(__file__).resolve().parents[2] / "pcc" / "stdlib" / "threading.py"
PROGRAM = '''from pcc.stdlib.threading import Event, Thread
import time


def wait_for_release(ready: Event, release: Event):
    ready.set()
    release.wait()


def exercise():
    ready = Event()
    release = Event()
    worker = Thread(target=wait_for_release, args=(ready, release))
    worker.start()
    print("ready", ready.wait(2.0), worker.is_alive())
    print("zero", worker.join(timeout=0) is None, worker.is_alive())
    print("negative", worker.join(-1) is None, worker.is_alive())
    start = time.monotonic()
    joined = worker.join(0.02)
    print("finite", joined is None, worker.is_alive(), time.monotonic() - start >= 0.015)
    try:
        worker.join("invalid")
    except TypeError:
        print("bad timeout rejected")
    release.set()
    print("released", worker.join(timeout=None) is None, worker.is_alive())
    print("again", worker.join() is None, worker.is_alive())


exercise()
'''


def _compile(tmp_path, out, *, ir_only=False):
    from pcc.frontends.python.pipeline import compile_python_multi

    entry = tmp_path / "thread_join_probe.py"
    entry.write_text(PROGRAM, encoding="utf-8")
    compile_python_multi(
        [str(entry), str(PROVIDER)], str(out), backend="self", ir_scaffold_mode="on",
        module_names=["thread_join_probe", "pcc.stdlib.threading"], entry_module="thread_join_probe",
        emit_llvm_only=ir_only,
    )


def test_thread_join_provider_forwards_timeout_to_runtime(tmp_path):
    ir = tmp_path / "thread_join_probe.ll"
    _compile(tmp_path, ir, ir_only=True)
    text = ir.read_text(encoding="utf-8")
    symbol = "user_pcc_stdlib_threading_Thread_join"

    def calls(function):
        body = re.search(r"^define [^\n]+@" + function + r"\([^\n]*\n(.*?)^}",
                         text, re.MULTILINE | re.DOTALL)
        assert body is not None, function
        return set(re.findall(r"\bcall [^\n]*?@([\w.]+)\(", body[1]))

    # A native intrinsic join in the consumer would not exercise the shim.
    entries = {"py_threading_thread_join", "py_threading_thread_join_args"}
    caller = calls("user_thread_join_probe_exercise")
    assert symbol in caller and caller.isdisjoint(entries)
    assert entries <= calls(symbol)


@pytest.mark.integration
def test_thread_join_provider_timeout_matches_cpython(tmp_path):
    exe = tmp_path / "thread_join_probe"
    reference = PROGRAM.replace("from pcc.stdlib.threading import Event, Thread", "from threading import Event, Thread")
    expected = subprocess.run([sys.executable, "-c", reference], capture_output=True, timeout=10, check=True)
    _compile(tmp_path, exe)
    for backend in (0,):
        run = subprocess.run([str(exe)], capture_output=True, timeout=10,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
