"""Generic subtraction publishes NEW into an owner before cleanup."""
from __future__ import annotations

import re

import pytest

from pcc.frontends.python import (
    type_infer,
)
from pcc.frontends.python.py_ast import (
    BinOp,
    ComplexType,
    DynType,
    FloatType,
    IntType,
    Name,
)
from pcc.frontends.python.py_lift import (
    parse_and_lift,
)
from tests.python.test_shared_call_binding import (
    _emit as emit_binding,
)
from tests.python.test_slot_call_operand_roots import (
    SlotProbeCodegen,
    _calls,
    _emit as emit_operand,
)


def assert_subtraction_publication(text, count=1):
    calls = list(re.finditer(r"^  (%[^ ]+) = call [^\n]*@py_obj_sub\([^\n]*\)\n", text, re.M))
    assert len(calls) == count
    for call in calls:
        following = text[call.end():].splitlines()[0]
        assert following.lstrip().startswith("store ptr " + call.group(1) + ","), following
    assert _calls(text, "pcc_gc_foreign_lease_acquire")
    assert _calls(text, "pcc_gc_foreign_lease_release")
    assert not _calls(text, "py_int_to_i64_lane")


@pytest.mark.parametrize("expression,count", [
    ("left - right", 1),
    ("left - 1", 1),
    ("1 - right", 1),
    ("(left - right) - right", 2),
    ("left - (right - left)", 2),
    ("left - len(right)", 1),
    ("left[0] - right", 1),
])
def test_subtraction_object_operands_publish_before_cleanup(expression, count):
    text = emit_operand("def probe(left, right):\n    return slot_operand_probe(" + expression + ")\n")
    assert_subtraction_publication(text, count)


@pytest.mark.parametrize("site", ("argument", "default", "return", "later-error"))
def test_subtraction_rooted_contexts(site):
    source = "def take(*, value, later=None):\n    return value\ndef fail():\n    raise ValueError('later')\n"
    if site == "default":
        body = "    def target(value=left - right):\n        return value\n    return target()\n"
    elif site == "return":
        body = "    return left - right\n"
    elif site == "later-error":
        body = "    return take(value=left - right, later=fail())\n"
    else:
        body = "    return take(value=left - right)\n"
    assert_subtraction_publication(emit_binding(source + "def probe(left, right):\n" + body))


@pytest.mark.parametrize("annotation", ("int", "float"))
def test_subtraction_annotations_do_not_prove_a_primitive_kernel(annotation):
    text = emit_operand("def probe(left: " + annotation + ", right: " + annotation + "):\n    return slot_operand_probe(left - right)\n")
    assert_subtraction_publication(text)
    assert not _calls(text, "py_int_sub")
    assert not _calls(text, "py_float_sub")


@pytest.mark.parametrize("numeric", [
    IntType(name="pcc.i64"),
    IntType(name="pcc.u64", signed=False),
    IntType(name="int", width=8),
    IntType(name="int", width=32),
    IntType(name="int", signed=False),
    FloatType(name="float", width=32),
    FloatType(name="pcc.f64"),
    ComplexType(name="complex"),
])
def test_subtraction_keeps_separate_explicit_and_complex_routes(numeric):
    module = type_infer.infer_module(parse_and_lift("", "subtraction_projection.py", "subtraction_projection"))
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    left = Name(span=None, ty=numeric, ident="left")
    right = Name(span=None, ty=numeric, ident="right")
    for result_ty in (numeric, DynType(name="dyn")):
        expr = BinOp(span=None, ty=result_ty, op="-", lhs=left, rhs=right)
        assert codegen._slot_call_binary_runtime(expr, object_boundary=True) is None


def test_subtraction_retains_the_literal_integer_kernel():
    text = emit_operand("def probe():\n    return slot_operand_probe((1 << 100) - 1)\n")
    assert _calls(text, "py_int_sub")
    assert not _calls(text, "py_obj_sub")
