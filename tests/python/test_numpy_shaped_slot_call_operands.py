"""Operand shapes from numpy's Python code that slot calls must root.

A call to an imported function roots each object operand in a slot. Two
shapes numpy uses had no operand producer: a slice inside a tuple subscript
key (numpy._core.numeric.outer: ``a.ravel()[:, newaxis]``) and the result of
``object.__getattribute__(obj, name)`` used as a receiver
(numpy.lib._npyio_impl: ``list(object.__getattribute__(self, '_obj').keys())``),
so compiling ``import numpy`` failed ("slot-call operand has no authoritative
source or owned result"). The getattr call also skipped its error check: a
missing attribute continued with NULL instead of raising AttributeError.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


HELPER = r'''
def multiply(a, b, out=None):
    return ("mul", a, b, out)
'''

PROGRAM = r'''
from helper import multiply

newaxis = None


def describe(part):
    if isinstance(part, slice):
        return ("slice", part.start, part.stop, part.step)
    return part


class Arr:
    def __init__(self, name):
        self.name = name

    def ravel(self):
        return self

    def __getitem__(self, key):
        if isinstance(key, tuple):
            return (self.name, tuple([describe(part) for part in key]))
        return (self.name, describe(key))


def lo(value):
    return value - 1


def outer(a, b, out=None):
    return multiply(a.ravel()[:, newaxis], b.ravel()[newaxis, :], out)


def bounded(a, k):
    return multiply(a[lo(k):k + 1, ::k], a[lo(3)::-1, k])


class Bag:
    def __init__(self):
        self._obj = {"x": 1, "y": 2}

    def names(self):
        return list(object.__getattribute__(self, '_obj').keys())

    def missing(self):
        try:
            return object.__getattribute__(self, 'nothing')
        except AttributeError as error:
            return "AttributeError " + str(error)


def main():
    print(outer(Arr("a"), Arr("b")))
    print(bounded(Arr("c"), 2))
    bag = Bag()
    print(sorted(bag.names()))
    print(bag.missing())


main()
'''

EXPECTED = (
    "('mul', ('a', (('slice', None, None, None), None)), ('b', (None, ('slice', None, None, None))), None)\n"
    "('mul', ('c', (('slice', 1, 3, None), ('slice', None, None, 2))), ('c', (('slice', 2, None, -1), 2)), None)\n"
    "['x', 'y']\n"
    "AttributeError 'Bag' object has no attribute 'nothing'\n"
)


@pytest.mark.integration
def test_numpy_operand_shapes_compile_and_run_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    (tmp_path / "helper.py").write_text(HELPER, encoding="utf-8")
    source = tmp_path / "shapes.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "shapes"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend
