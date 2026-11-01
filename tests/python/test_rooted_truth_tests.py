"""Truth tests of a frame's own local pass its root slot to the runtime.

``if not x`` on a local object used to load the value, retain and pin it,
call py_obj_truthy and unpin and release it again: four protocol calls
around one real call. A local's root slot belongs to this activation alone,
so the compiled code now passes the slot to py_obj_truthy_rooted, which
decides builtin values by a field read and retains and pins only around a
user ``__bool__``/``__len__``. Globals, class-body names and expressions
keep the general path. Python reference: "Truth Value Testing" in the
standard types documentation; ``object.__bool__`` and ``object.__len__``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


_PROGRAM = r'''
import math


class Flag:
    def __init__(self, value):
        self.value = value

    def __bool__(self):
        return self.value


class Sized:
    def __init__(self, size):
        self.size = size

    def __len__(self):
        return self.size


class Broken:
    def __bool__(self):
        raise ValueError("no truth here")


class Plain:
    pass


def truth(value):
    if value:
        return "T"
    return "F"


def negated(value):
    if not value:
        return "F"
    return "T"


def loop_until_empty(items):
    count = 0
    while items:
        items.pop()
        count += 1
    return count


def unbound(flag):
    if flag:
        value = [1]
    if value:
        return "set"
    return "empty"


values = [None, True, False, 0, 7, -1, 10 ** 30, 0.0, -0.0, 2.5, math.nan, "", "a",
          b"", b"x", bytearray(), [], [0], (), (0,), {}, {0: 0}, set(), {0},
          Flag(True), Flag(False), Sized(0), Sized(3), Plain(), range(0), range(2)]
print("".join(truth(value) for value in values))
print("".join(negated(value) for value in values))
print(loop_until_empty([1, 2, 3]))
for bad in (Broken(), NotImplemented):
    try:
        truth(bad)
    except Exception as error:
        print(type(error).__name__, error)
try:
    unbound(False)
except UnboundLocalError as error:
    print("UnboundLocalError", error)
print(unbound(True))
'''


def _ir(tmp_path, source: str) -> str:
    path = tmp_path / "truth.py"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "truth.ll"
    compile_python(str(path), str(output), emit_llvm_only=True)
    return output.read_text(encoding="utf-8")


def _function(ir_text: str, name: str) -> str:
    match = re.search(r"define [^@]*@" + name + r"\(.*?\n}\n", ir_text, re.S)
    assert match, name
    return match.group(0)


def test_local_truth_tests_pass_their_root_slot(tmp_path):
    text = _ir(tmp_path, _PROGRAM)
    for name in ("user_truth_truth", "user_truth_negated", "user_truth_loop_until_empty"):
        assert "@py_obj_truthy_rooted(" in _function(text, name), name
    # These two functions do nothing but the truth test: no protocol call
    # around it remains. (The loop also boxes its counter, which pins.)
    for name in ("user_truth_truth", "user_truth_negated"):
        body = _function(text, name)
        assert "@pcc_gc_pin(" not in body and "@pcc_gc_retain(" not in body, name


def test_globals_and_expressions_keep_the_general_path(tmp_path):
    text = _ir(tmp_path, '''
flag = [1]


def from_global():
    if flag:
        return 1
    return 0


def from_call(items):
    if items[0]:
        return 1
    return 0


print(from_global(), from_call([[]]))
''')
    for name in ("user_truth_from_global", "user_truth_from_call"):
        assert "@py_obj_truthy_rooted(" not in _function(text, name), name


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_rooted_truth_tests_match_cpython(tmp_path, backend):
    source = tmp_path / "truth.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "truth"
    compile_python(str(source), str(binary), backend="self")
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
    assert (ran.returncode, ran.stderr) == (0, "")
    assert ran.stdout == expected.stdout
