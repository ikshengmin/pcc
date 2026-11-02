"""Private frame inputs for membership and non-tagged arithmetic paths.

Reference: Python expressions, Binary arithmetic operations, Membership test
operations, and Evaluation order. Input leases must not consume caller roots.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


IR_SOURCE = '''
def sink(value):
    return value
def membership(needle, haystack):
    return needle in haystack
def absent(needle, haystack):
    return needle not in haystack
def strings(needle: str, haystack: str):
    return needle in haystack
def divide(left, right):
    return sink(left / right)
def floor(left, right):
    return sink(left // right)
def modulo(left, right):
    return sink(left % right)
print(membership(1, [1]), absent(2, [1]), divide(7, 2), floor(7, 2), modulo(7, 2))
'''


@pytest.fixture
def operations_ir(tmp_path):
    source = tmp_path / "frame_operations.py"
    source.write_text(IR_SOURCE, encoding="utf-8")
    output = tmp_path / "frame_operations.ll"
    compile_python(str(source), str(output), emit_llvm_only=True)
    return output.read_text(encoding="utf-8")


@pytest.mark.parametrize("name,inputs", (
    ("membership", ("needle", "haystack")), ("absent", ("needle", "haystack")),
    ("strings", ("needle", "haystack")),
    ("divide", ("left", "right")), ("floor", ("left", "right")),
    ("modulo", ("left", "right")),
))
def test_operations_lease_the_original_inputs_without_copying_them(operations_ir, name, inputs):
    matched = re.search(r"define [^@]*@user_frame_operations_" + name + r"\(.*?\n}\n",
                        operations_ir, re.S)
    assert matched, name
    body = matched.group(0)
    for source in inputs:
        assert re.search(r"@pcc_gc_borrowed_lease_acquire\(ptr %" + source + r"\.addr", body), body
        assert not re.search(r"@pcc_gc_root_copy_local\([^\n]*ptr %" + source + r"\.addr", body)
    if name in ("membership", "absent", "strings"):
        assert "@pcc_gc_pin(" not in body and "@pcc_gc_retain(" not in body
        assert "@py_obj_contains_builtin_rooted(" in body


PROGRAM = '''import gc

def sink(value):
    return value

def membership(needle, haystack):
    return needle in haystack, needle not in haystack

def strings(needle: str, haystack: str):
    return needle in haystack, needle not in haystack

def arithmetic(left, right):
    return sink(left / right), sink(left // right), sink(left % right)

class Value:
    def __init__(self, value):
        self.value = value
    def __truediv__(self, other):
        gc.collect()
        return self.value / other.value
    def __floordiv__(self, other):
        gc.collect()
        return self.value // other.value
    def __mod__(self, other):
        gc.collect()
        return self.value % other.value

class Container:
    def __init__(self, value):
        self.value = value
    def __contains__(self, needle):
        gc.collect()
        return needle.value == self.value

class Broken:
    def __init__(self, label):
        self.label = label
    def __contains__(self, needle):
        gc.collect()
        raise ValueError("contains failed")

def after_error(needle, haystack):
    try:
        needle in haystack
    except ValueError as error:
        print(str(error), needle.value, haystack.label)

def after_division_error(left, right):
    try:
        sink(left / right)
    except ZeroDivisionError:
        print("zero", left, right)

def owned_membership(value):
    needle = [value]
    haystack = [needle]
    result = needle in haystack
    needle.append(9)
    print(result, needle, haystack)

def rebound(needle, replacement):
    result = needle in (needle := replacement)
    return result, needle is replacement

def main():
    for needle, haystack in [(2, [1, 2]), (3, (1, 2)), ("key", {"key": 1}),
                             ("b", "abc"), (2, {1, 2})]:
        print(membership(needle, haystack))
    print(arithmetic(7, 2), arithmetic(-7, 2))
    print(arithmetic(2 ** 100, 2 ** 50))
    left = Value(7)
    right = Value(2)
    print(arithmetic(left, right), left.value, right.value)
    print(membership(left, Container(7)))
    print(strings("", ""), strings("β", "αβγ"))
    print(strings("missing", "prefix" * 100), strings("prefix", "prefix" * 100))
    after_error(left, Broken("kept"))
    after_division_error(7, 0)
    owned_membership(4)
    print(rebound(2, [1, 2]))

main()
'''


@pytest.mark.integration
def test_frame_operations_match_cpython_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "frame_operations.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=30)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "frame_operations"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for collector in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)))
        assert (result.returncode, result.stdout, result.stderr) == (
            expected.returncode, expected.stdout, expected.stderr,
        ), (collector, result.stdout, result.stderr)
