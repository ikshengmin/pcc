"""Decorators run once, at definition, and decorator factories are accepted.

A call to a decorated module function rebuilt the decorated object at every
call site execution, so each decorator ran once per call (a recording
decorator saw ['f', 'f', 'f', 'f'] for one definition and three calls) and a
stateful decorator started from fresh state each time. A decorator written as
a call to a module function (``@tag("b")``) was refused at compile time.
"""

from __future__ import annotations

import os
import subprocess
import sys

_PROGRAM = '''
applied = []


def record(fn):
    applied.append(fn.__name__)
    def wrapper(*args):
        return fn(*args) + 1
    return wrapper


def tag(label):
    applied.append("factory " + label)
    def deco(fn):
        def wrapper(*args, **kwargs):
            return "<" + label + ">" + fn(*args, **kwargs) + "</" + label + ">"
        return wrapper
    return deco


def counted(fn):
    state = {"calls": 0}
    def wrapper(x):
        state["calls"] += 1
        return (fn(x), state["calls"])
    return wrapper


@record
def double(x):
    return x * 2


@tag("b")
@tag("i")
def greet(name, punct="!"):
    return "hi " + name + punct


@counted
def square(x):
    return x * x


def main() -> None:
    print(double(1), double(2), double(3))
    print(greet("pcc"), greet("you", punct="?"))
    print(square(2), square(3), square(4))
    print(applied)


main()
'''


def test_decorators_run_once_and_factories_apply(tmp_path, pcc_runtime_archive, python_program_compiler):
    source = tmp_path / "decorators.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    output = tmp_path / "decorators"
    python_program_compiler(
        str(source), str(output), backend="self", libpython_mode="off",
        runtime_archive=str(pcc_runtime_archive),
    )
    for gc in range(5):
        ran = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PCC_GC_BACKEND=str(gc)),
        )
        assert ran.returncode == 0, f"GC{gc}: {ran.stdout}{ran.stderr}"
        assert ran.stdout == expected.stdout, f"GC{gc}: {ran.stdout}"
