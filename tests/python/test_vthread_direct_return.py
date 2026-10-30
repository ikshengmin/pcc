"""A virtual thread's return reaches its driver without a StopIteration.

The scheduler resumes a task's generator through py_gen_next_direct: a
return parks its value in the finished generator and the driver completes
the task with it, while generators the task drives itself keep the ordinary
StopIteration protocol (next(), for, a caught StopIteration's value). The
expected lines are the previous protocol's output on GC0-4.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import pcc.virtual_thread as vt


class Box:
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return "Box(%r)" % (self.value,)


def gen_with_return():
    yield 1
    yield 2
    return "inner-return"


def returns_none():
    vt.yield_now()


def returns_int():
    vt.yield_now()
    return 41 + 1


def returns_object():
    vt.yield_now()
    return Box([1, 2])


def returns_without_parking():
    return "no park"


def raises():
    vt.yield_now()
    raise ValueError("boom")


def nested_generators():
    it = gen_with_return()
    first = next(it)
    second = next(it)
    caught = None
    try:
        next(it)
    except StopIteration as stop:
        caught = stop.value
    total = 0
    for value in gen_with_return():
        total += value
    vt.yield_now()
    return (first, second, caught, total, next(iter([]), "default"))


def joins_children():
    children = [vt.spawn(returns_int), vt.spawn(returns_object), vt.spawn(returns_none)]
    out = []
    for child in children:
        vt.join(child)
        out.append(vt.result(child))
    return out


def main():
    tasks = [
        vt.spawn(returns_none),
        vt.spawn(returns_int),
        vt.spawn(returns_object),
        vt.spawn(returns_without_parking),
        vt.spawn(raises),
        vt.spawn(nested_generators),
        vt.spawn(joins_children),
    ]
    vt.run(1, 512)
    for task in tasks:
        print(vt.outcome(task), vt.result(task), repr(vt.exception(task)))


main()
'''

EXPECTED = (
    "1 None None\n"
    "1 42 None\n"
    "1 Box([1, 2]) None\n"
    "1 no park None\n"
    "2 None ValueError('boom')\n"
    "1 (1, 2, 'inner-return', 3, 'default') None\n"
    "1 [42, Box([1, 2]), None] None\n"
)


@pytest.mark.integration
def test_task_returns_and_nested_generators_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "direct_return.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "direct_return"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=60, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr
        assert ran.stdout == EXPECTED, f"GC{backend}: " + ran.stdout
