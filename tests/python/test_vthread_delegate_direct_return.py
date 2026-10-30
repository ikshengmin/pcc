"""A parking callee returns to its delegating caller without a StopIteration.

A call from a resumable function to a may-park function drives the callee's
generator. Its return used to finish with a raised StopIteration that the
caller matched, read and cleared (a graph-locked exception swap per call);
the driver now steps it with py_gen_next_direct and takes the delivered
value. Raised exceptions, None results and callees that return without
parking keep their meaning.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import pcc.virtual_thread as vt


class Box:
    def __init__(self, value):
        self.value = value


def child_value(n):
    vt.yield_now()
    return Box(n)


def child_maybe(n):
    if n < 0:
        vt.yield_now()
    return n * 2


def child_raises(n):
    vt.yield_now()
    raise ValueError(n)


def child_none(n):
    vt.yield_now()


def parent():
    total = 0
    for index in range(100):
        box = child_value(index)
        total += box.value
        total += child_maybe(index)
        try:
            child_raises(index)
        except ValueError as error:
            total += len(error.args)
        if child_none(index) is None:
            total += 1
    return total


def main():
    task = vt.spawn(parent)
    vt.run(1, 100000)
    print(vt.outcome(task), vt.result(task), repr(vt.exception(task)))


main()
'''

# 4950 (box values) + 9900 (doubled) + 100 (one arg per error) + 100 (None).
EXPECTED = "1 15050 None\n"


def test_delegation_steps_children_through_direct_return(tmp_path):
    source = tmp_path / "delegate_return.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "delegate_return.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text(encoding="utf-8")
    assert re.search(r"call ptr \([^)]*\) @py_gen_next_direct\(", text)
    assert not re.search(r"call ptr \([^)]*\) @py_gen_next\(", text)


@pytest.mark.integration
def test_delegated_returns_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "delegate_return.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "delegate_return"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=60, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend
