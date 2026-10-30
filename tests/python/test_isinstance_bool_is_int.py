"""bool is a subclass of int: isinstance(True, int) is True, as in CPython.

The builtin isinstance path compared type tags exactly, and compile-time
folding matched a statically bool operand against IntType only, so both
answered False for a bool tested against ``int`` (the runtime's general
isinstance already answered True).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
def flag(n):
    return n > 0


def as_int(value):
    return isinstance(value, int)


def as_int_or_str(value):
    return isinstance(value, (str, int))


def as_bool(value):
    return isinstance(value, bool)


def main():
    static = flag(3)
    print(isinstance(static, int), isinstance(True, int), isinstance(False, (int, str)))
    print(isinstance(flag(-1), int), isinstance(static, float), isinstance(static, bool))
    for value in (True, False, 0, 7, 2.5, "x", None, [1]):
        print(as_int(value), as_int_or_str(value), as_bool(value), isinstance([value][0], int))


main()
'''


@pytest.mark.integration
def test_bool_is_an_int_like_cpython_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "bool_is_int.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "bool_is_int"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
