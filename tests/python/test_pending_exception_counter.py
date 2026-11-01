"""Pending exceptions are seen by every check while other threads raise.

py_err_occurred answers 0 without reading thread-local storage while no
thread holds a pending exception (py_exc_pending_threads); each thread counts
only its own empty <-> set transitions of its TLS slot. Four threads raise,
propagate through a call and catch exceptions concurrently, so a lost count
would let a thread miss its own exception. The program runs under all five
collectors and must print CPython 3.15's output.
"""

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


_PROGRAM = 'import threading\n\n\nclass Boom(Exception):\n    pass\n\n\ndef risky(i):\n    if i % 3 == 0:\n        raise Boom(i)\n    return i\n\n\ndef middle(i):\n    return risky(i) + 1\n\n\ndef worker(results, index):\n    caught = 0\n    total = 0\n    for i in range(3000):\n        try:\n            total += middle(i)\n        except Boom as exc:\n            caught += 1\n            if exc.args[0] != i:\n                raise\n    results[index] = (caught, total)\n\n\nresults = [None] * 4\nthreads = [threading.Thread(target=worker, args=(results, k)) for k in range(4)]\nfor thread in threads:\n    thread.start()\nfor thread in threads:\n    thread.join()\nprint(results)\ntry:\n    middle(3)\nexcept Boom as exc:\n    print("caught", exc)\nprint(middle(4), sum(middle(i) for i in range(1, 30) if i % 3))\n'

_EXPECTED = '[(1000, 3002000), (1000, 3002000), (1000, 3002000), (1000, 3002000)]\ncaught 3\n5 320\n'


@pytest.fixture(scope="module")
def pending_exception_program(tmp_path_factory):
    work = tmp_path_factory.mktemp("pending_exception")
    source = work / "pending_exception.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    executable = work / "pending_exception.out"
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on", libpython_mode="off")
    return executable


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_threads_see_their_own_pending_exceptions(pending_exception_program, backend):
    run = subprocess.run(
        [str(pending_exception_program)], capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
    )
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == _EXPECTED
