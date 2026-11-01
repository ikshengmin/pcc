"""Frame-local comparisons preserve Python evaluation order and operand owners.

References: Python expressions, Comparisons and Evaluation order; data model,
object.__eq__/__lt__/__le__/__gt__/__ge__. Native probes compare unchanged
programs with CPython and execute every selected collector.
"""

from __future__ import annotations

import ast
import operator
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py.py_abi_constants import (
    PY_TYPE_BOOL,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
    PY_TYPE_STR,
    PY_TYPE_USER_CLASS_START,
)


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


def _primitive_probe():
    path = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj_ops_compare.py"
    node = next(item for item in ast.parse(path.read_text()).body
                if isinstance(item, ast.FunctionDef) and item.name == "py_obj_compare_builtin_rooted")
    node.decorator_list = []
    calls = []
    tags = {bool: PY_TYPE_BOOL, int: PY_TYPE_INT, float: PY_TYPE_FLOAT, str: PY_TYPE_STR}
    extensions = set()
    operations = {0: operator.lt, 1: operator.le, 2: operator.eq, 4: operator.gt, 5: operator.ge}

    def compare(left, right, op):
        calls.append((left, right, op))
        return int(operations[op](left, right))

    namespace = {
        "null": lambda: None,
        "ptr_is_null": lambda value: value is None,
        "pcc_gc_load_borrowed_ptr": lambda owner, slot: slot[0],
        "_type_of": lambda value: tags.get(type(value), PY_TYPE_USER_CLASS_START),
        "_is_number_tag": lambda tag: tag in (PY_TYPE_BOOL, PY_TYPE_INT, PY_TYPE_FLOAT),
        "pcc_capi_is_cext_type_tag": lambda tag: tag in extensions,
        "PY_TYPE_STR": PY_TYPE_STR,
        "_ORDER_LT": 0, "_ORDER_LE": 1, "_ORDER_GT": 4, "_ORDER_GE": 5,
        "py_obj_eq_value": lambda left, right: compare(left, right, 2),
        "_obj_order": compare,
    }
    exec(compile(ast.Module([node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["py_obj_compare_builtin_rooted"], calls, extensions, operations


@pytest.mark.parametrize("op", (0, 1, 2, 4, 5))
@pytest.mark.parametrize("left,right", (
    (1, 2), (2 ** 100, 2 ** 100 + 1), (2 ** 53 + 1, float(2 ** 53)),
    (True, 1.0), (-0.0, 0.0), (float("nan"), float("nan")),
    ("", "alpha"), ("same", "same"), ("alpha", "beta"),
))
def test_primitive_probe_preserves_comparison_and_leaves_input_slots_intact(left, right, op):
    probe, calls, _extensions, operations = _primitive_probe()
    slots = ([left], [right])
    assert probe(*slots, op) == int(operations[op](left, right))
    assert len(calls) == 1
    assert slots[0][0] is left and slots[1][0] is right


@pytest.mark.parametrize("left,right", (([], []), ({}, {}), (None, None), ("1", 1), (object(), object())))
def test_nonprimitive_pairs_are_left_to_the_leased_callback_path(left, right):
    probe, calls, _extensions, _operations = _primitive_probe()
    assert probe([left], [right], 2) == -1
    assert calls == []


def test_extension_tag_overrides_and_unknown_operations_do_not_enter_the_primitive_path():
    probe, calls, extensions, _operations = _primitive_probe()
    extensions.add(PY_TYPE_FLOAT)
    assert probe([1.0], [2.0], 2) == -1
    assert probe([1], [2], 99) == -1
    assert calls == []


def test_local_comparisons_reuse_frame_slots_and_keep_leases_on_the_slow_path(tmp_path):
    text = _ir(tmp_path, '''
def equal(left, right):
    return left == right
def less(left, right):
    return left < right
def strings(left: str, right: str):
    return left == right
def owned(left, right):
    first = [left]
    second = [right]
    return first == second
print(equal(1, 2), less(1, 2), strings("a", "b"), owned(1, 2))
''')
    for name in ("equal", "less", "strings", "owned"):
        body = _function(text, name)
        assert "frame.cmp.lhs" in body and "frame.cmp.rhs" in body
        assert "int.cmp.fast" in body and "int.cmp.slow" in body
        assert "@pcc_gc_foreign_lease_release(" in body
        assert "@py_obj_compare_builtin_rooted(" in body
        assert "obj.cmp.leased" in body and "obj.cmp.handled" in body
    for name in ("equal", "less", "strings"):
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

calls = []

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

class Left:
    def __init__(self):
        self.label = "left"
    def __eq__(self, other):
        calls.append(self.label)
        gc.collect()
        return NotImplemented

class Right:
    def __eq__(self, other):
        calls.append("right")
        gc.collect()
        return other.label == "left"

def equal(left, right):
    return left == right

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
    left = Left()
    right = Right()
    print(equal(left, right), calls)
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
