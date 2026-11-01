"""Frame-local comparisons preserve Python evaluation order and operand owners.

References: Python expressions, Comparisons and Evaluation order; data model,
object.__eq__/__lt__/__le__/__gt__/__ge__. Native probes compare unchanged
programs with CPython and execute every selected collector.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


def _ir(tmp_path, source):
    program = tmp_path / "rooted_compare.py"
    program.write_text(source, encoding="utf-8")
    output = tmp_path / "rooted_compare.ll"
    compile_python(str(program), str(output), emit_llvm_only=True)
    return output.read_text(encoding="utf-8")


def _function(text, name):
    matched = re.search(r"define [^@]*@user_rooted_compare_" + name + r"\(.*?\n}\n", text, re.S)
    assert matched, name
    return matched.group(0)


@pytest.mark.parametrize("name", (
    "_emit_frame_rooted_object_compare", "_frame_root_slot_for_operand",
))
def test_frame_operand_helpers_have_static_self_host_signatures(name):
    from pcc.frontends.python.codegen._l1_codegen_static_methods import L1_CODEGEN_STATIC_METHODS

    entry = next(item for item in L1_CODEGEN_STATIC_METHODS if item["name"] == name)
    assert tuple((item["name"], item["kind"], item["has_default"])
                 for item in entry["call_sig"]) == (
        ("self", "pos", False), ("expr", "pos", False),
    )


def test_local_comparisons_reuse_frame_slots_and_keep_leases_on_the_slow_path(tmp_path):
    text = _ir(tmp_path, '''
def equal(left, right):
    return left == right
def less(left, right):
    return left < right
def owned(left, right):
    first = [left]
    second = [right]
    return first == second
print(equal(1, 2), less(1, 2), owned(1, 2))
''')
    for name in ("equal", "less", "owned"):
        body = _function(text, name)
        assert "frame.cmp.lhs" in body and "frame.cmp.rhs" in body
        assert "int.cmp.fast" in body and "int.cmp.slow" in body
        assert "@pcc_gc_foreign_lease_release(" in body
    for name in ("equal", "less"):
        body = _function(text, name)
        assert "@pcc_gc_pin(" not in body
        assert "@pcc_gc_retain(" not in body
        assert "@pcc_gc_root_copy_local(" not in body
        for argument in ("left", "right"):
            assert re.search(r"@pcc_gc_borrowed_lease_acquire\(ptr %" + argument + r"\.addr", body)
    for local in ("first", "second"):
        assert re.search(r"@pcc_gc_foreign_lease_acquire\(ptr %" + local + r"\.addr",
                         _function(text, "owned"))


def test_global_attribute_and_callback_operands_keep_the_capture_path(tmp_path):
    text = _ir(tmp_path, '''
global_value = [1]
def global_compare(other):
    return global_value == other
def field_compare(value, other):
    return value.field == other
def call_compare(value, callback):
    return value == callback()
print(global_compare([1]))
''')
    for name in ("global_compare", "field_compare", "call_compare"):
        assert "frame.cmp.lhs" not in _function(text, name)


PROGRAM = '''import gc
import math

def compare(left, right):
    return (left == right, left != right, left < right,
            left <= right, left > right, left >= right)

class Number:
    def __init__(self, value):
        self.value = value
    def __eq__(self, other):
        gc.collect()
        return self.value == other.value
    def __lt__(self, other):
        gc.collect()
        return self.value < other.value
    def __le__(self, other):
        gc.collect()
        return self.value <= other.value
    def __gt__(self, other):
        gc.collect()
        return self.value > other.value
    def __ge__(self, other):
        gc.collect()
        return self.value >= other.value

class Broken:
    def __init__(self, label):
        self.label = label
    def __eq__(self, other):
        gc.collect()
        raise ValueError("comparison failed")

def after_error(left, right):
    try:
        left == right
    except ValueError as error:
        print(str(error), left.label, right.label)

def owned(left, right):
    first = [left]
    second = [right]
    result = first == second
    first.append(9)
    second.append(10)
    print(result, first, second)

def rebind(left, right):
    result = left == (left := right)
    return result, left is right

def closure(left, right):
    def replace():
        nonlocal left
        left = right
        return right
    return left == replace()

def unbound(flag, right):
    if flag:
        left = [1]
    return left == right

def generated(left, right):
    yield left == right

def main():
    for left, right in [(0, 1), (1.5, 2.5), (2 ** 100, 2 ** 100 + 1),
                        ("alpha", "beta"), ([1], [2]), ((2,), (3,)),
                        (math.nan, math.nan)]:
        print(compare(left, right))
    print(compare(Number(1), Number(2)))
    same = Number(7)
    print(compare(same, same))
    after_error(Broken("left"), Broken("right"))
    owned(1, 2)
    print(rebind([1], [2]))
    print(closure([1], [2]))
    try:
        unbound(False, [1])
    except UnboundLocalError:
        print("unbound")
    print(next(generated([1], [1])))

main()
'''


@pytest.mark.integration
def test_frame_comparisons_match_cpython_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "comparisons.py"
    source.write_text(PROGRAM, encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], text=True,
                               capture_output=True, timeout=30)
    assert reference.returncode == 0, reference.stderr
    binary = tmp_path / "comparisons"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                                         PATH="/nonexistent"))
        assert (result.returncode, result.stdout, result.stderr) == (
            reference.returncode, reference.stdout, reference.stderr,
        ), (backend, result.stdout, result.stderr)
