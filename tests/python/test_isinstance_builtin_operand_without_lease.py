"""isinstance against builtin types reads a parameter operand without a lease.

A stable parameter operand took a borrowed address lease that lasted across
classinfo evaluation and callbacks. Against a classinfo made only of
unshadowed builtin type names nothing is evaluated: the tests read the
operand's tag at once, with no gate in between, so one borrowed read-barrier
load replaces the lease (json encoding asks several per value). User
classes, shadowed names and dynamic classinfo keep the lease.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
class Box:
    def __init__(self, value):
        self.value = value


def builtin_kinds(obj):
    kinds = 0
    if isinstance(obj, int):
        kinds += 1
    if isinstance(obj, float):
        kinds += 2
    if isinstance(obj, str):
        kinds += 4
    if isinstance(obj, (list, tuple)):
        kinds += 8
    if isinstance(obj, dict):
        kinds += 16
    if isinstance(obj, (bytes, set)):
        kinds += 32
    return kinds


def user_kind(obj):
    return isinstance(obj, Box)


def churn(values, rounds):
    total = 0
    boxes = 0
    for index in range(rounds):
        garbage = [Box(index), {"k": index}, (index, str(index))]
        value = values[index % len(values)]
        total += builtin_kinds(value)
        if user_kind(value):
            boxes += 1
        total += len(garbage)
    return total, boxes


def main():
    values = [None, 7, -3, 2.5, "text", "", [1, 2], (3,), {"a": 1}, b"x", {4}, Box(1), True]
    print([builtin_kinds(value) for value in values])
    print([user_kind(value) for value in values])
    print(churn(values, 20000))


main()
'''


def _function_ir(text: str, name: str) -> str:
    match = re.search(r"^define [^\n]*@[\w.$]*" + name + r"\([^\n]*\{\n.*?^\}", text, re.M | re.S)
    assert match, name
    return match.group(0)


def test_builtin_classinfo_reads_parameter_without_a_lease(tmp_path):
    source = tmp_path / "kinds.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "kinds.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text(encoding="utf-8")
    builtin = _function_ir(text, "builtin_kinds")
    assert "@pcc_gc_borrowed_lease_acquire(" not in builtin
    assert builtin.count("@pcc_gc_load_borrowed_ptr(") >= 6
    user = _function_ir(text, "user_kind")
    assert "@pcc_gc_borrowed_lease_acquire(" in user


@pytest.mark.integration
def test_builtin_isinstance_matches_cpython_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "kinds.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "kinds"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
