"""Unpacking assignment raises and iterates as CPython 3.15 does.

Python reference 7.2 (assignment) and CPython's UNPACK_SEQUENCE/UNPACK_EX: a
non-iterable value raises "cannot unpack non-iterable int object" (an error
from __iter__ itself passes through); a starred target iterates the value
once, collects the middle items and raises "not enough values to unpack
(expected at least N, got M)". Each program runs under all five collectors
and must print CPython 3.15's output.
"""

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


_SCALAR = 'def unpack(value):\n    a, b = value\n    return a, b\n\n\nfor value in (5, None, 2.5, (1, 2)):\n    try:\n        print(unpack(value))\n    except TypeError as exc:\n        print("TypeError", exc)\n\n\ndef starred(value):\n    a, *rest = value\n    return a, rest\n\n\nfor value in (7, [1, 2, 3], "xy"):\n    try:\n        print(starred(value))\n    except TypeError as exc:\n        print("TypeError", exc)\n\n\nclass Faulty:\n    def __iter__(self):\n        raise TypeError("iter refused")\n\n\ntry:\n    a, b = Faulty()\nexcept TypeError as exc:\n    print("TypeError", exc)\n'

_SCALAR_EXPECTED = "TypeError cannot unpack non-iterable int object\nTypeError cannot unpack non-iterable NoneType object\nTypeError cannot unpack non-iterable float object\n(1, 2)\nTypeError cannot unpack non-iterable int object\n(1, [2, 3])\n('x', ['y'])\nTypeError iter refused\n"


_STARRED = 'class Box:\n    pass\n\n\ndef gen(n):\n    for i in range(n):\n        print("gen", i)\n        yield i\n\n\ndef attempt(label, fn):\n    try:\n        print(label, fn())\n    except Exception as exc:\n        print(label, "raised", type(exc).__name__, exc)\n\n\ndef head_tail(value):\n    first, *middle, last = value\n    return first, middle, last\n\n\nattempt("list", lambda: head_tail([1, 2, 3, 4]))\nattempt("tuple", lambda: head_tail((1, 2)))\nattempt("string", lambda: head_tail("abc"))\nattempt("generator", lambda: head_tail(gen(3)))\nattempt("iterator", lambda: head_tail(iter({"x": 1, "y": 2})))\nattempt("range", lambda: head_tail(range(5)))\nattempt("too-few", lambda: head_tail([1]))\nattempt("empty", lambda: head_tail([]))\nattempt("scalar", lambda: head_tail(3))\n\n\ndef lead(value):\n    *init, last = value\n    return init, last\n\n\nattempt("lead", lambda: lead([1, 2, 3]))\nattempt("lead-one", lambda: lead([9]))\nbox = Box()\nseq = [0, 0, 0]\nbox.a, *box.rest, seq[1] = [10, 20, 30, 40]\nprint("targets", box.a, box.rest, seq)\n'

_STARRED_EXPECTED = "list (1, [2, 3], 4)\ntuple (1, [], 2)\nstring ('a', ['b'], 'c')\ngen 0\ngen 1\ngen 2\ngenerator (0, [1], 2)\niterator ('x', [], 'y')\nrange (0, [1, 2, 3], 4)\ntoo-few raised ValueError not enough values to unpack (expected at least 2, got 1)\nempty raised ValueError not enough values to unpack (expected at least 2, got 0)\nscalar raised TypeError cannot unpack non-iterable int object\nlead ([1, 2], 3)\nlead-one ([], 9)\ntargets 10 [20, 30] [0, 40, 0]\n"


_PROGRAMS = {'scalar': (_SCALAR, _SCALAR_EXPECTED), 'starred': (_STARRED, _STARRED_EXPECTED)}


@pytest.fixture(scope="module")
def unpack_programs(tmp_path_factory):
    work = tmp_path_factory.mktemp("unpack")
    built = {}
    for name, (source, expected) in _PROGRAMS.items():
        path = work / (name + ".py")
        path.write_text(source, encoding="utf-8")
        executable = work / (name + ".out")
        compile_python(str(path), str(executable), backend="self",
                       ir_scaffold_mode="on", libpython_mode="off")
        built[name] = (executable, expected)
    return built


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
@pytest.mark.parametrize("program", sorted(_PROGRAMS))
def test_matches_cpython_output(unpack_programs, program, backend):
    executable, expected = unpack_programs[program]
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
    )
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == expected
