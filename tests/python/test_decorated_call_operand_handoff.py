"""A decorated function's call result can feed a slot-call operand directly.

A call to an imported function roots each object operand in a slot before
the callee runs. A nested call's new result must be stored there before any
release or error check that follows it can park or collect. A call through a
decorated module function (numpy's array_function_dispatch shape) returned
its result after releasing the argument tuple and checking the error, so
`transpose(flip(m), axes)` in numpy.lib._function_base_impl.rot90 failed to
compile ("slot-call Call lacks an immediate owned-result handoff").
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


HELPER = r'''
def transpose(a, axes=None):
    return ("T", a, axes)


def failing(a, axes=None):
    raise ValueError("bad " + str(a))
'''

PROGRAM = r'''
import functools
from helper import transpose, failing


def array_function_dispatch(dispatcher):
    def decorator(implementation):
        @functools.wraps(implementation)
        def public_api(*args, **kwargs):
            relevant_args = dispatcher(*args, **kwargs)
            return implementation(*args, **kwargs)
        return public_api
    return decorator


def _flip_dispatcher(m, axis=None):
    return (m,)


@array_function_dispatch(_flip_dispatcher)
def flip(m, axis=None):
    if axis == "boom":
        raise KeyError("boom")
    return ["F", m, axis]


def rot(m, k, axes):
    if k == 1:
        return transpose(flip(m, axes[1]), axes)
    return flip(transpose(m, axes), axes[1])


def main():
    print(rot("m", 1, [0, 1]))
    print(rot("m", 3, [0, 1]))
    try:
        transpose(flip("m", "boom"), [0])
    except KeyError as error:
        print("KeyError", error)
    try:
        failing(flip("x", 2))
    except ValueError as error:
        print("ValueError", error)
    print(flip.__name__)


main()
'''

EXPECTED = (
    "('T', ['F', 'm', 1], [0, 1])\n"
    "['F', ('T', 'm', [0, 1]), 1]\n"
    "KeyError 'boom'\n"
    "ValueError bad ['F', 'x', 2]\n"
    "flip\n"
)


@pytest.mark.integration
def test_decorated_call_result_feeds_an_imported_call_operand(tmp_path, threaded_pcc_runtime_archive):
    (tmp_path / "helper.py").write_text(HELPER, encoding="utf-8")
    source = tmp_path / "decorated_operand.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "decorated_operand"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend
